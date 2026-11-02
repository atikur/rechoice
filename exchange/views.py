"""Fictional, session-scoped exchange API. No commerce writes outside draft records."""
import functools
import hashlib
import hmac
import json
import logging
from collections import defaultdict
from pathlib import Path
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_protect
from kombu.exceptions import OperationalError as BrokerError
from PIL import Image
from rest_framework import serializers

from secondlook.domain import (
    DomainError, OrderLine as DomainLine, PatternLevel, Policy as DomainPolicy,
    Preferences as DomainPreferences, Quote as DomainQuote, Variant as DomainVariant,
    exclusions as domain_exclusions, make_quote, revalidate_quote, shortlist, size_options,
)
from secondlook.jewellery import (
    ACCESSORIES, COLOURS, JEWELLERY, JewelleryPreferences, jewellery_exclusions, jewellery_shortlist,
)
from secondlook.shades import BEAUTY, ShadePreferences, shade_exclusions, shade_shortlist
from .models import (
    EvidenceEvent, ExchangeRequest, ExchangeSession, LookPreview, OrderLine, PolicySnapshot,
    PreviewJob, PrivateAsset, ProductVariant, Quote,
)
from secondlook.providers import FAILED, PENDING, SUCCEEDED
from .assets import discard_asset, normalize_photo, private_image_exists, read_private_image, save_private_image
from . import assistant, fidelity_gate, preview_jobs
from .gate_visuals import change_overlay
from .preview_jobs import ProviderUnavailable, discard_person_asset, get_provider, preview_label
from .tasks import HEARTBEAT_KEY, drive_preview_job
from .serializers import (
    CompareSerializer, DraftSerializer, InterpretSerializer, ItemSerializer, JewelleryPreferenceSerializer, JudgeCodeSerializer,
    LookSerializer, PreferenceSerializer, ShadePreferenceSerializer, PreviewSerializer, QuoteSerializer,
    ReasonSerializer, ReplySerializer,
    StaffDecisionSerializer, StartSerializer,
)

log = logging.getLogger(__name__)


def _error(code, message, status=422):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


CLOSED_STATUSES = ("CANCELLED", "EXPIRED", "DRAFT_CREATED")
PATTERN_WORDS = {0: "quiet", 1: "moderate", 2: "busy"}
CATALOG_PHOTO_LABEL = "Product photo from Pixabay, used for this fictional store."
STATUS_ORDER = ("STARTED", "PREFERENCES_CONFIRMED", "CANDIDATES_READY",
                "COMPARISON_READY", "QUOTE_READY", "DRAFT_CREATED")
# Why the item is going back decides the path. A preview can only help with how something looks.
RETURN_REASONS = {
    "style": {"label": "I don't like how it looks", "next": "preferences"},
    "fit": {"label": "It doesn't fit", "next": "sizes",
            "message": "A try-on preview cannot show fit, so these are other sizes of the same top."},
    "shade": {"label": "The shade looks wrong", "next": "shade_preferences"},
    "look": {"label": "I don't like how they look on me", "next": "jewellery_preferences"},
    "reaction": {"label": "It irritated my skin, lips or nails", "next": "support",
                 "message": "Stop using it. A reaction needs the retailer's support team, and a doctor if it is "
                            "serious. Rechoice will not suggest another product after a reaction."},
    "damaged": {"label": "It arrived damaged or it is the wrong item", "next": "support",
                "message": "Damage or a wrong item needs the retailer's support team to check the order. "
                           "A try-on preview cannot help here, and this demo does not start a claim."},
    "changed_mind": {"label": "I changed my mind", "next": "return",
                     "message": "You can use the retailer's normal return route."},
}
# Which reasons each kind of item offers. A reaction always goes to people, never to a preview.
REASONS_FOR = {"apparel": ("style", "fit", "damaged", "changed_mind"),
               "beauty": ("shade", "reaction", "damaged", "changed_mind"),
               "jewellery": ("look", "reaction", "damaged", "changed_mind"),
               "accessories": ("look", "damaged", "changed_mind")}
CATEGORY_NAMES = {"tops": "Top", "lip": "Lipstick", "foundation": "Foundation", "nail": "Nail polish",
                  "earrings": "Earrings", "necklace": "Necklace", "hair": "Hair colour", "shoes": "Shoes",
                  "bag": "Bag"}
# The "look" reason reads "they" for a pair of earrings or shoes and "it" for a necklace or a bag.
LOOK_LABELS = {"necklace": "I don't like how it looks on me", "bag": "I don't like how it looks on me"}
# Categories with no try-on, and why. The bags pilot on 28 October 2026 failed its gate (docs/accessories-pilot-plan.md).
PREVIEW_OFF = {"bag": "Bag previews are off: YouCam's bag try-on dresses the person in a new outfit and setting, so it "
                      "can't show a bag on you fairly. Compare the bags by their details."}
# Jewellery and accessories share the size-and-colour exchange; accessories have colour families, not metals.
SIZE_AND_COLOUR = ("jewellery", "accessories")


def _group(line):
    """The kind of exchange an item gets: apparel, a beauty shade, jewellery, or shoes and bags."""
    category = line.variant.category
    return ("beauty" if category in BEAUTY else "jewellery" if category in JEWELLERY
            else "accessories" if category in ACCESSORIES else "apparel")


def _reasons_for(line):
    return [{"code": code, "label": LOOK_LABELS.get(line.variant.category, RETURN_REASONS[code]["label"])
             if code == "look" else RETURN_REASONS[code]["label"]} for code in REASONS_FOR[_group(line)]]


def _closed(session):
    if session.status in CLOSED_STATUSES:
        return _error("SESSION_CLOSED", "This session cannot be changed.", 409)
    return None


def _advance_status(session, status):
    """Move a session forward only. Re-confirming preferences is the one deliberate reset."""
    order = {name: rank for rank, name in enumerate(STATUS_ORDER)}
    if order[status] > order.get(session.status, len(STATUS_ORDER)):
        session.status = status
        session.save(update_fields=["status"])


def _body(request):
    if len(request.body) > 10000:
        raise ValueError("Request is too large.")
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Expected a JSON object.") from None
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def _validated(request, serializer_class):
    try:
        data = _body(request)
    except ValueError as exc:
        return None, _error("INVALID_INPUT", str(exc))
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        return None, _error("INVALID_INPUT", "Check the highlighted fields and try again.")
    return serializer.validated_data, None


def _owned(request, session_id):
    if not request.session.session_key:
        return None
    return ExchangeSession.objects.filter(
        id=session_id, browser_session_key=request.session.session_key,
        expires_at__gt=timezone.now(),
    ).select_related("order_line", "order_line__variant").first()


def _policy(line):
    return PolicySnapshot.objects.filter(retailer_id=line.retailer_id, version=line.policy_version).first()


def _domain_line(line):
    return DomainLine(line.retailer_id, line.variant.sku, line.quantity, line.paid_minor,
                      line.eligible_credit_minor, line.currency, line.simple_pricing, line.policy_version,
                      line.variant.style_code or line.variant.sku, line.variant.size_label,
                      line.variant.category, line.opened, line.variant.shade_hex or None,
                      line.variant.length_mm, line.variant.color_tag or None, line.variant.form or None)


def _domain_policy(policy):
    if policy is None:
        raise DomainError("POLICY_UNKNOWN", "Exchange policy is unavailable.")
    return DomainPolicy(policy.version, policy.allowed_category, policy.expires_at,
                        policy.fee_minor, policy.exchange_allowed, tuple(policy.categories or ()),
                        policy.opened_same_line)


def _domain_variant(item):
    pattern = PatternLevel(item.pattern_level) if item.pattern_level in (0, 1, 2) else None
    return DomainVariant(item.retailer_id, item.sku, item.category, item.size_label,
                         item.currency, item.price_minor, item.stock_qty, pattern,
                         item.color_tag or None, item.sleeve or None, item.neckline or None,
                         item.image_asset_id, item.version, item.style_code or item.sku, item.shade_hex or None,
                         item.length_mm, item.form or None)


def _domain_preferences(session):
    data = session.preferences
    if not data:
        raise DomainError("PREFERENCES_REQUIRED", "Confirm your preferences first.")
    group = _group(session.order_line)
    if group == "beauty":
        return ShadePreferences(data["max_top_up_minor"], data.get("lightness") or None, data.get("tone") or None,
                                data.get("intensity") or None, data["currency"])
    if group in SIZE_AND_COLOUR:
        return JewelleryPreferences(data["max_top_up_minor"], data.get("size") or None, data.get("metal") or None,
                                    data["currency"])
    return DomainPreferences(
        data["max_top_up_minor"], data["currency"],
        PatternLevel.BUSY if data.get("quieter_print") else None,
        data.get("sleeve_required") or None,
        tuple(data.get("color_preferences") or []),
        data.get("neckline_preference") or None,
    )


def _eligible(session):
    line = session.order_line
    items = list(ProductVariant.objects.filter(retailer_id=line.retailer_id))
    rank = {"beauty": shade_shortlist, "jewellery": jewellery_shortlist,
            "accessories": jewellery_shortlist}.get(_group(line), shortlist)
    return rank(_domain_line(line), _domain_policy(_policy(line)),
                _domain_preferences(session), [_domain_variant(x) for x in items], timezone.now())


def _exclusions(session):
    line = session.order_line
    items = list(ProductVariant.objects.filter(retailer_id=line.retailer_id))
    explain = {"beauty": shade_exclusions, "jewellery": jewellery_exclusions,
               "accessories": jewellery_exclusions}.get(_group(line), domain_exclusions)
    return explain(_domain_line(line), _domain_policy(_policy(line)),
                   _domain_preferences(session), [_domain_variant(x) for x in items], timezone.now())


def _shade_fields(item):
    if not item.shade_hex:
        return {}
    return {"shade_hex": item.shade_hex, "shade_name": item.shade_name, "product_line": item.product_line,
            "finish": item.finish, "swatch_label": "Shade swatch. Colours on screen vary with display."}


def _line_json(line):
    return {"line_id": line.id, "fictional_order_id": line.fictional_order_id, "sku": line.variant.sku,
            "title": line.variant.title, "category": line.variant.category,
            "category_name": CATEGORY_NAMES.get(line.variant.category, line.variant.category),
            "size_label": line.variant.size_label, "paid_minor": line.paid_minor,
            "eligible_credit_minor": line.eligible_credit_minor, "currency": line.currency, "opened": line.opened,
            **_image_fields(line.variant), **_shade_fields(line.variant)}


