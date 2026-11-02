"""Preview job lifecycle: claim, start, poll and finish one PreviewJob.

States: pending -> running -> succeeded | failed | uncertain. The three end states are final.
Provider calls happen outside database transactions. A job that may have reached the
provider is never restarted automatically: it ends as uncertain and keeps its unit
reservation until someone reconciles it with the provider's own records.
"""
import hashlib
import io
import json
import logging
import math
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db import connection, transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from secondlook.mock_provider import MOCK_LABEL, MockPreviewProvider
from secondlook.providers import (
    FAILED, PENDING, RUNNING, SUCCEEDED, TERMINAL_STATES, UNCERTAIN, PreviewInput, ProviderOutcomeUnknown,
    ProviderRejected,
)
from secondlook.jewellery import ACCESSORIES, JEWELLERY
from secondlook.shades import BEAUTY
from secondlook.youcam_provider import (
    YouCamBagProvider, YouCamClothesProvider, YouCamEarringProvider, YouCamHairColorProvider, YouCamMakeupProvider,
    YouCamNailProvider, YouCamNecklaceProvider, YouCamShoesProvider,
)
from .assets import discard_asset, read_private_image, save_private_image
from .colour_check import check_preview, check_shade
from .size_check import check_size, earring_anchor, rendered_height, resize_earrings, resize_factor, resized_check
from .models import EvidenceEvent, LookPreview, OrderLine, PreviewControl, PreviewJob, PrivateAsset, ProductVariant

log = logging.getLogger(__name__)

# Refusals made before anything reached the provider. Nothing was sent or charged, so the same job
# may be tried again later, for example after the pause is lifted or the next day begins.
REFUSALS = {"PREVIEWS_PAUSED", "BUDGET_CAP", "DAILY_CAP", "BALANCE_UNKNOWN", "BALANCE_FLOOR"}
# What advance() returns to a task chain that no longer drives the job. It is not a job state.
SUPERSEDED = "superseded"


class ProviderUnavailable(RuntimeError):
    pass


def youcam_from_settings():
    if not settings.YOUCAM_API_KEY:
        raise ProviderUnavailable("YOUCAM_API_KEY is not set.")
    return YouCamClothesProvider(settings.YOUCAM_API_KEY, base_url=settings.YOUCAM_BASE_URL,
                                 units_per_job=settings.YOUCAM_UNITS_PER_RESULT)


def _youcam_beauty(provider_cls, units):
    def build():
        if not settings.YOUCAM_API_KEY:
            raise ProviderUnavailable("YOUCAM_API_KEY is not set.")
        return provider_cls(settings.YOUCAM_API_KEY, base_url=settings.YOUCAM_BASE_URL, units_per_job=units())
    return build


def _replay():
    from .replay import ReplayPreviewProvider
    return ReplayPreviewProvider()


PROVIDERS = {"mock": MockPreviewProvider, "youcam": youcam_from_settings, "replay": _replay,
             "youcam-makeup": _youcam_beauty(YouCamMakeupProvider, lambda: settings.YOUCAM_MAKEUP_UNITS),
             "youcam-nail": _youcam_beauty(YouCamNailProvider, lambda: settings.YOUCAM_NAIL_UNITS),
             "youcam-earring": _youcam_beauty(YouCamEarringProvider, lambda: settings.YOUCAM_EARRING_UNITS),
             "youcam-necklace": _youcam_beauty(YouCamNecklaceProvider, lambda: settings.YOUCAM_NECKLACE_UNITS),
             "youcam-hair": _youcam_beauty(YouCamHairColorProvider, lambda: settings.YOUCAM_HAIR_UNITS),
             "youcam-shoes": _youcam_beauty(YouCamShoesProvider, lambda: settings.YOUCAM_SHOES_UNITS),
             "youcam-bag": _youcam_beauty(YouCamBagProvider, lambda: settings.YOUCAM_BAG_UNITS)}
# Which feature previews each category. Clothing uses the site's provider as it is.
FEATURES = {"lip": "makeup", "foundation": "makeup", "blush": "makeup", "nail": "nail", "earrings": "earring",
            "necklace": "necklace", "hair": "hair", "shoes": "shoes", "bag": "bag"}


def provider_name_for(category, base=None):
    """The registered provider for a category: e.g. youcam becomes youcam-makeup for a lipstick.

    A provider with no feature-specific version, such as the mock or recorded results, handles every
    category itself.
    """
    base = base or settings.PREVIEW_PROVIDER
    feature = FEATURES.get(category)
    candidate = f"{base}-{feature}" if feature else base
    return candidate if candidate in PROVIDERS else base
# Providers that replay recorded results: no AI run, no units, and available while live previews are paused.
RECORDED = {"replay"}
RECORDED_LABEL = "Recorded YouCam result from the 19 October 2026 pilot. No new AI run."
BEAUTY_RECORDED_LABEL = "Recorded YouCam result from the 27 October 2026 beauty pilot. No new AI run."
JEWELLERY_RECORDED_LABEL = "Recorded YouCam result from the 28 October 2026 earrings pilot. No new AI run."
NECKLACE_RECORDED_LABEL = "Recorded YouCam result from the 28 October 2026 necklace pilot. No new AI run."
HAIR_RECORDED_LABEL = "Recorded YouCam result from the 28 October 2026 hair colour pilot. No new AI run."
ACCESSORY_RECORDED_LABEL = "Recorded YouCam result from the 28 October 2026 shoes pilot. No new AI run."
# Wording fixed by the product brief for any real generated preview.
GENERATED_LABEL = "AI preview. Appearance may differ. Check the product details."
_BUDGET_LOCK_KEY = 0x5EC0


