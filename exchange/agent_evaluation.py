"""Separate agent evaluation: deterministic contracts and an explicitly approved public-image pilot.

No customer data or new YouCam rendering. The image cases are already known development/holdout
outputs; this is a new assessment task, not a new held-out generation study or human fidelity test.
"""
import hashlib
import itertools
import json
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import ai_client, copilot, visual_review
from .assets import normalize_photo
from .models import ExchangeSession, OrderLine, ProductVariant
from .replay import recorded, ReplayPreviewProvider
from django.core.files.uploadedfile import SimpleUploadedFile

VERSION = "exchange-agents-v1"
AGENT_CASES = [
    {"id": "agent-01", "budget": 0, "revisit": None, "expected": "CHOOSE_LIMIT"},
    {"id": "agent-02", "budget": 0, "revisit": "budget", "expected": "REVIEW_TRADEOFF"},
    {"id": "agent-03", "budget": 0, "revisit": "print", "expected": "CHOOSE_LIMIT"},
    {"id": "agent-04", "budget": 100, "revisit": "sleeves", "expected": "REVIEW_TRADEOFF"},
    {"id": "agent-05", "budget": 200, "revisit": None, "expected": "COMPARE_OPTIONS"},
    {"id": "agent-06", "budget": 1000, "revisit": None, "expected": "COMPARE_OPTIONS"},
]


def case_bundles():
    variants = {v.sku: v for v in ProductVariant.objects.filter(retailer_id="secondlook-demo")}
    original = variants["ORIGINAL"]
    prefs = {"sleeve_required": "long", "quieter_print": True}
    models = {m["name"]: m for m in recorded("exchange/models/manifest.json")["models"]}
    provider = ReplayPreviewProvider()
    bundles = []
    for sku in ("CALM-04", "CALM-07"):
        model = models["model-a"]
        source = visual_review.public_bytes(model["static_path"])
        normal = normalize_photo(SimpleUploadedFile("source.jpg", source, content_type="image/jpeg"))
        row = provider.results[("model-a", sku)]
        bundle = visual_review.make_bundle({"bought_catalog": visual_review.public_bytes(original.image_asset_id),
            "replacement_catalog": visual_review.public_bytes(variants[sku].image_asset_id),
            "displayed_preview": visual_review.served_result(visual_review.public_bytes("exchange/evidence/" + row["result_file"]))},
            prefs, visual_review.digest(normal))
        bundles.append({"id": "review-dev-" + sku, "cohort": "known development recording", "bundle": bundle})
    for row in recorded("exchange/evidence/heldout-v1/report.json")["cases"]:
        if row["role"] != "alternative":
            continue
        bundle = visual_review.make_bundle({"bought_catalog": visual_review.public_bytes(original.image_asset_id),
            "replacement_catalog": visual_review.public_bytes(row["product_image"]),
            "displayed_preview": visual_review.public_bytes(row["result_image"])}, prefs,
            visual_review.digest(visual_review.public_bytes(row["person_image"])))
        bundles.append({"id": "review-" + row["id"], "cohort": "known frozen photo-study output; new model assessment", "bundle": bundle})
    return bundles


def manifest():
    from .views import _domain_policy, _policy
    # Snapshots detect catalog/policy drift before any paid execution.
    line = OrderLine.objects.select_related("variant").get(token="DEMO-ORDER-001")
    variants = list(ProductVariant.objects.filter(retailer_id=line.retailer_id).order_by("sku").values(
        "sku", "price_minor", "stock_qty", "sleeve", "pattern_level", "color_tag", "neckline", "version"))
    policy = _domain_policy(_policy(line))
    value = {"version": VERSION, "agent_prompt_sha256": hashlib.sha256(copilot.SYSTEM.encode()).hexdigest(),
             "visual_prompt_sha256": visual_review.PROMPT_SHA, "agent_model": "claude-haiku-5-5",
             "primary_review_model": "claude-sonnet-5-5", "second_review_model": "claude-haiku-5-5",
             "agent_cases": AGENT_CASES, "visual_cases": [{k: v for k, v in item.items() if k != "bundle"} |
                 {k: v for k, v in item["bundle"].items() if k != "images"} for item in case_bundles()],
             "catalog_sha256": hashlib.sha256(json.dumps(variants, sort_keys=True).encode()).hexdigest(),
             "policy": {"version": policy.version, "expires_at": policy.expires_at.isoformat(), "fee_minor": policy.fee_minor,
                        "exchange_allowed": policy.exchange_allowed},
             "maximum_generation_calls": 6 * copilot.MAX_ROUNDS + 6 * 2,
             "worst_case_reservations_usd": str(6 * copilot.MAX_ROUNDS * ai_client.price("claude-haiku-5-5", 90000, 1000)
                 + 6 * ai_client.price("claude-sonnet-5-5", 90000, 1000) + 6 * ai_client.price("claude-haiku-5-5", 90000, 1000)),
             "new_youcam_calls": 0, "independent_human_labels": 0,
             "stopping_rule": "Stop on the first failed/uncertain call or contract failure; no retries or replacement cases.",
             "claims": "Contract compliance and descriptive model agreement only; no shopper study or independent fidelity accuracy."}
    return value


def offline_contracts():
    """192 catalog scenarios across budgets, sleeves, prints and selected limits with independent nonmutation and single-limit invariants."""
    from .views import _eligible
    line = OrderLine.objects.select_related("variant").get(token="DEMO-ORDER-001")
    session = ExchangeSession.objects.create(order_line=line, browser_session_key="agent-contract-evaluation",
        status="FEASIBILITY", return_reason="style", judge=True, preference_version=1,
        expires_at=timezone.now() + timezone.timedelta(hours=1))
    outcomes = []
    try:
        for budget, sleeve, quieter, revisit in itertools.product((0, 100, 200, 500, 1000, 2000),
                ("", "long", "short", "sleeveless"), (False, True), (None, "budget", "sleeves", "print")):
            session.preferences = {"max_top_up_minor": budget, "currency": "USD", "sleeve_required": sleeve,
                                   "quieter_print": quieter}
            before = json.dumps(session.preferences, sort_keys=True)
            tools = copilot.Tools(session, revisit)
            result = copilot.rules(tools)
            found = _eligible(session)
            failures = []
            if json.dumps(session.preferences, sort_keys=True) != before:
                failures.append("mutated preferences")
            if result["confirmed"] or not result["return_option"]:
                failures.append("confirmed a decision or removed return")
            if bool(found) != (result["status"] == "COMPARE_OPTIONS"):
                failures.append("eligible-option routing")
            for possibility in result.get("possibilities", []):
                revised = dict(session.preferences)
                field = {"budget": "max_top_up_minor", "sleeves": "sleeve_required", "print": "quieter_print"}[revisit]
                revised[field] = possibility["after"]
                session.preferences = revised
                eligible = {c.variant.sku for c in _eligible(session)}
                if not eligible or not set(possibility["candidate_skus"]).issubset(eligible):
                    failures.append("ineligible counterfactual")
                session.preferences = json.loads(before)
            outcomes.append({"budget": budget, "sleeves": sleeve, "quieter_print": quieter,
                             "selected_limit": revisit, "status": result["status"], "failures": failures})
    finally:
        session.delete()
    return {"kind": "deterministic contract scenarios; not model or human accuracy", "planned": len(outcomes),
            "passed": sum(not r["failures"] for r in outcomes), "cases": outcomes}
