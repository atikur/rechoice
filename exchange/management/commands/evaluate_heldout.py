import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange.heldout_evaluation import FOLDER, build_heldout


class Command(BaseCommand):
    help = "Reproduce the frozen held-out result report from public files, without network or private media."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--write", action="store_true")

    def handle(self, *args, **options):
        try:
            report = build_heldout(settings.BASE_DIR)
            encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            path = settings.BASE_DIR / FOLDER / "report.json"
            if options["check"]:
                if not path.exists() or path.read_text() != encoded:
                    raise CommandError("Held-out report is stale; inspect before using --write.")
                self.stdout.write("Held-out report reproduces exactly.")
            if options["write"]:
                path.write_text(encoded)
                self.stdout.write("Published " + str(path.relative_to(settings.BASE_DIR)))
            self.stdout.write(json.dumps(report["summary"], indent=2))
        except (OSError, ValueError, KeyError) as error:
            raise CommandError(str(error)) from error
