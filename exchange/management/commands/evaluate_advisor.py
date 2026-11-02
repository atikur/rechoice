"""Check what Claude writes for "Help me compare" and staff messages, against the rules' versions.

The scenarios are built from the seeded catalog: shopper requests across every category, and staff
decisions on some of the resulting exchanges. Nothing is saved to the database except the spend events.

Dry run by default: it builds every scenario and the rules' text, which costs nothing, and prints what the
Claude run would cost. --execute writes each scenario with Claude and needs --max-usd, the most that every
advisor evaluation together may spend, counted from the spend already recorded; it stops before any call
that could take the total past it. --judge also asks a second model, blind to which text is which, whether
Claude's or the rules' text is more helpful and whether each is accurate to the facts. ANTHROPIC_API_KEY
must be set for this one process; it is never printed.

Claude's text counts as usable when it passes the same checks the app applies before showing it: every
option covered, no words about the person, and no number that isn't in the facts. The report, with every
text for reading, goes to <PRIVATE_MEDIA_ROOT>/advisor-eval/report.json.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange import advisor, assistant
from exchange.models import EvidenceEvent, ExchangeSession, OrderLine, ProductVariant
from exchange.views import _eligible

COMPARISONS = [
    ("DEMO-ORDER-001", {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000}),
    ("DEMO-ORDER-001", {"quieter_print": True, "max_top_up_minor": 2000}),
    ("DEMO-ORDER-001", {"color_preferences": ["green"], "max_top_up_minor": 2000}),
    ("DEMO-ORDER-001", {"neckline_preference": "crew", "max_top_up_minor": 0}),
    ("DEMO-ORDER-001-LIP", {"tone": "cooler", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-LIP", {"lightness": "lighter", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-FDN", {"tone": "cooler", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-FDN", {"lightness": "darker", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-NAIL", {"tone": "cooler", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-NAIL", {"intensity": "softer", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-EAR", {"size": "smaller", "metal": "silver", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-EAR", {"size": "smaller", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-EAR", {"metal": "silver", "max_top_up_minor": 0}),
    ("DEMO-ORDER-001-NECK", {"size": "smaller", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-NECK", {"size": "smaller", "metal": "gold", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-HAIR", {"tone": "cooler", "max_top_up_minor": 500}),
    ("DEMO-ORDER-001-HAIR", {"lightness": "darker", "max_top_up_minor": 500}),
]
# Staff decide on the first option of these comparisons, three ways each.
STAFF_FROM = (0, 4, 10, 15)
STATES = ("REVIEWED", "NEEDS_CLARIFICATION", "DECLINED")
TYPICAL = {"compare": (1800, 400), "note": (1000, 150), "judge": (2200, 250)}
JUDGE_MODEL = "claude-sonnet-5-5"
JUDGE_INSTRUCTIONS = """You review two texts written for a shopper or a store's staff from the same facts. Judge \
only against the facts: a text is accurate when every detail, price and claim in it is supported by them. Then \
say which text a shopper or staff member would find more helpful for the decision, or "tie". Never favour \
length for its own sake."""
JUDGE_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["a_accurate", "b_accurate", "more_helpful", "reason"],
                "properties": {"a_accurate": {"type": "boolean"}, "b_accurate": {"type": "boolean"},
                               "more_helpful": {"type": "string", "enum": ["A", "B", "tie"]},
                               "reason": {"type": "string"}}}


