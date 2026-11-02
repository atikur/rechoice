"""Record what each paid preview actually cost, from the provider's unit history.

Reads the history live from the configured paid provider (a read-only call), or from a saved file
written by scripts/check_youcam_budget.py --save-history. The scheduled task does the same hourly.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from exchange import preview_jobs
from exchange.tasks import _paid_provider


def entries_from_file(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    pages = raw if isinstance(raw, list) else [raw]
    entries = []
    for page in pages:
        entries.extend(page.get("history") if "history" in page else (page.get("result") or {}).get("history") or [])
    return entries


class Command(BaseCommand):
    help = "Record actual charges for paid previews from the provider's unit history."

    def add_arguments(self, parser):
        parser.add_argument("--history", metavar="FILE", help="Saved unit history instead of a live read.")

    def handle(self, *args, **options):
        if options["history"]:
            entries = entries_from_file(options["history"])
        else:
            provider = _paid_provider()
            if provider is None:
                raise CommandError("No paid provider is configured. Pass --history FILE to reconcile offline.")
            entries = provider.unit_history()
        result = preview_jobs.reconcile_units(entries)
        self.stdout.write(f"Matched {result['matched']} tasks; {result['unmatched']} had no matching charge "
                          f"and kept their counts.")
        if result["outside_units"]:
            self.stdout.write(self.style.WARNING(
                f"{result['outside_units']} units were charged to tasks this app did not create."))
