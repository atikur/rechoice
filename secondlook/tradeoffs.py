"""Read-only, single-limit possibilities for an otherwise empty apparel shortlist.

The shopper names the one limit they are willing to revisit. Commerce eligibility and all other
preferences still use the normal shortlist. A possibility never confirms preferences or a quote.
"""
from dataclasses import dataclass, replace

from .domain import Candidate, DomainError, Preferences, price_parts, shortlist


@dataclass(frozen=True)
class Tradeoff:
    constraint: str
    before: object
    after: object
    candidates: tuple[Candidate, ...]


def explore(line, policy, preferences: Preferences, variants, constraint, now):
    if constraint not in ("budget", "sleeves", "print"):
        raise DomainError("INVALID_CONSTRAINT", "Choose budget, sleeves or print to explore.")
    # Includes policy, pricing and preference validation; an existing match needs no relaxation.
    if shortlist(line, policy, preferences, variants, now):
        return []
    if constraint == "budget":
        amounts = set()
        for variant in variants:
            try:
                _, _, top_up = price_parts(line, variant, policy, now)
            except DomainError:
                continue
            if preferences.max_top_up_minor < top_up <= 100000:
                amounts.add(top_up)
        for amount in sorted(amounts):
            found = shortlist(line, policy, replace(preferences, max_top_up_minor=amount), variants, now)
            if found:
                # First amount is the minimum feasible budget with every other limit unchanged.
                return [Tradeoff(constraint, preferences.max_top_up_minor, amount, tuple(found))]
        return []
    if constraint == "sleeves":
        if not preferences.sleeve_required:
            return []
        result = []
        for sleeve in ("long", "short", "sleeveless"):
            if sleeve == preferences.sleeve_required:
                continue
            found = shortlist(line, policy, replace(preferences, sleeve_required=sleeve), variants, now)
            if found:
                result.append(Tradeoff(constraint, preferences.sleeve_required, sleeve, tuple(found)))
        return sorted(result, key=lambda plan: (min(c.top_up_minor for c in plan.candidates), plan.after))
    if preferences.quieter_than is None:
        return []
    found = shortlist(line, policy, replace(preferences, quieter_than=None), variants, now)
    return [Tradeoff(constraint, True, False, tuple(found))] if found else []