def get_provider(name=None):
    name = name or settings.PREVIEW_PROVIDER
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise ProviderUnavailable(f"Preview provider {name!r} is not registered.") from None


SHADE_CAUTION = "Shades on screen vary with lighting and display; check the swatch."
SIZE_CAUTION = ("Size on screen is measured against the pair you bought, and resized when YouCam draws it out of "
                "proportion. Check the length in the product details.")
NECKLACE_CAUTION = "Length on screen is approximate. Check the length in the product details."
ACCESSORY_CAUTION = "Heel height on screen is approximate. Check it in the product details."


def demo_bag_gender(asset):
    """The gender recorded for an AI-generated demo person, which YouCam's bag try-on requires; None for any other
    photo. It is looked up from the demo people's manifest by Pixabay ID, never inferred from a photo."""
    found = re.search(r"Pixabay (?:image )?(\d+)", asset.license_or_consent if asset else "")
    path = finders.find("exchange/models/manifest.json")
    if not found or not path:
        return None
    for model in json.loads(Path(path).read_text(encoding="utf-8"))["models"]:
        if str(model["source"]["pixabay_id"]) == found.group(1):
            return model.get("bag_gender")
    return None


def preview_label(job):
    if job.provider == "mock":
        return MOCK_LABEL
    beauty = job.variant.category in BEAUTY
    if job.provider in RECORDED:
        recorded = (HAIR_RECORDED_LABEL if job.variant.category == "hair" else BEAUTY_RECORDED_LABEL if beauty
                    else NECKLACE_RECORDED_LABEL
                    if job.variant.category == "necklace" else JEWELLERY_RECORDED_LABEL
                    if job.variant.category in JEWELLERY else ACCESSORY_RECORDED_LABEL
                    if job.variant.category in ACCESSORIES else RECORDED_LABEL)
        label = f"{GENERATED_LABEL} {recorded}"
    else:
        label = GENERATED_LABEL
    if job.variant.category in JEWELLERY:
        return f"{label} {NECKLACE_CAUTION if job.variant.category == 'necklace' else SIZE_CAUTION}"
    if job.variant.category in ACCESSORIES:
        return f"{label} {ACCESSORY_CAUTION}"
    return f"{label} {SHADE_CAUTION}" if beauty else label


def committed_units(**filters):
    """Units spent or possibly spent by non-mock jobs, optionally narrowed by queryset filters.

    Finished jobs count what they were charged; open and uncertain jobs keep their reservation.
    """
    total = 0
    for state, reserved, observed in PreviewJob.objects.exclude(provider="mock").filter(**filters).values_list(
            "state", "reserved_units", "observed_units"):
        total += observed if state in (SUCCEEDED, FAILED) else max(reserved, observed)
    return total


def control():
    return PreviewControl.load()


def _day_start():
    return timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)


def limit_problem(session, units, provider_name=""):
    """Checks that need no provider call. Returns a refusal code, or an empty string."""
    if provider_name in RECORDED:
        return ""
    if control().paused:
        return "PREVIEWS_PAUSED"
    if not units:
        return ""
    if committed_units() + units > settings.PREVIEW_UNIT_CEILING:
        return "BUDGET_CAP"
    # Judges and the public draw on separate daily caps, both inside the lifetime ceiling.
    daily_cap = settings.JUDGE_DAILY_UNIT_CAP if session.judge else settings.PREVIEW_DAILY_UNIT_CAP
    if (committed_units(started_at__gte=_day_start(), owner_session__judge=session.judge) + units
            > daily_cap):
        return "DAILY_CAP"
    if (session.status != "FEASIBILITY"
            and committed_units(owner_session=session) + units > settings.PREVIEW_SESSION_UNIT_CAP):
        return "SESSION_CAP"
    return ""


def refresh_balance(provider, force=False):
    """Store a fresh balance reading when the stored one is stale. A failed read keeps the old one."""
    row = control()
    max_age = timedelta(seconds=settings.YOUCAM_BALANCE_MAX_AGE_SECONDS)
    if not force and row.balance_checked_at and timezone.now() - row.balance_checked_at < max_age:
        return row
    try:
        units = provider.balance()
    except Exception:
        log.warning("Balance check failed for provider %s", provider.name)
        return row
    PreviewControl.objects.filter(pk=row.pk).update(balance_units=units, balance_checked_at=timezone.now())
    row.refresh_from_db()
    return row


