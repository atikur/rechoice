"""Measure how accurate the published pilot results are, for the accuracy section on /evidence.

Reads the private pilot reports (the full-size results the preview jobs produced) and the published
evidence, and writes exchange/static/exchange/evidence/accuracy.json:

- Earrings: the length each earring renders at on Face A, measured from the pixels that changed at
  the ears, against the catalog length. The photo has no ruler, so lengths are compared relative to
  the pair bought (58 mm); nothing is measured or inferred about the person. An earring out of
  proportion is resized as the app resizes it, and measured again the same way.
- Beauty shades: the colour each preview applied, measured from the pixels that changed, beside the
  requested shade, as a CIEDE2000 difference. The live shade check ran on YouCam's original file, while
  the app stores results re-encoded as JPEG; the live reading is kept beside the re-measured one, and
  the gap between them shows the measurement's own noise.
- Clothes: the garment colour each preview shows beside the catalog reference, measured the same way.

Only results already published on /evidence are measured.

Run: DATABASE_URL=sqlite:///local.sqlite3 .venv/bin/python scripts/measure_pilot_accuracy.py
"""
import io
import json
import os
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from PIL import Image, ImageChops, ImageFilter  # noqa: E402

from exchange.assets import normalize_photo  # noqa: E402
from exchange.size_check import resize_earrings  # noqa: E402
from exchange.colour_check import (  # noqa: E402
    CLOSE, ciede2000, hex_to_rgb, preview_colour, rgb_to_hex, rgb_to_lab, shade_colour,
)

EVIDENCE = ROOT / "exchange/static/exchange/evidence"
MODELS = ROOT / "exchange/static/exchange/models"
REPORTS = ROOT / "private_media/feasibility/reports"
PRIVATE_MODELS = ROOT / "private_media/feasibility"
# Where Face A's ears are in the 1280 px photo, so only changes at the ears are measured.
EAR_BOXES = ((100, 380, 420, 1000), (860, 380, 1180, 1000))
EAR_CHANGE = 40
# A rendered length within this share of the catalog proportion counts as in proportion.
IN_PROPORTION = 0.15
SMALL_SHADE_DELTA = 5.0


def as_sent(path: Path) -> bytes:
    """The demo person exactly as a preview job received it: re-encoded by the photo step."""
    return normalize_photo(SimpleUploadedFile(path.name, path.read_bytes(), content_type="image/jpeg"))


def difference(colour, reference_hex):
    return round(ciede2000(rgb_to_lab(colour), rgb_to_lab(hex_to_rgb(reference_hex))), 1)


def report_rows(label):
    return {row["pair"]: row for row in json.loads((REPORTS / label / "report.json").read_text())["pairs"]}


def earring_length(person: Image.Image, result: Image.Image) -> float:
    """Mean height in pixels of the changed area at each ear: the earring as rendered."""
    diff = ImageChops.difference(person, result).convert("L").filter(ImageFilter.MedianFilter(5))
    heights = []
    for box in EAR_BOXES:
        bounds = diff.crop(box).point(lambda v: 255 if v > EAR_CHANGE else 0).getbbox()
        if bounds:
            heights.append(bounds[3] - bounds[1])
    if not heights:
        raise SystemExit("No earring change found at either ear; nothing written.")
    return statistics.mean(heights)


def earrings():
    published = json.loads((EVIDENCE / "jewellery.json").read_text())["results"]
    rows = report_rows("jewellery-pilot")
    person = Image.open(MODELS / "face-a.jpg").convert("RGB")
    measured = []
    for result in published:
        image = Image.open(REPORTS / "jewellery-pilot" / rows[result["pair"]]["result_file"]).convert("RGB")
        measured.append((result, image, earring_length(person, image)))
    reference, _, reference_px = measured[0]
    base_mm = reference["earring"]["length_mm"]
    out = []
    for result, image, px in measured:
        expected = result["earring"]["length_mm"] / base_mm
        rendered = px / reference_px
        off = rendered / expected - 1
        if result is reference:
            status = "reference"
        else:
            status = "in proportion" if abs(off) <= IN_PROPORTION else ("longer" if off > 0 else "shorter")
        row = {"sku": result["sku"], "title": result["earring"]["title"], "form": result["earring"]["form"],
               "length_mm": result["earring"]["length_mm"], "rendered_px": round(px),
               "expected_share": round(expected, 2), "rendered_share": round(rendered, 2),
               "difference_pct": round(off * 100), "status": status}
        if status in ("longer", "shorter"):
            resized = Image.open(io.BytesIO(resize_earrings(jpeg(person), jpeg(image), expected / rendered)))
            share = earring_length(person, resized.convert("RGB")) / reference_px
            row.update({"resized_share": round(share, 2), "resized_pct": round((share / expected - 1) * 100)})
        out.append(row)
    return out


