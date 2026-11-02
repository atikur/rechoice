import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from PIL import Image

from exchange import preview_jobs
from exchange.models import ExchangeSession, PreviewJob, PrivateAsset
from secondlook.youcam_fake import API, FakeYouCamTransport, reply
from secondlook.youcam_provider import TASK_PATH, YouCamClothesProvider

REAL_SOURCES = Path(__file__).resolve().parents[1] / "assets" / "pixabay_sources.json"


def jpeg(color):
    out = io.BytesIO()
    Image.new("RGB", (640, 800), color).save(out, format="JPEG")
    return out.getvalue()


class FeasibilityRunnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        media = Path(self.tmp.name)
        self.settings_patch = override_settings(PRIVATE_MEDIA_ROOT=media, PREVIEW_POLL_SECONDS=0)
        self.settings_patch.enable()
        self.addCleanup(self.settings_patch.disable)
        sources = json.loads(REAL_SOURCES.read_text())
        (media / "feasibility").mkdir()
        for i, model in enumerate(sources["models"]):
            data = jpeg((60 + i * 30, 90, 120))
            (media / "feasibility" / f"{model['name']}.jpg").write_bytes(data)
            model["source"]["source_sha256"] = hashlib.sha256(data).hexdigest()
        self.sources = media / "sources.json"
        self.sources.write_text(json.dumps(sources))
        self.media = media

    def run_command(self, *args):
        out = io.StringIO()
        call_command("run_feasibility", "--sources", str(self.sources), "--label", "t1", *args, stdout=out)
        return out.getvalue()

    def report(self):
        return json.loads((self.media / "feasibility" / "reports" / "t1" / "report.json").read_text())

    def youcam(self, fake, ceiling):
        adapter = lambda: YouCamClothesProvider("test-only-key", base_url=API, transport=fake)
        stack = [override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=ceiling,
                                   PREVIEW_DAILY_UNIT_CAP=ceiling),
                 mock.patch.dict(preview_jobs.PROVIDERS, {"youcam": adapter})]
        for patch in stack:
            patch.start() if hasattr(patch, "start") else patch.enable()
        self.addCleanup(lambda: [p.stop() if hasattr(p, "stop") else p.disable() for p in reversed(stack)])

    def test_dry_run_writes_nothing(self):
        output = self.run_command()
        self.assertIn("Pairs: 10. Unit estimate: 0.", output)
        self.assertIn("Dry run. Nothing was written.", output)
        self.assertFalse(PreviewJob.objects.exists())
        self.assertFalse(ExchangeSession.objects.filter(browser_session_key__startswith="feasibility:").exists())
        self.assertFalse((self.media / "feasibility" / "reports").exists())

    def test_mock_run_creates_ten_jobs_and_a_review_report(self):
        self.run_command("--execute")
        self.assertEqual(PreviewJob.objects.filter(state="succeeded").count(), 10)
        self.assertEqual(ExchangeSession.objects.filter(browser_session_key__startswith="feasibility:t1:").count(), 3)
        self.assertEqual(PrivateAsset.objects.filter(kind="person").count(), 3)
        report = self.report()
        self.assertEqual((report["summary"]["pairs"], report["summary"]["succeeded"]), (10, 10))
        self.assertEqual(report["summary"]["decision"], "pending human review")
        self.assertIsNone(report["pairs"][0]["review"]["usefulness"])
        self.assertTrue((self.media / "feasibility" / "reports" / "t1" / "report.md").exists())

    def test_rerun_reuses_jobs_and_keeps_review_notes(self):
        self.run_command("--execute")
        path = self.media / "feasibility" / "reports" / "t1" / "report.json"
        data = json.loads(path.read_text())
        data["pairs"][1]["review"]["usefulness"] = "usable"
        path.write_text(json.dumps(data))
        output = self.run_command("--execute")
        self.assertIn("from an earlier run", output)
        self.assertEqual(PreviewJob.objects.count(), 10)
        self.assertEqual(self.report()["pairs"][1]["review"]["usefulness"], "usable")

    def test_changed_or_missing_model_files_stop_before_any_write(self):
        (self.media / "feasibility" / "model-b.jpg").write_bytes(jpeg((1, 2, 3)))
        with self.assertRaisesMessage(CommandError, "model-b.jpg does not match its recorded hash"):
            self.run_command("--execute")
        (self.media / "feasibility" / "model-b.jpg").unlink()
        with self.assertRaisesMessage(CommandError, "model-b.jpg is missing"):
            self.run_command("--execute")
        self.assertFalse(PreviewJob.objects.exists())

    def test_zero_ceiling_blocks_a_paid_run_before_any_request(self):
        fake = FakeYouCamTransport()
        self.youcam(fake, ceiling=0)
        with self.assertRaisesMessage(CommandError, "only 0 remain under PREVIEW_UNIT_CEILING"):
            self.run_command("--execute", "--confirm-units", "20")
        self.assertEqual(fake.calls, [])
        self.assertFalse(PreviewJob.objects.exists())

    def test_paid_run_needs_the_exact_unit_confirmation(self):
        fake = FakeYouCamTransport(result_image=jpeg((200, 120, 60)))
        self.youcam(fake, ceiling=20)
        for confirm in ([], ["--confirm-units", "19"]):
            with self.subTest(confirm=confirm), self.assertRaisesMessage(CommandError, "--confirm-units 20"):
                self.run_command("--execute", *confirm)
        self.assertEqual(fake.calls, [])
        self.run_command("--execute", "--confirm-units", "20")
        self.assertEqual(len(fake.api_calls("POST", TASK_PATH)), 10)
        summary = self.report()["summary"]
        self.assertEqual((summary["succeeded"], summary["units"]["reserved"], summary["units"]["observed"]), (10, 20, 20))
        self.assertEqual(summary["units"]["billing"], "assumed: failed tasks counted as charged")
        self.assertTrue(all(row["result_file"] for row in self.report()["pairs"]))

    def test_single_pair_first_then_the_rest_without_resending(self):
        fake = FakeYouCamTransport(result_image=jpeg((200, 120, 60)))
        self.youcam(fake, ceiling=20)
        with self.assertRaisesMessage(CommandError, "--confirm-units 2"):
            self.run_command("--execute", "--pairs", "1", "--confirm-units", "20")
        self.run_command("--execute", "--pairs", "1", "--confirm-units", "2")
        self.assertEqual(len(fake.api_calls("POST", TASK_PATH)), 1)
        self.assertEqual(self.report()["summary"]["not_attempted"], 9)
        with self.assertRaisesMessage(CommandError, "only 18 remain"):
            self.run_command("--execute", "--confirm-units", "20")
        self.run_command("--execute", "--pairs", "2,3,4,5,6,7,8,9,10", "--confirm-units", "18")
        self.assertEqual(len(fake.api_calls("POST", TASK_PATH)), 10)
        self.assertEqual(self.report()["summary"]["succeeded"], 10)

    def test_run_stops_after_repeated_failures(self):
        fake = FakeYouCamTransport(file_api=reply(401, {"error_code": "InvalidAccessToken"}))
        self.youcam(fake, ceiling=20)
        output = self.run_command("--execute", "--confirm-units", "20", "--stop-after-failures", "2")
        self.assertIn("Stopped after 2 failed or uncertain pairs in a row.", output)
        self.assertEqual(PreviewJob.objects.count(), 2)
        self.assertEqual(self.report()["summary"]["not_attempted"], 8)
        self.assertEqual(fake.api_calls("POST", TASK_PATH), [])

    def test_reconcile_records_what_each_task_actually_cost(self):
        fake = FakeYouCamTransport(result_image=jpeg((200, 120, 60)))
        self.youcam(fake, ceiling=20)
        self.run_command("--execute", "--confirm-units", "20")
        PreviewJob.objects.filter(provider_task_id__in=["task-1", "task-2", "task-10"]).update(observed_units=9)
        history = {"status": 200, "result": {"history": [
            {"target_id": "task-1:176089673031", "info": {"credits": [{"amount_dec": -2.0}]}},
            {"target_id": "task-2", "info": {"credits": [{"amount_dec": -1.5}]}},
            {"target_id": "task-10:176089673099", "info": {"credits": [{"amount_dec": -2.0}]}},
            {"target_id": "order-9", "info": {"credits": [{"amount_dec": 100.0}]}}]}}
        saved = self.media / "history.json"
        saved.write_text(json.dumps(history))
        output = self.run_command("--reconcile", str(saved))
        self.assertIn("Recorded actual charges for 3 tasks.", output)
        self.assertIn("7 tasks had no matching charge in the history; their counts were left unchanged.", output)
        units = dict(PreviewJob.objects.values_list("provider_task_id", "observed_units"))
        self.assertEqual((units["task-1"], units["task-2"], units["task-10"], units["task-3"]), (2, 2, 2, 2))
        self.assertEqual(preview_jobs.committed_units(), 20)
        self.assertEqual(self.report()["summary"]["units"]["billing"], "reconciled from unit history")

    def test_unreadable_result_fails_and_still_counts_units(self):
        fake = FakeYouCamTransport(result_image=b"not an image")
        self.youcam(fake, ceiling=20)
        output = self.run_command("--execute", "--confirm-units", "20", "--stop-after-failures", "1")
        self.assertIn("failed (RESULT_UNREADABLE)", output)
        job = PreviewJob.objects.get()
        self.assertEqual((job.reserved_units, job.observed_units), (2, 2))
        self.assertEqual(preview_jobs.committed_units(), 2)
