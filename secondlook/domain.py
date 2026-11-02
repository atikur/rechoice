"""Deterministic commerce rules for a fictional exchange assistant.

No model output or preview provider response is an authority for these facts.
All money uses integer USD cents. No order or inventory mutation occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class DomainError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PatternLevel(int, Enum):
    QUIET = 0
    MODERATE = 1
    BUSY = 2


@dataclass(frozen=True)
class Variant:
    retailer_id: str
    sku: str
    category: str
    size_label: str
    currency: str | None
    price_minor: int | None
    stock_qty: int
    pattern_level: PatternLevel | None
    color_tag: str | None
    sleeve: str | None
    neckline: str | None
    image_asset_id: str
    version: int
    # Variants of one garment share a style; sizes of the same style are not style alternatives.
    # For beauty, the style is the product line and each shade is its own variant.
    style: str | None = None
    shade_hex: str | None = None
    # Earrings: drop length in millimetres and style (stud or drop); the metal is the colour tag.
    length_mm: int | None = None
    form: str | None = None

    @property
    def style_code(self) -> str:
        return self.style or self.sku


@dataclass(frozen=True)
class OrderLine:
    retailer_id: str
    sku: str
    quantity: int
    paid_minor: int
    eligible_credit_minor: int | None
    currency: str
    simple_pricing: bool
    policy_version: int
    style: str | None = None
    size_label: str | None = None
    category: str | None = None
    # Opened beauty products can only be exchanged within their own product line.
    opened: bool = False
    # The purchased shade, for beauty products.
    shade_hex: str | None = None
    # The purchased earrings' length, metal and style.
    length_mm: int | None = None
    metal: str | None = None
    form: str | None = None

    @property
    def style_code(self) -> str:
        return self.style or self.sku


@dataclass(frozen=True)
class Policy:
    version: int
    allowed_category: str
    expires_at: datetime
    fee_minor: int | None
    exchange_allowed: bool
    # Categories this policy covers; empty means only allowed_category.
    categories: tuple[str, ...] = ()
    opened_same_line: bool = False

    def covers(self, category: str) -> bool:
        return category in (self.categories or (self.allowed_category,))


@dataclass(frozen=True)
class Preferences:
    max_top_up_minor: int
    currency: str = "USD"
    quieter_than: PatternLevel | None = None
    sleeve_required: str | None = None
    color_preferences: tuple[str, ...] = ()
    neckline_preference: str | None = None

    def validate(self) -> None:
        _money(self.max_top_up_minor, "max_top_up_minor")
        if self.currency != "USD":
            raise DomainError("CURRENCY_UNSUPPORTED", "Only USD is supported.")
        if self.sleeve_required not in (None, "long", "short", "sleeveless"):
            raise DomainError("INVALID_PREFERENCE", "Choose a supported sleeve preference.")


@dataclass(frozen=True)
class Candidate:
    variant: Variant
    top_up_minor: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SizeOption:
    variant: Variant
    available: bool
    top_up_minor: int | None
    note: str


@dataclass(frozen=True)
class Quote:
    sku: str
    currency: str
    credit_minor: int
    replacement_minor: int
    fee_minor: int
    top_up_minor: int
    variant_version: int
    policy_version: int
    expires_at: datetime
    # "style" swaps to a different garment, "size" to another size of the same garment, and "shade"
    # to another shade of a beauty product.
    kind: str = "style"


def _money(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise DomainError("INVALID_MONEY", f"{field} must be a nonnegative integer of cents.")
    return value


def _base_check(line: OrderLine, policy: Policy, now: datetime) -> None:
    if not policy.exchange_allowed or now >= policy.expires_at:
        raise DomainError("POLICY_EXPIRED", "This line is no longer eligible for the demo exchange path.")
    if line.quantity != 1 or not line.simple_pricing:
        raise DomainError("STAFF_REVIEW", "This pricing requires staff review.")
    if line.currency != "USD":
        raise DomainError("CURRENCY_UNSUPPORTED", "Only USD is supported.")
    if line.policy_version != policy.version:
        raise DomainError("POLICY_CHANGED", "The policy changed; review the order again.")
    _money(line.paid_minor, "paid_minor")
    _money(line.eligible_credit_minor, "eligible_credit_minor")
    if policy.fee_minor is None:
        raise DomainError("FEE_UNKNOWN", "A complete quote is unavailable until the fee is known.")
    _money(policy.fee_minor, "fee_minor")


def price_parts(line: OrderLine, variant: Variant, policy: Policy, now: datetime) -> tuple[int, int, int]:
    _base_check(line, policy, now)
    if variant.retailer_id != line.retailer_id:
        raise DomainError("WRONG_RETAILER", "This item is not available for this order.")
    if not policy.covers(variant.category) or (line.category and variant.category != line.category):
        raise DomainError("CATEGORY_EXCLUDED", "This item is outside the exchange policy.")
    if (line.opened and policy.opened_same_line and line.style
            and variant.style_code != line.style_code):
        raise DomainError("OPENED_SAME_LINE", "An opened beauty product can only be exchanged within its product line.")
    if variant.stock_qty < 1:
        raise DomainError("OUT_OF_STOCK", "This variant is unavailable.")
    if variant.currency != line.currency:
        raise DomainError("CURRENCY_MISMATCH", "A price in the order currency is required.")
    replacement = _money(variant.price_minor, "price_minor")
    credit = _money(line.eligible_credit_minor, "eligible_credit_minor")
    fee = _money(policy.fee_minor, "fee_minor")
    top_up = replacement + fee - credit
    if top_up < 0:
        raise DomainError("STAFF_REVIEW", "A cheaper replacement may leave credit; staff review is needed.")
    return credit, fee, top_up


def same_category(line: OrderLine, variant: Variant) -> bool:
    """Alternatives come from the purchased item's own category; other categories are not compared."""
    return not line.category or variant.category == line.category


