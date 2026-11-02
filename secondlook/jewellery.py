"""Jewellery and accessory exchange rules: rank other earrings, necklaces, shoes or bags by the change asked for.

The shopper says what was wrong with the pair they bought: too big or too small, or the wrong metal.
Size is the catalog's drop length in millimetres and metal is a catalog fact; nothing is read from the
shopper's face or ears. Every stated change is a hard filter, never relaxed. Among earrings that make
it, the pair that changes least in ways the shopper did not ask for comes first: another style or
metal counts as one step, and length in steps of a quarter of the purchased length. Then the lower
extra cost, then SKU order. Standard library only.

Shoes and bags follow the same rules: their size is the heel height or the width, from the catalog, and in
place of a metal they have a colour family, neutral or bright. Nothing is read from the shopper's feet or body.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .domain import Candidate, DomainError, OrderLine, Policy, Variant, _base_check, _money, price_parts, same_category

SIZES = ("smaller", "larger")
METALS = ("gold", "silver")
COLOURS = ("neutral", "bright")
# A size change counts once the length moves by at least a quarter of the purchased length.
MIN_LENGTH_CHANGE = 0.25
JEWELLERY = {"earrings", "necklace"}
ACCESSORIES = {"shoes", "bag"}


@dataclass(frozen=True)
class JewelleryPreferences:
    max_top_up_minor: int
    size: str | None = None
    metal: str | None = None
    currency: str = "USD"

    def validate(self) -> None:
        _money(self.max_top_up_minor, "max_top_up_minor")
        if self.currency != "USD":
            raise DomainError("CURRENCY_UNSUPPORTED", "Only USD is supported.")
        if self.size not in (None, *SIZES) or self.metal not in (None, *METALS, *COLOURS):
            raise DomainError("INVALID_PREFERENCE", "Choose a supported change.")
        if not (self.size or self.metal):
            raise DomainError("CHANGE_REQUIRED", "Say what should change: the size or the metal.")


def _length_change(line: OrderLine, variant: Variant) -> float:
    return variant.length_mm / line.length_mm - 1


def _assess(line: OrderLine, policy: Policy, preferences: JewelleryPreferences, variants: list[Variant],
            now: datetime):
    """Yield (variant, code, length change, top_up): code is None for an eligible pair, else why it was left out."""
    if not (line.length_mm and line.metal and line.form):
        raise DomainError("ITEM_DETAILS_UNKNOWN", "The purchased pair's size and metal are not recorded.")
    for variant in variants:
        if variant.sku == line.sku or not same_category(line, variant):
            continue
        try:
            _, _, top_up = price_parts(line, variant, policy, now)
        except DomainError as exc:
            code = {"OUT_OF_STOCK": "out_of_stock", "STAFF_REVIEW": "staff_review"}.get(exc.code, "outside_policy")
            yield variant, code, None, None
            continue
        if not (variant.length_mm and variant.color_tag and variant.form):
            yield variant, "missing_details", None, top_up
            continue
        change = _length_change(line, variant)
        if ((preferences.size == "smaller" and change > -MIN_LENGTH_CHANGE)
                or (preferences.size == "larger" and change < MIN_LENGTH_CHANGE)):
            yield variant, "not_size", change, top_up
        elif preferences.metal and variant.color_tag != preferences.metal:
            yield variant, "not_metal", change, top_up
        elif top_up > preferences.max_top_up_minor:
            yield variant, "over_budget", change, top_up
        else:
            yield variant, None, change, top_up


def unrequested_change(line: OrderLine, variant: Variant, preferences: JewelleryPreferences, change: float) -> float:
    """How much a pair changes that the shopper did not ask for, in steps."""
    steps = 0.0 if variant.form == line.form else 1.0
    if preferences.metal is None and variant.color_tag != line.metal:
        steps += 1
    if preferences.size is None:
        steps += abs(change) / MIN_LENGTH_CHANGE
    return steps


def _a(noun: str) -> str:
    return f"{'an' if noun[:1] in 'aeiou' else 'a'} {noun}"


def size_words(category: str | None, size: str) -> str:
    """How a size change reads for this kind of item: a lower heel for shoes, smaller for a bag or earrings."""
    if category == "shoes":
        return {"smaller": "lower heel", "larger": "higher heel"}[size]
    return size


def _reasons(line: OrderLine, variant: Variant, preferences: JewelleryPreferences, top_up: int) -> tuple:
    reasons = []
    if preferences.size:
        unit = " wide" if line.category == "bag" else ""
        what = f"heel {variant.length_mm} mm instead of {line.length_mm} mm" if line.category == "shoes" else (
            f"{variant.length_mm} mm{unit} instead of {line.length_mm} mm")
        reasons.append(f"{size_words(line.category, preferences.size)}: {what}")
    if preferences.metal:
        reasons.append(f"{variant.color_tag} colour" if variant.color_tag in COLOURS else variant.color_tag)
    reasons.append(f"same style, {variant.form}" if variant.form == line.form
                   else f"{_a(variant.form)} instead of {_a(line.form)}")
    reasons.append(f"USD {top_up / 100:.2f} extra")
    return tuple(reasons)


def jewellery_shortlist(line: OrderLine, policy: Policy, preferences: JewelleryPreferences,
                        variants: list[Variant], now: datetime | None = None) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    preferences.validate()
    _base_check(line, policy, now)
    found = sorted(((unrequested_change(line, variant, preferences, change), top_up, variant.sku, variant, top_up)
                    for variant, code, change, top_up in _assess(line, policy, preferences, variants, now)
                    if code is None), key=lambda item: item[:3])
    return [Candidate(variant, top_up, _reasons(line, variant, preferences, top_up)) for *_, variant, top_up in found[:2]]


JEWELLERY_EXCLUSION_TEXT = {
    "out_of_stock": "{n} out of stock",
    "outside_policy": "{n} outside the exchange policy",
    "staff_review": "{n} cheaper than your credit, which needs staff review",
    "missing_details": "{n} without a recorded size or metal, so never guessed",
    "not_size": "{n} not clearly {size}",
    "not_metal": "{n} not in {metal}",
    "over_budget": "{n} over your extra-spend limit",
}


def jewellery_exclusions(line: OrderLine, policy: Policy, preferences: JewelleryPreferences,
                         variants: list[Variant], now: datetime | None = None) -> list[dict]:
    """Why each other pair was left out, counted by the first rule it failed. Nothing is relaxed."""
    now = now or datetime.now(timezone.utc)
    preferences.validate()
    _base_check(line, policy, now)
    counts: dict[str, int] = {}
    for _, code, _, _ in _assess(line, policy, preferences, variants, now):
        if code:
            counts[code] = counts.get(code, 0) + 1
    words = {"size": size_words(line.category, preferences.size) if preferences.size else None,
             "metal": f"a {preferences.metal} colour" if preferences.metal in COLOURS else preferences.metal}
    return [{"code": code, "count": counts[code], "text": template.format(n=counts[code], **words)}
            for code, template in JEWELLERY_EXCLUSION_TEXT.items() if code in counts]
