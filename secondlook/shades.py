"""Shade exchange rules for beauty products: rank other shades by the change the shopper asked for.

The shopper says what was wrong with the purchased shade (too dark, too orange, too strong). Shades
are compared with the purchased shade in CIELAB: lightness L*, hue angle for warm or cool (towards
yellow and orange is warmer, towards red, pink and blue is cooler), and chroma for intensity. Nothing is read from the shopper's face; the direction comes only from them.
Every stated direction is a hard filter, never relaxed. Among shades that move the right way, the one
that changes least in ways the shopper did not ask for comes first, then the lower extra cost, then
SKU order. Standard library only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .colour import ciede2000, hex_to_rgb, rgb_to_lab
from .domain import (
    Candidate, DomainError, OrderLine, Policy, Variant, _base_check, _money, price_parts, same_category,
)

LIGHTNESS = ("lighter", "darker")
TONE = ("warmer", "cooler")
INTENSITY = ("softer", "bolder")
# The smallest change that counts as moving in a direction: L* units, hue degrees and chroma units.
# Twelve degrees keeps a darker brick red from passing as "less orange" than a terracotta.
MIN_STEP = {"lightness": 3.0, "tone": 12.0, "intensity": 4.0}
# Below this chroma a shade is nearly grey, so its hue says nothing about warmth.
MIN_CHROMA_FOR_HUE = 5.0
BEAUTY = {"lip", "foundation", "blush", "nail", "hair"}


@dataclass(frozen=True)
class ShadePreferences:
    max_top_up_minor: int
    lightness: str | None = None
    tone: str | None = None
    intensity: str | None = None
    currency: str = "USD"

    def validate(self) -> None:
        _money(self.max_top_up_minor, "max_top_up_minor")
        if self.currency != "USD":
            raise DomainError("CURRENCY_UNSUPPORTED", "Only USD is supported.")
        for value, allowed in ((self.lightness, LIGHTNESS), (self.tone, TONE), (self.intensity, INTENSITY)):
            if value is not None and value not in allowed:
                raise DomainError("INVALID_PREFERENCE", "Choose a supported shade change.")
        if not (self.lightness or self.tone or self.intensity):
            raise DomainError("SHADE_CHANGE_REQUIRED", "Say how the shade should change.")


def lab(hex_value: str) -> tuple[float, float, float]:
    return rgb_to_lab(hex_to_rgb(hex_value))


def shade_moves(purchased: str, other: str) -> dict:
    """How another shade differs from the purchased one: lightness, warmth and intensity."""
    l1, a1, b1 = lab(purchased)
    l2, a2, b2 = lab(other)
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    tone = 0.0
    if min(c1, c2) >= MIN_CHROMA_FOR_HUE:
        # Positive turns towards yellow (warmer), negative towards red, pink and blue (cooler).
        tone = (math.degrees(math.atan2(b2, a2)) - math.degrees(math.atan2(b1, a1)) + 180) % 360 - 180
    return {"lightness": l2 - l1, "tone": tone, "intensity": c2 - c1,
            "distance": ciede2000((l1, a1, b1), (l2, a2, b2))}


def _wrong_way(moves: dict, preferences: ShadePreferences) -> str | None:
    """The first stated direction this shade fails to move in, or None."""
    wanted = (("lightness", preferences.lightness, "lighter"), ("tone", preferences.tone, "warmer"),
              ("intensity", preferences.intensity, "bolder"))
    for axis, value, positive in wanted:
        if value is None:
            continue
        step = moves[axis] if value == positive else -moves[axis]
        if step < MIN_STEP[axis]:
            return axis
    return None


def unrequested_change(moves: dict, preferences: ShadePreferences) -> float:
    """How much a shade changes on the axes the shopper did not mention, in units of a noticeable step."""
    stated = {"lightness": preferences.lightness, "tone": preferences.tone, "intensity": preferences.intensity}
    return sum(abs(moves[axis]) / MIN_STEP[axis] for axis, value in stated.items() if value is None)


def _size_word(step: float) -> str:
    return "slightly " if step < 6 else ("much " if step > 14 else "")


def _reasons(moves: dict, preferences: ShadePreferences, variant: Variant, line: OrderLine, top_up: int) -> tuple:
    reasons = []
    if preferences.lightness:
        reasons.append(_size_word(abs(moves["lightness"])) + preferences.lightness)
    if preferences.tone:
        reasons.append(("slightly " if abs(moves["tone"]) < 20 else "") +
                       ("warmer, more golden" if preferences.tone == "warmer" else "cooler, less orange or yellow"))
    if preferences.intensity:
        reasons.append(_size_word(abs(moves["intensity"])) + preferences.intensity)
    if variant.style_code == line.style_code:
        reasons.append("same product line")
    reasons.append(f"USD {top_up / 100:.2f} extra")
    return tuple(reasons)


def _purchased_hex(line: OrderLine, variants: list[Variant]) -> str:
    if line.shade_hex:
        return line.shade_hex
    purchased = next((v for v in variants if v.sku == line.sku), None)
    if purchased is None or not purchased.shade_hex:
        raise DomainError("SHADE_UNKNOWN", "The purchased shade is not recorded, so shades cannot be compared.")
    return purchased.shade_hex


def _assess(line: OrderLine, policy: Policy, preferences: ShadePreferences, variants: list[Variant],
            purchased: str, now: datetime):
    """Yield (variant, code, moves, top_up): code is None for an eligible shade, else why it was left out."""
    for variant in variants:
        if variant.sku == line.sku or not same_category(line, variant):
            continue
        try:
            _, _, top_up = price_parts(line, variant, policy, now)
        except DomainError as exc:
            code = {"OUT_OF_STOCK": "out_of_stock", "STAFF_REVIEW": "staff_review",
                    "OPENED_SAME_LINE": "opened_same_line"}.get(exc.code, "outside_policy")
            yield variant, code, None, None
            continue
        if not variant.shade_hex:
            yield variant, "missing_details", None, top_up
            continue
        moves = shade_moves(purchased, variant.shade_hex)
        wrong = _wrong_way(moves, preferences)
        if wrong:
            yield variant, f"not_{wrong}", moves, top_up
        elif top_up > preferences.max_top_up_minor:
            yield variant, "over_budget", moves, top_up
        else:
            yield variant, None, moves, top_up


def shade_shortlist(line: OrderLine, policy: Policy, preferences: ShadePreferences,
                    variants: list[Variant], now: datetime | None = None, purchased_hex: str | None = None) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    preferences.validate()
    _base_check(line, policy, now)
    purchased = purchased_hex or _purchased_hex(line, variants)
    found = sorted(((unrequested_change(moves, preferences), top_up, moves["distance"], variant.sku, variant, moves, top_up)
                    for variant, code, moves, top_up in _assess(line, policy, preferences, variants, purchased, now)
                    if code is None), key=lambda item: item[:4])
    return [Candidate(variant, top_up, _reasons(moves, preferences, variant, line, top_up))
            for *_, variant, moves, top_up in found[:2]]


SHADE_EXCLUSION_TEXT = {
    "out_of_stock": "{n} out of stock",
    "opened_same_line": "{n} in other product lines, which an opened product cannot be exchanged for",
    "outside_policy": "{n} outside the exchange policy",
    "staff_review": "{n} cheaper than your credit, which needs staff review",
    "missing_details": "{n} without a recorded shade, so never guessed",
    "not_lightness": "{n} not {lightness} enough",
    "not_tone": "{n} not {tone} enough",
    "not_intensity": "{n} not {intensity} enough",
    "over_budget": "{n} over your extra-spend limit",
}


def shade_exclusions(line: OrderLine, policy: Policy, preferences: ShadePreferences,
                     variants: list[Variant], now: datetime | None = None, purchased_hex: str | None = None) -> list[dict]:
    """Why each other shade was left out, counted by the first rule it failed. Nothing is relaxed."""
    now = now or datetime.now(timezone.utc)
    preferences.validate()
    _base_check(line, policy, now)
    purchased = purchased_hex or _purchased_hex(line, variants)
    counts: dict[str, int] = {}
    for _, code, _, _ in _assess(line, policy, preferences, variants, purchased, now):
        if code:
            counts[code] = counts.get(code, 0) + 1
    words = {"lightness": preferences.lightness, "tone": preferences.tone, "intensity": preferences.intensity}
    return [{"code": code, "count": counts[code], "text": template.format(n=counts[code], **words)}
            for code, template in SHADE_EXCLUSION_TEXT.items() if code in counts]
