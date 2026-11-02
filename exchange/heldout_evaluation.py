"""Reproduce the registered photo holdout from public inputs; no database, private media or network.

Reporting glue is new; the measurement functions and their thresholds were frozen before generation.
The immutable design is never rewritten with outcomes.
"""
import hashlib
import json
from pathlib import Path

from .colour_check import check_preview
from .preview_evaluation import outside_change


FOLDER = Path("exchange/static/exchange/evidence/heldout-v1")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build_heldout(root):
    manifest = root / "assets/heldout_v1.json"
    expected = (root / "assets/heldout_v1.sha256").read_text().split()[0]
    if digest(manifest.read_bytes()) != expected:
        raise ValueError("The registered held-out manifest changed.")
    plan = json.loads(manifest.read_text())
    inputs = {"assets/heldout_v1.json": expected}
    for name, sha in plan["input_sha256"].items():
        if name.startswith("private_media/"):
            continue  # The exact normalized bytes that went to the provider are public and checked below.
        if digest((root / name).read_bytes()) != sha:
            raise ValueError("Frozen input or measurement code changed: " + name)
        inputs[name] = sha
    observations = root / FOLDER / "observations.json"
    data = json.loads(observations.read_text())
    if data["manifest_sha256"] != expected:
        raise ValueError("Observations do not name the frozen manifest.")
    frozen = {case["id"]: case for case in plan["cases"]}
    if len(data["cases"]) != len(frozen) or {c["id"] for c in data["cases"]} != set(frozen):
        raise ValueError("Every frozen case must appear exactly once, including failures and unattempted cases.")
    cases = []
    for row in data["cases"]:
        declared = frozen[row["id"]]
        for key in ("pair", "model", "sku", "role", "reference_case_id"):
            if row[key] != declared[key]:
                raise ValueError("Case differs from the registered design: " + row["id"])
        if row["attempts"] > 1 or row["state"] not in ("succeeded", "failed", "uncertain", "not attempted"):
            raise ValueError("Only the first attempt and explicit terminal states belong in the report.")
        if row["attempts"] != (0 if row["state"] == "not attempted" else 1):
            raise ValueError("Attempt count does not match the reported state.")
        if row.get("review") is not None:
            raise ValueError("This release contains no independent rubric ratings; publish reviews separately.")
        model = next(m for m in plan["models"] if m["name"] == row["model"])
        person_path = root / FOLDER / row["person_file"]
        person = person_path.read_bytes()
        if digest(person) != row["person_sha256"] or row["person_sha256"] != model["normalized_sha256"]:
            raise ValueError("Public source photo differs from the registered provider input.")
        inputs[str(person_path.relative_to(root))] = digest(person)
        case = {**row, "source_page": model["source_page"], "result_image": None,
                "person_image": "exchange/evidence/heldout-v1/" + row["person_file"],
                "product_image": plan["catalog_snapshot"][row["sku"]]["image_asset_id"],
                "colour": None, "outside_change_pct": None}
        if row["state"] == "succeeded":
            path = root / FOLDER / row["result_file"]
            result = path.read_bytes()
            if digest(result) != row["result_sha256"]:
                raise ValueError("Output hash changed: " + row["id"])
            inputs[str(path.relative_to(root))] = digest(result)
            product = plan["catalog_snapshot"][row["sku"]]
            check = check_preview(person, result, product["reference_colour"], product["pattern_level"])
            case["colour"] = {"status": check.status, "delta": check.delta, "note": check.note}
            case["outside_change_pct"] = outside_change(person, result, [[.05, .2, .95, 1.0]], plan["thresholds"]["pixel_change"])
            case["result_image"] = "exchange/evidence/heldout-v1/" + row["result_file"]
        cases.append(case)
    units = sum(c.get("observed_units", 0) for c in cases)
    if units > data["approved_unit_cap"] or units != data["billing"]["units"]:
        raise ValueError("Billing and the approved unit cap disagree.")
    if data["billing"]["balance_before"] - data["billing"]["balance_after"] != units:
        raise ValueError("Provider balance change does not reconcile to this run.")
    alternatives = [c for c in cases if c["role"] == "alternative"]
    measured = [c for c in alternatives if c["colour"] and c["colour"]["delta"] is not None]
    summary = {"planned": len(cases), "source_photos": len(plan["models"]),
               **{s.replace(" ", "_"): sum(c["state"] == s for c in cases) for s in ("succeeded", "failed", "uncertain", "not attempted")},
               "plain_colour_planned": len(alternatives), "plain_colour_measured": len(measured),
               "plain_colour_close": sum(c["colour"]["status"] == "close" for c in measured),
               "plain_colour_different": sum(c["colour"]["status"] != "close" for c in measured),
               "plain_colour_missing": len(alternatives) - len(measured),
               "patterned_references": sum(c["role"] == "reference" for c in cases),
               "independent_human_reviews": 0, "requested_difference_planned": len(alternatives),
               "requested_difference_reviewed": 0, "units": units}
    inputs[str(observations.relative_to(root))] = digest(observations.read_bytes())
    inputs["exchange/heldout_evaluation.py"] = digest((root / "exchange/heldout_evaluation.py").read_bytes())
    return {"protocol": data["protocol"], "scope": plan["scope"], "registration_commit": data["registration_commit"],
            "manifest_sha256": expected, "frozen_at_utc": plan["frozen_at_utc"], "executed_at_utc": data["executed_at_utc"],
            "measurement_thresholds": plan["thresholds"], "summary": summary, "billing": data["billing"],
            "input_sha256": dict(sorted(inputs.items())), "cases": cases,
            "limits": data["limits"] + ["Colour closeness and successful task completion are separate observations, not overall fidelity or physical-fit scores.",
                "Outside-region changes use the original fixed boxes and threshold. A zero value is not evidence of identity, pose or background preservation.",
                "Human product-detail, requested-change and misleading-result judgments remain unassessed."]}