def scenarios():
    """Every comparison and staff decision, with its facts and the rules' text."""
    rows = []
    items = {v.sku: v for v in ProductVariant.objects.filter(retailer_id="secondlook-demo")}
    firsts = {}
    for n, (token, prefs) in enumerate(COMPARISONS):
        line = OrderLine.objects.select_related("variant").get(token=token)
        session = ExchangeSession(order_line=line, preferences={**prefs, "currency": "USD"})
        found = _eligible(session)
        if not found:
            rows.append({"kind": "compare", "n": n + 1, "token": token, "asked": prefs, "skipped": "no options"})
            continue
        facts = advisor.compare_facts(session, found, items, [])
        rows.append({"kind": "compare", "n": n + 1, "token": token, "facts": facts,
                     "rules": advisor.rules_compare(facts)})
        firsts[n] = (session, found[0], items[found[0].variant.sku])
    for n in STAFF_FROM:
        if n not in firsts:
            continue
        session, candidate, item = firsts[n]
        line = session.order_line
        draft = SimpleNamespace(
            owner_session=SimpleNamespace(order_line=line, return_reason="", preferences=session.preferences),
            quote=SimpleNamespace(variant=item, kind="style", currency=line.currency, credit_minor=line.eligible_credit_minor,
                                  replacement_minor=item.price_minor, fee_minor=0, top_up_minor=candidate.top_up_minor),
            shopper_reply="")
        for state in STATES:
            facts = {**advisor.staff_facts(draft), "decision": advisor.DECISIONS[state]}
            rows.append({"kind": "note", "n": len(rows) + 1, "token": line.token, "state": state, "facts": facts,
                         "rules": {"message": advisor.rules_staff_note(facts, state)}})
    return rows