def _image_fields(item):
    if not item.image_asset_id:
        return {"image_url": None, "image_alt": None, "image_label": None}
    if item.category == "necklace":
        alt = f"Photo of {item.title}: a {item.color_tag} {item.form} necklace, {item.length_mm // 10} cm long."
        return {"image_url": static(item.image_asset_id), "image_alt": alt, "image_label": CATALOG_PHOTO_LABEL,
                "details": f"{item.color_tag.capitalize()} · {item.form} · {item.length_mm // 10} cm"}
    if item.category == "shoes":
        alt = f"Photo of {item.title}: {item.form} shoes, heel {item.length_mm} mm."
        return {"image_url": static(item.image_asset_id), "image_alt": alt, "image_label": CATALOG_PHOTO_LABEL,
                "details": f"{item.color_tag.capitalize()} colour · {item.form} · heel {item.length_mm} mm"}
    if item.category == "bag":
        alt = f"Photo of {item.title}: a {item.form}, {item.length_mm // 10} cm wide."
        return {"image_url": static(item.image_asset_id), "image_alt": alt, "image_label": CATALOG_PHOTO_LABEL,
                "details": f"{item.color_tag.capitalize()} colour · {item.form} · {item.length_mm // 10} cm wide"}
    if item.category in JEWELLERY:
        alt = f"Photo of {item.title}: one {item.color_tag} {item.form} earring, {item.length_mm} mm long."
        return {"image_url": static(item.image_asset_id), "image_alt": alt, "image_label": CATALOG_PHOTO_LABEL,
                "details": f"{item.color_tag.capitalize()} · {item.form} · {item.length_mm} mm"}
    pattern = PATTERN_WORDS.get(item.pattern_level, "unrecorded")
    alt = (f"Photo of {item.title}: {item.color_tag} {item.sleeve}-sleeve top, "
           f"{item.neckline} neckline, {pattern} pattern.")
    return {"image_url": static(item.image_asset_id), "image_alt": alt, "image_label": CATALOG_PHOTO_LABEL}


def _candidate_json(candidate, items):
    item = items[candidate.variant.sku]
    return {
        "sku": item.sku, "title": item.title, "size_label": item.size_label,
        "price_minor": item.price_minor, "currency": item.currency,
        "top_up_minor": candidate.top_up_minor, "reasons": candidate.reasons,
        **_image_fields(item), **_shade_fields(item),
    }


def _is_baseline(job):
    """True for a preview of the shopper's own purchased top rather than an alternative."""
    return job.variant_id == job.owner_session.order_line.variant_id


STUDY_RECORDED_LABEL = "Recorded YouCam result from the 30 October 2026 gate study. No new AI run."


def _label(job):
    """The preview label, naming the gate study when a recorded top replays from it rather than the October pilot."""
    label = preview_label(job)
    if job.provider == "replay" and job.provider_task_id and preview_jobs.RECORDED_LABEL in label:
        from .replay import recorded, recorded_gate_study
        key = tuple(job.provider_task_id.split(":", 2)[1:])
        pilot = {(r["model"], r["sku"]) for r in recorded()["results"] if r["state"] == "succeeded"}
        study = {(r["model"], r["sku"]) for r in recorded_gate_study()["results"]}
        # The replay provider prefers a pilot recording, so only pairs the pilot never rendered came from the study.
        if key in study and key not in pilot:
            label = label.replace(preview_jobs.RECORDED_LABEL, STUDY_RECORDED_LABEL)
    return label


def _gate_pending(job, visible):
    return bool(visible and job.state == SUCCEEDED and not job.look_id and not _is_baseline(job)
                and job.variant.category == "tops" and not job.gate_status)


def _job_json(job):
    result = job.result_asset
    visible = result is not None and result.expires_at > timezone.now()
    # An earring resized to its catalog length is shown resized, with YouCam's own render a click away.
    sized = (job.sized_asset_id if visible and job.size_check == "size-resized" and job.sized_asset
             and job.sized_asset.expires_at > timezone.now() else None)
    size_note = job.size_note
    size_status = job.size_check
    if visible and size_status == "size-resized" and not sized:
        size_note = "The corrected copy is unavailable. Showing YouCam's original render; its relative size is not verified."
        size_status = "size-differs"
    candidate = None
    if not _is_baseline(job) and not job.look_id:
        try:
            candidate = next((c for c in _eligible(job.owner_session) if c.variant.sku == job.variant.sku), None)
        except DomainError:
            pass
    from .preview_trust import preview_trust
    return {"job_id": str(job.id), "sku": job.variant.sku, "state": job.state, "baseline": _is_baseline(job),
            "provider": job.provider, "error_code": job.error_code, "label": _label(job),
            "message": preview_jobs.job_message(job), "retry_allowed": not preview_jobs.retry_problem(job),
            "attempt": 2 if job.retry_of_id else 1,
            "image_url": f"/api/assets/{sized or result.id}" if visible else None,
            "rendered_url": f"/api/assets/{result.id}" if sized else None,
            "colour_check": ({"status": job.colour_check, "note": job.colour_note}
                             if visible and job.colour_note and _shown_to_shopper(job.colour_check)
                             and (job.variant.category != "tops" or job.variant.reference_colour_status == "reviewed"
                                  or job.colour_check == "pattern") else None),
            "size_check": {"status": size_status, "note": size_note} if visible and size_note else None,
            "gate": ({"status": job.gate_status, "note": job.gate_note, "print_ratio": job.print_ratio,
                      "outside_change_pct": job.outside_change_pct, "flags": _gate_flags(job),
                      "badge": _gate_badge(job)}
                     if visible and job.gate_status and job.gate_status != "reference" else None),
            # The catalog colour beside the colour the check measured on this preview, shown only where the
            # colour check itself is shown: plain tops with a reviewed reference colour.
            "swatches": ({"product": job.variant.reference_colour, "preview": job.preview_colour}
                         if visible and job.preview_colour and job.variant.category == "tops"
                         and job.colour_check in ("close", "differs")
                         and job.variant.reference_colour_status == "reviewed" else None),
            # The stability check's comparison drawn over the preview: what changed inside and outside the garment.
            "changes": ({"url": f"/api/previews/{job.id}/changes", "outside_change_pct": job.outside_change_pct,
                         "stable_pct": fidelity_gate.STABLE_PCT}
                        if visible and job.variant.category == "tops" and not job.look_id and job.person_asset_id
                        else None),
            # The worker measures a finished top just after saving it, so a page can see it before its verdict.
            "gate_pending": _gate_pending(job, visible),
            "trust": preview_trust(job, visible=visible, baseline=_is_baseline(job), candidate=candidate, corrected=bool(sized))
                     if not job.look_id else None}


def _gate_flags(job):
    """The measured checks a concern verdict rests on, in the shopper's words."""
    flags = []
    if job.colour_check == "differs":
        flags.append("colour")
    if (job.owner_session.preferences.get("quieter_print") and job.print_ratio is not None
            and job.print_ratio > fidelity_gate.QUIET_RATIO):
        flags.append("print")
    if job.outside_change_pct is not None and job.outside_change_pct > fidelity_gate.STABLE_PCT:
        flags.append("redrawn")
    return flags


def _gate_badge(job):
    """The verdict in two or three words, for the label drawn on the preview itself."""
    if job.gate_status == "shows":
        return "Checks agree"
    if job.gate_status == "check":
        return "Check it yourself"
    flags = _gate_flags(job)
    return "Flagged: " + ", ".join(flags) if flags else "Flagged"


def _shown_to_shopper(status):
    from .colour_check import SHADE_CHECK_CALIBRATED
    return not status.startswith("shade-") or SHADE_CHECK_CALIBRATED


RETRY_REFUSALS = {
    "OUTCOME_UNKNOWN": "This preview's outcome is unknown, so it cannot be tried again.",
    "ALREADY_RETRIED": "This preview was already tried again.",
    "PHOTO_PROBLEM": "This photo did not work. Try a different photo or a demo model instead.",
}


def _person_source(job):
    """How staff should describe the person in a preview, without exposing the photo."""
    asset = job.person_asset
    if asset and asset.license_or_consent.startswith("AI-generated demo model "):
        return "Demo " + asset.license_or_consent.split()[3]
    if asset:
        return "Shopper photo"
    return "Photo removed" if job.person_asset_sha256 else "No photo"


def _enqueue(job):
    try:
        drive_preview_job.delay(str(job.id))
    except BrokerError:
        # The job stays pending; the next preview request for it enqueues it again.
        log.warning("Preview queue unavailable for job %s", job.id)


def _demo_models():
    """AI-generated demo people published by scripts/build_pixabay_assets.py."""
    path = finders.find("exchange/models/manifest.json")
    return json.loads(Path(path).read_text(encoding="utf-8"))["models"] if path else []


# Each try-on needs its own framing: half-body models for tops, a face for makeup, a hand for nails.
DEMO_KIND_HELP = {"person": "Tops, necklaces and hair colour need one of the half-body demo models.",
                  "face": "Makeup and earring previews need a face. Choose one of the demo faces.",
                  "hand": "Nail previews need a hand. Choose the demo hand or a photo of your own hand.",
                  "full": "Shoe previews need a full-length photo with the feet showing. Choose Model F."}


def _demo_kind(category):
    if category == "nail":
        return "hand"
    if category == "shoes":
        return "full"
    return "face" if category in (BEAUTY | JEWELLERY) - {"necklace", "hair"} else "person"


def _live_preview_label(request):
    """How the site's own preview provider is described to this visitor, or None if it is not offered."""
    if settings.PREVIEW_PROVIDER == "mock":
        return "Test mode: no image and no AI call"
    if settings.PUBLIC_LIVE_PREVIEWS or request.session.get("judge"):
        return "Live preview: a new YouCam run, within this demo's unit limits"
    return None


def _recorded_looks():
    """Whether the looks pilot has recorded results to replay, so the page offers recorded looks only then."""
    from .replay import recorded_looks
    return any(look["state"] == "succeeded" for look in recorded_looks().get("results", []))


