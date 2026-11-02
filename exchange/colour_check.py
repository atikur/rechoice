"""Check a generated preview's garment colour against the catalog's reference colour.

A try-on model can shift a garment's colour. This compares the garment area of a preview with the
reviewed reference colour of the catalog item and says whether they look alike. It is a check on
the generated image, not proof of the product's colour: the catalog photo stays the authority.

The garment area is found by comparing the result with the person photo it was made from, because
the provider keeps the person and changes the clothing. Patterned garments have no single colour,
so they get a pattern note instead of a colour verdict.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter

from secondlook.colour import ciede2000, hex_to_rgb, rgb_to_hex, rgb_to_lab  # noqa: F401

# CIEDE2000 distance. On the seven plain garments in the recorded pilot, six previews scored 1.5 to 5.5
# and the one that is visibly lighter than its catalog photo (SHORT-03) scored 14.8.
CLOSE = 10.0
WORK_SIZE = 192
CHANGE_THRESHOLD = 40
# Keep the middle of the changed area, the torso, so hair, hands and edges do not decide the colour.
TORSO_INSET = 0.3
MIN_GARMENT_PIXELS = 100


@dataclass(frozen=True)
class ColourCheck:
    status: str          # close, differs, pattern or unchecked
    delta: float | None  # CIEDE2000 distance between preview and reference
    note: str


def _small(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    scale = WORK_SIZE / max(image.size)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))


def dominant_colour(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int] | None:
    """The centre of the largest colour cluster, so seams, shadows and stray skin do not drag it."""
    if not pixels:
        return None
    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    quantized = strip.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    count, index = max(quantized.getcolors())
    return tuple(palette[index * 3:index * 3 + 3])


def catalog_colour(photo: bytes) -> tuple[int, int, int] | None:
    """The garment's dominant colour in a catalog photo, ignoring a plain background."""
    image = _small(Image.open(io.BytesIO(photo)))
    w, h = image.size
    border = [image.getpixel((x, y)) for x in range(w) for y in (0, 1, h - 2, h - 1)]
    border += [image.getpixel((x, y)) for y in range(h) for x in (0, 1, w - 2, w - 1)]
    strip = Image.new("RGB", (len(border), 1))
    strip.putdata(border)
    q = strip.quantize(colors=3, method=Image.Quantize.MEDIANCUT)
    palette = q.getpalette()
    background = [rgb_to_lab(palette[i * 3:i * 3 + 3]) for n, i in q.getcolors() if n > len(border) * 0.1]
    garment = [p for p in image.get_flattened_data() if all(ciede2000(rgb_to_lab(p), bg) > 12 for bg in background)]
    return dominant_colour(garment)


def preview_colour(person: bytes, result: bytes) -> tuple[tuple[int, int, int] | None, int]:
    """The dominant colour in the middle of what changed between the person photo and the preview."""
    before = Image.open(io.BytesIO(person)).convert("RGB")
    after = Image.open(io.BytesIO(result)).convert("RGB")
    if abs(before.width / before.height - after.width / after.height) > 0.02:
        return None, 0
    after = _small(after)
    before = before.resize(after.size)
    mask = ImageChops.difference(before, after).convert("L").filter(ImageFilter.MedianFilter(5))
    mask = mask.point(lambda v: 255 if v > CHANGE_THRESHOLD else 0)
    box = mask.getbbox()
    if box is None:
        return None, 0
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    torso = (x0 + int(w * TORSO_INSET), y0 + int(h * TORSO_INSET), x1 - int(w * TORSO_INSET), y1 - int(h * 0.1))
    if torso[2] <= torso[0] or torso[3] <= torso[1]:
        return None, 0
    changed = [p for p, m in zip(after.crop(torso).get_flattened_data(), mask.crop(torso).get_flattened_data()) if m]
    return dominant_colour(changed), len(changed)


# Shade checks for makeup and nails are not calibrated until the beauty pilot returns real results,
# so until then staff see them and shoppers do not.
SHADE_CLOSE = 15.0
SHADE_CHECK_CALIBRATED = False
SHADE_CHANGE_THRESHOLD = 18
MIN_SHADE_PIXELS = 30


def shade_colour(person: bytes, result: bytes) -> tuple[int, int, int] | None:
    """The dominant colour that changed between the person photo and a makeup or nail preview, or None."""
    before = Image.open(io.BytesIO(person)).convert("RGB")
    after = Image.open(io.BytesIO(result)).convert("RGB")
    if abs(before.width / before.height - after.width / after.height) > 0.02:
        return None
    after = _small(after)
    before = before.resize(after.size)
    mask = ImageChops.difference(before, after).convert("L").filter(ImageFilter.MedianFilter(3))
    changed = [p for p, m in zip(after.get_flattened_data(), mask.get_flattened_data()) if m > SHADE_CHANGE_THRESHOLD]
    return dominant_colour(changed) if len(changed) >= MIN_SHADE_PIXELS else None


def check_shade(person: bytes | None, result: bytes | None, shade_hex: str) -> ColourCheck:
    """Compare the colour that changed between the person photo and a makeup or nail preview with the shade."""
    if not person or not result or not shade_hex:
        return ColourCheck("unchecked", None, "")
    try:
        colour = shade_colour(person, result)
    except (OSError, ValueError, Image.DecompressionBombError):
        return ColourCheck("unchecked", None, "")
    if colour is None:
        return ColourCheck("unchecked", None, "")
    preview_lab, shade_lab = rgb_to_lab(colour), rgb_to_lab(hex_to_rgb(shade_hex))
    delta = round(ciede2000(preview_lab, shade_lab), 1)
    if delta <= SHADE_CLOSE:
        return ColourCheck("shade-close", delta, "Shade check: the applied colour is close to the shade.")
    return ColourCheck("shade-differs", delta, f"Shade check: the applied colour looks {_direction(preview_lab, shade_lab)} "
                                               "than the shade. Trust the swatch.")


def _direction(preview_lab, reference_lab) -> str:
    dl = preview_lab[0] - reference_lab[0]
    chroma = math.hypot(*preview_lab[1:]) - math.hypot(*reference_lab[1:])
    if abs(dl) >= 8 and abs(dl) >= abs(chroma):
        return "darker" if dl < 0 else "lighter"
    if abs(chroma) >= 8:
        return "duller" if chroma < 0 else "brighter"
    return "a different shade"


def check_preview(person: bytes | None, result: bytes | None, reference_hex: str, pattern_level: int | None) -> ColourCheck:
    if pattern_level is not None and pattern_level > 0:
        return ColourCheck("pattern", None, "Patterned top: the preview may simplify the pattern. Check the product photo.")
    if not person or not result or not reference_hex:
        return ColourCheck("unchecked", None, "")
    try:
        colour, pixels = preview_colour(person, result)
    except (OSError, ValueError, Image.DecompressionBombError):
        return ColourCheck("unchecked", None, "")
    if colour is None or pixels < MIN_GARMENT_PIXELS:
        return ColourCheck("unchecked", None, "")
    preview_lab, reference_lab = rgb_to_lab(colour), rgb_to_lab(hex_to_rgb(reference_hex))
    delta = round(ciede2000(preview_lab, reference_lab), 1)
    if delta <= CLOSE:
        return ColourCheck("close", delta, "Colour check: close to the product photo.")
    return ColourCheck("differs", delta, f"Colour check: this preview looks {_direction(preview_lab, reference_lab)} "
                                         "than the product. Trust the product photo for colour.")
