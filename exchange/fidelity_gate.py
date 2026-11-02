"""Does a generated top preview show what the shopper asked for? Three local measurements, one verdict.

A try-on can draw a convincing image that still misses the request: the print is as busy as the top
being returned, the colour has shifted, or the model changed the person rather than the garment. The
gate measures those three things on every finished preview, with no model call and no network, so it
runs on recorded results, live renders and a judge's own photo alike. It compares images with each
other and with the catalog; it never reads the person.

- Colour: the existing CIEDE2000 check against the reviewed catalog colour (exchange/colour_check.py).
- Print: how busy the garment looks, compared with the top bought rendered on the same photo. Busyness is
  the mean edge strength inside the garment area, so a plain knit scores low and a dense floral high.
- Stability: how much of the image outside the garment band changed between the photo and the preview.
  It is the held-out study's fixed-box measurement, now applied to every preview.

The verdict gates the quote: a preview that fails is marked as not showing the request, and the shopper
must choose that option knowingly. Nothing here establishes physical fit, fabric, or exact colour.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter

VERSION = "gate-v1"
WORK_SIZE = 192
CHANGE_THRESHOLD = 40
TORSO_INSET = 0.3
MIN_GARMENT_PIXELS = 100
# An alternative counts as quieter when its garment edge strength is at most this share of the bought
# top's, on the same photo. Between the two bounds the prints read as alike, so the request is not met.
# On the 16 recorded development renders the plain tops scored 0.11 to 0.42 of the floral blouse and the
# two patterned tops 0.50 to 0.70, so the bound sits between them; the live study is the first unseen test.
QUIET_RATIO = 0.5
NOT_QUIETER_RATIO = 1.0
# The band that the garment may occupy, as fractions of width and height: everything outside it should
# stay as photographed. The box and the pixel threshold are the held-out study's frozen values.
GARMENT_BAND = (0.05, 0.2, 0.95, 1.0)
STABLE_PCT = 3.0


@dataclass(frozen=True)
class Measure:
    status: str           # see each function
    value: float | None
    note: str


def _small(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    scale = WORK_SIZE / max(image.size)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))


def _pair(person: bytes, result: bytes):
    before = Image.open(io.BytesIO(person)).convert("RGB")
    after = Image.open(io.BytesIO(result)).convert("RGB")
    if abs(before.width / before.height - after.width / after.height) > 0.02:
        return None, None
    after = _small(after)
    return before.resize(after.size), after


def garment_box(person: bytes, result: bytes) -> tuple[int, int, int, int] | None:
    """The middle of what changed between the photo and the preview: the torso, clear of hair and hands."""
    before, after = _pair(person, result)
    if before is None:
        return None
    mask = ImageChops.difference(before, after).convert("L").filter(ImageFilter.MedianFilter(5))
    mask = mask.point(lambda v: 255 if v > CHANGE_THRESHOLD else 0)
    box = mask.getbbox()
    if box is None:
        return None
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    torso = (x0 + int(w * TORSO_INSET), y0 + int(h * TORSO_INSET), x1 - int(w * TORSO_INSET), y1 - int(h * 0.1))
    if torso[2] <= torso[0] or torso[3] <= torso[1] or (torso[2] - torso[0]) * (torso[3] - torso[1]) < MIN_GARMENT_PIXELS:
        return None
    return torso


def busyness(person: bytes, result: bytes) -> float | None:
    """Mean edge strength inside the garment area of a preview, 0 to 255. None when no garment area is found."""
    box = garment_box(person, result)
    if box is None:
        return None
    _, after = _pair(person, result)
    edges = after.crop(box).convert("L").filter(ImageFilter.FIND_EDGES)
    values = list(edges.get_flattened_data())
    return round(sum(values) / len(values), 2) if values else None


def print_check(person: bytes | None, bought_result: bytes | None, result: bytes | None) -> Measure:
    """Is the alternative's print quieter than the bought top's, both drawn on the same photo?

    Status: quieter, similar, busier, or not_measured.
    """
    if not person or not bought_result or not result:
        return Measure("not_measured", None, "Not measured: the top you bought was not previewed on this photo.")
    try:
        bought, alternative = busyness(person, bought_result), busyness(person, result)
    except (OSError, ValueError, Image.DecompressionBombError):
        return Measure("not_measured", None, "Not measured: the images could not be compared.")
    if not bought or alternative is None:
        return Measure("not_measured", None, "Not measured: no garment area was found to compare.")
    ratio = round(alternative / bought, 2)
    if ratio <= QUIET_RATIO:
        return Measure("quieter", ratio, f"Print check: this preview looks quieter than the top you bought ({ratio:g}× its detail).")
    if ratio >= NOT_QUIETER_RATIO:
        return Measure("busier", ratio, f"Print check: this preview is not quieter than the top you bought ({ratio:g}× its detail).")
    return Measure("similar", ratio, f"Print check: this preview is about as busy as the top you bought ({ratio:g}× its detail).")


def stability_check(person: bytes | None, result: bytes | None) -> Measure:
    """How much changed outside the garment band. Status: stable, changed, or not_measured."""
    if not person or not result:
        return Measure("not_measured", None, "Not measured: no photo to compare with.")
    try:
        before = Image.open(io.BytesIO(person)).convert("RGB")
        after = _small(Image.open(io.BytesIO(result)))
    except (OSError, ValueError, Image.DecompressionBombError):
        return Measure("not_measured", None, "Not measured: the images could not be compared.")
    # Clothes V4 keeps the photo's shape; the rejected bag probe did not, because it redrew the whole scene.
    # A changed shape is measured anyway, stretched to fit, and reported as a change in itself.
    reshaped = abs(before.width / before.height - after.width / after.height) > 0.02
    before = before.resize(after.size)
    diff = ImageChops.difference(before, after).convert("L").filter(ImageFilter.MedianFilter(3))
    x0, y0, x1, y1 = GARMENT_BAND
    w, h = after.size
    changed = total = 0
    for y in range(h):
        for x in range(w):
            if x0 <= x / w <= x1 and y0 <= y / h <= y1:
                continue
            total += 1
            changed += diff.getpixel((x, y)) > CHANGE_THRESHOLD
    if not total:
        return Measure("not_measured", None, "Not measured: nothing lies outside the garment band.")
    pct = round(100 * changed / total, 2)
    if reshaped:
        return Measure("changed", pct, f"Stability check: the preview has a different shape from the photo and {pct:g}% "
                                       "of the image outside the garment band changed. More than the garment was redrawn.")
    if pct <= STABLE_PCT:
        return Measure("stable", pct, f"Stability check: {pct:g}% of the image outside the garment band changed.")
    return Measure("changed", pct, f"Stability check: {pct:g}% of the image outside the garment band changed. "
                                   "Look at the face, hair and background before trusting this preview.")


def verdict(colour_status: str, print_status: str, stability_status: str, *, quieter_asked: bool) -> Measure:
    """Combine the checks. Status: shows, check, or concern.

    concern: a measured check contradicts the request or the preview changed more than the garment.
    check: nothing contradicts the request, but at least one relevant check could not be measured.
    shows: every relevant check was measured and passed.
    """
    problems = []
    if colour_status == "differs":
        problems.append("the colour differs from the product photo")
    if quieter_asked and print_status in ("similar", "busier"):
        problems.append("the print is not quieter than the top you bought")
    if stability_status == "changed":
        problems.append("more than the garment changed")
    if problems:
        return Measure("concern", None, "This preview does not show what you asked for: " + "; ".join(problems) + ".")
    unmeasured = []
    if colour_status not in ("close", "pattern"):
        unmeasured.append("colour")
    if quieter_asked and print_status != "quieter":
        unmeasured.append("print")
    if stability_status != "stable":
        unmeasured.append("stability")
    if unmeasured:
        return Measure("check", None, "Check this preview yourself: " + ", ".join(unmeasured) + " could not be measured.")
    return Measure("shows", None, "Measured checks agree with your request: colour, print and stability.")