def _recorded_categories():
    """The item categories that have recorded results to replay, so the page offers them only there."""
    from .replay import (
        recorded, recorded_accessories, recorded_beauty, recorded_hair, recorded_jewellery, recorded_necklace,
    )
    categories = {"tops"} if recorded().get("results") else set()
    categories |= {r["product"]["category"] for r in recorded_accessories().get("results", [])}
    categories |= {"hair"} if recorded_hair().get("results") else set()
    categories |= {"necklace"} if recorded_necklace().get("results") else set()
    categories |= {r["earring"]["category"] if "category" in r.get("earring", {}) else "earrings"
                   for r in recorded_jewellery().get("results", [])}
    return sorted(categories | {r["shade"]["category"] for r in recorded_beauty().get("results", [])})


def _uploads_allowed(request, session=None):
    """Personal photos are off by default. Judges who entered the access code may upload their own."""
    return bool(settings.ALLOW_PHOTO_UPLOAD or request.session.get("judge") or (session and session.judge))


def home(request):
    return render(request, "exchange/home.html", {"demo_models": _demo_models(),
                                                  "recorded_categories": _recorded_categories(),
                                                  "recorded_looks": _recorded_looks(),
                                                  "assistant_on": settings.ASSISTANT_PROVIDER == "claude",
                                                  "allow_upload": _uploads_allowed(request),
                                                  "live_label": _live_preview_label(request),
                                                  "judge": bool(request.session.get("judge")),
                                                  "judge_configured": bool(settings.JUDGE_ACCESS_CODE)})


def store(request):
    """A fictional store's order page, where a return starts: what a shopper sees before Rechoice opens."""
    token = request.GET.get("order", "DEMO-ORDER-001")
    first = OrderLine.objects.select_related("variant").filter(token=token).first()
    if first is None:
        return render(request, "exchange/store.html", {"order": None}, status=404)
    lines = list(OrderLine.objects.select_related("variant").filter(
        retailer_id=first.retailer_id, fictional_order_id=first.fictional_order_id).order_by("id"))
    items = [{**_line_json(line), "paid": f"{line.currency} {line.paid_minor / 100:.2f}"} for line in lines]
    return render(request, "exchange/store.html", {"order": first, "items": items, "token": token})


def evaluation(request):
    """Public, reproducible development evaluation; never reads shopper sessions or private images."""
    from .replay import recorded
    data = recorded("exchange/evidence/evaluation.json")
    heldout = recorded("exchange/evidence/heldout-v1/report.json")
    gate = recorded("exchange/evidence/gate-v1.json")
    from .gate_outcomes import outcomes
    return render(request, "exchange/evaluation.html", {"evaluation": data, "heldout": heldout, "gate": gate,
                                                        "outcomes": outcomes(gate)},
                  status=200 if data.get("summary") else 503)


def agent_evaluation(request):
    from .replay import recorded
    data = recorded("exchange/evidence/agents-evaluation.json")
    return render(request, "exchange/agent_evaluation.html", {"report": data},
                  status=200 if data.get("offline") else 503)


def heldout_evaluation(request):
    """The separate, registered photo holdout; public files only, with no provider requests."""
    from .replay import recorded
    data = recorded("exchange/evidence/heldout-v1/report.json")
    return render(request, "exchange/heldout_evaluation.html", {"report": data},
                  status=200 if data.get("summary") else 503)


def evidence(request):
    """The recorded pilot: what was tried, what came back, how long it took and what it cost."""
    from .replay import recorded
    data = recorded()
    models = {m["name"]: m for m in _demo_models()}
    for row in data.get("results", []):
        row["model_label"] = models.get(row["model"], {}).get("label", row["model"])
        row["model_image"] = models.get(row["model"], {}).get("static_path", "")
        item = ProductVariant.objects.filter(sku=row["sku"]).first()
        row["title"] = item.title if item else row["sku"]
    seconds = sorted(r["elapsed_seconds"] for r in data.get("results", []))
    return render(request, "exchange/evidence.html", {
        "data": data, "results": data.get("results", []),
        "median_seconds": seconds[len(seconds) // 2] if seconds else None,
        "range_seconds": (seconds[0], seconds[-1]) if seconds else None,
        **_beauty_evidence(models), **_hair_evidence(models), **_jewellery_evidence(models),
        **_accessories_evidence(models),
        **_necklace_evidence(models), **_looks_evidence(models),
        "accuracy": _accuracy()})


def _accuracy():
    """Our own measurements of the published results, from scripts/measure_pilot_accuracy.py."""
    from .replay import recorded
    data = recorded("exchange/evidence/accuracy.json")
    if not data.get("summary"):
        return None
    titles = dict(ProductVariant.objects.filter(sku__in=[c["sku"] for c in data["clothes"]]).values_list("sku", "title"))
    for row in data["clothes"]:
        row["title"] = titles.get(row["sku"], row["sku"])
    data["off_proportion"] = [{**e, "difference_abs": abs(e["difference_pct"])}
                              for e in data["earrings"] if e["status"] in ("longer", "shorter")]
    data["unreadable_shades"] = data["summary"]["shades"]["measured"] - data["summary"]["shades"]["readable"]
    return data


def _looks_evidence(models):
    """The published looks: each top on Model A, then the lipstick added to that result."""
    from .replay import recorded_looks
    data = recorded_looks()
    if not data.get("results"):
        return {"looks": None}
    model = models.get(data["results"][0]["model"], {})
    seconds = sorted(r["elapsed_seconds"] for r in data["results"])
    return {"looks": data, "look_model": {"label": model.get("label", ""), "image": model.get("static_path", "")},
            "look_seconds": (seconds[0], seconds[-1])}


def _hair_evidence(models):
    """The published hair colour comparison: the shade bought, then the two cooler shades, on one demo person."""
    from .replay import recorded_hair
    data = recorded_hair()
    if not data.get("results"):
        return {"hair": None}
    by_pair = {r["pair"]: r for r in data["results"]}
    groups = []
    for comparison in data["comparisons"]:
        model = models.get(comparison["model"], {})
        groups.append({"title": comparison["title"], "model_label": model.get("label", comparison["model"]),
                       "model_image": model.get("static_path", ""),
                       "model_source": by_pair[comparison["pairs"][0]]["model_source"],
                       "results": [by_pair[n] for n in comparison["pairs"]]})
    seconds = sorted(r["elapsed_seconds"] for r in data["results"])
    return {"hair": data, "hair_groups": groups, "hair_seconds": (seconds[0], seconds[-1]),
            "hair_private": data["pilot"]["pairs"] - len(data["results"])}


def _necklace_evidence(models):
    """The published necklace comparison: the one bought, then the shorter ones, on one demo person."""
    from .replay import recorded_necklace
    data = recorded_necklace()
    if not data.get("results"):
        return {"necklaces": None}
    by_pair = {r["pair"]: r for r in data["results"]}
    groups = []
    for comparison in data["comparisons"]:
        model = models.get(comparison["model"], {})
        groups.append({"title": comparison["title"], "model_label": model.get("label", comparison["model"]),
                       "model_image": model.get("static_path", ""),
                       "model_source": by_pair[comparison["pairs"][0]]["model_source"],
                       "results": [by_pair[n] for n in comparison["pairs"]]})
    seconds = sorted(r["elapsed_seconds"] for r in data["results"])
    return {"necklaces": data, "necklace_groups": groups, "necklace_seconds": (seconds[0], seconds[-1]),
            "necklaces_private": data["pilot"]["pairs"] - len(data["results"])}


def _accessories_evidence(models):
    """The published shoes comparison on the full-length demo model, and the bag probe that turned bag try-on off."""
    from .replay import recorded_accessories
    data = recorded_accessories()
    if not data.get("results"):
        return {"accessories": None}
    by_pair = {r["pair"]: r for r in data["results"]}
    groups = []
    for comparison in data["comparisons"]:
        model = models.get(comparison["model"], {})
        groups.append({"title": comparison["title"], "model_label": model.get("label", comparison["model"]),
                       "model_image": model.get("static_path", ""),
                       "model_source": by_pair[comparison["pairs"][0]]["model_source"],
                       "results": [by_pair[n] for n in comparison["pairs"]]})
    seconds = sorted(r["elapsed_seconds"] for r in data["results"])
    return {"accessories": data, "accessory_groups": groups, "accessory_seconds": (seconds[0], seconds[-1]),
            "bag_model": models.get(data["bag_probe"]["model"], {})}


def _jewellery_evidence(models):
    """The published earrings comparison: the pair bought, then the two suggestions, on one face."""
    from .replay import recorded_jewellery
    data = recorded_jewellery()
    if not data.get("results"):
        return {"earrings": None}
    by_pair = {r["pair"]: r for r in data["results"]}
    groups = []
    for comparison in data["comparisons"]:
        model = models.get(comparison["model"], {})
        groups.append({"title": comparison["title"], "model_label": model.get("label", comparison["model"]),
                       "model_image": model.get("static_path", ""),
                       "model_source": by_pair[comparison["pairs"][0]]["model_source"],
                       "results": [by_pair[n] for n in comparison["pairs"]]})
    seconds = sorted(r["elapsed_seconds"] for r in data["results"])
    return {"earrings": data, "earring_groups": groups, "earring_seconds": (seconds[0], seconds[-1]),
            "earrings_private": data["pilot"]["pairs"] - len(data["results"])}


def _beauty_evidence(models):
    """The published beauty comparisons, grouped as the shopper sees them: the bought shade, then two suggestions."""
    from .replay import recorded_beauty
    data = recorded_beauty()
    if not data.get("results"):
        return {"beauty": None}
    by_pair = {r["pair"]: r for r in data["results"]}
    groups = []
    for comparison in data["comparisons"]:
        model = models.get(comparison["model"], {})
        groups.append({"title": comparison["title"], "model_label": model.get("label", comparison["model"]),
                       "model_image": model.get("static_path", ""),
                       "model_source": by_pair[comparison["pairs"][0]]["model_source"],
                       "results": [by_pair[n] for n in comparison["pairs"]]})
    seconds = sorted(r["elapsed_seconds"] for r in data["results"])
    return {"beauty": data, "beauty_groups": groups, "beauty_seconds": (seconds[0], seconds[-1]),
            "beauty_private": data["pilot"]["pairs"] - len(data["results"])}


def _grant_judge(request):
    """Turn on judge access in this browser.

    The session key changes on the way in, as on any sign-in. Orders already open in this browser move to
    the new key and gain judge access, so a judge who enters the code mid-order keeps their place.
    """
    if request.session.session_key is None:
        request.session.create()
    old_key = request.session.session_key
    request.session["judge"] = True
    request.session.cycle_key()
    ExchangeSession.objects.filter(browser_session_key=old_key, expires_at__gt=timezone.now()).update(
        browser_session_key=request.session.session_key, judge=True)
    EvidenceEvent.objects.create(actor="judge", action="judge_access_granted", facts={})


def _judge_code_problem(request, code):
    """Why this code does not grant judge access, or "" when it does."""
    if not settings.JUDGE_ACCESS_CODE:
        return "The judge path is not configured on this server."
    if _over_address_limit(request, "judge", 10):
        return TOO_MANY
    if not isinstance(code, str) or not hmac.compare_digest(code.encode(), settings.JUDGE_ACCESS_CODE.encode()):
        return "That code is not right."
    return ""


def judge(request):
    """Judges enter the access code here for live previews and the staff pages."""
    granted = bool(request.session.get("judge"))
    problem = ""
    if request.method == "POST" and not granted:
        problem = _judge_code_problem(request, request.POST.get("code", ""))
        if not problem:
            _grant_judge(request)
            granted = True
    return render(request, "exchange/judge.html", {
        "granted": granted, "problem": problem, "configured": bool(settings.JUDGE_ACCESS_CODE),
        "live": settings.PREVIEW_PROVIDER != "mock", "judge_cap": settings.JUDGE_DAILY_UNIT_CAP})


@csrf_protect
def judge_access(request):
    """The judges' code entered on the shopper page, so the order in progress stays open and gains live previews."""
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    if not request.session.get("judge"):
        try:
            code = _body(request).get("code", "")
        except ValueError as exc:
            return _error("INVALID_INPUT", str(exc))
        problem = _judge_code_problem(request, code)
        if problem:
            status = 429 if problem == TOO_MANY else 503 if not settings.JUDGE_ACCESS_CODE else 403
            return _error("JUDGE_CODE", problem, status)
        _grant_judge(request)
    return JsonResponse({"judge": True, "allow_upload": _uploads_allowed(request),
                         "live_label": _live_preview_label(request)})


def staff_or_judge(view):
    """Staff pages for staff accounts, and for judges who entered the access code. All data is fictional."""
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.session.get("judge"):
            return view(request, *args, **kwargs)
        return staff_member_required(view)(request, *args, **kwargs)
    return wrapped


def _client_address(request):
    """The client address, trusting X-Forwarded-For only for the configured number of proxies."""
    proxies = settings.TRUSTED_PROXY_COUNT
    if proxies:
        hops = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
        if len(hops) >= proxies:
            return hops[-proxies]
    return request.META.get("REMOTE_ADDR") or "unknown"


def _over_limits(window, *counters):
    """Count one attempt against every (key, limit) pair. True when any is over its limit.

    Every attempt counts against all of them, so switching sessions cannot buy extra tries.
    """
    over = False
    for key, limit in counters:
        cache.add(key, 0, timeout=window)
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window)
            count = 1
        over = over or count > limit
    return over


