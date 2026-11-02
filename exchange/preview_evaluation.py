"""Reproduce a retrospective evaluation from committed pilot images. No database, network or AI calls."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageChops, ImageFilter

from .colour_check import check_preview, check_shade
from .size_check import check_size, rendered_height, resize_earrings, resize_factor

RUBRIC = {
    "product_detail": ["recognizable", "partly_changed", "misleading", "not_assessed"],
    "requested_difference": ["visible", "not_visible", "not_applicable", "not_assessed"],
    "unrelated_parts": ["preserved", "changed", "not_assessed"],
    "misleading_result": ["yes", "no", "not_assessed"],
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def outside_change(person, result, regions, threshold):
    """Exploratory pixel-change rate outside fixed target boxes, not an identity or preservation verdict."""
    before = Image.open(io.BytesIO(person)).convert("RGB")
    after = Image.open(io.BytesIO(result)).convert("RGB")
    if abs(before.width / before.height - after.width / after.height) > 0.02:
        return None
    after.thumbnail((256, 256))
    before = before.resize(after.size)
    diff = ImageChops.difference(before, after).convert("L").filter(ImageFilter.MedianFilter(3))
    pixels = diff.load()
    changed = count = 0
    for y in range(after.height):
        for x in range(after.width):
            if any(x0 <= x / after.width <= x1 and y0 <= y / after.height <= y1 for x0, y0, x1, y1 in regions):
                continue
            count += 1
            changed += pixels[x, y] > threshold
    return round(100 * changed / count, 2) if count else None


def load_cases(root, protocol):
    static = root / "exchange/static"
    cases, inputs = [], {}

    def load(path):
        data = path.read_bytes()
        inputs[str(path.relative_to(root))] = digest(data)
        return data

    titles = {}
    for group in ("catalog", "jewellery", "accessories"):
        manifest = json.loads(load(static / "exchange" / group / "manifest.json"))
        titles.update({row["sku"]: row["title"] for row in manifest["images"]})
    for source in protocol["sources"]:
        if source["split"] not in ("development", "held_out"):
            raise ValueError("Every source needs an explicit development or held_out split.")
        path = static / "exchange/evidence" / (source["name"] + ".json")
        data = json.loads(load(path))
        for row in data["results"]:
            category = source.get("category") or row.get("shade", {}).get("category", "beauty")
            product = row.get("earring", row.get("product", {}))
            key = row.get("pair", row.get("look"))
            case = {"id": f"{source['name']}-{key:02d}", "source": source["name"],
                    "split": source["split"], "category": category,
                    "sku": row.get("sku", "+".join(row.get("skus", []))), "model": row["model"],
                    "person_image": f"exchange/models/{row['model']}.jpg",
                    "result_image": "exchange/evidence/" + row["result_file"],
                    "recorded_on": data["recorded_on"], "provider_version": row.get("provider_version", data["provider_version"]),
                    "title": titles.get(row.get("sku")) or row.get("shade", {}).get("name") or f"Look {key}",
                    "product_image": row.get("product_image", "exchange/catalog/" + row.get("sku", "") + ".jpg")
                                     if category in ("tops", "earrings", "necklace", "shoes") else None,
                    "purpose": row.get("tests", ""), "prior_review": row.get("review"), "product": product,
                    "shade": row.get("shade"), "finding": None}
            cases.append(case)
            if category == "earrings":
                case["size_reference_sku"] = source["reference_sku"]
        if data.get("bag_probe"):
            row = data["bag_probe"]
            cases.append({"id": "accessories-bag-probe", "source": source["name"], "split": source["split"], "category": "bag",
                          "sku": row["sku"], "title": row["title"], "model": row["model"],
                          "person_image": f"exchange/models/{row['model']}.jpg",
                          "result_image": "exchange/evidence/" + row["result_file"],
                          "product_image": "exchange/accessories/" + row["sku"] + ".jpg",
                          "recorded_on": data["recorded_on"], "provider_version": row["provider_version"],
                          "purpose": "Bag probe: test whether only the bag changes.",
                          "prior_review": None, "product": {}, "shade": None, "finding": row["finding"]})
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Evaluation case IDs must be unique.")
    for case in cases:
        case["person_bytes"] = load(static / case["person_image"])
        case["result_bytes"] = load(static / case["result_image"])
        if case["product_image"]:
            load(static / case["product_image"])
    references = json.loads(load(root / "assets/reference_colours.json"))["colours"]
    return cases, inputs, references


def review_template(cases):
    return {"reviewer": "", "reviewer_kind": "human", "blinded": False,
            "ratings": {case["id"]: {key: None for key in RUBRIC} for case in cases}}


def validate_reviews(reviews, case_ids):
    if reviews is None:
        return {}
    if (not isinstance(reviews, dict) or not isinstance(reviews.get("reviewer"), str)
            or not reviews["reviewer"].strip() or reviews.get("reviewer_kind") not in ("human", "ai")):
        raise ValueError("Reviews need a named reviewer and reviewer_kind human or ai.")
    if not isinstance(reviews.get("blinded"), bool) or not isinstance(reviews.get("ratings"), dict):
        raise ValueError("Reviews need a blinded boolean and a ratings object.")
    ratings = reviews["ratings"]
    if set(ratings) - case_ids:
        raise ValueError("Review contains unknown case IDs.")
    for case_id, rating in ratings.items():
        if not isinstance(rating, dict) or set(rating) != set(RUBRIC):
            raise ValueError(f"{case_id}: every rubric field is required.")
        for key, value in rating.items():
            if value is not None and value not in RUBRIC[key]:
                raise ValueError(f"{case_id}: invalid {key} rating.")
    return ratings


def build_evaluation(root, reviews=None):
    protocol_path = root / "assets/preview_evaluation_protocol.json"
    protocol = read_json(protocol_path)
    cases, inputs, references = load_cases(root, protocol)
    ratings = validate_reviews(reviews, {c["id"] for c in cases})
    thresholds = protocol["thresholds"]
    from .colour_check import CLOSE
    from .size_check import IN_PROPORTION
    if thresholds["garment_delta"] != CLOSE or thresholds["relative_size_tolerance"] != IN_PROPORTION:
        raise ValueError("Image-check thresholds changed. Version the protocol before evaluating again.")
    inputs[str(protocol_path.relative_to(root))] = digest(protocol_path.read_bytes())
    for name in ("preview_evaluation.py", "colour_check.py", "size_check.py"):
        path = root / "exchange" / name
        inputs[str(path.relative_to(root))] = digest(path.read_bytes())
    from importlib.metadata import version
    dependencies = {name: version(name) for name in ("pillow",)}
    baselines = {}
    for case in cases:
        if case["category"] == "earrings":
            case["height"] = rendered_height(case["person_bytes"], case["result_bytes"])
            if case["sku"] == case["size_reference_sku"]:
                baselines[(case["split"], case["source"], case["model"])] = case.copy()
    corrections = {}
    for case in cases:
        checks = []
        person, result = case["person_bytes"], case["result_bytes"]
        category = case["category"]
        if category == "tops":
            reference = references.get(case["sku"], {})
            patterned = case["sku"] in protocol["patterned_skus"]
            check = check_preview(person, result, reference.get("hex", ""), 2 if patterned else 0)
            checks.append({"kind": "garment_colour", "status": check.status, "delta": check.delta,
                           "reference_hex": reference.get("hex"), "note": check.note})
        elif category == "earrings":
            key = (case["split"], case["source"], case["model"])
            if key not in baselines:
                raise ValueError(f"{case['id']}: missing size reference in the same source and split.")
            base = baselines[key]
            item = SimpleNamespace(pk=case["sku"], length_mm=case["product"]["length_mm"])
            bought = SimpleNamespace(pk=base["sku"], length_mm=base["product"]["length_mm"])
            check = check_size(item, case["height"], bought, base["height"])
            measured = {"kind": "relative_size", "status": check.status, "note": check.note,
                        "rendered_px": case["height"], "corrected_status": None}
            if check.status == "size-differs":
                factor = resize_factor(item, case["height"], bought, base["height"])
                corrected = resize_earrings(person, result, factor) if factor else None
                if corrected:
                    height = rendered_height(person, corrected)
                    measured.update(corrected_status=check_size(item, height, bought, base["height"]).status,
                                    corrected_px=height)
                    if measured["corrected_status"] == "size-close":
                        name = f"exchange/evidence/evaluation-{case['id']}-resized.jpg"
                        corrections[name] = corrected
                        case["corrected_image"] = name
                        measured["corrected_sha256"] = digest(corrected)
            checks.append(measured)
        elif case["shade"]:
            check = check_shade(person, result, case["shade"]["hex"])
            checks.append({"kind": "applied_shade", "status": "uncalibrated", "delta": check.delta,
                           "reference_hex": case["shade"]["hex"],
                           "note": "Exploratory colour reading only; no calibrated match or requested-change verdict."})
        else:
            checks.append({"kind": "image_fidelity", "status": "not_measured",
                           "note": "No validated automated product-detail check for this category."})
        regions = protocol["target_regions"][category]
        case["outside_change_pct"] = outside_change(person, result, regions, thresholds["pixel_change"])
        case["checks"] = checks
        case["review"] = ratings.get(case["id"])
        # Raw bytes and measurement helpers never enter the public report.
        for name in ("person_bytes", "result_bytes", "height", "product", "shade"):
            case.pop(name, None)
    split_metrics = {split: summarize([case for case in cases if case["split"] == split])
                     for split in ("development", "held_out")}
    summary = {**split_metrics["development"], "metrics_split": "development", "outcomes": len(cases),
               "development": sum(c["split"] == "development" for c in cases),
               "held_out": sum(c["split"] == "held_out" for c in cases)}
    report = {"protocol_version": protocol["version"], "protocol_frozen_on": protocol["frozen_on"],
              "scope": "Retrospective development evidence from published pilot images, not a held-out accuracy benchmark.",
              "summary": summary, "split_metrics": split_metrics,
              "thresholds": thresholds, "target_regions": protocol["target_regions"],
              "rubric": RUBRIC, "reviewer": {k: reviews[k] for k in ("reviewer", "reviewer_kind", "blinded")} if reviews else None,
              "dependencies": dependencies, "input_sha256": dict(sorted(inputs.items())), "cases": cases,
              "limits": protocol["limits"]}
    return report, corrections


def summarize(cases):
    """A cohort gets its own denominators; held-out outcomes are never pooled with development."""
    all_checks = [check for case in cases for check in case["checks"]]
    colour = [c for c in all_checks if c["kind"] == "garment_colour" and c["delta"] is not None]
    size = [c for c in all_checks if c["kind"] == "relative_size" and c["status"] in ("size-close", "size-differs")]
    return {
        "outcomes": len(cases),
        "plain_colour_measured": len(colour), "plain_colour_close": sum(c["status"] == "close" for c in colour),
        "patterned_not_scored": sum(c["status"] == "pattern" for c in all_checks),
        "relative_size_compared": len(size), "relative_size_close": sum(c["status"] == "size-close" for c in size),
        "size_warnings": sum(c["status"] == "size-differs" for c in size),
        "size_corrections_passed": sum(c.get("corrected_status") == "size-close" for c in size),
        "uncalibrated_shades": sum(c["kind"] == "applied_shade" for c in all_checks),
        "prior_ai_reviews": sum(bool(c["prior_review"]) for c in cases),
        "new_complete_reviews": sum(bool(c["review"]) and all(value not in (None, "not_assessed")
                                    for value in c["review"].values()) for c in cases),
        "requested_difference_reviewed": sum(bool(c["review"]) and c["review"]["requested_difference"]
                                             in ("visible", "not_visible") for c in cases),
    }