def _balance_problem(units):
    """Refuse a paid start that could take the provider balance below the floor."""
    floor = settings.YOUCAM_BALANCE_FLOOR
    if not units or floor is None:
        return ""
    row = control()
    max_age = timedelta(seconds=settings.YOUCAM_BALANCE_MAX_AGE_SECONDS)
    if row.balance_units is None or not row.balance_checked_at or timezone.now() - row.balance_checked_at > max_age:
        return "BALANCE_UNKNOWN"
    spent_since = committed_units(started_at__gte=row.balance_checked_at)
    if row.balance_units - spent_since - units < floor:
        return "BALANCE_FLOOR"
    return ""


# Failures where the same inputs may well work a second time.
RETRYABLE = {"PROVIDER_FAILED", "RESULT_UNREADABLE", "RESULT_MISSING", "UPLOAD_FAILED", "RATE_LIMITED",
             "QUEUE_TIMEOUT", "unknown_internal_error", "error_inference", "error_upload", "error_download_image"}
# Failures caused by the person photo. Another try with the same photo would fail the same way.
PHOTO_PROBLEMS = {"PERSON_IMAGE_REQUIRED", "error_pose", "error_no_face", "error_no_shoulder",
                  "error_multiple_people", "error_multi_person", "error_large_face_angle", "error_invalid_src",
                  "error_apply_region_mismatch", "error_face_parsing", "error_unsupport_ratio",
                  "error_below_min_image_size", "exceed_max_filesize", "error_exceed_max_image_size",
                  "error_decode_image", "error_nsfw_content_detected",
                  # Earrings VTO: the ear is too small or too far from the centre of the photo.
                  "PHOTO_CHECK_INVALID"}
WITHDRAWN = {"CONSENT_WITHDRAWN", "CONSENT_CHANGED", "ASSET_UNAVAILABLE"}


def latest_attempt(job):
    """The shopper's retry of this job if there is one, otherwise the job itself."""
    retry = PreviewJob.objects.filter(retry_of=job).select_related("variant", "result_asset").first()
    return retry or job


def retry_problem(job):
    """Why this job cannot be retried by the shopper, or an empty string if it can."""
    if job.state != FAILED:
        return "NOT_FAILED" if job.state != UNCERTAIN else "OUTCOME_UNKNOWN"
    if job.look_id:
        return "LOOK_STEP"
    if job.retry_of_id or PreviewJob.objects.filter(retry_of=job).exists():
        return "ALREADY_RETRIED"
    if job.error_code in PHOTO_PROBLEMS:
        return "PHOTO_PROBLEM"
    if job.error_code not in RETRYABLE:
        return "NOT_RETRYABLE"
    return ""


def job_message(job):
    """Plain words for the shopper about one preview's state."""
    if job.state == PENDING:
        return "Queued."
    if job.state == RUNNING:
        return "Creating the preview. This usually takes about 20 seconds."
    if job.state == SUCCEEDED:
        return preview_label(job)
    if job.state == UNCERTAIN:
        return "We could not confirm this preview, so it will not be retried."
    code = job.error_code
    if code in WITHDRAWN:
        return "This preview was discarded because the photo was removed."
    if code == "SESSION_CAP":
        return "You have reached the preview limit for this session."
    if code in REFUSALS:
        return "Previews are unavailable right now."
    if code == "DEMO_PERSON_REQUIRED":
        return ("Bag previews use the AI-generated demo people: YouCam needs details about the person that Rechoice "
                "never guesses from a photo.")
    if code in PHOTO_PROBLEMS:
        return "This photo did not work for a preview. Try a different photo or a demo model."
    if code == "NOT_RECORDED":
        own_photo = job.person_asset is not None and not job.person_asset.license_or_consent.startswith(
            "AI-generated demo model")
        if own_photo:
            return ("Recorded results only cover the AI-generated demo people. Choose a live preview to see "
                    "this on your own photo.")
        if job.variant.category == "shoes":
            return ("There is no recorded result for these shoes on this person. Recorded results cover Model F with "
                    "the store's four pairs; a live preview can show any pair.")
        if job.variant.category == "necklace":
            return ("There is no recorded result for this necklace on this person. Recorded results cover Model A "
                    "with the suggested necklaces; a live preview can show any necklace.")
        if job.variant.category in JEWELLERY:
            return ("There are no recorded results for these earrings on this face. Recorded results cover Face A "
                    "with the suggested earrings; a live preview can show any pair.")
        if job.variant.category in BEAUTY:
            person = {"nail": "Hand A", "hair": "Model A"}.get(job.variant.category, "Face A")
            where = {"Hand A": "hand", "Model A": "person"}.get(person, "face")
            return (f"There is no recorded result for this shade on this {where}. "
                    f"Recorded results cover {person} with the shades suggested for a cooler shade; "
                    "a live preview can show any shade.")
        return ("There is no recorded result for this top on this model. Recorded results cover Model A with the "
                "suggested tops; a live preview can make any pair.")
    if code == "QUEUE_TIMEOUT":
        return "This preview took too long to start." + ("" if retry_problem(job) else " You can try once more.")
    if not retry_problem(job):
        return "The preview failed. You can try once more."
    return "The preview failed."


