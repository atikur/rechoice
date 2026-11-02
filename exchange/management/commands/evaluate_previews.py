import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange.preview_evaluation import build_evaluation, review_template


class Command(BaseCommand):
    help = "Recompute public-pilot evaluation offline; no provider, database data or private media needed."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--write", action="store_true", help="Publish the reproducible report and corrected images.")
        mode.add_argument("--check", action="store_true", help="Fail if the committed report or correction images are stale.")
        parser.add_argument("--reviews", type=Path, help="Optional completed rubric file, with named reviewer provenance.")
        parser.add_argument("--review-template", type=Path, help="Write a blank rubric for every case to this new file.")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        try:
            reviews = json.loads(options["reviews"].read_text()) if options["reviews"] else None
            report, corrections = build_evaluation(root, reviews)
            if options["review_template"]:
                with options["review_template"].open("x") as stream:
                    json.dump(review_template(report["cases"]), stream, indent=2)
                    stream.write("\n")
            path = root / "exchange/static/exchange/evidence/evaluation.json"
            encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            if options["check"]:
                if not path.exists() or path.read_text() != encoded:
                    raise CommandError("Evaluation report is stale. Inspect the changes, then run evaluate_previews --write.")
                for name, data in corrections.items():
                    if (root / "exchange/static" / name).read_bytes() != data:
                        raise CommandError("Evaluation correction image is stale: " + name)
                self.stdout.write("Evaluation report and correction images reproduce exactly.")
            if options["write"]:
                for name, data in corrections.items():
                    (root / "exchange/static" / name).write_bytes(data)
                path.write_text(encoded)
                self.stdout.write("Published " + str(path.relative_to(root)))
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report["summary"], indent=2))