def _over_upload_limit(request, session):
    return _over_limits(settings.UPLOAD_WINDOW_SECONDS,
                        (f"upload:session:{session.id}", settings.UPLOAD_LIMIT_PER_SESSION),
                        (f"upload:address:{_client_address(request)}", settings.UPLOAD_LIMIT_PER_ADDRESS))


def _over_address_limit(request, action, limit):
    return _over_limits(settings.RATE_LIMIT_WINDOW_SECONDS, (f"{action}:address:{_client_address(request)}", limit))


TOO_MANY = "Too many requests from this connection. Try again later."


def _person_asset_json(asset, label):
    return {"asset_id": str(asset.id), "image_url": f"/api/assets/{asset.id}",
            "expires_at": asset.expires_at.isoformat(), "label": label}


def _grant_photo_consent(session):
    session.consent_given = True
    session.consent_version += 1
    session.save(update_fields=["consent_given", "consent_version"])


def _broker_state():
    from config.celery import app
    try:
        with app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1, interval_start=0, interval_step=0, timeout=2)
        return "ok"
    except Exception:
        return "down"


def health(request):
    """Liveness for load balancers. Answers 503 when a part the shopper flow needs is down. No secrets."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"
    try:
        cache.set("health:probe", "1", timeout=10)
        checks["cache"] = "ok" if cache.get("health:probe") == "1" else "down"
    except Exception:
        checks["cache"] = "down"
    if settings.CELERY_TASK_ALWAYS_EAGER:
        checks["queue"] = checks["worker"] = "inline"
    else:
        checks["queue"] = _broker_state()
        stamp = cache.get(HEARTBEAT_KEY) if checks["cache"] == "ok" else None
        beat = parse_datetime(stamp) if stamp else None
        checks["worker"] = "ok" if beat and timezone.now() - beat < timedelta(seconds=180) else "stale"
    control = preview_jobs.control() if checks["database"] == "ok" else None
    if settings.PREVIEW_PROVIDER != "mock" and control is not None:
        age = timedelta(seconds=settings.YOUCAM_BALANCE_MAX_AGE_SECONDS)
        checked = control.balance_checked_at
        checks["balance"] = "unknown" if not checked else ("fresh" if timezone.now() - checked < age else "stale")
    healthy = all(checks[name] in ("ok", "inline") for name in ("database", "cache", "queue", "worker"))
    mode = "unknown" if control is None else (
        "paused" if control.paused else ("live" if settings.PREVIEW_PROVIDER != "mock" else "mock"))
    return JsonResponse({"status": "ok" if healthy else "degraded", "provider": settings.PREVIEW_PROVIDER,
                         "previews": mode, "exchange_ai": settings.EXCHANGE_AI_PROVIDER,
                         "checks": checks}, status=200 if healthy else 503)


@csrf_protect
def sessions(request):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    data, error = _validated(request, StartSerializer)
    if error:
        return error
    if _over_address_limit(request, "session", settings.SESSION_LIMIT_PER_ADDRESS):
        return _error("RATE_LIMITED", TOO_MANY, 429)
    line = OrderLine.objects.select_related("variant").filter(token=data["order_token"]).first()
    if line is None:
        return _error("ORDER_NOT_FOUND", "Demo order not found.", 404)
    policy = _policy(line)
    if policy is None:
        return _error("POLICY_UNKNOWN", "Demo policy is unavailable.", 422)
    if request.session.session_key is None:
        request.session.create()
    session = ExchangeSession.objects.create(
        browser_session_key=request.session.session_key, order_line=line, judge=bool(request.session.get("judge")),
        expires_at=timezone.now() + timedelta(hours=24))
    EvidenceEvent.objects.create(actor="shopper", action="session_started", session=session,
                                 facts={"order_line_id": line.id, "policy_version": line.policy_version})
    items = OrderLine.objects.select_related("variant").filter(
        retailer_id=line.retailer_id, fictional_order_id=line.fictional_order_id).order_by("id")
    return JsonResponse({
        "session_id": str(session.id), "status": session.status,
        "order": _line_json(line),
        "items": [_line_json(item) for item in items],
        "policy": {"exchange_allowed": policy.exchange_allowed and policy.expires_at > timezone.now(),
                   "window_ends_at": policy.expires_at.isoformat(),
                   "opened_same_line": policy.opened_same_line},
        "reasons": _reasons_for(line),
        "return_option": "You may continue with the retailer's normal return process.",
        "demo": True,
    }, status=201)


@csrf_protect
def choose_item(request, session_id):
    """Pick which item of the order is going back. Only before anything else has been decided."""
    if request.method != "PUT":
        return _error("METHOD_NOT_ALLOWED", "PUT is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    data, error = _validated(request, ItemSerializer)
    if error:
        return error
    current = session.order_line
    line = OrderLine.objects.select_related("variant").filter(
        id=data["line_id"], retailer_id=current.retailer_id, fictional_order_id=current.fictional_order_id).first()
    if line is None:
        return _error("ITEM_NOT_FOUND", "That item is not part of this order.", 404)
    if line.id != current.id:
        if session.status != "STARTED" or session.preferences or PreviewJob.objects.filter(owner_session=session).exists():
            return _error("ITEM_LOCKED", "Start again to return a different item from this order.", 409)
        session.order_line = line
        session.return_reason = ""
        session.save(update_fields=["order_line", "return_reason"])
        EvidenceEvent.objects.create(actor="shopper", action="item_chosen", session=session,
                                     facts={"order_line_id": line.id, "category": line.variant.category})
    return JsonResponse({"order": _line_json(line), "reasons": _reasons_for(line), "return_option": True})


@csrf_protect
def return_reason(request, session_id):
    """Record why the item is going back, and say which path fits that reason."""
    if request.method != "PUT":
        return _error("METHOD_NOT_ALLOWED", "PUT is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    data, error = _validated(request, ReasonSerializer)
    if error:
        return error
    if data["reason"] not in {r["code"] for r in _reasons_for(session.order_line)}:
        return _error("REASON_NOT_OFFERED", "Choose one of the reasons shown for this item.")
    session.return_reason = data["reason"]
    session.save(update_fields=["return_reason"])
    EvidenceEvent.objects.create(actor="shopper", action="return_reason_given", session=session,
                                 facts={"reason": data["reason"]})
    spec = RETURN_REASONS[data["reason"]]
    return JsonResponse({"reason": data["reason"], "next": spec["next"], "message": spec.get("message", ""),
                         "return_option": True})


def sizes(request, session_id):
    """Other sizes of the purchased top, with stock and price. Only for a fit return."""
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if session.return_reason != "fit":
        return _error("REASON_MISMATCH", "Other sizes are offered when the item does not fit.", 409)
    line = session.order_line
    items = {x.sku: x for x in ProductVariant.objects.filter(retailer_id=line.retailer_id)}
    try:
        options = size_options(_domain_line(line), _domain_policy(_policy(line)),
                               [_domain_variant(x) for x in items.values()], timezone.now())
    except DomainError as exc:
        return _error(exc.code, str(exc))
    return JsonResponse({
        "message": RETURN_REASONS["fit"]["message"],
        "current_size": line.variant.size_label,
        "sizes": [{"sku": o.variant.sku, "title": items[o.variant.sku].title, "size_label": o.variant.size_label,
                   "available": o.available, "top_up_minor": o.top_up_minor,
                   "note": o.note,
                   **_image_fields(items[o.variant.sku])} for o in options],
        "return_option": True})


@csrf_protect
def choose_return(request, session_id):
    """The shopper ends the exchange flow and uses the normal return route. Nothing is refunded here."""
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    session.status = "CANCELLED"
    session.save(update_fields=["status"])
    EvidenceEvent.objects.create(actor="shopper", action="return_chosen", session=session,
                                 facts={"reason": session.return_reason or "unstated"})
    return JsonResponse({"status": session.status,
                         "message": "This demo has not started a refund or return. In a real store, "
                                    "follow the retailer's usual return instructions."})


@csrf_protect
def interpret(request, session_id):
    """Propose control values from the shopper's own words. Nothing is saved or decided here."""
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    data, error = _validated(request, InterpretSerializer)
    if error:
        return error
    line = session.order_line
    result = assistant.read(session, _group(line), line.variant.category, data["text"])
    return JsonResponse({**result, "notice": _reader_notice(result["reader"])})