def reopen_if_refused(job):
    """Let a job refused before reaching the provider run again. Returns the job, refreshed."""
    if job.state == FAILED and job.error_code in REFUSALS and not job.provider_task_id and not job.started_at:
        now = timezone.now()
        PreviewJob.objects.filter(pk=job.pk, state=FAILED, error_code=job.error_code).update(
            state=PENDING, error_code="", ended_at=None, queued_at=now, last_step_at=now)
        job.refresh_from_db()
    return job


def _lock_budget():
    # Serializes unit checks across workers. SQLite already serializes writers.
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_BUDGET_LOCK_KEY])


def _locked(job_id):
    return PreviewJob.objects.select_for_update(of=("self",)).select_related(
        "owner_session", "person_asset", "variant").get(pk=job_id)


def _finish(job, state, error_code="", **fields):
    """Record an end state. The caller holds the row lock."""
    job.state = state
    job.error_code = error_code
    job.ended_at = timezone.now()
    for name, value in fields.items():
        setattr(job, name, value)
    job.save()
    EvidenceEvent.objects.create(
        actor="system", action="preview_job_ended", session_id=job.owner_session_id,
        facts={"job_id": str(job.id), "state": state, "error_code": error_code,
               "provider": job.provider, "reserved_units": job.reserved_units,
               "observed_units": job.observed_units,
               **({"approval": settings.PREVIEW_LIVE_APPROVAL} if job.provider != "mock" else {})})
    return state


def _end(job_id, state, error_code="", only_from=None, **fields):
    """End a job unless it already ended, or, with only_from, unless it has moved on from that state."""
    with transaction.atomic():
        job = _locked(job_id)
        if job.state in TERMINAL_STATES or (only_from and job.state != only_from):
            return job.state
        return _finish(job, state, error_code, **fields)


def _input_problem(job):
    session = job.owner_session
    if not session.consent_given or session.consent_version != job.consent_version:
        return "CONSENT_CHANGED"
    if job.person_asset_sha256:
        asset = job.person_asset
        if (asset is None or asset.expires_at <= timezone.now()
                or asset.sha256 != job.person_asset_sha256):
            return "ASSET_UNAVAILABLE"
    return ""


def _claim(job_id, provider, driver=""):
    """Move pending to running after consent and unit checks. Returns (job or None, state).

    The claiming task chain becomes the job's driver, the only chain that polls it from then on.
    """
    with transaction.atomic():
        _lock_budget()
        job = _locked(job_id)
        if job.state != PENDING:
            return None, job.state
        if problem := _input_problem(job):
            return None, _finish(job, FAILED, problem)
        units = provider.units_per_job
        if problem := limit_problem(job.owner_session, units, provider.name) or _balance_problem(units):
            return None, _finish(job, FAILED, problem)
        job.state = RUNNING
        job.started_at = job.last_step_at = timezone.now()
        job.reserved_units = units
        job.driver = driver
        job.save(update_fields=["state", "started_at", "reserved_units", "driver", "last_step_at"])
        return job, RUNNING


def _store_result(job, data):
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        out = io.BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=90)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None
    key, digest = save_private_image(out.getvalue())
    limits = [job.owner_session.expires_at, timezone.now() + timedelta(hours=24)]
    if job.person_asset_id:
        limits.append(job.person_asset.expires_at)
    expires = min(limits)
    return PrivateAsset.objects.create(
        owner_session=job.owner_session, kind="preview", sha256=digest, storage_key=key,
        license_or_consent=f"generated under consent version {job.consent_version}",
        expires_at=expires)


def _garment_bytes(variant):
    """The shopper-facing catalog photo, which is the retouched copy for labelled garments."""
    path = finders.find(variant.image_asset_id) if variant.image_asset_id else None
    return Path(path).read_bytes() if path else None


def start_job(job_id, driver=""):
    job = PreviewJob.objects.select_related("owner_session").get(pk=job_id)
    if job.state != PENDING:
        return job.state
    try:
        provider = get_provider(job.provider)
    except ProviderUnavailable:
        return _end(job_id, FAILED, "PROVIDER_UNAVAILABLE")
    # Cheap ledger checks first, so a refused job never even reads the provider balance.
    if problem := limit_problem(job.owner_session, provider.units_per_job, provider.name):
        return _end(job_id, FAILED, problem)
    if provider.units_per_job and settings.YOUCAM_BALANCE_FLOOR is not None and hasattr(provider, "balance"):
        refresh_balance(provider)
    job, state = _claim(job_id, provider, driver)
    if job is None:
        return state
    try:
        person = read_private_image(job.person_asset.storage_key) if job.person_asset else None
    except OSError:
        return _end(job_id, FAILED, "ASSET_UNAVAILABLE")
    beauty = job.variant.category in BEAUTY
    try:
        garment = None if beauty else _garment_bytes(job.variant)
    except OSError:
        return _end(job_id, FAILED, "GARMENT_UNAVAILABLE")
    if person and getattr(provider, "max_long_side", None):
        person = _fit_long_side(person, provider.max_long_side)
    if beauty:
        params = {"category": job.variant.category, "color": job.variant.shade_hex, "finish": job.variant.finish}
    elif job.variant.category == "necklace":
        params = {"category": job.variant.category, "length_mm": job.variant.length_mm}
    elif job.variant.category in JEWELLERY:
        params = {"category": job.variant.category, "length_mm": job.variant.length_mm,
                  "scale_mm": job.variant.scale_mm, "anchor": earring_anchor(garment) if garment else None}
    elif job.variant.category == "bag":
        params = {"category": "bag", "gender": demo_bag_gender(job.person_asset)}
    else:
        params = None
    try:
        started = provider.start(PreviewInput(
            str(job.id), job.variant.sku, job.variant.version, job.variant.image_asset_id,
            person, consent=True, consent_version=job.consent_version, garment_image=garment, params=params))
    except ProviderRejected as exc:
        return _end(job_id, FAILED, exc.code)
    except ProviderOutcomeUnknown as exc:
        log.error("Preview start outcome unknown for job %s: %s", job_id, exc.code)
        return _end(job_id, UNCERTAIN, exc.code)
    except Exception:
        log.exception("Preview start outcome unknown for job %s", job_id)
        return _end(job_id, UNCERTAIN, "START_UNCERTAIN")
    PreviewJob.objects.filter(pk=job_id, state=RUNNING).update(
        provider_task_id=started.provider_task_id[:180],
        reserved_units=max(job.reserved_units, started.reserved_units), last_step_at=timezone.now())
    return poll_job(job_id)


