import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from exchange import preview_jobs
from exchange.models import OrderLine, PreviewJob
from secondlook.youcam_fake import API, FakeYouCamTransport
from secondlook.youcam_provider import HAIR_COLOR_PATH, YouCamHairColorProvider

REAL_SOURCES = Path(__file__).resolve().parents[1] / "assets" / "pixabay_sources.json"


def jpeg(colour):
    out = io.BytesIO()
    Image.new("RGB", (640, 640), colour).save(out, format="JPEG")
    return out.getvalue()


class HairColourTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        patch = override_settings(PRIVATE_MEDIA_ROOT=self.media, PREVIEW_POLL_SECONDS=0)
        patch.enable()
        self.addCleanup(patch.disable)

    def send(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json")

    def start(self, **prefs):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        line = OrderLine.objects.get(token="DEMO-ORDER-001-HAIR")
        data = self.send("put", f"/api/sessions/{sid}/item", {"line_id": line.id}).json()
        self.assertEqual([r["code"] for r in data["reasons"]], ["shade", "reaction", "damaged", "changed_mind"])
        self.assertEqual((data["order"]["category_name"], data["order"]["opened"]), ("Hair colour", True))
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": "shade"}).json()["next"],
                         "shade_preferences")
        if prefs:
            response = self.send("put", f"/api/sessions/{sid}/preferences", {"max_top_up_minor": 500, **prefs})
            self.assertEqual(response.status_code, 200, response.content)
        return sid

    def previews(self, sid, mode="live", model="model-a"):
        skus = [c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]]
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model})
        self.assertEqual(asset.status_code, 201, asset.content)
        return self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": skus, "consent": True,
                         "asset_id": asset.json()["asset_id"], "include_original": True, "mode": mode}).json()

    def test_too_brassy_finds_the_two_cooler_blondes_in_the_same_line(self):
        sid = self.start()
        words = self.send("post", f"/api/sessions/{sid}/interpret", {"text": "It went brassy and orange on me."}).json()
        self.assertEqual(words["proposal"], {"tone": "cooler"})
        self.send("put", f"/api/sessions/{sid}/preferences", {"tone": "cooler", "max_top_up_minor": 500})
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual([c["sku"] for c in data["candidates"]], ["HAIR-TONE-7.1", "HAIR-TONE-6.1"])
        # The lighter ash blonde turns too little towards cool by hue to count, and the golden shades turn warmer.
        self.assertEqual({x["code"]: x["count"] for x in data["exclusions"]}, {"not_tone": 3})
        self.assertEqual(data["candidates"][0]["shade_name"], "7.1 Ash Blonde")
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "HAIR-TONE-7.1"}).json()
        self.assertEqual((quote["kind"], quote["top_up_minor"]), ("shade", 0))

    def test_a_hair_colour_comparison_colours_the_hair_on_a_half_body_demo_person(self):
        fake = FakeYouCamTransport(result_image=jpeg((164, 146, 122)), units_per_task=1)
        sid = self.start(tone="cooler")
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-hair": lambda: YouCamHairColorProvider("test-only-key", base_url=API, transport=fake)}):
            data = self.previews(sid)
        self.assertEqual(data["provider"], "youcam-hair")
        self.assertEqual([(j["sku"], j["state"]) for j in data["jobs"]],
                         [("HAIR-TONE-7.3", "succeeded"), ("HAIR-TONE-7.1", "succeeded"), ("HAIR-TONE-6.1", "succeeded")])
        self.assertEqual([path for path, _ in fake.tasks], [HAIR_COLOR_PATH] * 3)
        self.assertEqual([payload["palettes"][0]["color"] for _, payload in fake.tasks], ["#c08f45", "#9f8572", "#7a6b62"])
        # Only the person photo is uploaded: a shade has no product photo.
        self.assertEqual(len(fake.uploads), 3)
        self.assertEqual(preview_jobs.committed_units(), 3)
        self.assertIn("Shades on screen vary", data["jobs"][1]["label"])

    def test_hair_colour_needs_a_half_body_demo_person_with_the_hair_in_view(self):
        sid = self.start(tone="cooler")
        refused = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "face-a"})
        self.assertEqual(refused.json()["error"]["code"], "MODEL_NOT_SUITABLE")
        self.assertIn("hair colour need one of the half-body demo models", refused.json()["error"]["message"])

    def test_the_recorded_hair_shades_replay_on_model_a_for_no_units_and_say_what_is_not_covered(self):
        sid = self.start(tone="cooler")
        data = self.previews(sid, mode="recorded")
        self.assertEqual(data["provider"], "replay")
        self.assertEqual([(j["sku"], j["state"]) for j in data["jobs"]],
                         [("HAIR-TONE-7.3", "succeeded"), ("HAIR-TONE-7.1", "succeeded"), ("HAIR-TONE-6.1", "succeeded")])
        self.assertIn("28 October 2026 hair colour pilot", data["jobs"][0]["label"])
        # The shade check is not calibrated for hair, so no reading reaches the shopper.
        self.assertTrue(all(j["colour_check"] is None for j in data["jobs"]))
        self.assertEqual(preview_jobs.committed_units(), 0)
        other = self.start(tone="cooler")
        elsewhere = self.previews(other, mode="recorded", model="model-b")
        self.assertEqual({j["error_code"] for j in elsewhere["jobs"]}, {"NOT_RECORDED"})
        self.assertIn("no recorded result for this shade on this person", elsewhere["jobs"][0]["message"])

    def test_the_evidence_page_shows_the_hair_comparison_and_the_finding(self):
        from django.contrib.staticfiles import finders
        html = self.client.get("/evidence").content.decode()
        for text in ("Hair colour pilot", "Hair colour on Model A", "7.3 Golden Blonde, the shade bought",
                     "not calibrated for hair", "tints hair rather than lifting it", "The other 1 result stays private"):
            self.assertIn(text, html)
        data = json.loads(Path(finders.find("exchange/evidence/hair.json")).read_text())
        self.assertEqual([r["sku"] for r in data["results"]], ["HAIR-TONE-7.3", "HAIR-TONE-7.1", "HAIR-TONE-6.1"])
        self.assertNotIn("task_id", json.dumps(data))
        for r in data["results"]:
            self.assertIsNotNone(finders.find(f"exchange/evidence/{r['result_file']}"))

    def test_the_hair_pilot_runs_four_shades_on_two_demo_people_for_four_units(self):
        out = io.StringIO()
        call_command("run_feasibility", "--set", "hair", "--sources", str(REAL_SOURCES), stdout=out)
        self.assertIn("On YouCam these pairs cost 4 units: AI Hair Color 4.", out.getvalue())
        self.assertIn("4. model-c x HAIR-TONE-9.1 (9.1 Very Light Ash Blonde #d3bcaa, permanent) on AI Hair Color",
                      out.getvalue())
        fake = FakeYouCamTransport(result_image=jpeg((164, 146, 122)), units_per_task=1)
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=4, PREVIEW_DAILY_UNIT_CAP=4), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-hair": lambda: YouCamHairColorProvider("test-only-key", base_url=API, transport=fake)}):
            call_command("run_feasibility", "--set", "hair", "--sources", str(REAL_SOURCES), "--execute",
                         "--confirm-units", "4", stdout=io.StringIO())
        report = json.loads((self.media / "feasibility" / "reports" / "hair-pilot" / "report.json").read_text())
        self.assertEqual((report["summary"]["succeeded"], report["summary"]["units"]["observed"]), (4, 4))
        self.assertIn("colours the hair alone", report["summary"]["gate"])
        self.assertEqual(report["pairs"][0]["inputs"]["shade"]["feature"], "AI Hair Color")
        self.assertIn("shade_check", report["pairs"][0])
        self.assertEqual(PreviewJob.objects.filter(provider="youcam-hair").count(), 4)
