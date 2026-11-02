"""Merchant insights from this demo's own records: outcomes, unmet demand, preview cost and quality.

Counts only. They describe what happened in these sessions; they are not evidence that the product
reduces returns or retains revenue.
"""
from collections import Counter
from statistics import median

from .assistant import CLAUDE_ACTIONS
from .models import EvidenceEvent, ExchangeRequest, ExchangeSession, PreviewJob

REASON_NAMES = {"style": "Doesn't like how it looks", "fit": "Doesn't fit", "shade": "Shade looks wrong",
                "look": "Jewellery doesn't look right",
                "reaction": "Skin, lip or nail reaction", "damaged": "Damaged or wrong item",
                "changed_mind": "Changed mind", "": "Not asked (older sessions)"}
EXCLUSION_NAMES = {"out_of_stock": "out of stock", "outside_policy": "outside policy",
                   "staff_review": "needs staff review", "missing_details": "missing catalog details",
                   "sleeves": "sleeves", "pattern": "print too busy", "over_budget": "over budget",
                   "opened_same_line": "opened, other product line", "not_lightness": "not light or dark enough",
                   "not_tone": "not warm or cool enough", "not_intensity": "not soft or bold enough",
                   "not_size": "not smaller or larger enough", "not_metal": "other metal"}
STATE_ORDER = ("succeeded", "failed", "uncertain", "running", "pending")
# Reasons a try-on preview can help with: the item is fine but does not look right on the shopper.
LOOKS_REASONS = ("style", "shade", "look")
CATEGORY_NAMES = {"tops": "Tops", "lip": "Lipstick", "foundation": "Foundation", "nail": "Nail polish",
                  "earrings": "Earrings", "necklace": "Necklaces", "hair": "Hair colour"}


def _usd(cents):
    return f"USD {cents / 100:,.2f}"


def _case(sessions, drafts, live_units, claude_usd):
    """What the store kept: exchange drafts instead of refunds, from these sessions only."""
    exchanges = [d for d in drafts if d.quote.kind != "size"]
    looks = [s for s in sessions if s.return_reason in LOOKS_REASONS]
    looks_ids = {s.id for s in looks}
    looks_exchanges = sum(1 for d in exchanges if d.owner_session_id in looks_ids)
    looks_returns = sum(1 for s in looks if s.status == "CANCELLED")
    decided = looks_exchanges + looks_returns
    kept = sum(d.quote.credit_minor for d in drafts)
    reviewed = sum(d.quote.credit_minor for d in drafts if d.state == "REVIEWED")
    rows = [
        ("Returns that didn't look right", len(looks)),
        ("Of those decided, kept as an exchange",
         f"{looks_exchanges} of {decided} ({looks_exchanges / decided:.0%})" if decided else "none decided yet"),
        ("Credit kept in the store instead of refunded", _usd(kept)),
        ("Of that, approved by staff", _usd(reviewed)),
        ("Top-ups the shopper agreed to pay", _usd(sum(d.quote.top_up_minor for d in drafts))),
        ("YouCam units per exchange", f"{live_units / len(drafts):.1f}" if drafts else "no exchanges yet"),
        ("Claude spend per exchange", f"USD {claude_usd / len(drafts):.4f}" if drafts else "no exchanges yet"),
    ]
    by_category = {}
    for session in sessions:
        row = by_category.setdefault(session.order_line.variant.category, [0, 0, 0, 0])
        row[0] += 1
        row[2] += session.status == "CANCELLED"
    for draft in drafts:
        row = by_category.setdefault(draft.owner_session.order_line.variant.category, [0, 0, 0, 0])
        row[1] += 1
        row[3] += draft.quote.credit_minor
    categories = [(CATEGORY_NAMES.get(code, code), started, exchanged, returned, _usd(credit))
                  for code, (started, exchanged, returned, credit) in
                  sorted(by_category.items(), key=lambda item: -item[1][0])]
    return {"case": rows, "categories": categories}


def _budget_band(cents):
    if cents is None:
        return "any budget"
    if cents == 0:
        return "nothing extra"
    for limit in (500, 1000, 2000):
        if cents <= limit:
            return f"up to USD {limit // 100}"
    return "over USD 20"


def _wanted(preferences):
    shade = [preferences[key] for key in ("lightness", "tone", "intensity") if preferences.get(key)]
    if shade:
        return " · ".join(["shade: " + ", ".join(shade), _budget_band(preferences.get("max_top_up_minor"))])
    earrings = [preferences[key] for key in ("size", "metal") if preferences.get(key)]
    if earrings:
        return " · ".join(["jewellery: " + ", ".join(earrings), _budget_band(preferences.get("max_top_up_minor"))])
    parts = [f"{preferences['sleeve_required']} sleeves" if preferences.get("sleeve_required") else "any sleeves",
             "quieter print" if preferences.get("quieter_print") else "any print"]
    if preferences.get("color_preferences"):
        parts.append(" or ".join(preferences["color_preferences"]))
    if preferences.get("neckline_preference"):
        parts.append(preferences["neckline_preference"])
    parts.append(_budget_band(preferences.get("max_top_up_minor")))
    return " · ".join(parts)