def poll_job(job_id):
    job = PreviewJob.objects.select_related("owner_session").get(pk=job_id)
    if job.state != RUNNING or not job.provider_task_id:
        return job.state
    try:
        result = get_provider(job.provider).poll(job.provider_task_id)
    except Exception:
        log.warning("Preview poll failed for job %s; will retry", job_id, exc_info=True)
        return RUNNING
    units = result.observed_units
    if result.state in (PENDING, RUNNING):
        if units:
            PreviewJob.objects.filter(pk=job_id, state=RUNNING).update(observed_units=units)
        return RUNNING
    if result.state == SUCCEEDED:
        # Consent or the photo may have been withdrawn while the provider worked. Never release a
        # late result in that case; the units are already spent, so they stay counted.
        current = PreviewJob.objects.select_related("owner_session", "person_asset").get(pk=job_id)
        if problem := _input_problem(current):
            code = "CONSENT_WITHDRAWN" if problem == "CONSENT_CHANGED" else problem
            return _end(job_id, FAILED, code, observed_units=units)
        asset = _store_result(job, result.image) if result.image else None
        if result.image and asset is None:
            return _end(job_id, FAILED, "RESULT_UNREADABLE", observed_units=units)
        state = _end(job_id, SUCCEEDED, observed_units=units, result_asset=asset,
                     **(_colour_fields(current, result.image) if result.image else {}))
        if asset and not PreviewJob.objects.filter(pk=job_id, result_asset=asset).exists():
            discard_asset(asset)
        if current.variant.category in JEWELLERY and result.image:
            _size_checks(current)
        return state
    if result.state == FAILED:
        return _end(job_id, FAILED, result.error_code or "PROVIDER_FAILED", observed_units=units)
    return _end(job_id, UNCERTAIN, result.error_code or "PROVIDER_UNCERTAIN", observed_units=units)


def _fit_long_side(data, limit):
    """Scale a photo down so its longest side fits a feature's limit. Smaller photos are sent as they are."""
    image = Image.open(io.BytesIO(data))
    if max(image.size) <= limit:
        return data
    image = image.convert("RGB")
    image.thumbnail((limit, limit))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _colour_fields(job, image):
    """Compare the result's colour with the catalog: the garment colour, or the applied shade. Never fails the job."""
    try:
        person = read_private_image(job.person_asset.storage_key) if job.person_asset else None
        if job.variant.category in BEAUTY:
            check = check_shade(person, image, job.variant.shade_hex)
        else:
            check = check_preview(person, image, job.variant.reference_colour, job.variant.pattern_level)
    except Exception:
        log.warning("Colour check failed for job %s", job.pk, exc_info=True)
        return {"colour_check": "unchecked"}
    return {"colour_check": check.status, "colour_delta": check.delta, "colour_note": check.note}


def _size_checks(job):
    """Compare every earring rendered on this photo with the pair bought, rendered the same way. Never fails the job."""
    try:
        siblings = list(PreviewJob.objects.filter(owner_session=job.owner_session, person_asset=job.person_asset,
                                                  state=SUCCEEDED, result_asset__isnull=False)
                        .select_related("variant", "result_asset", "owner_session__order_line"))
        person = read_private_image(job.person_asset.storage_key) if job.person_asset else None
        for sibling in siblings:
            if sibling.variant.category not in JEWELLERY:
                continue
            if sibling.size_px is None:
                sibling.size_px = rendered_height(person, read_private_image(sibling.result_asset.storage_key))
        bought = next((s for s in siblings if s.variant_id == job.owner_session.order_line.variant_id), None)
        for sibling in siblings:
            # A resized earring's check was settled when it was resized.
            if sibling.variant.category not in JEWELLERY or sibling.sized_asset_id:
                continue
            check = check_size(sibling.variant, sibling.size_px, bought.variant if bought else None,
                               bought.size_px if bought else None)
            sized = None
            if check.status == "size-differs" and sibling.variant.category == "earrings":
                if sized := _resize(sibling, person, bought):
                    check = resized_check(check)
            PreviewJob.objects.filter(pk=sibling.pk).update(size_px=sibling.size_px, size_check=check.status,
                                                            size_note=check.note, sized_asset_id=sized)
    except Exception:
        log.warning("Size check failed for job %s", job.pk, exc_info=True)


