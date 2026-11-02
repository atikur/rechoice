"""How big an earring rendered, and whether that fits its catalog length.

A photo has no ruler, so sizes are compared within one comparison: every earring is drawn on the same
photo, so the rendered heights should stand in the same ratio as the catalog lengths. The pair bought
is the yardstick. Nothing is measured about the person.

YouCam's earring engine sizes each earring by its own rule, whatever length it is told, so an earring
that renders out of proportion is resized here: cut from the result by its changed pixels, scaled from
where it hangs, and checked again. The shopper can still see it as YouCam drew it.
"""
import io
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# A pixel counts as changed above this difference, after a median filter takes out compression noise.
CHANGE_THRESHOLD = 40
# A change covering more of the photo than this is not an earring, so the height is not read.
MAX_CHANGED_SHARE = 0.2
MIN_CHANGED_PIXELS = 60
# Rendered within this share of the expected proportion counts as in proportion.
IN_PROPORTION = 0.15
# A resize outside this range would mean the render, not just its size, is wrong, so it is not attempted.
RESIZE_RANGE = (0.4, 2.5)
# Room around each earring's changed pixels, so its edge and the shadow it casts move with it.
RESIZE_MARGIN = 12


@dataclass(frozen=True)
class SizeCheck:
    status: str
    note: str


def earring_anchor(product: bytes) -> list[int] | None:
    """Where the hook meets the ear in the product photo: the top of the earring, at its centre."""
    try:
        image = Image.open(io.BytesIO(product)).convert("RGB")
        background = Image.new("RGB", image.size, image.getpixel((1, 1)))
        box = ImageChops.difference(image, background).convert("L").point(lambda v: 255 if v > 28 else 0).getbbox()
    except (OSError, ValueError):
        return None
    if not box:
        return None
    return [(box[0] + box[2]) // 2, box[1]]


def _changes(person: bytes | None, result: bytes | None):
    """The photo and the result at one size, the mask of what changed, and the factor back to the photo's pixels.

    A recorded result is a smaller copy of the photo, so the two are compared at the smaller size: enlarging
    the copy would blur every edge past the threshold.
    """
    if not person or not result:
        return None
    try:
        before = Image.open(io.BytesIO(person)).convert("RGB")
        after = Image.open(io.BytesIO(result)).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    scale = 1.0
    if after.size != before.size:
        if after.height < before.height:
            scale = before.height / after.height
            before = before.resize(after.size)
        else:
            after = after.resize(before.size)
    mask = ImageChops.difference(before, after).convert("L").filter(ImageFilter.MedianFilter(5)).point(
        lambda v: 255 if v > CHANGE_THRESHOLD else 0)
    changed = sum(1 for v in mask.get_flattened_data() if v)
    if changed < MIN_CHANGED_PIXELS or changed > MAX_CHANGED_SHARE * before.width * before.height:
        return None
    return before, after, mask, scale


def _ears(mask):
    """The box of what changed at each ear, in the mask's pixels, for the ears with enough change to read."""
    boxes = []
    for x0, x1 in ((0, mask.width // 2), (mask.width // 2, mask.width)):
        half = mask.crop((x0, 0, x1, mask.height))
        box = half.getbbox()
        if box and sum(1 for v in half.get_flattened_data() if v) >= MIN_CHANGED_PIXELS:
            boxes.append((box[0] + x0, box[1], box[2] + x0, box[3]))
    return boxes


def rendered_height(person: bytes | None, result: bytes | None) -> int | None:
    """The height in pixels of what changed at each ear, averaged over the ears that changed."""
    changes = _changes(person, result)
    if changes is None:
        return None
    _, _, mask, scale = changes
    heights = [box[3] - box[1] for box in _ears(mask)]
    return round(scale * sum(heights) / len(heights)) if heights else None


def resize_earrings(person: bytes | None, result: bytes | None, factor: float) -> bytes | None:
    """The result with each earring scaled by `factor` from where it hangs, on the photo as it was.

    Each ear's box goes back to the original photo first, so nothing of the old earring is left, then the
    earring cut from the result by its changed pixels is pasted back smaller or larger, its top centre kept
    at the lobe. Only the earrings move; the rest of the result is untouched. None when it cannot be done.
    """
    if not RESIZE_RANGE[0] <= factor <= RESIZE_RANGE[1]:
        return None
    changes = _changes(person, result)
    if changes is None:
        return None
    before, after, mask, _ = changes
    boxes = _ears(mask)
    if not boxes:
        return None
    out = after.copy()
    for box in boxes:
        region = (max(box[0] - RESIZE_MARGIN, 0), max(box[1] - RESIZE_MARGIN, 0),
                  min(box[2] + RESIZE_MARGIN, after.width), min(box[3] + RESIZE_MARGIN, after.height))
        width, height = region[2] - region[0], region[3] - region[1]
        # Faint edges against the background fall under the change threshold, so the whole box is cleared,
        # feathered at its border.
        clear = Image.new("L", (width, height), 0)
        ImageDraw.Draw(clear).rectangle((4, 4, width - 5, height - 5), fill=255)
        out.paste(before.crop(region), region[:2], clear.filter(ImageFilter.GaussianBlur(3)))
        # The earring itself: its changed pixels, grown a little and feathered so its edge blends.
        cut = mask.crop(region).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(3))
        size = (max(1, round(width * factor)), max(1, round(height * factor)))
        centre, top = (box[0] + box[2]) / 2, box[1]
        at = (round(centre - (centre - region[0]) * factor), round(top - (top - region[1]) * factor))
        out.paste(after.crop(region).resize(size, Image.LANCZOS), at, cut.resize(size, Image.LANCZOS))
    data = io.BytesIO()
    out.save(data, format="JPEG", quality=90)
    return data.getvalue()


def check_size(variant, rendered_px, bought, bought_px) -> SizeCheck:
    """Compare an earring's rendered height with the pair bought, rendered on the same photo."""
    if rendered_px is None:
        return SizeCheck("unchecked", "")
    if bought is None or bought_px is None or not bought.length_mm or not variant.length_mm:
        return SizeCheck("unchecked", "")
    if variant.pk == bought.pk:
        return SizeCheck("size-reference", "Size check: the pair bought, which the other sizes are measured against.")
    expected = variant.length_mm / bought.length_mm
    rendered = rendered_px / bought_px
    off = rendered / expected - 1
    if abs(off) <= IN_PROPORTION:
        return SizeCheck("size-close", "Size check: renders in proportion to the pair bought.")
    direction = "longer" if off > 0 else "shorter"
    return SizeCheck("size-differs", f"Size check: renders {abs(round(off * 100))}% {direction} than its length implies. "
                                     "Check the length in the product details.")


def resize_factor(variant, rendered_px, bought, bought_px) -> float | None:
    """How much to scale an earring so it stands to the pair bought as its catalog length does."""
    if not rendered_px or not bought_px or not bought or not bought.length_mm or not variant.length_mm:
        return None
    return (bought_px * variant.length_mm / bought.length_mm) / rendered_px


def resized_check(check: SizeCheck) -> SizeCheck:
    """The size check once a resized copy reads in proportion: what YouCam drew, and what was changed."""
    drawn = check.note.removeprefix("Size check: renders ").split(" than")[0]
    return SizeCheck("size-resized", f"Size check: YouCam drew it {drawn}; Rechoice resized it to its catalog "
                                     "length, in proportion to the pair bought.")