def summary():
    sessions = ExchangeSession.objects.all()
    reasons = Counter(sessions.values_list("return_reason", flat=True))
    drafts = list(ExchangeRequest.objects.select_related("quote", "owner_session__order_line__variant"))
    by_kind = Counter("size" if d.quote.kind == "size" else "item" for d in drafts)
    returns_chosen = sessions.filter(status="CANCELLED").count()
    outcomes = [
        ("Sessions started", sessions.count()),
        ("Exchange drafts: a different item", by_kind.get("item", 0)),
        ("Exchange drafts: another size", by_kind.get("size", 0)),
        ("Chose the normal return", returns_chosen),
        ("No decision yet", sessions.exclude(status__in=("DRAFT_CREATED", "CANCELLED")).count()),
    ]
    reason_rows = [(REASON_NAMES.get(code, code), count) for code, count in reasons.most_common()]

    unmet, blockers = Counter(), {}
    for facts in EvidenceEvent.objects.filter(action="candidates_checked").values_list("facts", flat=True):
        if facts.get("matches"):
            continue
        key = _wanted(facts.get("preferences", {}))
        unmet[key] += 1
        # In how many of these searches each rule left tops out.
        blockers.setdefault(key, Counter()).update({code: 1 for code in facts.get("exclusions", {})})
    unmet_rows = [(wanted, count, ", ".join(f"{EXCLUSION_NAMES.get(code, code)} ({n})"
                                           for code, n in blockers[wanted].most_common()))
                  for wanted, count in unmet.most_common(10)]

    jobs = list(PreviewJob.objects.select_related("owner_session__order_line"))
    states = Counter(job.state for job in jobs)
    finished = sum(states.get(s, 0) for s in ("succeeded", "failed", "uncertain"))
    # Timing and cost come from live provider jobs only: mock and recorded results are instant and free.
    live = [job for job in jobs if job.provider not in ("mock", "replay")]
    seconds = [(job.ended_at - job.started_at).total_seconds() for job in live
               if job.state == "succeeded" and job.started_at and job.ended_at]
    compared = {job.owner_session_id for job in live}
    units = sum(job.observed_units for job in live)
    colour = Counter(job.colour_check for job in jobs if job.colour_check)
    previews = [
        ("Preview jobs", len(jobs)),
        ("Succeeded / failed / unclear",
         f"{states.get('succeeded', 0)} / {states.get('failed', 0)} / {states.get('uncertain', 0)}"),
        ("Success rate", f"{states.get('succeeded', 0) / finished:.0%} of {finished}" if finished else "no finished jobs"),
        ("Median live generation time", f"{median(seconds):.0f} s over {len(seconds)}" if seconds else "no live previews"),
        ("Units charged", units),
        ("Units per live comparison", f"{units / len(compared):.1f} over {len(compared)}" if compared else "no live previews"),
        ("Baseline previews of the current top",
         sum(1 for job in jobs if job.variant_id == job.owner_session.order_line.variant_id)),
        ("Colour check: close / differs / pattern / unchecked",
         " / ".join(str(colour.get(k, 0)) for k in ("close", "differs", "pattern", "unchecked"))),
    ]
    review = Counter(d.state for d in drafts)
    calls = list(EvidenceEvent.objects.filter(action__in=CLAUDE_ACTIONS).values_list("action", "facts"))
    reads = [facts for action, facts in calls if action == "assistant_read"]
    written = Counter(action for action, facts in calls if facts.get("outcome") == "read")
    outcomes_read = Counter(f.get("outcome", "") for f in reads)
    instead = ", ".join(f"{why} {n}" for why, n in sorted(outcomes_read.items()) if why != "read")
    every = [facts for _, facts in calls]
    tokens = (sum(f.get("input_tokens", 0) for f in every), sum(f.get("output_tokens", 0) for f in every))
    assistant = [
        ("Notes read by Claude", outcomes_read.get("read", 0)),
        ("Notes read by the rules instead", instead or "none"),
        ("Comparisons written by Claude", written.get("assistant_compare", 0)),
        ("Staff messages drafted by Claude", written.get("assistant_staff_note", 0)),
        ("Tokens in / out", f"{tokens[0]} / {tokens[1]}"),
        ("Spend", f"USD {sum(f.get('cost_usd', 0) for f in every):.4f}"),
    ]
    claude_usd = sum(f.get("cost_usd", 0) for f in every)
    # The store's case counts shoppers only: the pilot runner's sessions are ours, not a store's.
    shoppers = sessions.exclude(browser_session_key__startswith="feasibility:")
    shopper_units = sum(job.observed_units for job in live
                        if not job.owner_session.browser_session_key.startswith("feasibility:"))
    return {"outcomes": outcomes, "reasons": reason_rows, "unmet": unmet_rows, "previews": previews,
            "assistant": assistant,
            **_case(list(shoppers.select_related("order_line__variant")), drafts, shopper_units, claude_usd),
            "providers": sorted(Counter(job.provider for job in jobs).items()),
            "review": sorted(review.items())}