def _reader_notice(reader):
    check = "Check the controls before confirming. Your words do not decide anything."
    if reader == "claude":
        return (f"Read by {assistant.MODEL_NAMES.get(settings.ASSISTANT_MODEL, 'Claude')}, Anthropic's AI, from your "
                f"words alone. {check}")
    return f"Read by the store's rules. {check}" if settings.ASSISTANT_PROVIDER == "claude" else check


@csrf_protect
def preferences(request, session_id):
    if request.method != "PUT":
        return _error("METHOD_NOT_ALLOWED", "PUT is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    group = _group(session.order_line)
    if session.return_reason not in {"beauty": ("shade",), "jewellery": ("look",),
                                     "accessories": ("look",)}.get(group, ("", "style")):
        return _error("REASON_MISMATCH", "Preferences are for an item you don't like the look of.", 409)
    data, error = _validated(request, {"beauty": ShadePreferenceSerializer, "jewellery": JewelleryPreferenceSerializer,
                                       "accessories": JewelleryPreferenceSerializer}.get(group, PreferenceSerializer))
    if error:
        return error
    # Earrings and necklaces come in gold or silver; shoes and bags in a neutral or a bright colour.
    if group in SIZE_AND_COLOUR and data["metal"] and (data["metal"] in COLOURS) != (group == "accessories"):
        return _error("INVALID_INPUT", "Choose a colour this item comes in.")
    session.preferences = data
    session.preference_version += 1
    session.status = "PREFERENCES_CONFIRMED"
    session.active_preview_ids = []
    session.save(update_fields=["preferences", "preference_version", "status", "active_preview_ids"])
    EvidenceEvent.objects.create(actor="shopper", action="preferences_confirmed", session=session,
                                 facts={"preference_version": session.preference_version})
    return JsonResponse({"status": session.status, "preference_version": session.preference_version,
                         "confirmed_preferences": data})


def candidates(request, session_id):
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    try:
        found = _eligible(session)
        left_out = _exclusions(session)
    except DomainError as exc:
        return _error(exc.code, str(exc))
    items = {x.sku: x for x in ProductVariant.objects.filter(
        retailer_id=session.order_line.retailer_id)}
    # What was asked and why tops were left out, for merchant insights. Never the shopper's own words.
    asked = {key: value for key, value in session.preferences.items() if key != "note"}
    EvidenceEvent.objects.create(actor="system", action="candidates_checked", session=session,
                                 facts={"matches": len(found), "preferences": asked,
                                        "exclusions": {x["code"]: x["count"] for x in left_out}})
    if not found:
        noun = {"beauty": "shade", "jewellery": "pair", "accessories": "pair"}.get(_group(session.order_line), "top")
        if session.order_line.variant.category == "bag":
            noun = "bag"
        if session.order_line.variant.category == "necklace":
            noun = "necklace"
        return JsonResponse({"status": "NO_MATCH", "candidates": [], "exclusions": left_out,
                             "message": f"No {noun} meets every limit you confirmed. Nothing was relaxed. "
                                        "Change a preference, or use the normal return route.",
                             "return_option": True})
    _advance_status(session, "CANDIDATES_READY")
    return JsonResponse({"status": session.status,
                         "candidates": [_candidate_json(x, items) for x in found], "exclusions": left_out,
                         "return_option": True})


def tradeoffs(request, session_id):
    """Explore one shopper-selected limit; never change preferences, quote or start a preview."""
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if error := _closed(session):
        return error
    if session.order_line.variant.category != "tops" or session.return_reason != "style":
        return _error("REASON_MISMATCH", "Trade-off exploration is for an appearance-related top exchange.", 409)
    from secondlook.tradeoffs import explore
    constraint = request.GET.get("constraint", "")
    items = {item.sku: item for item in ProductVariant.objects.filter(retailer_id=session.order_line.retailer_id)}
    try:
        prefs = _domain_preferences(session)
        line, policy = _domain_line(session.order_line), _domain_policy(_policy(session.order_line))
        variants = [_domain_variant(item) for item in items.values()]
        now = timezone.now()
        if constraint not in ("budget", "sleeves", "print"):
            raise DomainError("INVALID_CONSTRAINT", "Choose budget, sleeves or print to explore.")
        if shortlist(line, policy, prefs, variants, now):
            return JsonResponse({"status": "MATCHES_EXIST", "possibilities": [],
                                 "preference_version": session.preference_version,
                                 "message": "Your current limits now have an eligible option. Confirm them again to refresh the shortlist.",
                                 "return_option": True})
        plans = explore(line, policy, prefs, variants, constraint, now)
    except DomainError as exc:
        return _error(exc.code, str(exc))
    kept = []
    if constraint != "budget":
        kept.append(f"Maximum extra spend: USD {prefs.max_top_up_minor / 100:.2f}")
    if constraint != "sleeves" and prefs.sleeve_required:
        kept.append(f"Sleeves: {prefs.sleeve_required}")
    if constraint != "print" and prefs.quieter_than is not None:
        kept.append("Quieter catalog print than the item bought")
    kept.append("Current stock, exchange policy and known pricing remain required")
    labels = {"budget": "Maximum extra spend", "sleeves": "Sleeves", "print": "Quieter-print limit"}
    possibilities = []
    for plan in plans:
        if constraint == "budget":
            change = f"Maximum extra spend: USD {plan.before / 100:.2f} → USD {plan.after / 100:.2f}"
        elif constraint == "sleeves":
            change = f"Sleeves: {plan.before} → {plan.after}"
        else:
            change = "Quieter catalog print → any catalog print"
        possibilities.append({"constraint": constraint, "label": labels[constraint], "before": plan.before,
                              "after": plan.after, "change": change, "kept": kept,
                              "minimum_budget": constraint == "budget", "confirmed": False,
                              "candidates": [_candidate_json(candidate, items) for candidate in plan.candidates]})
    return JsonResponse({"status": "POSSIBILITIES" if possibilities else "NO_SINGLE_CHANGE",
                         "preference_version": session.preference_version, "possibilities": possibilities,
                         "message": ("These are current catalog possibilities. Review and confirm any revised limit before seeing an eligible shortlist or quote."
                                     if possibilities else "Changing only this limit does not unlock an eligible top. Try another limit, edit your preferences, or use the normal return route."),
                         "return_option": True})


@csrf_protect
def copilot_plan(request, session_id):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if error := _closed(session):
        return error
    if session.order_line.variant.category != "tops" or session.return_reason != "style":
        return _error("REASON_MISMATCH", "The copilot currently guides appearance-related top exchanges.", 409)
    try:
        data = _body(request)
    except ValueError as exc:
        return _error("INVALID_INPUT", str(exc))
    if (not isinstance(data, dict) or set(data) - {"intent", "revisit"}
            or not isinstance(data.get("intent", ""), str) or len(data.get("intent", "")) > 500
            or data.get("revisit") not in (None, "budget", "sleeves", "print")):
        return _error("INVALID_INPUT", "Use a short request and, optionally, one supported limit.")
    def snapshot():
        variants = list(ProductVariant.objects.filter(retailer_id=session.order_line.retailer_id, category="tops").order_by("sku").values_list(
            "sku", "stock_qty", "price_minor", "currency", "version", "sleeve", "pattern_level", "style_code", "color_tag", "neckline"))
        policy = list(PolicySnapshot.objects.filter(retailer_id=session.order_line.retailer_id, version=session.order_line.policy_version).values_list(
            "exchange_allowed", "fee_minor", "expires_at", "allowed_category", "categories"))
        return variants, policy
    before = snapshot()
    from .copilot import plan
    result = plan(session, intent=data.get("intent", ""), revisit=data.get("revisit"))
    if snapshot() != before:
        return JsonResponse({"status": "STALE", "reader": result.get("reader", "rules"), "summary": "The catalog or policy changed while this plan was prepared. Refresh your choices and plan again.",
                             "questions": [], "trace": [], "confirmed": False, "return_option": True})
    return JsonResponse(result)


@csrf_protect
def visual_review(request, job_id):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    job = PreviewJob.objects.select_related("owner_session__order_line__variant", "variant", "person_asset", "result_asset").filter(pk=job_id).first()
    if job is None or _owned(request, job.owner_session_id) is None:
        return _error("NOT_FOUND", "Preview not found.", 404)
    if error := _closed(job.owner_session):
        return error
    version = job.owner_session.preference_version
    from .visual_review import requirements, review, unassessed
    result = review(job)
    job.owner_session.refresh_from_db()
    if (job.owner_session.preference_version != version or job.owner_session.status in CLOSED_STATUSES
            or job.owner_session.expires_at <= timezone.now()):
        return JsonResponse(unassessed("The confirmed request changed during this assessment. Review the current controls before asking again."))
    return JsonResponse({**result, "preference_version": version, "requested": requirements(job.owner_session.preferences)})


def _active_previews(session):
    """Only the server-selected batch; missing/deleted jobs never revive an older photo."""
    return list(PreviewJob.objects.filter(owner_session=session, id__in=session.active_preview_ids or [],
                                         look__isnull=True).select_related("variant", "person_asset", "result_asset"))


def _visible_preview(job):
    now = timezone.now()
    return (job.state == SUCCEEDED and job.result_asset is not None and job.result_asset.expires_at > now
            and (not job.person_asset_sha256 or
                 (job.person_asset is not None and job.person_asset.expires_at > now)))


def _preview_concern(job):
    if job is None or job.state != SUCCEEDED or job.gate_status != "concern":
        return ""
    return hashlib.sha256(f"{job.id}:{job.gate_note}".encode()).hexdigest()


@csrf_protect
def compare(request, session_id):
    """A written comparison of the options found, from the catalog facts, the confirmed controls and the checks."""
    from . import advisor
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    try:
        found = _eligible(session)
    except DomainError as exc:
        return _error(exc.code, str(exc))
    if not found:
        return _error("NO_OPTIONS", "There are no options to compare.", 409)
    items = {x.sku: x for x in ProductVariant.objects.filter(retailer_id=session.order_line.retailer_id)}
    if closed := _closed(session):
        return closed
    data, error = _validated(request, CompareSerializer)
    if error:
        return error
    if session.active_preview_ids is None:
        return _error("PREVIEW_CHANGED", "Reopen previews to compare the current photo.", 409)
    if "preview_job_ids" in data and set(map(str, data["preview_job_ids"])) != set(session.active_preview_ids):
        return _error("PREVIEW_CHANGED", "The previews changed. Reopen previews before comparing them.", 409)
    jobs = [job for job in _active_previews(session) if _visible_preview(job)]
    written = advisor.compare(session, advisor.compare_facts(session, found, items, jobs))
    label = (f"Written by {assistant.MODEL_NAMES.get(settings.ASSISTANT_MODEL, 'Claude')} from the catalog facts, "
             "what you asked for and our checks. It never sees the photos."
             if written["reader"] == "claude" else "Put together from the catalog facts and what you asked for.")
    return JsonResponse({**written, "label": label})


@csrf_protect
def previews(request, session_id):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    if session.order_line.variant.category in PREVIEW_OFF:
        return _error("PREVIEW_OFF", PREVIEW_OFF[session.order_line.variant.category], 409)
    data, error = _validated(request, PreviewSerializer)
    if error:
        return error
    if not data["consent"]:
        return _error("CONSENT_REQUIRED", "Confirm permission before a preview.", 422)
    if _over_address_limit(request, "preview", settings.PREVIEW_REQUEST_LIMIT_PER_ADDRESS):
        return _error("RATE_LIMITED", TOO_MANY, 429)
    asset = None
    if data.get("asset_id"):
        asset = PrivateAsset.objects.filter(id=data["asset_id"], owner_session=session,
                                            expires_at__gt=timezone.now()).first()
        if asset is None:
            return _error("ASSET_NOT_FOUND", "The image is unavailable.", 404)
    if len(set(data["candidate_skus"])) != len(data["candidate_skus"]):
        return _error("DUPLICATE_SKU", "Choose distinct garments.")
    try:
        eligible = {x.variant.sku: x for x in _eligible(session)}
    except DomainError as exc:
        return _error(exc.code, str(exc))
    if not set(data["candidate_skus"]).issubset(eligible):
        return _error("INELIGIBLE", "One or more selected garments are unavailable.")
    recorded = data["mode"] == "recorded"
    try:
        provider = get_provider(preview_jobs.provider_name_for(session.order_line.variant.category,
                                                               "replay" if recorded else None))
    except ProviderUnavailable:
        return _error("PREVIEW_UNAVAILABLE", "Previews are unavailable right now.", 503)
    if not recorded and preview_jobs.control().paused:
        return _error("PREVIEWS_PAUSED", "Previews are paused right now. Recorded results are still available, "
                      "and your options and quote are unaffected.", 503)
    if provider.units_per_job and not session.judge and not settings.PUBLIC_LIVE_PREVIEWS:
        return _error("LIVE_FOR_JUDGES", "Live previews are reserved for judges right now. "
                      "Recorded results are available.", 403)
    if (provider.units_per_job or recorded) and asset is None:
        return _error("PHOTO_REQUIRED", "Choose a demo model or your own photo first.")
    if not session.consent_given:
        session.consent_given = True
        session.consent_version += 1
        session.save(update_fields=["consent_given", "consent_version"])
    planned = []
    skus = list(data["candidate_skus"])
    if data["include_original"]:
        # Only ever the session's own purchased top; never a SKU taken from the request.
        skus.insert(0, session.order_line.variant.sku)
    for sku in skus:
        item = ProductVariant.objects.get(retailer_id=session.order_line.retailer_id, sku=sku)
        fingerprint = hashlib.sha256(
            f"{session.id}:{session.consent_version}:{asset.sha256 if asset else 'none'}:"
            f"{item.sku}:{item.version}:{provider.name}:{provider.version}".encode()
        ).hexdigest()
        planned.append((item, fingerprint))
    units = provider.units_per_job
    if units:
        existing = PreviewJob.objects.filter(fingerprint__in=[f for _, f in planned])
        reusable = {j.fingerprint for j in existing if j.state != FAILED or j.error_code not in preview_jobs.REFUSALS}
        new_units = units * sum(1 for _, f in planned if f not in reusable)
        if new_units and (preview_jobs.committed_units(owner_session=session) + new_units
                          > settings.PREVIEW_SESSION_UNIT_CAP):
            return _error("PREVIEW_LIMIT", "You've reached the preview limit for this session. "
                          "Your options and quote are unaffected.", 429)
    jobs = []
    for item, fingerprint in planned:
        job, _ = PreviewJob.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={"owner_session": session, "variant": item, "provider": provider.name,
                      "provider_version": provider.version,
                      "consent_version": session.consent_version, "person_asset": asset,
                      "person_asset_sha256": asset.sha256 if asset else ""},
        )
        # Only pending jobs are queued. A job refused before reaching the provider may run again;
        # a job that reached the provider is never restarted here.
        job = preview_jobs.reopen_if_refused(preview_jobs.latest_attempt(job))
        if job.state == PENDING:
            _enqueue(job)
        jobs.append(job)
    session.active_preview_ids = [str(job.id) for job in jobs]
    session.save(update_fields=["active_preview_ids"])
    # Read back after all are queued: run inline, a later job's size check can change an earlier one.
    for job in jobs:
        job.refresh_from_db()
    _advance_status(session, "COMPARISON_READY")
    return JsonResponse({"jobs": [_job_json(job) for job in jobs], "provider": provider.name, "return_option": True},
                        status=202)