def shortlist(line: OrderLine, policy: Policy, preferences: Preferences,
              variants: list[Variant], now: datetime | None = None) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    preferences.validate()
    _base_check(line, policy, now)
    selected = []
    for variant in variants:
        if variant.sku == line.sku or variant.style_code == line.style_code or not same_category(line, variant):
            continue
        try:
            _, _, top_up = price_parts(line, variant, policy, now)
        except DomainError:
            continue
        if variant.sleeve is None or variant.pattern_level is None:
            continue
        if preferences.sleeve_required and variant.sleeve != preferences.sleeve_required:
            continue
        if preferences.quieter_than is not None and variant.pattern_level >= preferences.quieter_than:
            continue
        if top_up > preferences.max_top_up_minor:
            continue
        reasons = [f"{variant.sleeve} sleeves", f"{variant.pattern_level.name.lower()} catalog pattern",
                   f"USD {top_up / 100:.2f} extra"]
        color_match = bool(preferences.color_preferences and variant.color_tag in preferences.color_preferences)
        neckline_match = bool(preferences.neckline_preference and variant.neckline == preferences.neckline_preference)
        if color_match:
            reasons.append(f"{variant.color_tag} catalog color")
        if neckline_match:
            reasons.append(f"{variant.neckline} neckline")
        selected.append((not color_match, not neckline_match, top_up, variant.sku,
                         Candidate(variant, top_up, tuple(reasons))))
    selected.sort(key=lambda item: item[:4])
    return [item[4] for item in selected[:2]]


def size_options(line: OrderLine, policy: Policy, variants: list[Variant],
                 now: datetime | None = None) -> list[SizeOption]:
    """Other sizes of the purchased garment, for a shopper whose item does not fit.

    No preview is involved: an image cannot show fit. Sizes that cannot be quoted stay listed as
    unavailable with the reason, so nothing is silently substituted.
    """
    now = now or datetime.now(timezone.utc)
    _base_check(line, policy, now)
    options = []
    for variant in variants:
        if (variant.style_code != line.style_code or variant.sku == line.sku
                or variant.size_label == line.size_label or not same_category(line, variant)):
            continue
        try:
            _, _, top_up = price_parts(line, variant, policy, now)
        except DomainError as exc:
            options.append(SizeOption(variant, False, None, "Out of stock" if exc.code == "OUT_OF_STOCK" else str(exc)))
            continue
        options.append(SizeOption(variant, True, top_up, "In stock"))
    return sorted(options, key=lambda option: (not option.available, option.variant.size_label, option.variant.sku))


