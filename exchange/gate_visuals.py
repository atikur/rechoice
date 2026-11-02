"""A picture of what the stability check compared, for the shopper's card. It shows a measurement; it is not one.

The stability check (exchange/fidelity_gate.py) counts the pixels that changed between the photo and the
preview outside a fixed garment band. This draws the same comparison, at the same working size, threshold
and band, over a pale copy of the preview: changes inside the band in blue, where the garment is expected to
change, and changes outside it in orange, where the face, hair and edges should stay as photographed.

Kept apart from fidelity_gate.py, whose source the registered gate studies pin by hash.
"""
from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .fidelity_gate import CHANGE_THRESHOLD, GARMENT_BAND, WORK_SIZE

# Blue and orange stay distinct for the common kinds of colour blindness; the card's legend names both.
INSIDE = (31, 111, 178)
OUTSIDE = (221, 107, 32)
DISPLAY_SIZE = 640


def change_overlay(person: bytes, result: bytes, size: int = DISPLAY_SIZE) -> bytes:
    """A JPEG of the preview, paled, with what changed from the photo marked inside and outside the garment band."""
    before = Image.open(io.BytesIO(person)).convert("RGB")
    after = Image.open(io.BytesIO(result)).convert("RGB")
    # The stability check's own comparison: the preview at its working size, the photo stretched to match.
    scale = WORK_SIZE / max(after.size)
    small = after.resize((max(1, round(after.width * scale)), max(1, round(after.height * scale))))
    diff = ImageChops.difference(before.resize(small.size), small).convert("L").filter(ImageFilter.MedianFilter(3))
    changed = diff.point(lambda v: 255 if v > CHANGE_THRESHOLD else 0)

    fit = size / max(after.size)
    width, height = max(1, round(after.width * fit)), max(1, round(after.height * fit))
    pale = Image.blend(ImageOps.grayscale(after.resize((width, height))).convert("RGB"),
                       Image.new("RGB", (width, height), "white"), 0.45)
    changed = changed.resize((width, height), Image.Resampling.BILINEAR)
    x0, y0, x1, y1 = GARMENT_BAND
    box = (round(x0 * width), round(y0 * height), round(x1 * width) - 1, round(y1 * height) - 1)
    band = Image.new("L", (width, height), 0)
    ImageDraw.Draw(band).rectangle(box, fill=255)
    inside = ImageChops.multiply(changed, band)
    outside = ImageChops.multiply(changed, ImageOps.invert(band))
    out = Image.composite(Image.new("RGB", (width, height), INSIDE), pale, inside.point(lambda v: v * 6 // 10))
    out = Image.composite(Image.new("RGB", (width, height), OUTSIDE), out, outside.point(lambda v: v * 9 // 10))
    draw = ImageDraw.Draw(out)
    draw.rectangle(box, outline=(255, 255, 255), width=4)
    draw.rectangle(box, outline=INSIDE, width=2)
    buffer = io.BytesIO()
    out.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()
