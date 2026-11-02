"""Measure a proposed reference colour for every catalog photo, for the owner to review.

Writes assets/reference_colours.json with status "proposed" and a swatch sheet for review in
private_media/reviews/. A colour becomes a reviewed catalog fact only when the owner sets its
status to "reviewed", optionally correcting the hex value. Rerunning keeps reviewed entries.

Run: DATABASE_URL=sqlite:///local.sqlite3 .venv/bin/python scripts/measure_reference_colours.py
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from PIL import Image, ImageDraw  # noqa: E402

from exchange.colour_check import catalog_colour, hex_to_rgb, rgb_to_hex  # noqa: E402

CATALOG = ROOT / "exchange/static/exchange/catalog"
OUT = ROOT / "assets/reference_colours.json"
SHEET = ROOT / "private_media/reviews/reference-colours.jpg"


def main():
    existing = json.loads(OUT.read_text()) if OUT.exists() else {"colours": {}}
    colours = existing["colours"]
    for photo in sorted(CATALOG.glob("*.jpg")):
        if colours.get(photo.stem, {}).get("status") == "reviewed":
            continue
        colours[photo.stem] = {"hex": rgb_to_hex(catalog_colour(photo.read_bytes())), "status": "proposed",
                               "measured_on": date.today().isoformat()}
    OUT.write_text(json.dumps({
        "about": "Garment reference colours for the preview colour check. 'proposed' values were measured "
                 "from the catalog photo by scripts/measure_reference_colours.py; 'reviewed' values were "
                 "checked by the owner.",
        "colours": dict(sorted(colours.items()))}, indent=2) + "\n")
    tiles = []
    for sku, entry in sorted(colours.items()):
        tile = Image.new("RGB", (220, 300), "white")
        photo = Image.open(CATALOG / f"{sku}.jpg").convert("RGB")
        photo.thumbnail((200, 200))
        tile.paste(photo, (10, 10))
        draw = ImageDraw.Draw(tile)
        draw.rectangle((10, 215, 210, 255), fill=hex_to_rgb(entry["hex"]))
        draw.text((10, 262), f"{sku} {entry['hex']} {entry['status']}", fill="black")
        tiles.append(tile)
    columns = 5
    sheet = Image.new("RGB", (220 * columns, 300 * -(-len(tiles) // columns)), "white")
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (220 * (i % columns), 300 * (i // columns)))
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(SHEET, quality=90)
    print(f"Wrote {OUT.relative_to(ROOT)} and {SHEET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