def _resize(job, person, bought):
    """Store the earring resized to its catalog length, and return the stored copy's id. None if it fails.

    Necklaces are not resized: a chain runs round the neck, so scaling it from one point would break it.
    """
    factor = resize_factor(job.variant, job.size_px, bought.variant, bought.size_px)
    data = resize_earrings(person, read_private_image(job.result_asset.storage_key), factor or 0)
    # The resized copy is measured like any result, and kept only if it now reads in proportion.
    if data is None or check_size(job.variant, rendered_height(person, data), bought.variant,
                                  bought.size_px).status != "size-close":
        return None
    key, digest = save_private_image(data)
    return PrivateAsset.objects.create(
        owner_session=job.owner_session, kind="preview", sha256=digest, storage_key=key,
        license_or_consent=job.result_asset.license_or_consent, expires_at=job.result_asset.expires_at).id


def advance(job_id, driver=""):
    """Do the next step for one job and return its state afterwards.

    A running job is polled only by its driver. Any other task chain gets SUPERSEDED and stops, so a
    repeated request or a recovery never leaves two chains polling one job.
    """
    job = PreviewJob.objects.filter(pk=job_id).values("state", "driver").first()
    if job is None:
        return None
    if job["state"] == PENDING:
        return start_job(job_id, driver)
    if job["state"] == RUNNING:
        if job["driver"] and job["driver"] != driver:
            return SUPERSEDED
        PreviewJob.objects.filter(pk=job_id, state=RUNNING).update(last_step_at=timezone.now())
        return poll_job(job_id)
    return job["state"]


def give_up(job_id):
    """Polling ran out. The provider may still finish and charge, so the reservation stays."""
    return _end(job_id, UNCERTAIN, "POLL_TIMEOUT")