def _look_json(look):
    from .preview_trust import look_trust
    steps = preview_jobs.look_steps(look)
    state, decided = preview_jobs.look_state(look, steps)
    line = look.owner_session.order_line
    titles = dict(ProductVariant.objects.filter(retailer_id=line.retailer_id, sku__in=[e["sku"] for e in look.plan])
                  .values_list("sku", "title"))
    result = decided.result_asset if state == SUCCEEDED else None
    visible = result is not None and result.expires_at > timezone.now()
    items = [{"sku": entry["sku"], "title": titles[entry["sku"]],
              "feature": preview_jobs.feature_name(entry["category"]),
              "state": steps[n].state if n < len(steps) else "waiting",
              "colour_check": _job_json(steps[n])["colour_check"] if n < len(steps) else None}
             for n, entry in enumerate(look.plan)]
    return {"look_id": str(look.id), "state": state, "baseline": line.variant.sku in {e["sku"] for e in look.plan},
            "trust": look_trust(look.owner_session, items, visible=visible,
                                recorded=bool(steps) and all(step.provider == "replay" for step in steps)),
            "items": items, "image_url": f"/api/assets/{result.id}" if visible else None,
            "message": preview_jobs.look_message(look, state, decided),
            "left_out": [f"{variant.title}: {why}." for variant, why in preview_jobs.look_left_out(look.owner_session)]}


@csrf_protect
def looks(request, session_id):
    """The shopper's choice, or the item they bought, with the rest of their order on one photo."""
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    data, error = _validated(request, LookSerializer)
    if error:
        return error
    if not data["consent"]:
        return _error("CONSENT_REQUIRED", "Confirm permission before a preview.", 422)
    if _over_address_limit(request, "preview", settings.PREVIEW_REQUEST_LIMIT_PER_ADDRESS):
        return _error("RATE_LIMITED", TOO_MANY, 429)
    asset = PrivateAsset.objects.filter(id=data["asset_id"], owner_session=session, kind="person",
                                        expires_at__gt=timezone.now()).first()
    if asset is None:
        return _error("ASSET_NOT_FOUND", "The image is unavailable.", 404)
    line = session.order_line
    if data["sku"] != line.variant.sku:
        try:
            eligible = {x.variant.sku for x in _eligible(session)}
        except DomainError as exc:
            return _error(exc.code, str(exc))
        if data["sku"] not in eligible:
            return _error("INELIGIBLE", "That choice is unavailable.")
    replacement = ProductVariant.objects.get(retailer_id=line.retailer_id, sku=data["sku"])
    variants = preview_jobs.look_items(session, replacement)
    if len(variants) < 2:
        return _error("NOTHING_TO_ADD", "Nothing else in this order can be shown on the same photo.", 422)
    recorded = data["mode"] == "recorded"
    if recorded:
        # A recording may cover fewer items than the order has now; replay as far as it goes.
        from .replay import recorded_look_length, replay_key
        note = asset.license_or_consent
        model = note.split()[3] if note.startswith("AI-generated demo model ") else ""
        covered = recorded_look_length(model, [replay_key(v) for v in variants])
        variants = variants[:max(covered, 1)]
    try:
        plan = preview_jobs.look_plan(variants, "replay" if recorded else None)
    except ProviderUnavailable:
        return _error("PREVIEW_UNAVAILABLE", "Previews are unavailable right now.", 503)
    live = any(entry["units"] for entry in plan)
    if not recorded and preview_jobs.control().paused:
        return _error("PREVIEWS_PAUSED", "Previews are paused right now. Recorded results are still available, "
                      "and your options and quote are unaffected.", 503)
    if live and not session.judge and not settings.PUBLIC_LIVE_PREVIEWS:
        return _error("LIVE_FOR_JUDGES", "Live previews are reserved for judges right now. "
                      "Recorded results are available.", 403)
    if not session.consent_given:
        session.consent_given = True
        session.consent_version += 1
        session.save(update_fields=["consent_given", "consent_version"])
    existing = LookPreview.objects.filter(
        fingerprint=preview_jobs.look_fingerprint(session, variants, plan, asset)).first()
    # The whole look must fit under the limits before it starts, so it is never left half paid for.
    units = preview_jobs.unstarted_look_units(plan, preview_jobs.look_steps(existing) if existing else [])
    if units and (problem := preview_jobs.limit_problem(session, units)):
        if problem == "SESSION_CAP":
            return _error("PREVIEW_LIMIT", f"This look needs {units} preview units, more than this session has left. "
                          "Your options and quote are unaffected.", 429)
        return _error(problem, "Previews are unavailable right now. Your options and quote are unaffected.", 503)
    look, _ = preview_jobs.create_look(session, variants, plan, asset)
    # A step refused before reaching the provider may run now; a step that reached it is never restarted.
    steps = preview_jobs.look_steps(look)
    step = preview_jobs.reopen_if_refused(steps[-1])
    if step.state == SUCCEEDED:
        step = preview_jobs.next_look_step(step.id) or step
    if step.state == PENDING:
        _enqueue(step)
    _advance_status(session, "COMPARISON_READY")
    return JsonResponse(_look_json(look), status=202)


