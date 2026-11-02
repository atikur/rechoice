"""Publish the recorded YouCam pilots as demo evidence: results, measurements and reviews.

Reads a private feasibility report and writes exchange/static/exchange/evidence/: pilot.json for the
clothes pilot, beauty.json for the beauty pilot or jewellery.json for the earrings pilot, and each
result scaled to 800 px, except earrings, which keep their full size for the size check. The pilots
used AI-generated Pixabay people, so no real person appears. Reviewer ratings are copied only when
the owner has recorded them in the report; until then the page says they are pending. Provider task
IDs are not published.

The beauty pilot publishes only the three comparisons the owner chose on 27 October 2026: the
purchased shade and the two suggested shades for the lipstick and foundation on Face A and the nail
polish on Hand A. The other four results stay private.

The earrings pilot publishes the comparison the owner chose on 28 October 2026: the pair bought and the
two silver suggestions on Face A. The gold-stud result stays private.

The looks pilot publishes its three looks to looks.json, each with both steps: the top on Model A, then
the lipstick added to that result. Replay files each step under the items added so far.

Run: DATABASE_URL=sqlite:///local.sqlite3 .venv/bin/python scripts/publish_pilot_evidence.py [--set beauty|jewellery|looks]
"""
import argparse
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from PIL import Image  # noqa: E402

from exchange.colour_check import check_preview  # noqa: E402

PILOT = ROOT / "private_media/feasibility/reports/pilot"
BEAUTY_PILOT = ROOT / "private_media/feasibility/reports/beauty-pilot"
JEWELLERY_PILOT = ROOT / "private_media/feasibility/reports/jewellery-pilot"
NECKLACE_PILOT = ROOT / "private_media/feasibility/reports/necklace-pilot"
SHOES_PILOT = ROOT / "private_media/feasibility/reports/shoes-pilot"
BAGS_PILOT = ROOT / "private_media/feasibility/reports/bags-pilot"
HAIR_PILOT = ROOT / "private_media/feasibility/reports/hair-pilot"
LOOKS_PILOT = ROOT / "private_media/feasibility/reports/looks-pilot"
MODELS = ROOT / "private_media/feasibility"
OUT = ROOT / "exchange/static/exchange/evidence"
REFERENCES = json.loads((ROOT / "assets/reference_colours.json").read_text())["colours"]
PATTERNED = {"ORIGINAL", "BOLD-01", "BOLD-02"}
OWNER_DECISION = "Passed the gate on 20 October 2026: the owner judged the pilot useful enough to continue."
# The comparisons the shopper page makes for "too orange": the purchased shade, then the two suggestions.
BEAUTY_COMPARISONS = [
    {"title": "Lipstick on Face A", "model": "face-a", "category": "lip", "pairs": [1, 2, 3]},
    {"title": "Foundation on Face A", "model": "face-a", "category": "foundation", "pairs": [7, 8, 9]},
    {"title": "Nail polish on Hand A", "model": "hand-a", "category": "nail", "pairs": [11, 12, 13]},
]
BEAUTY_DECISION = ("On 27 October 2026 the owner chose to publish the lipstick, foundation and nail comparisons "
                   "as recorded results. Per-result ratings are not yet recorded.")
BEAUTY_PROBES = ("The first lipstick and nail probes were refused with InvalidParameters before any task existed, "
                 "and were not charged. The adapter was fixed to send the fields YouCam's API definitions require, "
                 "and both were run again.")
BEAUTY_READING = {
    "by": "Claude (AI assistant), 27 October 2026, before the owner's ratings",
    "notes": [
        "Lipstick: each shade sits on the lips only and keeps the lip outline; the three shades are clearly distinct.",
        "Nail polish: every nail is painted with a gloss highlight, and the hand is otherwise unchanged.",
        "Foundation: the colour stays on the skin and moves the right way, cooler for 210C and darker for 310C, "
        "but the three results are only 3 to 5 \u0394E apart on the cheek, so the difference is subtle.",
        "Makeup VTO brightens the whole image by about 5 L*, background included; showing the purchased shade "
        "on the same face cancels this out.",
    ],
}