def jpeg(image):
    data = io.BytesIO()
    image.save(data, format="JPEG", quality=95)
    return data.getvalue()


def shades():
    published = json.loads((EVIDENCE / "beauty.json").read_text())["results"]
    rows = report_rows("beauty-pilot")
    out = []
    for result in published:
        person = as_sent(MODELS / f"{result['model']}.jpg")
        image = (REPORTS / "beauty-pilot" / rows[result["pair"]]["result_file"]).read_bytes()
        colour = shade_colour(person, image)
        out.append({"pair": result["pair"], "sku": result["sku"], "shade": result["shade"]["name"],
                    "category": result["shade"]["category"], "requested_hex": result["shade"]["hex"],
                    "measured_hex": rgb_to_hex(colour) if colour else None,
                    "delta": difference(colour, result["shade"]["hex"]) if colour else None,
                    "live_delta": result["shade_check"]["delta"]})
    return out


def clothes():
    published = json.loads((EVIDENCE / "pilot.json").read_text())["results"]
    rows = report_rows("pilot")
    references = json.loads((ROOT / "assets/reference_colours.json").read_text())["colours"]
    out = []
    for result in published:
        check = result["colour_check"]
        row = {"pair": result["pair"], "sku": result["sku"], "model": result["model"],
               "reference_hex": references[result["sku"]]["hex"], "status": check["status"],
               "delta": check["delta"], "measured_hex": None}
        if check["status"] != "pattern":
            person = (PRIVATE_MODELS / f"{result['model']}.jpg").read_bytes()
            image = (REPORTS / "pilot" / rows[result["pair"]]["result_file"]).read_bytes()
            colour, _ = preview_colour(person, image)
            row["measured_hex"] = rgb_to_hex(colour) if colour else None
            row["delta"] = difference(colour, row["reference_hex"]) if colour else None
        out.append(row)
    return out


def main():
    ear, shade, cloth = earrings(), shades(), clothes()
    readable = [s["delta"] for s in shade if s["delta"] is not None]
    gaps = [abs(s["delta"] - s["live_delta"]) for s in shade if s["delta"] is not None and s["live_delta"] is not None]
    plain = [c["delta"] for c in cloth if c["status"] != "pattern" and c["delta"] is not None]
    summary = {
        "earrings": {"compared": sum(e["status"] != "reference" for e in ear),
                     "in_proportion": sum(e["status"] == "in proportion" for e in ear),
                     "in_proportion_resized": sum(e["status"] == "in proportion"
                                                  or abs(e.get("resized_pct", 100)) <= IN_PROPORTION * 100 for e in ear
                                                  if e["status"] != "reference")},
        "shades": {"measured": len(shade), "readable": len(readable),
                   "small": sum(d <= SMALL_SHADE_DELTA for d in readable),
                   "range": [min(readable), max(readable)] if readable else None,
                   "largest_gap_to_live": round(max(gaps), 1) if gaps else None},
        "clothes": {"measured": len(cloth), "plain": len(plain), "close": sum(d <= CLOSE for d in plain),
                    "patterned": sum(c["status"] == "pattern" for c in cloth),
                    "range": [min(plain), max(plain)] if plain else None},
    }
    (EVIDENCE / "accuracy.json").write_text(json.dumps({
        "measured_on": date.today().isoformat(), "built_by": "scripts/measure_pilot_accuracy.py",
        "thresholds": {"in_proportion_pct": round(IN_PROPORTION * 100), "small_shade_delta": SMALL_SHADE_DELTA,
                       "close_garment_delta": CLOSE},
        "summary": summary, "earrings": ear, "shades": shade, "clothes": cloth}, indent=2) + "\n")
    print(json.dumps(summary))
    for e in ear:
        print(f"{e['title']}: {e['length_mm']} mm, expected {e['expected_share']}, rendered {e['rendered_share']} "
              f"({e['difference_pct']:+d}%) {e['status']}"
              + (f", resized {e['resized_share']} ({e['resized_pct']:+d}%)" if "resized_share" in e else ""))


if __name__ == "__main__":
    main()
