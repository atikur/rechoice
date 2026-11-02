"""Run a registered fidelity-gate study (assets/gate_study_v1.json, or --plan) on the live provider, once, and publish it.

Dry run by default. --execute needs --confirm-units equal to the plan's estimate, room under the unit
ceiling and daily cap (or, for a plan that declares an owner-approved batch outside the daily caps, under
its approved cap), and the registration file unchanged since it was frozen. Each case is attempted
once; failures stay in the report. --publish copies the renders and inputs to the public evidence folder
with a report that keeps every case, so the gate evaluation can re-measure them from public files.
"""
import hashlib
import json
import shutil
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone

from exchange import preview_jobs
from exchange.assets import read_private_image
from exchange.gate_hook import gate_finished
from exchange.management.commands.run_feasibility import Command as Feasibility
from exchange.models import PreviewJob, ProductVariant

PLAN = Path("assets/gate_study_v1.json")
EVIDENCE = Path("exchange/static/exchange/evidence")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Command(Feasibility):
    help = "Run or publish a registered fidelity-gate study on demo people the gate has not measured."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Create and drive the live jobs.")
        parser.add_argument("--confirm-units", type=int, default=None)
        parser.add_argument("--publish", action="store_true", help="Copy finished renders and the report to the evidence folder.")
        parser.add_argument("--out", default=None, help="Write the published files here instead (for a read-only container).")
        parser.add_argument("--plan", default=str(PLAN), help="The registered study plan.")

    def load_plan(self, root, path):
        data = (root / path).read_bytes()
        expected = (root / path.with_suffix(".sha256")).read_text().split()[0]
        if digest(data) != expected:
            raise CommandError("The registered study plan changed since it was frozen.")
        plan = json.loads(data)
        for name, sha in {**plan["code_sha256"], **plan.get("sources", {})}.items():
            if digest((root / name).read_bytes()) != sha:
                raise CommandError(f"{name} changed since the study was registered.")
        return plan, expected

    def items(self, plan):
        sources = json.loads((Path(settings.BASE_DIR) / "assets/pixabay_sources.json").read_text())
        models = {m["name"]: m for m in sources["models"]}
        variants = {v.sku: v for v in ProductVariant.objects.filter(retailer_id="secondlook-demo")}
        try:
            provider = preview_jobs.get_provider(preview_jobs.provider_name_for("tops"))
        except preview_jobs.ProviderUnavailable as exc:
            raise CommandError(str(exc)) from None
        items = []
        for case in plan["cases"]:
            entry = next(m for m in plan["models"] if m["name"] == case["model"])
            # A repeat runs in its own session, so it is a new job on the same photo, never the earlier one.
            session = case.get("session", "main")
            label = plan["version"] if session == "main" else f"{plan['version']}-{session}"
            from django.contrib.staticfiles import finders
            path = finders.find(entry["static_path"])
            if not path or digest(Path(path).read_bytes()) != entry["sha256"]:
                raise CommandError(f"{case['model']} is missing or differs from the registered hash.")
            if case["sku"] not in variants:
                raise CommandError(f"{case['sku']} is not seeded.")
            items.append({"n": case["pair"], "case": case, "label": label, "model": case["model"],
                          "model_entry": entry if "source" in entry else models[case["model"]],
                          "model_path": Path(path), "sku": case["sku"], "variant": variants[case["sku"]],
                          "category": "tops", "provider": provider, "providers": [provider]})
        return items, provider

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        plan, plan_sha = self.load_plan(root, Path(options["plan"]))
        items, provider = self.items(plan)
        if options["publish"]:
            self.publish(Path(options["out"]) if options["out"] else root / EVIDENCE / plan["version"], plan, plan_sha, items)
            return
        estimate = sum(case["estimated_units"] for case in plan["cases"])
        committed = preview_jobs.committed_units()
        self.stdout.write(f"Provider: {provider.name} {provider.version}. Cases: {len(items)}. Estimate: {estimate} units. "
                          f"Committed: {committed} of {settings.PREVIEW_UNIT_CEILING}.")
        for item in items:
            self.stdout.write(f"  {item['n']:>2}. {item['model']} x {item['sku']} ({item['case']['role']}, {item['label']})")
        if not options["execute"]:
            self.stdout.write("Dry run. Nothing was written. Add --execute --confirm-units N to run.")
            return
        if estimate > plan["budget"]["approved_cap_units"]:
            raise CommandError("The estimate exceeds the approved cap.")
        if preview_jobs.control().paused:
            raise CommandError("Previews are paused.")
        if committed + estimate > settings.PREVIEW_UNIT_CEILING:
            raise CommandError("Not enough room under PREVIEW_UNIT_CEILING. Nothing was sent.")
        today = preview_jobs.committed_units(started_at__gte=preview_jobs._day_start())
        if (not plan["budget"].get("outside_daily_caps")
                and today + estimate > max(settings.PREVIEW_DAILY_UNIT_CAP, settings.JUDGE_DAILY_UNIT_CAP)):
            raise CommandError("Not enough room under today's unit cap. Nothing was sent.")
        if options["confirm_units"] != estimate:
            raise CommandError(f"Re-run with --confirm-units {estimate} to proceed. Nothing was sent.")
        for item in items:
            session = self.session_for(item["label"], item)
            if session.preferences != plan["request"]:
                session.preferences = plan["request"]
                session.preference_version += 1
                session.save(update_fields=["preferences", "preference_version"])
            job = self.job_for(item["label"], item)
            if job.state != "pending" and (job.state != "pending" or job.started_at):
                self.stdout.write(f"  {item['n']:>2}. {item['model']} x {item['sku']}: already {job.state}; not attempted again.")
                continue
            started = time.monotonic()
            state = self.drive(job)
            gate_finished(job.id)
            job.refresh_from_db()
            self.stdout.write(f"  {item['n']:>2}. {item['model']} x {item['sku']}: {state} in {time.monotonic() - started:.0f}s, "
                              f"gate {job.gate_status or '-'} print {job.print_ratio} outside {job.outside_change_pct}% colour {job.colour_check}")
        self.stdout.write("Done. Reconcile units with manage.py reconcile_units, then publish with --publish.")

    def publish(self, folder, plan, plan_sha, items):
        folder.mkdir(parents=True, exist_ok=True)
        rows = []
        for item in items:
            session = self.session_for(item["label"], item)
            job = PreviewJob.objects.filter(owner_session=session, variant=item["variant"]).order_by("created_at").first()
            case = dict(item["case"])
            if job is None:
                rows.append({**case, "state": "not attempted", "attempts": 0})
                continue
            person_file = f"{item['model']}-input.jpg"
            if not (folder / person_file).exists():
                (folder / person_file).write_bytes(read_private_image(job.person_asset.storage_key))
            case.update({"state": job.state, "attempts": 1, "provider": job.provider, "provider_version": job.provider_version,
                         "task_id_sha256": digest(job.provider_task_id.encode()) if job.provider_task_id else None,
                         "elapsed_seconds": round((job.ended_at - job.started_at).total_seconds(), 3) if job.ended_at and job.started_at else None,
                         "observed_units": job.observed_units, "error_code": job.error_code,
                         "person_file": person_file, "person_sha256": digest((folder / person_file).read_bytes()),
                         "job_measurements": {"colour_check": job.colour_check, "colour_delta": job.colour_delta,
                                              "print_ratio": job.print_ratio, "outside_change_pct": job.outside_change_pct,
                                              "gate_status": job.gate_status, "gate_note": job.gate_note}})
            if job.state == "succeeded" and job.result_asset:
                result_file = f"{item['n']:02d}-{item['model']}-{item['sku']}.jpg"
                (folder / result_file).write_bytes(read_private_image(job.result_asset.storage_key))
                case.update({"result_file": result_file, "result_sha256": digest((folder / result_file).read_bytes()),
                             "person_image": f"exchange/evidence/{plan['version']}/{person_file}",
                             "result_image": f"exchange/evidence/{plan['version']}/{result_file}"})
            rows.append(case)
        report = {"version": plan["version"], "plan_sha256": plan_sha, "executed_at_utc": timezone.now().isoformat(),
                  "request": plan["request"], "thresholds": plan["thresholds"], "cases": rows,
                  "summary": {"planned": len(rows), "succeeded": sum(r["state"] == "succeeded" for r in rows),
                              "failed": sum(r["state"] == "failed" for r in rows), "uncertain": sum(r["state"] == "uncertain" for r in rows),
                              "not_attempted": sum(r["state"] == "not attempted" for r in rows),
                              "units": sum(r.get("observed_units") or 0 for r in rows)},
                  "note": "Job-time measurements are kept for provenance; the public gate report re-measures these files with the same code."}
        (folder / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        self.stdout.write(f"Published {report['summary']} to {folder}.")