def look_status(request, look_id):
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    look = LookPreview.objects.select_related("owner_session__order_line__variant").filter(id=look_id).first()
    if not look or _owned(request, look.owner_session_id) is None:
        return _error("NOT_FOUND", "Look not found.", 404)
    steps = preview_jobs.look_steps(look)
    # Normally the finished step's task starts the next one; this covers a task that was lost in between.
    if steps and steps[-1].state == SUCCEEDED and len(steps) < len(look.plan):
        nxt = preview_jobs.next_look_step(steps[-1].id)
        if nxt is not None and nxt.state == PENDING:
            _enqueue(nxt)
    return JsonResponse(_look_json(look))


@csrf_protect
def assets(request, session_id):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if "url" in request.POST:
        return _error("URL_NOT_ALLOWED", "Upload a file instead of providing a URL.")
    if not _uploads_allowed(request, session):
        return _error("UPLOAD_DISABLED", "Personal photo uploads are open to judges only. Enter the access code "
                      "on the judges' page, or use a demo person.", 503)
    if request.POST.get("adult_permission") != "true":
        return _error("CONSENT_REQUIRED", "Confirm adult permission before upload.")
    if _over_upload_limit(request, session):
        return _error("UPLOAD_LIMIT", "You've reached the photo upload limit for now. Try a demo model instead.", 429)
    upload = request.FILES.get("image")
    if upload is None:
        return _error("IMAGE_REQUIRED", "Choose a JPEG or PNG image.")
    try:
        data = normalize_photo(upload)
    except ValidationError as exc:
        return _error("INVALID_IMAGE", exc.messages[0])
    key, digest = save_private_image(data)
    asset = PrivateAsset.objects.create(
        owner_session=session, kind="person", license_or_consent="confirmed adult permission",
        sha256=digest, storage_key=key, expires_at=timezone.now() + timedelta(hours=24))
    _grant_photo_consent(session)
    return JsonResponse(_person_asset_json(asset, "Your photo"), status=201)


@csrf_protect
def demo_person(request, session_id):
    """Use an AI-generated demo model instead of a personal photo."""
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    try:
        name = _body(request).get("model")
    except ValueError as exc:
        return _error("INVALID_INPUT", str(exc))
    model = {m["name"]: m for m in _demo_models()}.get(name)
    if model is None:
        return _error("UNKNOWN_MODEL", "Choose one of the demo models shown.")
    needed = _demo_kind(session.order_line.variant.category)
    if model.get("kind", "person") != needed:
        return _error("MODEL_NOT_SUITABLE", DEMO_KIND_HELP[needed])
    note = f"AI-generated demo model {name} (Pixabay {model['source']['pixabay_id']}); no real person"
    asset = PrivateAsset.objects.filter(owner_session=session, kind="person", license_or_consent=note,
                                        expires_at__gt=timezone.now()).first()
    if asset is None or not private_image_exists(asset.storage_key):
        source = finders.find(model["static_path"])
        if not source:
            return _error("UNKNOWN_MODEL", "That demo model is unavailable.", 503)
        data = normalize_photo(SimpleUploadedFile(f"{name}.jpg", Path(source).read_bytes(), content_type="image/jpeg"))
        key, digest = save_private_image(data)
        asset = PrivateAsset.objects.create(
            owner_session=session, kind="person", license_or_consent=note, sha256=digest,
            storage_key=key, expires_at=timezone.now() + timedelta(hours=24))
        _grant_photo_consent(session)
    return JsonResponse(_person_asset_json(asset, model["label"]), status=201)