class Command(BaseCommand):
    help = "Check Claude's comparisons and staff messages against the rules' versions."

    def add_arguments(self, parser):
        parser.add_argument("--model", default=None, help="Default: ASSISTANT_MODEL.")
        parser.add_argument("--execute", action="store_true", help="Also write each scenario with Claude.")
        parser.add_argument("--judge", action="store_true", help=f"Also ask {JUDGE_MODEL} to compare the texts, blind.")
        parser.add_argument("--max-usd", type=float, default=None,
                            help="Required with --execute: the most all advisor evaluations together may spend.")

    def handle(self, *args, **options):
        model = options["model"] or settings.ASSISTANT_MODEL
        rows = scenarios()
        live = [r for r in rows if "facts" in r]
        compares = sum(r["kind"] == "compare" for r in live)
        typical = (compares * assistant.cost_usd(model, *TYPICAL["compare"])
                   + (len(live) - compares) * assistant.cost_usd(model, *TYPICAL["note"]))
        judged = len(live) * assistant.cost_usd(JUDGE_MODEL, *TYPICAL["judge"]) if options["judge"] else 0
        spent = self.spent()
        self.stdout.write(f"Scenarios: {compares} comparisons and {len(live) - compares} staff messages "
                          f"({len(rows) - len(live)} skipped with no options). Model: {model}. "
                          f"With Claude, about USD {typical:.4f}" + (f", and USD {judged:.4f} to judge" if judged else "")
                          + f". Advisor evaluation spend so far: USD {spent:.4f}.")
        if options["execute"]:
            if options["max_usd"] is None:
                raise CommandError("Writing with Claude spends money. Add --max-usd, the most all advisor evaluations "
                                   "together may spend. Nothing was sent.")
            if not settings.ANTHROPIC_API_KEY:
                raise CommandError("Set ANTHROPIC_API_KEY for this one process. Nothing was sent.")
            self.write_with_claude(live, model, spent, options["max_usd"], options["judge"])
        self.report(rows, model)

    def spent(self):
        return sum(f.get("cost_usd", 0) for f in
                   EvidenceEvent.objects.filter(action="advisor_eval").values_list("facts", flat=True))

    def call(self, model, system, facts, schema, spent, cap, purpose):
        worst = assistant.cost_usd(model, TYPICAL[purpose][0], settings.ASSISTANT_MAX_TOKENS)
        if spent + worst > cap:
            raise StopIteration
        usage, outcome, data = (0, 0), "read", None
        try:
            data, usage = advisor.ask(system, facts, schema, model=model)
        except assistant.Unusable as exc:
            outcome, usage = str(exc), getattr(exc, "usage", None) or usage
        except Exception as exc:
            outcome = f"error: {type(exc).__name__}"
        cost = assistant.cost_usd(model, *usage)
        EvidenceEvent.objects.create(actor="system", action="advisor_eval", facts={
            "model": model, "purpose": purpose, "outcome": outcome, "input_tokens": usage[0],
            "output_tokens": usage[1], "cost_usd": cost, "approval": settings.ASSISTANT_APPROVAL})
        return data, outcome, cost

    def write_with_claude(self, rows, model, spent, cap, judge):
        for row in rows:
            facts = row["facts"]
            try:
                if row["kind"] == "compare":
                    item = assistant.ITEMS.get(facts["category"], "an item")
                    data, outcome, cost = self.call(
                        model, advisor.COMPARE_INSTRUCTIONS.format(item=item), facts,
                        advisor._schema_compare([o["sku"] for o in facts["options"]]), spent, cap, "compare")
                    accept = lambda d: advisor.accept_compare(d, facts)  # noqa: E731
                else:
                    data, outcome, cost = self.call(model, advisor.STAFF_INSTRUCTIONS, facts, advisor.SCHEMA_NOTE,
                                                    spent, cap, "note")
                    accept = lambda d: {"message": advisor.accept_note(d, facts)}  # noqa: E731
                spent += cost
                result = {"outcome": outcome, "cost_usd": cost}
                if data is not None:
                    try:
                        result.update(text=accept(data), usable=True)
                    except assistant.Unusable as exc:
                        result.update(text=data, usable=False, outcome=str(exc))
                row["claude"] = result
                if judge and result.get("usable"):
                    row["judge"], cost = self.judge(row, spent, cap)
                    spent += cost
            except StopIteration:
                self.stdout.write(self.style.WARNING(f"Stopped before scenario {row['n']}: another call could take "
                                                     f"the advisor evaluation spend past USD {cap:.2f}."))
                return

    def judge(self, row, spent, cap):
        """Ask the judge blind: odd scenarios put Claude's text first, even ones the rules'."""
        claude_first = row["n"] % 2 == 1
        texts = [row["claude"]["text"], row["rules"]]
        a, b = texts if claude_first else texts[::-1]
        facts = {"facts": row["facts"], "text_A": a, "text_B": b}
        data, outcome, cost = self.call(JUDGE_MODEL, JUDGE_INSTRUCTIONS, facts, JUDGE_SCHEMA, spent, cap, "judge")
        if data is None:
            return {"outcome": outcome}, cost
        winner = {"A": "claude" if claude_first else "rules", "B": "rules" if claude_first else "claude",
                  "tie": "tie"}.get(data.get("more_helpful"), "tie")
        return {"more_helpful": winner,
                "claude_accurate": data.get("a_accurate") if claude_first else data.get("b_accurate"),
                "rules_accurate": data.get("b_accurate") if claude_first else data.get("a_accurate"),
                "reason": str(data.get("reason", ""))[:300]}, cost

    def report(self, rows, model):
        written = [r for r in rows if "claude" in r]
        if written:
            usable = sum(bool(r["claude"].get("usable")) for r in written)
            spend = sum(r["claude"]["cost_usd"] for r in written)
            self.stdout.write(f"Claude: {usable} of {len(written)} usable; USD {spend:.4f} this run.")
            refused = sorted({r["claude"]["outcome"] for r in written if not r["claude"].get("usable")})
            if refused:
                self.stdout.write("Not usable because: " + ", ".join(refused) + ".")
            judged = [r["judge"] for r in written if "more_helpful" in r.get("judge", {})]
            if judged:
                wins = {who: sum(j["more_helpful"] == who for j in judged) for who in ("claude", "rules", "tie")}
                self.stdout.write(f"Judge ({JUDGE_MODEL}), {len(judged)} blind comparisons: Claude more helpful "
                                  f"{wins['claude']}, rules {wins['rules']}, tie {wins['tie']}; accurate: Claude "
                                  f"{sum(bool(j['claude_accurate']) for j in judged)}, rules "
                                  f"{sum(bool(j['rules_accurate']) for j in judged)}.")
        folder = Path(settings.PRIVATE_MEDIA_ROOT) / "advisor-eval"
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        (folder / "report.json").write_text(json.dumps({"model": model, "scenarios": rows}, indent=2, default=str) + "\n")
        os.chmod(folder / "report.json", 0o600)
        self.stdout.write(f"Report: {folder / 'report.json'}")