def recover_stuck_jobs(enqueue, now=None):
    """Drive again the open jobs whose worker task was lost, and end the ones past their time limit.

    enqueue(job_id, attempt, driver) queues a drive task. Recovery never starts a job at the provider
    a second time. A pending job reached nothing, so it is queued again and still faces every claim
    check, including the pause switch, the unit caps and the balance floor. A running job is only
    ever polled again, which costs no units. One without a provider task may still be starting, so
    it is left alone until its time limit and then marked uncertain, keeping its reservation.
    """
    now = now or timezone.now()
    poll, stall = settings.PREVIEW_POLL_SECONDS, timedelta(seconds=settings.PREVIEW_STALL_SECONDS)
    run_limit = timedelta(seconds=settings.PREVIEW_MAX_POLLS * poll) + stall
    queue_limit = timedelta(seconds=settings.PREVIEW_QUEUE_LIMIT_SECONDS)
    counts = {"requeued": 0, "repolled": 0, "timed_out": 0, "gave_up": 0}
    stuck = PreviewJob.objects.filter(state__in=(PENDING, RUNNING), last_step_at__lt=now - stall)
    for job in stuck.order_by("last_step_at")[:200]:
        # Each change is conditional on the job being untouched since it was read, so a worker that
        # made progress meanwhile, or an overlapping sweep, wins.
        untouched = PreviewJob.objects.filter(pk=job.pk, state=job.state, last_step_at=job.last_step_at)
        if job.state == PENDING:
            if job.queued_at < now - queue_limit:
                if _end(job.pk, FAILED, "QUEUE_TIMEOUT", only_from=PENDING) == FAILED:
                    counts["timed_out"] += 1
            elif untouched.update(last_step_at=now):
                _recovered(job, "requeued")
                enqueue(str(job.pk), 0, None)
                counts["requeued"] += 1
            continue
        started = job.started_at or job.created_at
        if started < now - run_limit:
            code = "POLL_TIMEOUT" if job.provider_task_id else "START_UNCERTAIN"
            if _end(job.pk, UNCERTAIN, code, only_from=RUNNING) == UNCERTAIN:
                counts["gave_up"] += 1
        elif job.provider_task_id:
            driver = uuid.uuid4().hex
            if untouched.update(driver=driver, last_step_at=now):
                _recovered(job, "repolled")
                attempt = min(settings.PREVIEW_MAX_POLLS, int((now - started).total_seconds() // poll))
                enqueue(str(job.pk), attempt, driver)
                counts["repolled"] += 1
    return counts


def _recovered(job, how):
    log.warning("Preview job %s was stuck %s; %s", job.pk, job.state, how)
    EvidenceEvent.objects.create(actor="system", action="preview_job_recovered", session_id=job.owner_session_id,
                                 facts={"job_id": str(job.pk), "state": job.state, "how": how,
                                        "last_step_at": job.last_step_at.isoformat()})


def reconcile_units(entries, jobs=None):
    """Record what each paid task actually cost, from provider unit history entries.

    A task with no entry keeps its count, because missing history is not proof of no charge. A charge
    the provider refunded, as YouCam does when an earring task fails, nets to what was kept. Charges
    tied to no known job are reported as spend outside the app.
    """
    net = {}
    for entry in entries:
        change = sum(c.get("amount_dec") or 0 for c in (entry.get("info") or {}).get("credits", []))
        target = str(entry.get("target_id") or "")
        if target and change:
            task = target.split(":", 1)[0]
            net[task] = net.get(task, 0) + change
    charges = {task: max(0, -change) for task, change in net.items()}
    paid = PreviewJob.objects.exclude(provider="mock").exclude(provider_task_id="")
    scope = paid if jobs is None else jobs
    matched = unmatched = 0
    for job in scope:
        if job.provider_task_id not in charges:
            unmatched += 1
            continue
        PreviewJob.objects.filter(pk=job.pk).update(observed_units=math.ceil(charges[job.provider_task_id]))
        matched += 1
    known = set(paid.values_list("provider_task_id", flat=True))
    outside = math.ceil(sum(units for task, units in charges.items() if task not in known))
    if outside:
        log.warning("Unit history shows %s units charged to tasks this app did not create.", outside)
    EvidenceEvent.objects.create(actor="system", action="units_reconciled", facts={
        "matched": matched, "unmatched": unmatched, "outside_units": outside})
    return {"matched": matched, "unmatched": unmatched, "outside_units": outside}


def discard_person_asset(asset):
    """Delete a person photo together with every preview generated from it.

    A look's later steps are generated from the step before, so results of results go too.
    """
    derived, sources = [], [asset.id]
    while sources:
        made = PreviewJob.objects.filter(person_asset_id__in=sources)
        found = list(PrivateAsset.objects.filter(id__in=made.values("result_asset"))
                     | PrivateAsset.objects.filter(id__in=made.values("sized_asset")))
        derived += found
        sources = [result.id for result in found]
    for result in derived:
        discard_asset(result)
    discard_asset(asset)


# Looks: the replacement with the rest of the order on one photo, one item at a time in this order. The top
# goes first because Clothes V4 redraws the whole person; the lipstick and the necklace are then added to that result.
LOOK_ORDER = ("tops", "lip", "necklace")
# Items a look leaves out, and why. Earrings VTO refused the looks probe's chest-up photo on 28 October 2026:
# "[PHOTO_CHECK_INVALID] Ear size should fit in range and position should be in the center."
LOOK_LEFT_OUT = {"nail": "nail polish needs a photo of a hand",
                 "foundation": "foundation barely shows on a photo of this size",
                 "blush": "blush barely shows on a photo of this size",
                 "earrings": "earrings need a close photo of the ear",
                 "hair": "hair colour is not part of a look yet"}
FEATURE_NAMES = {"makeup": "Makeup VTO", "nail": "Nail VTO", "earring": "Earrings VTO", "necklace": "Necklace VTO",
                 "hair": "AI Hair Color", "shoes": "Shoes V3", "bag": "Bag V2", None: "Clothes V4"}
LOOKS_RECORDED_LABEL = ("Recorded YouCam results from the 28 October 2026 looks pilot, added in turn to one photo. "
                        "No new AI run.")


def feature_name(category):
    return FEATURE_NAMES[FEATURES.get(category)]


def _rest_of_order(session):
    line = session.order_line
    return (OrderLine.objects.filter(retailer_id=line.retailer_id, fictional_order_id=line.fictional_order_id)
            .exclude(pk=line.pk).select_related("variant").order_by("pk"))


def look_items(session, replacement):
    """The replacement and the rest of the order that a look can show, in the order they are applied."""
    items = [replacement] + [other.variant for other in _rest_of_order(session)
                             if other.variant.category in LOOK_ORDER and other.variant.category != replacement.category]
    return sorted((v for v in items if v.category in LOOK_ORDER), key=lambda v: LOOK_ORDER.index(v.category))


def look_left_out(session):
    """The rest of the order that a look cannot show, each with the reason."""
    return [(other.variant, LOOK_LEFT_OUT[other.variant.category]) for other in _rest_of_order(session)
            if other.variant.category in LOOK_LEFT_OUT]


def look_plan(variants, base=None):
    """Each item's provider: youcam becomes youcam-makeup for a lipstick, and recorded results stay recorded."""
    from .replay import replay_key
    plan = []
    for variant in variants:
        provider = get_provider(provider_name_for(variant.category, base))
        plan.append({"sku": variant.sku, "key": replay_key(variant), "category": variant.category,
                     "provider": provider.name, "version": provider.version, "units": provider.units_per_job})
    return plan


def _step_fingerprint(look, step):
    return hashlib.sha256(f"look:{look.id}:{step}".encode()).hexdigest()


def look_fingerprint(session, variants, plan, person_asset):
    return hashlib.sha256(
        (f"{session.id}:{session.consent_version}:{person_asset.sha256 if person_asset else 'none'}:"
         + "|".join(f"{v.sku}:{v.version}:{p['provider']}:{p['version']}" for v, p in zip(variants, plan)))
        .encode()).hexdigest()


def create_look(session, variants, plan, person_asset):
    """The look for these items on this photo, with its first step. Asking again returns the same look."""
    look, _ = LookPreview.objects.get_or_create(
        fingerprint=look_fingerprint(session, variants, plan, person_asset), defaults={
            "owner_session": session, "plan": plan, "person_asset": person_asset,
            "person_asset_sha256": person_asset.sha256 if person_asset else "",
            "consent_version": session.consent_version})
    first, _ = PreviewJob.objects.get_or_create(fingerprint=_step_fingerprint(look, 0), defaults={
        "owner_session": session, "variant": variants[0], "provider": plan[0]["provider"],
        "provider_version": plan[0]["version"], "consent_version": look.consent_version, "person_asset": person_asset,
        "person_asset_sha256": look.person_asset_sha256, "look": look, "look_step": 0})
    return look, first


def continue_look(base, steps, variants, plan):
    """A longer look made from the first steps of an earlier one, with the job for its first new step.

    The new step works on the last earlier step's result, so those steps are neither repeated nor paid for
    again. Asking again returns the same look.
    """
    session, n = base.owner_session, len(steps)
    look, _ = LookPreview.objects.get_or_create(
        fingerprint=look_fingerprint(session, variants, plan, base.person_asset), defaults={
            "owner_session": session, "plan": plan, "person_asset": base.person_asset,
            "person_asset_sha256": base.person_asset_sha256, "consent_version": base.consent_version,
            "continues": base, "continued_steps": n})
    person = steps[-1].result_asset
    job, _ = PreviewJob.objects.get_or_create(fingerprint=_step_fingerprint(look, n), defaults={
        "owner_session": session, "variant": variants[n], "provider": plan[n]["provider"],
        "provider_version": plan[n]["version"], "consent_version": look.consent_version, "person_asset": person,
        "person_asset_sha256": person.sha256 if person else "", "look": look, "look_step": n})
    return look, job


def unstarted_look_units(plan, steps):
    """Units the look may still spend: every step not yet started at the provider."""
    return sum(entry["units"] for n, entry in enumerate(plan) if n >= len(steps) or not steps[n].started_at)


def next_look_step(job_id):
    """After a look's step succeeds, the job for its next step, created once. None when there is no next step.

    Each step works on the result of the one before. A provider that makes no image, the mock, keeps the photo.
    """
    job = PreviewJob.objects.select_related("look", "result_asset", "variant").filter(pk=job_id).first()
    if job is None or job.look_id is None or job.state != SUCCEEDED:
        return None
    look, step = job.look, job.look_step + 1
    if step >= len(look.plan):
        return None
    entry = look.plan[step]
    variant = ProductVariant.objects.get(retailer_id=job.variant.retailer_id, sku=entry["sku"])
    person = job.result_asset or look.person_asset
    nxt, _ = PreviewJob.objects.get_or_create(fingerprint=_step_fingerprint(look, step), defaults={
        "owner_session": job.owner_session, "variant": variant, "provider": entry["provider"],
        "provider_version": entry["version"], "consent_version": look.consent_version, "person_asset": person,
        "person_asset_sha256": person.sha256 if person else "", "look": look, "look_step": step})
    return nxt


def look_steps(look):
    """The look's steps in order; for a look that continues another, that look's first steps come first."""
    own = list(look.steps.select_related("variant", "result_asset", "person_asset", "owner_session")
               .order_by("look_step"))
    return (look_steps(look.continues)[:look.continued_steps] + own) if look.continues_id else own


def look_state(look, steps, length=None):
    """The look's state and the step that decided it: the first step that failed, or the last one.

    length counts only a look's first steps, when those alone make a shorter look.
    """
    for step in steps:
        if step.state in (FAILED, UNCERTAIN):
            return step.state, step
    if len(steps) == (length or len(look.plan)) and steps[-1].state == SUCCEEDED:
        return SUCCEEDED, steps[-1]
    if len(steps) == 1 and steps[0].state == PENDING:
        return PENDING, None
    return RUNNING, None


def look_label(look):
    providers = {entry["provider"] for entry in look.plan}
    if providers == {"mock"}:
        return MOCK_LABEL
    label = f"{GENERATED_LABEL} {LOOKS_RECORDED_LABEL}" if providers <= RECORDED else (
        f"{GENERATED_LABEL} Each item was added in turn to the same photo.")
    if any(e["category"] == "necklace" for e in look.plan):
        label = f"{label} {NECKLACE_CAUTION}"
    return f"{label} {SHADE_CAUTION}" if any(e["category"] in BEAUTY for e in look.plan) else label


def look_message(look, state, step):
    """Plain words for the shopper about a look."""
    if state == SUCCEEDED:
        return look_label(look)
    if state in (PENDING, RUNNING):
        return f"Adding the items one at a time, {len(look.plan)} steps. This usually takes about a minute."
    if step.error_code == "NOT_RECORDED":
        return ("There is no recorded look for this choice on this person. Recorded looks cover Model A with the "
                "suggested tops; a live preview can make any look.")
    return f"Step {step.look_step + 1} of {len(look.plan)}, {step.variant.title}: {job_message(step)}"
