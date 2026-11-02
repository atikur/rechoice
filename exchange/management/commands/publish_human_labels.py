"""Publish blind human labels for gate-study-v2 under pseudonyms, as assets/gate_labels_addendum.json registers.

The labelling page (scripts/label_gate_study_local.py --rater ID) saves each person's answers to .local/. This
copies complete sets to the evidence folder as Person 1, Person 2, ... in the order given, and the owner's set as
Owner, marked as not independent. It refuses an incomplete set rather than publishing part of it. Afterwards,
python manage.py evaluate_gate --write recomputes the gate report with every published set.
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange import gate_evaluation, gate_labels

PREFIX = "gate-study-v2-labels-"


def shown(path, root):
    return path.relative_to(root) if path.is_relative_to(root) else path
ADDENDUM = "assets/gate_labels_addendum.json"


class Command(BaseCommand):
    help = "Publish complete blind human labels for gate-study-v2 under pseudonyms."

    def add_arguments(self, parser):
        parser.add_argument("raters", nargs="*", help="Rater ids in the order to number them; every saved set if omitted.")
        parser.add_argument("--source", default=".local", help="The folder the labelling page saved answers in.")
        parser.add_argument("--dest", default=str(gate_evaluation.EVIDENCE / "gate-study-v2"))
        parser.add_argument("--owner", default="owner", help="The rater id of the person who built the gate.")
        parser.add_argument("--dry-run", action="store_true", help="Check the sets and say what would be written.")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        source, dest = root / options["source"], root / options["dest"]
        saved = {path.name[len(PREFIX):-len(".json")]: path for path in sorted(source.glob(PREFIX + "*.json"))}
        raters = options["raters"] or sorted(saved)
        if not raters:
            raise CommandError(f"No saved labels in {source}. Run scripts/label_gate_study_local.py --rater ID first.")
        missing = [r for r in raters if r not in saved]
        if missing:
            raise CommandError("No saved labels for: " + ", ".join(missing))
        evidence = root / gate_evaluation.EVIDENCE
        items = gate_labels.items(root, json.loads((evidence / "gate-study-v2/report.json").read_text()),
                                  json.loads((evidence / "gate-study-v1/report.json").read_text()))
        allowed = {key: set(q["answers"]) for key, q in gate_labels.questions(root).items()}
        writes, person = [], 0
        for rater in raters:
            data = json.loads(saved[rater].read_text())
            answers = data.get("answers", {})
            gaps = sum(1 for item in items for q in item["questions"] if q not in answers.get(item["id"], {}))
            if gaps:
                raise CommandError(f"Rater {rater} has {gaps} unanswered questions. Finish labelling, or leave them out.")
            bad = [(i, q) for i, given in answers.items() for q, a in given.items() if a not in allowed.get(q, ())]
            if bad:
                raise CommandError(f"Rater {rater} has answers outside the registered choices: {bad[:3]}")
            owner = rater == options["owner"]
            if not owner:
                person += 1
            published = {
                "rater": "Owner (human, built the gate)" if owner else f"Person {person} (human)",
                "kind": "human", "independent": not owner, "protocol": ADDENDUM,
                "blind_to": data.get("blind_to", ""), "order_seed": data.get("order_seed"),
                "collected_on": str(data.get("updated_at_utc", ""))[:10],
                "answers": {item["id"]: {q: answers[item["id"]][q] for q in item["questions"]} for item in items},
            }
            writes.append((dest / ("labels-owner.json" if owner else f"labels-person-{person}.json"), published))
        for path, published in writes:
            if options["dry_run"]:
                self.stdout.write(f"Would write {shown(path, root)} for {published['rater']}.")
                continue
            path.write_text(json.dumps(published, indent=1) + "\n")
            self.stdout.write(f"Wrote {shown(path, root)} for {published['rater']}.")
        if not options["dry_run"]:
            self.stdout.write("Next: python manage.py evaluate_gate --write")