def main():
    report = json.loads((PILOT / "report.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    pairs = []
    for pair in report["pairs"]:
        result = (PILOT / pair["result_file"]).read_bytes()
        check = check_preview((MODELS / pair["inputs"]["model_file"]).read_bytes(), result,
                              REFERENCES[pair["sku"]]["hex"], 2 if pair["sku"] in PATTERNED else 0)
        image = Image.open(io.BytesIO(result)).convert("RGB")
        image.thumbnail((800, 800))
        name = f"pilot-{pair['pair']:02d}-{pair['model']}-{pair['sku']}.jpg"
        image.save(OUT / name, quality=85)
        review = pair.get("review") or {}
        rated = review.get("usefulness") is not None
        pairs.append({
            "pair": pair["pair"], "model": pair["model"], "sku": pair["sku"], "tests": pair["tests"],
            "model_source": pair["inputs"]["model_source"], "state": pair["state"],
            "elapsed_seconds": round(pair["elapsed_seconds"], 1), "observed_units": pair["observed_units"],
            "provider_version": pair["provider_version"], "result_file": name,
            "colour_check": {"status": check.status, "delta": check.delta, "note": check.note},
            "review": review if rated else None,
        })
    summary = report["summary"]
    (OUT / "pilot.json").write_text(json.dumps({
        "recorded_on": summary["generated_at"][:10], "provider": summary["provider"],
        "provider_version": summary["provider_version"], "pairs": len(pairs),
        "succeeded": summary["succeeded"], "failed": summary["failed"], "uncertain": summary["uncertain"],
        "units": summary["units"], "gate": summary["gate"], "owner_decision": OWNER_DECISION,
        "reviews_recorded": sum(1 for p in pairs if p["review"]),
        "rated_usable": sum(1 for p in pairs if p["review"] and p["review"]["usefulness"] == "usable"),
        "reviewers": sorted({p["review"].get("reviewer", "owner") for p in pairs if p["review"]}),
        "results": pairs}, indent=2) + "\n")
    print(f"Published {len(pairs)} recorded results to {OUT.relative_to(ROOT)}")


# The comparison the shopper page makes for "too long, and I wanted silver": the pair bought, then two suggestions.
JEWELLERY_COMPARISONS = [{"title": "Earrings on Face A", "model": "face-a", "category": "earrings", "pairs": [1, 2, 3]}]
JEWELLERY_DECISION = ("On 28 October 2026 the owner chose to publish the earrings comparison as recorded results. "
                      "Per-result ratings are not yet recorded.")
JEWELLERY_READING = {
    "by": "Claude (AI assistant), 28 October 2026, before the owner's ratings",
    "notes": [
        "Each earring keeps the design and metal of its product photo.",
        "Each hangs from the earlobe on both ears, from a photo of a single earring, and replaces Face A's own studs.",
        "Size does not always follow the catalog length: measured against the 58 mm pearl drop, the 12 mm stud "
        "renders in proportion, but the 38 mm crystal drop renders about as long as the pearl drop. The previews "
        "show style and metal; the page says their size is approximate.",
        "The face is otherwise unchanged.",
    ],
}


def publish_jewellery():
    report = json.loads((JEWELLERY_PILOT / "report.json").read_text())
    rows = {row["pair"]: row for row in report["pairs"]}
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for comparison in JEWELLERY_COMPARISONS:
        for n in comparison["pairs"]:
            row = rows[n]
            if row["state"] != "succeeded" or row["model"] != comparison["model"]:
                raise SystemExit(f"Earring pair {n} is not a succeeded {comparison['model']} result; nothing published.")
            # Earrings are published at full size: an 800 px copy loses the pearl's fine chain, so replay
            # would measure, and resize, against a shorter pair bought than YouCam drew.
            image = Image.open(JEWELLERY_PILOT / row["result_file"]).convert("RGB")
            name = f"earrings-{n:02d}-{row['model']}-{row['sku']}.jpg"
            image.save(OUT / name, quality=90)
            review = row.get("review") or {}
            earring = {k: row["inputs"]["earring"][k] for k in ("sku", "title", "metal", "form", "length_mm", "feature")}
            results.append({
                "pair": n, "model": row["model"], "sku": row["sku"], "tests": row["tests"], "earring": earring,
                "product_image": row["inputs"]["earring"]["image_file"].replace("exchange/static/", ""),
                "model_source": row["inputs"]["model_source"], "state": row["state"],
                "elapsed_seconds": round(row["elapsed_seconds"], 1), "observed_units": row["observed_units"],
                "provider_version": row["provider_version"], "result_file": name,
                "review": review if review.get("usefulness") is not None else None})
    summary = report["summary"]
    (OUT / "jewellery.json").write_text(json.dumps({
        "recorded_on": summary["generated_at"][:10], "provider": summary["provider"],
        "provider_version": summary["provider_version"],
        "pilot": {"pairs": summary["pairs"], "succeeded": summary["succeeded"], "failed": summary["failed"],
                  "uncertain": summary["uncertain"], "units": summary["units"]},
        "gate": summary["gate"], "owner_decision": JEWELLERY_DECISION, "reading": JEWELLERY_READING,
        "comparisons": JEWELLERY_COMPARISONS, "results": results}, indent=2, ensure_ascii=False) + "\n")
    print(f"Published {len(results)} recorded earring results to {OUT.relative_to(ROOT)}")


LOOKS_DECISION = ("On 28 October 2026 the owner made looks the top and lipstick, after Earrings VTO refused the "
                  "probe's photo, and chose to publish the three looks as recorded results. Later that day the owner "
                  "approved adding the necklace from the order to each look, 1 unit each, and publishing them. "
                  "Per-look ratings are not yet recorded.")
LOOKS_PROBE = ("The first probe was planned with the earrings as a third step. Earrings VTO refused the chest-up photo: "
               "\"[PHOTO_CHECK_INVALID] Ear size should fit in range and position should be in the center.\" YouCam "
               "refunded that charge, and look 1 is that probe's first two steps. The necklace was added to each look "
               "afterwards, on its lipstick result, with look 2's crew neck as the probe; the top and lipstick were "
               "not made again.")
LOOKS_READING = {
    "by": "Claude (AI assistant), 28 October 2026, before the owner's ratings",
    "notes": [
        "Each top keeps its design: the turtleneck's ribs, the sage knit's heathered texture and the blouse's busy "
        "floral print.",
        "The 230 Terracotta lipstick sits on the lips alone, added to the result with the top.",
        "Model A stays recognisably the same person across the three looks and all three steps.",
        "The Amethyst Y Chain hangs at the neck on each top, keeping its design and metal, with the top, the lipstick "
        "and the face unchanged by the third step.",
        "On the turtleneck the chain sits below the rolled collar and hangs a little lower than on the crew neck, "
        "as it would be worn. On the floral blouse it is hard to pick out against the print.",
        "Look 1 has a small pink smudge on the jeans near the hem, made by Clothes V4 in its step.",
        "The shade check could not read the lips: on a chest-up photo they cover too few pixels at the check's "
        "working size.",
    ],
}


def publish_looks():
    from exchange.models import ProductVariant
    from exchange.replay import replay_key
    report = json.loads((LOOKS_PILOT / "report.json").read_text())
    variants = {v.sku: v for v in ProductVariant.objects.filter(retailer_id="secondlook-demo")}
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for row in report["pairs"]:
        if row["state"] != "succeeded" or row["model"] != "model-a":
            raise SystemExit(f"Look {row['pair']} is not a succeeded model-a look; nothing published.")
        steps = []
        for step in row["steps"]:
            image = Image.open(LOOKS_PILOT / step["result_file"]).convert("RGB")
            image.thumbnail((800, 800))
            name = f"look-{row['pair']:02d}-step{step['step']}-{row['model']}-{step['sku']}.jpg"
            image.save(OUT / name, quality=85)
            steps.append({"step": step["step"], "sku": step["sku"], "title": variants[step["sku"]].title,
                          "feature": step["feature"], "elapsed_seconds": round(step["elapsed_seconds"], 1),
                          "observed_units": step["observed_units"], "provider_version": step["provider_version"],
                          "result_file": name})
        review = row.get("review") or {}
        results.append({
            "look": row["pair"], "model": row["model"], "skus": row["skus"],
            "keys": [replay_key(variants[sku]) for sku in row["skus"]], "tests": row["tests"],
            "model_source": row["inputs"]["model_source"], "state": row["state"],
            "elapsed_seconds": round(row["elapsed_seconds"], 1), "observed_units": row["observed_units"],
            # Look 1's first steps came from the probe: taken whole at first, then continued with the necklace.
            "from_probe": bool(row.get("reused_from") or "+EAR-" in row.get("continued_from", "")), "steps": steps, "result_file": steps[-1]["result_file"],
            "review": review if review.get("usefulness") is not None else None})
    summary = report["summary"]
    (OUT / "looks.json").write_text(json.dumps({
        "recorded_on": summary["generated_at"][:10], "provider": summary["provider"],
        "provider_version": summary["provider_version"],
        "pilot": {"looks": summary["pairs"], "succeeded": summary["succeeded"], "failed": summary["failed"],
                  "uncertain": summary["uncertain"], "units": summary["units"], "probe": LOOKS_PROBE},
        "gate": summary["gate"], "owner_decision": LOOKS_DECISION, "reading": LOOKS_READING,
        "results": results}, indent=2, ensure_ascii=False) + "\n")
    print(f"Published {len(results)} recorded looks to {OUT.relative_to(ROOT)}")


# The comparison the shopper page makes for "too long" in either metal: the necklace bought, then the shorter
# silver one, plus the collar at a middling length. The gold pendant's result failed and stays private.
NECKLACE_COMPARISONS = [{"title": "Necklaces on Model A", "model": "model-a", "category": "necklace", "pairs": [1, 3, 4]}]
NECKLACE_DECISION = ("On 28 October 2026 the owner chose to publish the three necklaces that rendered properly as "
                     "recorded results. Per-result ratings are not yet recorded.")
NECKLACE_FAILURE = ("The Rose Gold Leaf Pendant's task returned success but pasted a beige block over the chest: the "
                    "engine's background removal failed on that product photo. The size check refused to read it, "
                    "because the change covered 24% of the photo, and the result stays private.")
NECKLACE_READING = {
    "by": "Claude (AI assistant), 28 October 2026, before the owner's ratings",
    "notes": [
        "Each necklace hangs at the neck over the tee, keeping its design and metal; the Y chain's pendant falls "
        "down the chest and the two collars sit at the collarbone.",
        "Measured against the 55 cm chain bought, the 40 cm bib and the 45 cm collar both render in proportion.",
        "Model A is otherwise unchanged.",
        NECKLACE_FAILURE,
    ],
}


def publish_necklace():
    report = json.loads((NECKLACE_PILOT / "report.json").read_text())
    rows = {row["pair"]: row for row in report["pairs"]}
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for comparison in NECKLACE_COMPARISONS:
        for n in comparison["pairs"]:
            row = rows[n]
            if row["state"] != "succeeded" or row["model"] != comparison["model"]:
                raise SystemExit(f"Necklace pair {n} is not a succeeded {comparison['model']} result; nothing published.")
            image = Image.open(NECKLACE_PILOT / row["result_file"]).convert("RGB")
            image.thumbnail((800, 800))
            name = f"necklace-{n:02d}-{row['model']}-{row['sku']}.jpg"
            image.save(OUT / name, quality=85)
            review = row.get("review") or {}
            item = {k: row["inputs"]["earring"][k] for k in ("sku", "title", "category", "metal", "form", "length_mm",
                                                              "feature")}
            check = row.get("size_check") or {}
            results.append({
                "pair": n, "model": row["model"], "sku": row["sku"], "tests": row["tests"], "earring": item,
                "product_image": row["inputs"]["earring"]["image_file"].replace("exchange/static/", ""),
                "model_source": row["inputs"]["model_source"], "state": row["state"],
                "elapsed_seconds": round(row["elapsed_seconds"], 1), "observed_units": row["observed_units"],
                "provider_version": row["provider_version"], "result_file": name,
                "size_check": {"status": check.get("status") or "unchecked", "rendered_px": check.get("rendered_px")},
                "review": review if review.get("usefulness") is not None else None})
    summary = report["summary"]
    (OUT / "necklace.json").write_text(json.dumps({
        "recorded_on": summary["generated_at"][:10], "provider": summary["provider"],
        "provider_version": summary["provider_version"],
        "pilot": {"pairs": summary["pairs"], "succeeded": summary["succeeded"], "failed": summary["failed"],
                  "uncertain": summary["uncertain"], "units": summary["units"], "failure": NECKLACE_FAILURE},
        "gate": summary["gate"], "owner_decision": NECKLACE_DECISION, "reading": NECKLACE_READING,
        "comparisons": NECKLACE_COMPARISONS, "results": results}, indent=2, ensure_ascii=False) + "\n")
    print(f"Published {len(results)} recorded necklace results to {OUT.relative_to(ROOT)}")


# The comparison the shopper page makes for "too brassy": the shade bought, then the two cooler shades. The very
# light blonde on dark hair stays private as a finding.
HAIR_COMPARISONS = [{"title": "Hair colour on Model A", "model": "model-a", "category": "hair", "pairs": [1, 2, 3]}]
HAIR_DECISION = ("On 28 October 2026 the owner chose to publish the hair colour comparison as recorded results. "
                 "Per-result ratings are not yet recorded.")
HAIR_FINDING = ("The fourth pair, 9.1 Very Light Ash Blonde on Model C's dark curly hair, barely changed it: the engine tints "
                "hair rather than lifting it, so a shade far lighter than the hair in the photo does not show. It stays "
                "private.")
HAIR_READING = {
    "by": "Claude (AI assistant), 28 October 2026, before the owner's ratings",
    "notes": [
        "Each shade colours the hair alone; the face, skin and tee are unchanged.",
        "7.3 Golden Blonde comes out a brighter, more saturated gold than its swatch; 7.1 Ash Blonde and 6.1 Dark Ash "
        "Blonde both come out as a cool grey-blonde and are close to each other, so the step between them is smaller "
        "on screen than in the catalog.",
        "The shade check is not calibrated for hair: it reads the dominant changed colour, and shaded strands dominate, "
        "so it called the ash blonde dark. Its raw ΔE is recorded here but not shown to shoppers.",
        HAIR_FINDING,
    ],
}


def publish_hair():
    report = json.loads((HAIR_PILOT / "report.json").read_text())
    rows = {row["pair"]: row for row in report["pairs"]}
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for comparison in HAIR_COMPARISONS:
        for n in comparison["pairs"]:
            row = rows[n]
            if row["state"] != "succeeded" or row["model"] != comparison["model"]:
                raise SystemExit(f"Hair pair {n} is not a succeeded {comparison['model']} result; nothing published.")
            image = Image.open(HAIR_PILOT / row["result_file"]).convert("RGB")
            image.thumbnail((800, 800))
            name = f"hair-{n:02d}-{row['model']}-{row['sku']}.jpg"
            image.save(OUT / name, quality=85)
            review = row.get("review") or {}
            check = row.get("shade_check") or {}
            results.append({
                "pair": n, "model": row["model"], "sku": row["sku"], "tests": row["tests"],
                "shade": row["inputs"]["shade"], "model_source": row["inputs"]["model_source"],
                "state": row["state"], "elapsed_seconds": round(row["elapsed_seconds"], 1),
                "observed_units": row["observed_units"], "provider_version": row["provider_version"],
                "result_file": name, "shade_check": {"status": "uncalibrated", "delta": check.get("delta")},
                "review": review if review.get("usefulness") is not None else None})
    summary = report["summary"]
    (OUT / "hair.json").write_text(json.dumps({
        "recorded_on": summary["generated_at"][:10], "provider": summary["provider"],
        "provider_version": summary["provider_version"],
        "pilot": {"pairs": summary["pairs"], "succeeded": summary["succeeded"], "failed": summary["failed"],
                  "uncertain": summary["uncertain"], "units": summary["units"], "finding": HAIR_FINDING},
        "gate": summary["gate"], "owner_decision": HAIR_DECISION, "reading": HAIR_READING,
        "comparisons": HAIR_COMPARISONS, "results": results}, indent=2, ensure_ascii=False) + "\n")
    print(f"Published {len(results)} recorded hair colour results to {OUT.relative_to(ROOT)}")


def publish_beauty():
    report = json.loads((BEAUTY_PILOT / "report.json").read_text())
    rows = {row["pair"]: row for row in report["pairs"]}
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for comparison in BEAUTY_COMPARISONS:
        for n in comparison["pairs"]:
            row = rows[n]
            if row["state"] != "succeeded" or row["model"] != comparison["model"]:
                raise SystemExit(f"Beauty pair {n} is not a succeeded {comparison['model']} result; nothing published.")
            image = Image.open(BEAUTY_PILOT / row["result_file"]).convert("RGB")
            image.thumbnail((800, 800))
            name = f"beauty-{n:02d}-{row['model']}-{row['sku']}.jpg"
            image.save(OUT / name, quality=85)
            review = row.get("review") or {}
            check = row.get("shade_check") or {}
            results.append({
                "pair": n, "model": row["model"], "sku": row["sku"], "tests": row["tests"],
                "shade": row["inputs"]["shade"], "model_source": row["inputs"]["model_source"],
                "state": row["state"], "elapsed_seconds": round(row["elapsed_seconds"], 1),
                "observed_units": row["observed_units"], "provider_version": row["provider_version"],
                "result_file": name, "shade_check": {"status": check.get("status") or "unchecked",
                                                     "delta": check.get("delta")},
                "review": review if review.get("usefulness") is not None else None})
    summary = report["summary"]
    (OUT / "beauty.json").write_text(json.dumps({
        "recorded_on": summary["generated_at"][:10], "provider": summary["provider"],
        "provider_version": summary["provider_version"],
        "pilot": {"pairs": summary["pairs"], "succeeded": summary["succeeded"], "failed": summary["failed"],
                  "uncertain": summary["uncertain"], "units": summary["units"], "probes": BEAUTY_PROBES},
        "gate": summary["gate"], "owner_decision": BEAUTY_DECISION, "reading": BEAUTY_READING,
        "comparisons": BEAUTY_COMPARISONS, "results": results}, indent=2, ensure_ascii=False) + "\n")
    print(f"Published {len(results)} recorded beauty results to {OUT.relative_to(ROOT)}")


# The shoe comparison: the pair bought, then the three alternatives, on the full-length demo model.
SHOE_COMPARISONS = [{"title": "Shoes on Model F", "model": "model-f", "category": "shoes", "pairs": [1, 2, 3, 4]}]
ACCESSORY_DECISION = ("On 28 October 2026 the owner approved up to 16 units, a probe first for shoes and for bags, and "
                      "their publication. The shoes passed and are published as recorded results; the bags probe failed "
                      "its gate, so the other bags did not run and bag try-on is off. Per-result ratings are not yet "
                      "recorded.")
BAG_FINDING = ("Bag V2 drew the red shoulder bag well, but it made a new picture around it: a gallery background, a green "
               "dress in place of the white tee and jeans, and a new pose. Its documentation describes preset styles, "
               "chosen at random by default, and a fixed output size. That cannot show a shopper's own bag on them, and "
               "each bag would come with a different outfit, so the comparison would not be fair. The other three bags "
               "did not run, and Rechoice compares bags by their catalog details only.")
ACCESSORY_READING = {
    "by": "Claude (AI assistant), 28 October 2026, before the owner's ratings",
    "notes": [
        "Each pair of shoes appears on Model F's feet below the trouser hem, keeping its design and colour: the red "
        "pointed boots, the cream peep-toes, the metallic platform brogues with their striped sole and the nude pumps.",
        "Model F, her trousers and the background are unchanged in all four.",
        "The wide trousers cover the ankles, so the boots' shafts and the heel heights are mostly hidden; the "
        "previews show style and colour, and the page says to check the heel height in the details.",
        BAG_FINDING,
    ],
}


def publish_accessories():
    report = json.loads((SHOES_PILOT / "report.json").read_text())
    rows = {row["pair"]: row for row in report["pairs"]}
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for comparison in SHOE_COMPARISONS:
        for n in comparison["pairs"]:
            row = rows[n]
            if row["state"] != "succeeded" or row["model"] != comparison["model"]:
                raise SystemExit(f"Shoe pair {n} is not a succeeded {comparison['model']} result; nothing published.")
            image = Image.open(SHOES_PILOT / row["result_file"]).convert("RGB")
            image.thumbnail((800, 800))
            name = f"shoes-{n:02d}-{row['model']}-{row['sku']}.jpg"
            image.save(OUT / name, quality=88)
            review = row.get("review") or {}
            product = row["inputs"]["earring"]
            results.append({
                "pair": n, "model": row["model"], "sku": row["sku"], "tests": row["tests"],
                "product": {k: product[k] for k in ("sku", "title", "category", "metal", "form", "length_mm", "feature")},
                "product_image": product["image_file"].replace("exchange/static/", ""),
                "model_source": row["inputs"]["model_source"], "state": row["state"],
                "elapsed_seconds": round(row["elapsed_seconds"], 1), "observed_units": row["observed_units"],
                "provider_version": row["provider_version"], "result_file": name,
                "review": review if review.get("usefulness") is not None else None})
    bags = json.loads((BAGS_PILOT / "report.json").read_text())
    probe = next(row for row in bags["pairs"] if row["pair"] == 1)
    image = Image.open(BAGS_PILOT / probe["result_file"]).convert("RGB")
    image.thumbnail((800, 800))
    probe_file = f"bag-probe-{probe['model']}-{probe['sku']}.jpg"
    image.save(OUT / probe_file, quality=85)
    summary, bag_summary = report["summary"], bags["summary"]
    (OUT / "accessories.json").write_text(json.dumps({
        "recorded_on": summary["generated_at"][:10], "provider": summary["provider"],
        "provider_version": summary["provider_version"],
        "pilot": {"pairs": summary["pairs"], "succeeded": summary["succeeded"], "failed": summary["failed"],
                  "uncertain": summary["uncertain"], "units": summary["units"]},
        "bag_probe": {"sku": probe["sku"], "title": probe["inputs"]["earring"]["title"], "model": probe["model"],
                      "provider_version": probe["provider_version"], "elapsed_seconds": round(probe["elapsed_seconds"], 1),
                      "observed_units": probe["observed_units"], "units": bag_summary["units"],
                      "result_file": probe_file, "finding": BAG_FINDING},
        "gate": summary["gate"], "owner_decision": ACCESSORY_DECISION, "reading": ACCESSORY_READING,
        "comparisons": SHOE_COMPARISONS, "results": results}, indent=2, ensure_ascii=False) + "\n")
    print(f"Published {len(results)} recorded shoe results and the bag probe to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish a recorded pilot as demo evidence.")
    parser.add_argument("--set", dest="which", choices=("clothes", "beauty", "jewellery", "necklace", "hair", "looks",
                                                         "accessories"), default="clothes")
    {"beauty": publish_beauty, "jewellery": publish_jewellery, "necklace": publish_necklace, "hair": publish_hair,
     "looks": publish_looks, "accessories": publish_accessories}.get(
        parser.parse_args().which, main)()
