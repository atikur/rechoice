"""Compare the rules with Claude on the notes in assets/assistant_eval.json.

Dry run by default: it scores the rules, which cost nothing, and prints what reading the notes with Claude
would cost. --execute also reads each note with Claude, and needs --max-usd: the most that every evaluation
run together may spend, counted from the evaluation spend already recorded. It stops before any read that
could take the total past it. ANTHROPIC_API_KEY must be set for this one process; it is never printed.

--runs N reads the notes N times and reports each run and the average, because the model's answers vary a
little from run to run. The cap covers all of them.

A note scores as right when the controls it fills in match the expected ones exactly (an empty answer, which
means no change, counts as not filled in) and it asks about every control it should ask about. The report
goes to <PRIVATE_MEDIA_ROOT>/assistant-eval/report.json.
"""
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange import assistant
from exchange.models import EvidenceEvent

# What one read is expected to use, and the most it can use with the output cap.
TYPICAL_TOKENS = (1500, 500)


def score(result, note):
    asked = {q["field"] for q in result["questions"]}
    expected_asks = set(note.get("asks", []))
    proposal = {k: v for k, v in result["proposal"].items() if v != "" and k not in expected_asks}
    expected = {k: v for k, v in note["expect"].items() if v != ""}
    right = proposal == expected and expected_asks <= asked and (bool(asked) or not note.get("asks_any"))
    return right, proposal, sorted(asked)


class Command(BaseCommand):
    help = "Score the rules and, with --execute, Claude on the assistant evaluation notes."

    def add_arguments(self, parser):
        parser.add_argument("--notes", default=str(Path(settings.BASE_DIR) / "assets" / "assistant_eval.json"))
        parser.add_argument("--model", default=None, help="Default: ASSISTANT_MODEL.")
        parser.add_argument("--execute", action="store_true", help="Also read each note with Claude.")
        parser.add_argument("--max-usd", type=float, default=None,
                            help="Required with --execute: the most all evaluation runs together may spend.")
        parser.add_argument("--runs", type=int, default=1, help="How many times to read the notes with Claude.")

    def handle(self, *args, **options):
        notes = json.loads(Path(options["notes"]).read_text(encoding="utf-8"))["notes"]
        model = options["model"] or settings.ASSISTANT_MODEL
        typical = assistant.cost_usd(model, *TYPICAL_TOKENS)
        worst = assistant.cost_usd(model, TYPICAL_TOKENS[0], settings.ASSISTANT_MAX_TOKENS)
        spent = self.spent()
        self.stdout.write(f"Notes: {len(notes)}. Model: {model}. With Claude, about USD {typical * len(notes):.4f} "
                          f"and at most USD {worst * len(notes):.4f}. Evaluation spend so far: USD {spent:.4f}.")
        rows = []
        for n, note in enumerate(notes, 1):
            right, proposal, asked = score(assistant.rules_reading(note["group"], note["category"], note["text"]).as_dict(), note)
            rows.append({"n": n, "group": note["group"], "text": note["text"], "expect": note["expect"],
                         "asks": note.get("asks", []), "rules": {"right": right, "proposal": proposal, "asked": asked}})
        if options["execute"]:
            if options["max_usd"] is None:
                raise CommandError("Reading the notes with Claude spends money. Add --max-usd, the most all evaluation "
                                   "runs together may spend. Nothing was sent.")
            if not settings.ANTHROPIC_API_KEY:
                raise CommandError("Set ANTHROPIC_API_KEY for this one process. Nothing was sent.")
            for run in range(1, options["runs"] + 1):
                key = "claude" if options["runs"] == 1 else f"claude run {run}"
                spent, stopped = self.read_with_claude(notes, rows, model, worst, spent, options["max_usd"], key)
                if stopped:
                    break
        self.report(rows, model)

    def spent(self):
        return sum(f.get("cost_usd", 0) for f in
                   EvidenceEvent.objects.filter(action="assistant_eval").values_list("facts", flat=True))

    def read_with_claude(self, notes, rows, model, worst, spent, cap, key="claude"):
        """Read every note once. Returns the evaluation spend afterwards, and whether the cap stopped it."""
        for note, row in zip(notes, rows):
            if spent + worst > cap:
                self.stdout.write(self.style.WARNING(f"Stopped before note {row['n']}: another read could take the "
                                                     f"evaluation spend past USD {cap:.2f}."))
                return spent, True
            usage, outcome = (0, 0), "read"
            try:
                data, usage = assistant.ask_claude(note["group"], note["category"], note["text"], model=model)
                right, proposal, asked = score(assistant.accept(note["group"], data), note)
                row[key] = {"right": right, "proposal": proposal, "asked": asked}
            except assistant.Unusable as exc:
                outcome, usage = str(exc), getattr(exc, "usage", None) or usage
                row[key] = {"right": False, "outcome": outcome}
            except Exception as exc:
                outcome = f"error: {type(exc).__name__}"
                row[key] = {"right": False, "outcome": outcome}
            cost = assistant.cost_usd(model, *usage)
            spent += cost
            row[key].update(input_tokens=usage[0], output_tokens=usage[1], cost_usd=cost)
            EvidenceEvent.objects.create(actor="system", action="assistant_eval", facts={
                "model": model, "outcome": outcome, "input_tokens": usage[0], "output_tokens": usage[1],
                "cost_usd": cost, "approval": settings.ASSISTANT_APPROVAL})
        return spent, False

    def report(self, rows, model):
        readers = ["rules"] + sorted({key for r in rows for key in r if key.startswith("claude")})
        totals = {}
        for reader in readers:
            scored = [r for r in rows if reader in r]
            right = sum(r[reader]["right"] for r in scored)
            by_group = {g: (sum(r[reader]["right"] for r in scored if r["group"] == g),
                            sum(1 for r in scored if r["group"] == g)) for g in ("apparel", "beauty", "jewellery")}
            spend = sum(r[reader].get("cost_usd", 0) for r in scored)
            totals[reader] = (right, len(scored), by_group)
            self.stdout.write(f"{reader.capitalize()}: {right} of {len(scored)} right ("
                              + ", ".join(f"{g} {a}/{b}" for g, (a, b) in by_group.items() if b)
                              + (f"); USD {spend:.4f} this run." if reader.startswith("claude") else ")."))
        runs = [totals[r] for r in readers if r.startswith("claude run") and totals[r][1] == len(rows)]
        if len(runs) > 1:
            groups = {g: sum(t[2][g][0] for t in runs) / len(runs) for g in ("apparel", "beauty", "jewellery")}
            self.stdout.write(f"Claude, average of {len(runs)} complete runs: "
                              f"{sum(t[0] for t in runs) / len(runs):.1f} of {len(rows)} ("
                              + ", ".join(f"{g} {v:.1f}" for g, v in groups.items()) + ").")
        folder = Path(settings.PRIVATE_MEDIA_ROOT) / "assistant-eval"
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        (folder / "report.json").write_text(json.dumps({"model": model, "notes": rows}, indent=2) + "\n")
        os.chmod(folder / "report.json", 0o600)
        self.stdout.write(f"Report: {folder / 'report.json'}")
