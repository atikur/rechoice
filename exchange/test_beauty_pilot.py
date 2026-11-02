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
from exchange.models import ExchangeSession, PreviewJob
from secondlook.youcam_fake import API, FakeYouCamTransport
from secondlook.youcam_provider import YouCamMakeupProvider, YouCamNailProvider

REAL_SOURCES = Path(__file__).resolve().parents[1] / "assets" / "pixabay_sources.json"


def jpeg(color):
    out = io.BytesIO()
    Image.new("RGB", (640, 640), color).save(out, format="JPEG")
    return out.getvalue()


class BeautyPilotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media = Path(self.tmp.name)
        patch = override_settings(PRIVATE_MEDIA_ROOT=self.media, PREVIEW_POLL_SECONDS=0)
        patch.enable()
        self.addCleanup(patch.disable)

    def run_command(self, *args, sources=None):
        out = io.StringIO()
        call_command("run_feasibility", "--set", "beauty", "--label", "b1", "--sources", str(sources or REAL_SOURCES),
                     *args, stdout=out)
        return out.getvalue()

    def report(self):
        return json.loads((self.media / "feasibility" / "reports" / "b1" / "report.json").read_text())

    def youcam(self, fake, ceiling):
        stack = [override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=ceiling, PREVIEW_DAILY_UNIT_CAP=ceiling),
                 mock.patch.dict(preview_jobs.PROVIDERS, {
                     "youcam-makeup": lambda: YouCamMakeupProvider("test-only-key", base_url=API, transport=fake),
                     "youcam-nail": lambda: YouCamNailProvider("test-only-key", base_url=API, transport=fake)})]
        for patch in stack:
            patch.start() if hasattr(patch, "start") else patch.enable()
        self.addCleanup(lambda: [p.stop() if hasattr(p, "stop") else p.disable() for p in reversed(stack)])

    def test_dry_run_prints_the_thirteen_pairs_and_their_youcam_cost_and_writes_nothing(self):
        output = self.run_command()
        self.assertIn("Pairs: 13. Unit estimate: 0.", output)
        self.assertIn("On YouCam these pairs cost 13 units: Makeup VTO 10, Nail VTO 3.", output)
        self.assertIn("1. face-a x LIP-VELVET-230 (230 Terracotta #b5532f, matte) on Makeup VTO", output)
        self.assertIn("11. hand-a x NAIL-GLOSS-01 (01 Coral Crush #ef6f5a, gloss) on Nail VTO", output)
        self.assertIn("Dry run. Nothing was written.", output)
        self.assertFalse(PreviewJob.objects.exists())
        self.assertFalse((self.media / "feasibility").exists())

    def test_mock_run_uses_one_session_per_person_and_item_and_writes_a_shade_report(self):
        self.run_command("--execute")
        self.assertEqual(PreviewJob.objects.filter(state="succeeded").count(), 13)
        sessions = {s.browser_session_key.split(":", 2)[2]: s.order_line.token
                    for s in ExchangeSession.objects.filter(browser_session_key__startswith="feasibility:b1:")}
        self.assertEqual(sessions, {"face-a:lip": "DEMO-ORDER-001-LIP", "face-a:foundation": "DEMO-ORDER-001-FDN",
                                    "face-b:lip": "DEMO-ORDER-001-LIP", "face-b:foundation": "DEMO-ORDER-001-FDN",
                                    "face-c:lip": "DEMO-ORDER-001-LIP", "hand-a:nail": "DEMO-ORDER-001-NAIL"})
        report = self.report()
        self.assertEqual((report["summary"]["set"], report["summary"]["succeeded"]), ("beauty", 13))
        self.assertIn("reads as the requested shade", report["summary"]["gate"])
        first = report["pairs"][0]
        self.assertEqual(first["inputs"]["shade"], {"sku": "LIP-VELVET-230", "name": "230 Terracotta", "hex": "#b5532f",
                                                    "finish": "matte", "category": "lip", "feature": "Makeup VTO"})
        self.assertEqual(first["inputs"]["model_file"], "exchange/static/exchange/models/face-a.jpg")
        self.assertIsNone(first["review"]["shade_match"])
        self.assertIn("Shade: 230 Terracotta, #b5532f, matte, on Makeup VTO.",
                      (self.media / "feasibility" / "reports" / "b1" / "report.md").read_text())

    def test_probes_first_then_the_rest_on_the_fake_api_for_exactly_thirteen_units(self):
        fake = FakeYouCamTransport(result_image=jpeg((160, 70, 80)), units_per_task=1)
        self.youcam(fake, ceiling=13)
        with self.assertRaisesMessage(CommandError, "--confirm-units 13"):
            self.run_command("--execute")
        self.assertEqual(fake.calls, [])
        self.run_command("--execute", "--pairs", "1,7,11", "--confirm-units", "3")
        self.assertEqual([(path.rsplit("/", 1)[1], payload["effects"][0].get("category", "nail"))
                          for path, payload in fake.tasks],
                         [("makeup-vto", "lip_color"), ("makeup-vto", "foundation"), ("nail-vto", "nail")])
        self.assertEqual(self.report()["summary"]["not_attempted"], 10)
        with self.assertRaisesMessage(CommandError, "only 10 remain"):
            self.run_command("--execute", "--confirm-units", "13")
        self.run_command("--execute", "--pairs", "2,3,4,5,6,8,9,10,12,13", "--confirm-units", "10")
        sent = [payload["effects"][0]["palettes"][0]["color"] if "palettes" in payload["effects"][0]
                else payload["effects"][0]["color"] for _, payload in fake.tasks]
        self.assertEqual(len(sent), 13)
        self.assertEqual(set(sent), {"#b5532f", "#d9ae7f", "#ef6f5a", "#a04a55", "#9c5a6e", "#c4707f", "#dcb2a3",
                                     "#b8877a", "#7e5a43", "#b3203a", "#b9a3d6"})
        summary = self.report()["summary"]
        self.assertEqual((summary["succeeded"], summary["units"]["reserved"], summary["units"]["observed"]), (13, 13, 13))
        self.assertEqual(summary["provider"], "youcam-makeup, youcam-nail")
        self.assertTrue(all(row["result_file"] and row["shade_check"]["status"] for row in self.report()["pairs"]))
        self.assertEqual(preview_jobs.committed_units(), 13)

    def test_an_offline_reconcile_keeps_the_providers_that_actually_ran(self):
        fake = FakeYouCamTransport(result_image=jpeg((160, 70, 80)), units_per_task=1)
        self.youcam(fake, ceiling=13)
        self.run_command("--execute", "--pairs", "1,11", "--confirm-units", "2")
        history = self.media / "history.json"
        history.write_text(json.dumps({"status": 200, "result": {"history": []}}))
        with override_settings(PREVIEW_PROVIDER="mock"):
            self.run_command("--reconcile", str(history))
        summary = self.report()["summary"]
        self.assertEqual((summary["provider"], summary["provider_version"]),
                         ("youcam-makeup, youcam-nail", "makeup-vto-1.1, nail-vto-1.1"))

    def test_zero_ceiling_blocks_a_paid_beauty_run_before_any_request(self):
        fake = FakeYouCamTransport(units_per_task=1)
        self.youcam(fake, ceiling=0)
        with self.assertRaisesMessage(CommandError, "only 0 remain under PREVIEW_UNIT_CEILING"):
            self.run_command("--execute", "--confirm-units", "13")
        self.assertEqual(fake.calls, [])
        self.assertFalse(PreviewJob.objects.exists())

    def test_a_pair_on_the_wrong_kind_of_demo_person_or_a_changed_file_stops_before_any_write(self):
        sources = json.loads(REAL_SOURCES.read_text())
        sources["beauty_pairs"][10]["model"] = "face-a"
        wrong = self.media / "sources.json"
        wrong.write_text(json.dumps(sources))
        with self.assertRaisesMessage(CommandError, "Pair 11 previews nail on face-a, but it needs a hand."):
            self.run_command("--execute", sources=wrong)
        with mock.patch("exchange.management.commands.run_feasibility.sha256", return_value="0" * 64), \
                self.assertRaisesMessage(CommandError, "face-a.jpg does not match its recorded hash"):
            self.run_command("--execute")
        self.assertFalse(PreviewJob.objects.exists())