def make_quote(line: OrderLine, variant: Variant, policy: Policy, preferences: Preferences | None,
               expires_at: datetime, now: datetime | None = None, kind: str = "style") -> Quote:
    now = now or datetime.now(timezone.utc)
    if kind == "size":
        if variant.style_code != line.style_code or variant.sku == line.sku or variant.size_label == line.size_label:
            raise DomainError("INELIGIBLE", "Only another size of the same item can replace it for fit.")
    elif kind == "shade":
        from .shades import ShadePreferences, shade_shortlist
        if not isinstance(preferences, ShadePreferences):
            raise DomainError("PREFERENCES_REQUIRED", "Say what is wrong with the shade first.")
        price_parts(line, variant, policy, now)  # stock, policy and price problems get their own codes
        if variant.sku == line.sku or not shade_shortlist(line, policy, preferences, [variant], now):
            raise DomainError("INELIGIBLE", "This shade no longer meets your confirmed preferences and policy.")
    elif kind == "jewellery":
        from .jewellery import JewelleryPreferences, jewellery_shortlist
        if not isinstance(preferences, JewelleryPreferences):
            raise DomainError("PREFERENCES_REQUIRED", "Say what is wrong with how they look first.")
        price_parts(line, variant, policy, now)
        if variant.sku == line.sku or not jewellery_shortlist(line, policy, preferences, [variant], now):
            raise DomainError("INELIGIBLE", "These no longer meet your confirmed preferences and policy.")
    elif kind == "style":
        if preferences is None:
            raise DomainError("PREFERENCES_REQUIRED", "Confirm your preferences first.")
        preferences.validate()
        if variant.sku == line.sku or not shortlist(line, policy, preferences, [variant], now):
            raise DomainError("INELIGIBLE", "This option no longer meets the confirmed preferences and policy.")
    else:
        raise DomainError("INVALID_INPUT", "Unknown exchange type.")
    credit, fee, top_up = price_parts(line, variant, policy, now)
    if expires_at <= now:
        raise DomainError("QUOTE_EXPIRED", "Request a fresh quote.")
    return Quote(variant.sku, line.currency, credit, variant.price_minor, fee, top_up,
                 variant.version, policy.version, expires_at, kind)


def revalidate_quote(quote: Quote, line: OrderLine, variant: Variant, policy: Policy,
                     preferences: Preferences | None, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if now >= quote.expires_at:
        raise DomainError("QUOTE_EXPIRED", "The quote expired; review a fresh one.")
    if variant.sku != quote.sku or variant.version != quote.variant_version or policy.version != quote.policy_version:
        raise DomainError("QUOTE_CHANGED", "The item or policy changed; review a fresh quote.")
    fresh = make_quote(line, variant, policy, preferences, quote.expires_at, now, quote.kind)
    if (fresh.credit_minor, fresh.replacement_minor, fresh.fee_minor, fresh.top_up_minor) != (
        quote.credit_minor, quote.replacement_minor, quote.fee_minor, quote.top_up_minor
    ):
        raise DomainError("QUOTE_CHANGED", "The price or credit changed; review a fresh quote.")


EXCLUSION_TEXT = {
    "out_of_stock": "{n} out of stock",
    "outside_policy": "{n} outside the exchange policy",
    "staff_review": "{n} cheaper than your credit, which needs staff review",
    "missing_details": "{n} missing catalog details, so never guessed",
    "sleeves": "{n} with different sleeves",
    "pattern": "{n} with a busier print",
    "over_budget": "{n} over your extra-spend limit",
}


def exclusions(line: OrderLine, policy: Policy, preferences: Preferences,
               variants: list[Variant], now: datetime | None = None) -> list[dict]:
    """Why each other top was left out, counted by the first rule it failed, in shortlist order.

    This explains a result; it never relaxes a rule. For tops left out only by budget it also
    reports the smallest extra cost, so the shopper can decide whether to change the limit.
    """
    now = now or datetime.now(timezone.utc)
    preferences.validate()
    _base_check(line, policy, now)
    counts: dict[str, int] = {}
    cheapest_over = None
    for variant in variants:
        if variant.sku == line.sku or variant.style_code == line.style_code or not same_category(line, variant):
            continue
        try:
            _, _, top_up = price_parts(line, variant, policy, now)
        except DomainError as exc:
            code = {"OUT_OF_STOCK": "out_of_stock", "STAFF_REVIEW": "staff_review"}.get(exc.code, "outside_policy")
        else:
            if variant.sleeve is None or variant.pattern_level is None:
                code = "missing_details"
            elif preferences.sleeve_required and variant.sleeve != preferences.sleeve_required:
                code = "sleeves"
            elif preferences.quieter_than is not None and variant.pattern_level >= preferences.quieter_than:
                code = "pattern"
            elif top_up > preferences.max_top_up_minor:
                code = "over_budget"
                cheapest_over = top_up if cheapest_over is None else min(cheapest_over, top_up)
            else:
                continue
        counts[code] = counts.get(code, 0) + 1
    result = []
    for code, template in EXCLUSION_TEXT.items():
        if code in counts:
            text = template.format(n=counts[code])
            if code == "over_budget":
                text += f" (the closest would be USD {cheapest_over / 100:.2f} extra)"
            result.append({"code": code, "count": counts[code], "text": text})
    return result
