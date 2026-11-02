"""Verify the frozen held-out design and inputs without generating any previews.

Optional --audit-db checks prior paid person hashes in the configured database. No .env is loaded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_design(plan):
    if plan["split"] != "held_out" or plan["status"] != "frozen_not_run":
        raise ValueError("This file must remain a frozen, not-run held-out design; store outcomes separately.")
    cases = plan["cases"]
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Case IDs must be nonempty and unique.")
    models = {c["model"] for c in cases}
    if models & set(plan["known_development_models"]):
        raise ValueError("Held-out photos must not reuse a known development subject.")
    if models != {m["name"] for m in plan["models"]}:
        raise ValueError("Every selected model must have frozen metadata and cases.")
    for model in models:
        rows = [c for c in cases if c["model"] == model]
        if sum(c["role"] == "reference" for c in rows) != 1:
            raise ValueError("Each subject needs exactly one reference render.")
    for case in cases:
        if case["category"] != "tops" or case["role"] not in ("reference", "alternative"):
            raise ValueError("This cohort covers reference and alternative tops only.")
        if case.get("result") is not None or case.get("rating") is not None:
            raise ValueError("Completed results and ratings cannot be inserted into the frozen design.")
    units = sum(c["estimated_units"] for c in cases)
    if units != plan["budget"]["proposed_max_units"]:
        raise ValueError("The frozen cases and proposed unit budget disagree.")
    return {"planned_outcomes": len(cases), "distinct_source_photos": len(models),
            "planned_colour_measurements": sum(c["role"] == "alternative" for c in cases),
            "completed_held_out_outcomes": 0, "proposed_units": units}


def check_hashes(root, expected):
    for name, wanted in expected.items():
        path = root / name
        if not path.is_file() or digest(path.read_bytes()) != wanted:
            raise ValueError(f"Frozen input missing or changed: {name}. Restore it; do not silently reseal v1.")


def check_catalog(actual, expected):
    if actual != expected:
        raise ValueError("Catalog facts changed from the frozen snapshot; do not run on different product inputs.")


def verify(root=ROOT, audit_db=False):
    path = root / "assets/heldout_v1.json"
    expected = (root / "assets/heldout_v1.sha256").read_text().split()[0]
    if digest(path.read_bytes()) != expected:
        raise ValueError("Held-out manifest differs from its frozen SHA-256. Version a new protocol if needed.")
    plan = json.loads(path.read_text())
    summary = validate_design(plan)
    check_hashes(root, plan["input_sha256"])
    from django.core.files.uploadedfile import SimpleUploadedFile
    from exchange.assets import normalize_photo
    as_sent = set()
    for model in plan["models"]:
        raw = (root / model["file"]).read_bytes()
        normalized = normalize_photo(SimpleUploadedFile(model["name"] + ".jpg", raw, content_type="image/jpeg"))
        if digest(normalized) != model["normalized_sha256"]:
            raise ValueError(f"{model['name']}: normalized bytes changed. Check the frozen runtime.")
        as_sent.update((digest(raw), digest(normalized)))
    if audit_db:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django
        django.setup()
        from exchange.models import PreviewJob, ProductVariant
        fields = list(next(iter(plan["catalog_snapshot"].values())).keys())
        rows = ProductVariant.objects.filter(retailer_id="secondlook-demo", sku__in=plan["catalog_snapshot"]).values(*fields)
        check_catalog({row["sku"]: row for row in rows}, plan["catalog_snapshot"])
        jobs = PreviewJob.objects.filter(provider__startswith="youcam")
        collisions = set(jobs.values_list("person_asset_sha256", flat=True)) & as_sent
        if collisions:
            raise ValueError("A selected subject already appears in paid job history; do not claim unseen evaluation.")
        summary["prior_paid_jobs_checked"] = jobs.count()
        summary["prior_subject_collisions"] = 0
        summary["catalog_matches_frozen_snapshot"] = True
    return {"protocol": plan["version"], "manifest_sha256": expected, **summary,
            "execution": "not_authorized_or_run_by_this_checker"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(audit_db=args.audit_db), indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