@csrf_protect
def asset_file(request, asset_id):
    asset = PrivateAsset.objects.select_related("owner_session").filter(id=asset_id).first()
    if not asset or _owned(request, asset.owner_session_id) is None or asset.expires_at <= timezone.now():
        return _error("NOT_FOUND", "Image not found.", 404)
    if request.method == "DELETE":
        session = asset.owner_session
        if asset.kind == "person":
            discard_person_asset(asset)
            session.consent_given = False
            session.consent_version += 1
            session.save(update_fields=["consent_given", "consent_version"])
        else:
            discard_asset(asset)
        return JsonResponse({"deleted": True})
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET or DELETE is required.", 405)
    try:
        data = read_private_image(asset.storage_key)
    except FileNotFoundError:
        return _error("NOT_FOUND", "Image not found.", 404)
    response = HttpResponse(data, content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    return response


@csrf_protect
def preview_retry(request, job_id):
    """The shopper's one manual retry of a failed preview. It creates a new job; nothing is restarted."""
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    job = PreviewJob.objects.select_related("owner_session", "variant", "person_asset").filter(id=job_id).first()
    if not job or _owned(request, job.owner_session_id) is None:
        return _error("NOT_FOUND", "Preview not found.", 404)
    session = job.owner_session
    if closed := _closed(session):
        return closed
    if preview_jobs.control().paused:
        return _error("PREVIEWS_PAUSED", "Previews are paused right now. Your options and quote are unaffected.", 503)
    if problem := preview_jobs.retry_problem(job):
        return _error("RETRY_NOT_ALLOWED", RETRY_REFUSALS.get(problem, "This preview cannot be tried again."), 409)
    if _over_address_limit(request, "preview", settings.PREVIEW_REQUEST_LIMIT_PER_ADDRESS):
        return _error("RATE_LIMITED", TOO_MANY, 429)
    if job.person_asset_sha256 and (job.person_asset is None or job.person_asset.expires_at <= timezone.now()
                                    or not session.consent_given):
        return _error("RETRY_NOT_ALLOWED", "The photo for this preview was removed.", 409)
    try:
        provider = get_provider(job.provider)
    except ProviderUnavailable:
        return _error("PREVIEW_UNAVAILABLE", "Previews are unavailable right now.", 503)
    units = provider.units_per_job
    if units and session.status != "FEASIBILITY" and (
            preview_jobs.committed_units(owner_session=session) + units > settings.PREVIEW_SESSION_UNIT_CAP):
        return _error("PREVIEW_LIMIT", "You've reached the preview limit for this session. "
                      "Your options and quote are unaffected.", 429)
    try:
        with transaction.atomic():
            retry = PreviewJob.objects.create(
                owner_session=session, variant=job.variant, retry_of=job,
                fingerprint=hashlib.sha256(f"{job.fingerprint}:retry".encode()).hexdigest(),
                provider=job.provider, provider_version=job.provider_version,
                consent_version=session.consent_version, person_asset=job.person_asset,
                person_asset_sha256=job.person_asset_sha256)
    except IntegrityError:
        retry = PreviewJob.objects.get(retry_of=job)
    if retry.state == PENDING:
        _enqueue(retry)
    retry = PreviewJob.objects.select_related("variant", "result_asset").get(pk=retry.pk)
    # Retrying an old batch must not replace the shopper's current photo.
    session.refresh_from_db(fields=["active_preview_ids"])
    if str(job.id) in (session.active_preview_ids or []):
        replaced = [str(retry.id) if value == str(job.id) else value for value in session.active_preview_ids]
        ExchangeSession.objects.filter(pk=session.pk, active_preview_ids=session.active_preview_ids).update(
            active_preview_ids=replaced)
    return JsonResponse(_job_json(retry), status=201)


def preview_status(request, job_id):
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    job = PreviewJob.objects.select_related("owner_session", "variant", "result_asset").filter(
        id=job_id).first()
    if not job or _owned(request, job.owner_session_id) is None:
        return _error("NOT_FOUND", "Preview not found.", 404)
    return JsonResponse(_job_json(job))


def preview_changes(request, job_id):
    """What a top preview changed compared with the photo, drawn from the stability check's own comparison."""
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    job = PreviewJob.objects.select_related("variant", "result_asset", "person_asset").filter(id=job_id).first()
    if not job or _owned(request, job.owner_session_id) is None:
        return _error("NOT_FOUND", "Preview not found.", 404)
    now = timezone.now()
    result, person = job.result_asset, job.person_asset
    if (job.look_id or job.variant.category != "tops" or result is None or person is None
            or result.expires_at <= now or person.expires_at <= now):
        return _error("NOT_FOUND", "No comparison is available for this preview.", 404)
    try:
        image = change_overlay(read_private_image(person.storage_key), read_private_image(result.storage_key))
    except (OSError, ValueError, Image.DecompressionBombError):
        return _error("NOT_FOUND", "No comparison is available for this preview.", 404)
    response = HttpResponse(image, content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    return response


@csrf_protect
def quotes(request, session_id):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    session = _owned(request, session_id)
    if session is None:
        return _error("NOT_FOUND", "Session not found.", 404)
    if closed := _closed(session):
        return closed
    data, error = _validated(request, QuoteSerializer)
    if error:
        return error
    item = ProductVariant.objects.filter(retailer_id=session.order_line.retailer_id,
                                         sku=data["selected_sku"]).first()
    if item is None:
        return _error("INELIGIBLE", "This option is unavailable.")
    # A preview that measurably misses the request does not block the choice, but the shopper makes it knowingly.
    if session.active_preview_ids is None:
        return _error("PREVIEW_CHANGED", "Reopen previews before choosing this item.", 409)
    preview = next((job for job in _active_previews(session) if job.variant_id == item.id), None)
    if "preview_job_id" in data and (str(data["preview_job_id"]) if data["preview_job_id"] else None) != (
            str(preview.id) if preview else None):
        return _error("PREVIEW_CHANGED", "The preview changed. Reopen previews before choosing this item.", 409)
    concern = _preview_concern(preview)
    if concern and (not data.get("accept_preview_concern") or data["preview_concern_token"] != concern):
        return JsonResponse({"error": {"code": "PREVIEW_CONCERN", "message": preview.gate_note,
                                       "preview_job_id": str(preview.id), "concern_token": concern,
                                       "detail": "Choose it anyway, or pick another option."}}, status=422)
    kind = {"fit": "size", "shade": "shade", "look": "jewellery"}.get(session.return_reason, "style")
    try:
        quote_data = make_quote(_domain_line(session.order_line), _domain_variant(item),
                                _domain_policy(_policy(session.order_line)),
                                _domain_preferences(session) if kind != "size" else None,
                                timezone.now() + timedelta(minutes=10), kind=kind)
    except DomainError as exc:
        return _error(exc.code, str(exc))
    quote = Quote.objects.create(
        owner_session=session, variant=item, kind=kind, currency=quote_data.currency,
        preview_concern_ack=concern if data.get("accept_preview_concern") else "",
        credit_minor=quote_data.credit_minor, replacement_minor=quote_data.replacement_minor,
        fee_minor=quote_data.fee_minor, top_up_minor=quote_data.top_up_minor,
        variant_version=quote_data.variant_version, policy_version=quote_data.policy_version,
        preference_version=session.preference_version, expires_at=quote_data.expires_at)
    _advance_status(session, "QUOTE_READY")
    return JsonResponse({"quote_id": str(quote.id), "quote_version": 1, "kind": quote.kind,
                         "sku": item.sku, "title": item.title, "size_label": item.size_label,
                         "credit_minor": quote.credit_minor, "replacement_minor": quote.replacement_minor,
                         "fee_minor": quote.fee_minor, "top_up_minor": quote.top_up_minor,
                         "currency": quote.currency, "expires_at": quote.expires_at.isoformat(),
                         "notice": "Informational quote. No stock is reserved."}, status=201)


@csrf_protect
def requests(request):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    key = request.headers.get("Idempotency-Key", "")
    if not key or len(key) > 100:
        return _error("IDEMPOTENCY_KEY_REQUIRED", "A request key is required.")
    data, error = _validated(request, DraftSerializer)
    if error:
        return error
    if not data["confirmed"] or data["quote_version"] != 1:
        return _error("CONFIRMATION_REQUIRED", "Review and confirm the current quote.")
    with transaction.atomic():
        quote = Quote.objects.select_for_update().select_related(
            "owner_session", "owner_session__order_line",
            "owner_session__order_line__variant", "variant"
        ).filter(id=data["quote_id"]).first()
        if quote is None or _owned(request, quote.owner_session_id) is None:
            return _error("NOT_FOUND", "Quote not found.", 404)
        existing = ExchangeRequest.objects.filter(owner_session=quote.owner_session,
                                                   idempotency_key=key).first()
        if existing:
            if existing.quote_id != quote.id:
                return _error("KEY_REUSED", "Use a new request key for a different quote.", 409)
            return JsonResponse({"request_id": str(existing.id), "state": existing.state,
                                 "duplicate": True})
        session = quote.owner_session
        if closed := _closed(session):
            return closed
        if quote.kind != "size" and session.preference_version != quote.preference_version:
            return _error("QUOTE_CHANGED", "Preferences changed; review a fresh quote.", 409)
        if session.active_preview_ids is None:
            return _error("QUOTE_CHANGED", "Reopen previews and review a fresh quote.", 409)
        current_previews = _active_previews(session)
        # The original's result can change an alternative's print/size check, too.
        if any(job.state in (PENDING, "running") or _gate_pending(job, _visible_preview(job))
               for job in current_previews):
            return _error("PREVIEW_PENDING", "Previews are still processing. Wait for the checks, then review and save your draft.", 409)
        preview = next((job for job in current_previews if job.variant_id == quote.variant_id), None)
        concern = _preview_concern(preview)
        if concern and concern != quote.preview_concern_ack:
            return _error("QUOTE_CHANGED", "The preview now has a concern. Review a fresh quote and its warning.", 409)
        line = session.order_line
        item = ProductVariant.objects.select_for_update().get(pk=quote.variant_id)
        policy = _policy(line)
        try:
            revalidate_quote(
                DomainQuote(item.sku, quote.currency, quote.credit_minor,
                            quote.replacement_minor, quote.fee_minor, quote.top_up_minor,
                            quote.variant_version, quote.policy_version, quote.expires_at, quote.kind),
                _domain_line(line), _domain_variant(item), _domain_policy(policy),
                _domain_preferences(session) if quote.kind != "size" else None, timezone.now())
        except DomainError as exc:
            return _error(exc.code, str(exc), 409)
        try:
            with transaction.atomic():
                draft = ExchangeRequest.objects.create(
                    owner_session=session, quote=quote, idempotency_key=key,
                    customer_confirmed_at=timezone.now())
        except IntegrityError:
            existing = ExchangeRequest.objects.filter(owner_session=session,
                                                      idempotency_key=key).first()
            if existing is None or existing.quote_id != quote.id:
                return _error("DRAFT_CONFLICT", "A draft already exists for this session.", 409)
            return JsonResponse({"request_id": str(existing.id), "state": existing.state,
                                 "duplicate": True})
        _advance_status(session, "DRAFT_CREATED")
        EvidenceEvent.objects.create(actor="shopper", action="draft_created", session=session,
                                     facts={"quote_id": str(quote.id), "kind": quote.kind,
                                            "variant_version": item.version, "policy_version": policy.version})
    return JsonResponse({"request_id": str(draft.id), "state": draft.state,
                         "message": "Draft saved for staff review. No exchange is confirmed.",
                         "return_option": True}, status=201)


REQUEST_STATES = {
    "DRAFT": "Waiting for staff review. No exchange is confirmed.",
    "NEEDS_CLARIFICATION": "Staff need more information before they can review it.",
    "REVIEWED": "Staff reviewed your request. In a real store they would now arrange the exchange; "
                "this demo changes no order and takes no payment.",
    "DECLINED": "Staff declined this exchange. You can still use the normal return route.",
}


def _request_json(draft):
    return {"request_id": str(draft.id), "state": draft.state, "state_text": REQUEST_STATES.get(draft.state, ""),
            "message": draft.message_to_shopper, "reply": draft.shopper_reply,
            "can_reply": draft.state == "NEEDS_CLARIFICATION", "return_option": True}


def request_status(request, request_id):
    """The shopper's view of their draft: its review state and any question from staff."""
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    draft = ExchangeRequest.objects.filter(id=request_id).first()
    if draft is None or _owned(request, draft.owner_session_id) is None:
        return _error("NOT_FOUND", "Request not found.", 404)
    return JsonResponse(_request_json(draft))


@csrf_protect
def request_reply(request, request_id):
    """The shopper answers staff's question; the draft goes back to staff for review."""
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    data, error = _validated(request, ReplySerializer)
    if error:
        return error
    with transaction.atomic():
        draft = ExchangeRequest.objects.select_for_update().filter(id=request_id).first()
        if draft is None or _owned(request, draft.owner_session_id) is None:
            return _error("NOT_FOUND", "Request not found.", 404)
        if draft.state != "NEEDS_CLARIFICATION":
            return _error("INVALID_TRANSITION", "Staff have not asked a question about this request.", 409)
        draft.shopper_reply = data["reply"].strip()
        draft.replied_at = timezone.now()
        draft.state = "DRAFT"
        draft.save(update_fields=["shopper_reply", "replied_at", "state"])
        EvidenceEvent.objects.create(actor="shopper", action="shopper_replied", session_id=draft.owner_session_id,
                                     facts={"request_id": str(draft.id)})
    return JsonResponse(_request_json(draft))


@staff_or_judge
def staff_insights(request):
    from .insights import summary
    return render(request, "exchange/staff_insights.html", summary())


@staff_or_judge
def staff_queue(request):
    drafts = list(ExchangeRequest.objects.select_related(
        "quote", "quote__variant", "owner_session__order_line", "owner_session__order_line__variant"
    ).order_by("-customer_confirmed_at")[:100])
    by_session = defaultdict(list)
    for job in PreviewJob.objects.filter(owner_session_id__in={d.owner_session_id for d in drafts}).select_related(
            "variant", "person_asset", "owner_session__order_line").order_by("created_at"):
        job.person_source = _person_source(job)
        job.baseline = _is_baseline(job)
        by_session[job.owner_session_id].append(job)
    for draft in drafts:
        draft.preview_jobs = by_session.get(draft.owner_session_id, [])
        reason = RETURN_REASONS.get(draft.owner_session.return_reason or "style")
        draft.reason_label = reason["label"] if reason else draft.owner_session.return_reason
    return render(request, "exchange/staff.html", {"drafts": drafts})


@csrf_protect
@staff_or_judge
def staff_suggestion(request, request_id):
    """A draft message for staff to edit before they send it. Nothing is sent or decided here."""
    from . import advisor
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    try:
        state = _body(request).get("state")
    except ValueError as exc:
        return _error("INVALID_INPUT", str(exc))
    if state not in ("REVIEWED", "DECLINED", "NEEDS_CLARIFICATION"):
        return _error("INVALID_INPUT", "Choose REVIEWED, DECLINED or NEEDS_CLARIFICATION.")
    draft = ExchangeRequest.objects.select_related(
        "quote__variant", "owner_session__order_line__variant").filter(id=request_id).first()
    if draft is None:
        return _error("NOT_FOUND", "Draft not found.", 404)
    return JsonResponse(advisor.staff_note(draft, state))


@csrf_protect
@staff_or_judge
def staff_request(request, request_id):
    if request.method != "PATCH":
        return _error("METHOD_NOT_ALLOWED", "PATCH is required.", 405)
    data, error = _validated(request, StaffDecisionSerializer)
    if error:
        return error
    with transaction.atomic():
        draft = ExchangeRequest.objects.select_for_update().filter(id=request_id).select_related("owner_session").first()
        if not draft:
            return _error("NOT_FOUND", "Draft not found.", 404)
        if draft.state == "NEEDS_CLARIFICATION":
            return _error("INVALID_TRANSITION", "Waiting for the shopper's reply.", 409)
        if draft.state != "DRAFT":
            return _error("INVALID_TRANSITION", "This draft has already been reviewed.", 409)
        draft.state = data["state"]
        draft.message_to_shopper = data["message"].strip()
        draft.reviewed_at = timezone.now()
        draft.save(update_fields=["state", "message_to_shopper", "reviewed_at"])
        EvidenceEvent.objects.create(actor="staff", action="draft_reviewed", session=draft.owner_session,
                                     facts={"request_id": str(draft.id), "state": draft.state})
    return JsonResponse({"request_id": str(draft.id), "state": draft.state,
                         "message": "Demo review recorded; no commerce action was taken."})
