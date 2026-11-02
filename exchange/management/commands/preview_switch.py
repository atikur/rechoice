"""Pause or resume new previews instantly, or show the live preview status.

Pausing stops new paid starts at once. Jobs already running at the provider are still polled to the
end, because their units are already committed.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange import preview_jobs
from exchange.models import EvidenceEvent, PreviewControl


class Command(BaseCommand):
    help = "Pause or resume new previews, or show their status."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["pause", "resume", "status"])
        parser.add_argument("--reason", default="", help="Why previews are paused; required to pause.")

    def handle(self, *args, **options):
        row = PreviewControl.load()
        if options["action"] == "pause":
            if not options["reason"]:
                raise CommandError("Give a --reason when pausing previews.")
            row.paused, row.reason = True, options["reason"][:200]
            row.save()
            EvidenceEvent.objects.create(actor="staff", action="previews_paused", facts={"reason": row.reason})
        elif options["action"] == "resume":
            row.paused, row.reason = False, ""
            row.save()
            EvidenceEvent.objects.create(actor="staff", action="previews_resumed", facts={})
        today = preview_jobs.committed_units(started_at__gte=preview_jobs._day_start())
        self.stdout.write(f"Provider: {settings.PREVIEW_PROVIDER}. Previews: {'paused' if row.paused else 'on'}"
                          + (f" ({row.reason})" if row.reason else "") + ".")
        self.stdout.write(f"Units committed: {preview_jobs.committed_units()} of {settings.PREVIEW_UNIT_CEILING} lifetime, "
                          f"{today} of {settings.PREVIEW_DAILY_UNIT_CAP} today.")
        balance = "unknown" if row.balance_units is None else f"{row.balance_units:.2f} at {row.balance_checked_at:%Y-%m-%d %H:%M} UTC"
        self.stdout.write(f"Provider balance: {balance}. Floor: {settings.YOUCAM_BALANCE_FLOOR}.")
