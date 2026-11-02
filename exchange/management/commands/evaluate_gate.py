"""Run the fidelity gate over the published top renders and write or check its public report."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange import gate_evaluation


class Command(BaseCommand):
    help = "Measure every published top render with the fidelity gate; no database, private media or network."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--write", action="store_true", help="Publish the report to the evidence folder.")
        mode.add_argument("--check", action="store_true", help="Fail if the committed report is stale.")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        if options["check"]:
            if not gate_evaluation.check(root):
                raise CommandError("The committed gate report is stale. Run evaluate_gate --write.")
            self.stdout.write("Gate report matches the published files and code.")
            return
        report = gate_evaluation.build(root)
        (root / gate_evaluation.REPORT).write_text(json.dumps(report, indent=1) + "\n")
        s = report["summary"]
        self.stdout.write(f"Wrote {gate_evaluation.REPORT}: {s['alternatives']} alternatives, verdicts {s['verdicts']}, "
                          f"print agrees with catalog on {s['print_agrees_with_catalog']}/{s['print_measured']}, "
                          f"known failure flagged: {s['known_failure_flagged']}.")
