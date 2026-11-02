"""Publish separate contract/model outcomes, retaining failures and unattempted cases."""
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exchange import agent_evaluation, visual_review


class Command(BaseCommand):
    help = "Publish the separate agent evaluation and hash-matched recorded AI assessments."

    def handle(self, *args, **options):
        work = Path(settings.PRIVATE_MEDIA_ROOT) / "agent-evaluation"
        offline_path = work / "offline.json"
        if not offline_path.exists():
            raise CommandError("Run evaluate_agents first; no contract evidence exists.")
        offline = json.loads(offline_path.read_text())
        if offline["planned"] != len(offline["cases"]) or offline["passed"] != sum(not c["failures"] for c in offline["cases"]):
            raise CommandError("Contract counts do not match the case outcomes.")
        reports = []
        base = settings.BASE_DIR / "exchange/static/exchange/evidence"
        bundles = {case["id"]: case["bundle"] for case in agent_evaluation.case_bundles()}
        for path in sorted(work.glob("run-[12].json")):
            report = json.loads(path.read_text())
            registration = settings.BASE_DIR / "assets" / f"exchange_ai_pilot_v1_run{report['run_index']}.json"
            if not registration.exists() or hashlib.sha256(registration.read_bytes()).hexdigest() != report["registration_sha256"]:
                raise CommandError("Run registration does not match its retained report.")
            report["registration_file"] = registration.name
            annotation = work / f"run-{report['run_index']}-observations.json"
            if annotation.exists():
                report["implementation_observation"] = json.loads(annotation.read_text())
            report["agent_completed"] = sum(r["state"] == "completed" for r in report["agent_cases"])
            report["visual_completed"] = sum(r["state"] == "completed" for r in report["visual_cases"])
            agreements = [same for r in report["visual_cases"] for same in r.get("same_finding", {}).values()]
            report["model_agreement"] = {"same": sum(agreements), "compared": len(agreements)}
            for case in report["visual_cases"]:
                if not case.get("sources"):
                    continue
                bundle = bundles[case["id"]]
                case["input_images"] = []
                for role, image in bundle["images"].items():
                    digest = hashlib.sha256(image).hexdigest()
                    if digest != case["sources"][role]:
                        raise CommandError("An assessed input changed; do not publish different pixels as evidence.")
                    folder = base / "ai-review-inputs-v1"
                    folder.mkdir(exist_ok=True)
                    target = folder / (digest + ".jpg")
                    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise CommandError("Published input digest mismatch.")
                    target.write_bytes(image)
                    case["input_images"].append({"role": role, "path": "exchange/evidence/ai-review-inputs-v1/" + target.name,
                                                  "sha256": digest})
            reports.append(report)
        data = {"version": "exchange-agents-v1", "offline": {k: v for k, v in offline.items() if k != "cases"},
                "model_state": "executed" if reports else "prepared_not_executed", "runs": reports,
                "independent_human_reviews": 0, "fidelity_accuracy": None, "new_youcam_calls": 0,
                "limitations": ["Known public synthetic recordings, not a new unseen-product study.",
                    "Both reviewing models belong to the same family; agreement is descriptive, not independent human accuracy.",
                    "Deterministic contracts do not measure shopper benefit, return reduction or visual fidelity.",
                    "Failed and unattempted cases remain visible; repeated runs retain their own denominators."]}
        (base / "agents-evaluation.json").write_text(json.dumps(data, indent=2) + "\n")
        # Only fully completed runs can supply replayed model assessments. The separate report keeps
        # partial attempts visible, but they cannot silently become successful demo evidence.
        replay = []
        completed = [r for r in reports if r["completed"]]
        if completed:
            for case in completed[-1]["visual_cases"]:
                primary, second = case["reviews"]
                checks = []
                a = {c["attribute"]: c for c in primary["checks"]}
                b = {c["attribute"]: c for c in second["checks"]}
                for attribute, first in a.items():
                    if first["finding"] == b[attribute]["finding"]:
                        checks.append(first)
                    else:
                        checks.append({"attribute": attribute, "finding": "unclear",
                            "observation": "The two AI reviews disagree about this detail. Inspect the recording yourself."})
                requested = {key: "registered request" for key in a}
                visual_review.accept({"checks": checks}, requested)
                replay.append({"identity": case["identity"], "prompt_sha256": case["prompt_sha256"],
                    "model": primary["model"] + " + " + second["model"] + " (same model family)",
                    "checks": checks, "sources": case["sources"], "case_id": case["id"],
                    "run_index": completed[-1]["run_index"], "human_reviewed": False})
        (base / "ai-reviews-v1.json").write_text(json.dumps({"version": visual_review.VERSION,
            "state": "recorded_model_assessments" if replay else "prepared_not_executed",
            "results": replay, "independent_human_reviews": 0}, indent=2) + "\n")
        self.stdout.write(f"Published {offline['passed']}/{offline['planned']} contract outcomes, {len(reports)} retained model runs and {len(replay)} recorded AI assessments. Human reviews: 0.")
