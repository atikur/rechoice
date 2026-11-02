"""Prepare or execute the separately registered exchange-agent pilot. Never generates YouCam images."""
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from django.utils import timezone

from exchange import agent_evaluation as evaluation, copilot, visual_review
from exchange.models import ExchangeAIBudget, ExchangeAICall, ExchangeSession, OrderLine


class Command(BaseCommand):
    help = "Run offline agent contracts; prepare a frozen pilot or execute a specifically approved run."

    def add_arguments(self, parser):
        parser.add_argument("--prepare", action="store_true")
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--run-index", type=int, choices=(1, 2), default=1)
        parser.add_argument("--max-usd")
        parser.add_argument("--approval")

    def save(self, path, value):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)

    def handle(self, *args, **options):
        if options["prepare"] and options["execute"]:
            raise CommandError("Prepare and review the pilot before executing it.")
        if settings.PREVIEW_PROVIDER != "mock":
            raise CommandError("Agent evaluation requires the mock YouCam backend.")
        offline = evaluation.offline_contracts()
        work = Path(settings.PRIVATE_MEDIA_ROOT) / "agent-evaluation"
        self.save(work / "offline.json", offline)
        if offline["passed"] != offline["planned"]:
            raise CommandError("Offline contract failures; no paid run is allowed.")
        plan = evaluation.manifest()
        source_files = ("exchange/copilot.py", "exchange/visual_review.py", "exchange/ai_client.py",
                        "exchange/agent_evaluation.py", "exchange/management/commands/evaluate_agents.py")
        plan["source_sha256"] = {name: hashlib.sha256((settings.BASE_DIR / name).read_bytes()).hexdigest() for name in source_files}
        plan.update(run_index=options["run_index"], maximum_runs=2,
                    maximum_pilot_generation_calls=2 * plan["maximum_generation_calls"], maximum_cumulative_usd="3.00")
        encoded = json.dumps(plan, sort_keys=True, indent=2) + "\n"
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        registration = settings.BASE_DIR / "assets" / f"exchange_ai_pilot_v1_run{options['run_index']}.json"
        report_path = work / f"run-{options['run_index']}.json"
        self.stdout.write(f"Offline contracts: {offline['passed']}/{offline['planned']}. "
                          f"Pilot: 6 copilot cases, 6 public-image cases reviewed by two models; "
                          f"maximum {plan['maximum_generation_calls']} generation calls per run, "
                          f"conservative per-run reservations USD {plan['worst_case_reservations_usd']}. No YouCam calls.")
        if options["prepare"]:
            if report_path.exists():
                raise CommandError("An executed registration cannot be replaced.")
            if registration.exists() and registration.read_text() != encoded:
                raise CommandError("This registration already exists with different inputs or code.")
            registration.write_text(encoded)
            registration.with_suffix(".sha256").write_text(digest + "\n")
            self.stdout.write(f"Prepared {registration.name}; SHA-256 {digest}. No model call made.")
            return
        if not options["execute"]:
            self.stdout.write("Dry run only. --prepare registers exact inputs and prompts for review.")
            return
        if not registration.exists() or registration.read_text() != encoded or registration.with_suffix(".sha256").read_text().strip() != digest:
            raise CommandError("Prepared inputs, prompts, catalog or code changed; do not execute this registration.")
        if report_path.exists():
            raise CommandError("This run already has a report; it cannot be retried or overwritten.")
        if options["run_index"] == 2 and not (work / "run-1.json").exists():
            raise CommandError("Run 2 requires a retained run-1 report.")
        if ExchangeAICall.objects.filter(state="uncertain").exists():
            raise CommandError("An earlier call has an uncertain outcome. Resolve it before another paid run.")
        try:
            maximum = Decimal(options["max_usd"] or "0")
            valid = maximum.is_finite() and 0 < maximum <= Decimal("3")
        except InvalidOperation:
            valid = False
        if not valid or not options["approval"] or not os.environ.get("ANTHROPIC_API_KEY"):
            raise CommandError("Execution needs explicit approval, --max-usd up to 3 cumulative, and a process API key.")
        report = {"version": evaluation.VERSION, "registration_sha256": digest, "run_index": options["run_index"],
                  "started_at": timezone.now().isoformat(), "offline_contracts": {k: v for k, v in offline.items() if k != "cases"},
                  "agent_cases": [{"id": c["id"], "state": "not_attempted"} for c in evaluation.AGENT_CASES],
                  "visual_cases": [{"id": c["id"], "state": "not_attempted"} for c in evaluation.case_bundles()],
                  "independent_human_labels": 0, "new_youcam_calls": 0, "stopped": "", "completed": False}
        self.save(report_path, report)
        start_id = ExchangeAICall.objects.order_by("-id").values_list("id", flat=True).first() or 0
        overrides = dict(EXCHANGE_AI_PROVIDER="claude", EXCHANGE_AI_APPROVAL=options["approval"],
            EXCHANGE_AI_MAX_USD=str(maximum), EXCHANGE_AI_DAILY_CALLS=100, EXCHANGE_AI_SESSION_CALLS=12,
            EXCHANGE_AI_JUDGE_ONLY=True, EXCHANGE_AI_MODEL=plan["agent_model"],
            ANTHROPIC_API_KEY=os.environ["ANTHROPIC_API_KEY"])
        try:
            with override_settings(**overrides):
                line = OrderLine.objects.select_related("variant").get(token="DEMO-ORDER-001")
                for number, case in enumerate(evaluation.AGENT_CASES):
                    session = ExchangeSession.objects.create(order_line=line, browser_session_key="registered-agent-pilot",
                        status="FEASIBILITY", judge=True, return_reason="style", preference_version=1,
                        preferences={"quieter_print": True, "sleeve_required": "long", "currency": "USD", "max_top_up_minor": case["budget"]},
                        expires_at=timezone.now() + timezone.timedelta(hours=24))
                    result = copilot.plan(session, revisit=case["revisit"])
                    success = result["reader"] == "claude" and result["status"] == case["expected"]
                    report["agent_cases"][number] = {"id": case["id"], "state": "completed" if success else "failed",
                        "expected": case["expected"], "result": result}
                    self.save(report_path, report)
                    if not success:
                        raise CommandError("A copilot case did not complete its registered model/tool contract.")
                for number, case in enumerate(evaluation.case_bundles()):
                    row = report["visual_cases"][number]
                    row.update(identity=case["bundle"]["identity"], prompt_sha256=visual_review.PROMPT_SHA,
                               sources=case["bundle"]["sources"], cohort=case["cohort"], reviews=[])
                    for model in (plan["primary_review_model"], plan["second_review_model"]):
                        result = visual_review.review_bundle(case["bundle"], None, model)
                        row["reviews"].append(result)
                        row["state"] = "in_progress"
                        self.save(report_path, report)
                        if result["status"] != "ASSESSED":
                            row["state"] = "failed_or_uncertain"
                            raise CommandError("A visual assessment did not complete; stop without retry.")
                    row["state"] = "completed"
                    a, b = row["reviews"]
                    findings_a = {c["attribute"]: c["finding"] for c in a["checks"]}
                    findings_b = {c["attribute"]: c["finding"] for c in b["checks"]}
                    row["same_finding"] = {key: findings_a[key] == findings_b[key] for key in findings_a}
                    self.save(report_path, report)
                report["completed"] = True
        except Exception as exc:
            report["stopped"] = type(exc).__name__
        finally:
            calls = ExchangeAICall.objects.filter(id__gt=start_id)
            report["calls"] = list(calls.values("feature", "model", "state", "reserved_usd", "charged_usd", "input_tokens", "output_tokens"))
            for call in report["calls"]:
                for field in ("reserved_usd", "charged_usd"):
                    call[field] = str(call[field])
            budget = ExchangeAIBudget.objects.filter(key="exchange-ai").first()
            report["cumulative_charged_usd"] = str(budget.charged_usd if budget else 0)
            report["cumulative_reserved_usd"] = str(budget.reserved_usd if budget else 0)
            report["ended_at"] = timezone.now().isoformat()
            self.save(report_path, report)
        self.stdout.write(f"Report retained: {report_path}. Completed: {report['completed']}. "
                          f"Charged USD {report['cumulative_charged_usd']}; reserved USD {report['cumulative_reserved_usd']}.")
        if not report["completed"]:
            raise CommandError("Registered run stopped. Preserve the report and inspect before further work.")
