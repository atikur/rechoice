import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw

from exchange import preview_jobs
from exchange.models import OrderLine, PreviewJob
from secondlook.youcam_fake import API, FakeYouCamTransport
from secondlook.youcam_provider import NECKLACE_PATH, YouCamNecklaceProvider

REAL_SOURCES = Path(__file__).resolve().parents[1] / "assets" / "pixabay_sources.json"
MODEL_A = Path(__file__).resolve().parent / "static/exchange/models/model-a.jpg"


def jpeg(colour):
    out = io.BytesIO()
    Image.new("RGB", (640, 640), colour).save(out, format="JPEG")
    return out.getvalue()


class NecklaceTests(TestCase):
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

    def send(self, method, path, payload=None, **headers):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}),
                                            content_type="application/json", **headers)

    def start(self, reason="look", **prefs):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        line = OrderLine.objects.get(token="DEMO-ORDER-001-NECK")
        data = self.send("put", f"/api/sessions/{sid}/item", {"line_id": line.id}).json()
        self.assertEqual(data["reasons"][0]["label"], "I don't like how it looks on me")
        if reason:
            self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": reason}).status_code, 200)
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

    def test_too_long_and_wanted_gold_finds_the_short_gold_pendant(self):
        sid = self.start(reason=None)
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": "look"}).json()["next"],
                         "jewellery_preferences")
        words = self.send("post", f"/api/sessions/{sid}/interpret",
                          {"text": "It's too long and I wanted gold. Nothing extra please."}).json()
        self.assertEqual(words["proposal"], {"size": "smaller", "metal": "gold", "max_top_up_minor": 0})
        self.assertEqual(words["understood"], ["a shorter necklace", "gold", "nothing extra"])
        self.send("put", f"/api/sessions/{sid}/preferences", {"size": "smaller", "metal": "gold", "max_top_up_minor": 500})
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual([c["sku"] for c in data["candidates"]], ["NECK-LEAF-G40"])
        none = self.send("put", f"/api/sessions/{sid}/preferences", {"size": "larger", "metal": "gold", "max_top_up_minor": 0})
        self.assertEqual(none.status_code, 200)
        self.assertIn("No necklace meets every limit", self.client.get(f"/api/sessions/{sid}/candidates").json()["message"])
        self.send("put", f"/api/sessions/{sid}/preferences", {"size": "smaller", "metal": "gold", "max_top_up_minor": 500})
        first = data["candidates"][0]
        self.assertEqual((first["details"], first["image_url"]),
                         ("Gold · drop · 40 cm", "/static/exchange/jewellery/NECK-LEAF-G40.jpg"))
        self.assertIn("a gold drop necklace, 40 cm long", first["image_alt"])
        self.assertEqual(sorted(x["code"] for x in data["exclusions"]), ["not_metal", "not_size"])
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "NECK-LEAF-G40"}).json()
        self.assertEqual((quote["kind"], quote["top_up_minor"]), ("jewellery", 0))

    def test_shorter_in_either_metal_finds_the_two_short_necklaces(self):
        sid = self.start(size="smaller")
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual(sorted(c["sku"] for c in data["candidates"]), ["NECK-BLOOD-S40", "NECK-LEAF-G40"])

    def test_a_necklace_comparison_sends_each_product_photo_and_measures_every_result(self):
        person = Image.open(MODEL_A).convert("RGB")

        def drawn(height):
            out = io.BytesIO()
            image = person.copy()
            ImageDraw.Draw(image).rectangle((380, 560, 480, 560 + height), fill=(20, 20, 20))
            image.save(out, format="JPEG", quality=92)
            return out.getvalue()
        # The 55 cm necklace bought, then the 40 cm gold pendant at 40/55 of its height: in proportion.
        images = [drawn(220), drawn(160)]
        fake = FakeYouCamTransport(result_image=lambda: images.pop(0), units_per_task=1)
        sid = self.start(size="smaller", metal="gold")
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-necklace": lambda: YouCamNecklaceProvider("test-only-key", base_url=API, transport=fake)}):
            data = self.previews(sid)
        self.assertEqual(data["provider"], "youcam-necklace")
        self.assertEqual([(j["sku"], j["state"]) for j in data["jobs"]],
                         [("NECK-AMETHYST-S55", "succeeded"), ("NECK-LEAF-G40", "succeeded")])
        self.assertEqual([path for path, _ in fake.tasks], [NECKLACE_PATH, NECKLACE_PATH])
        self.assertEqual(len(fake.uploads), 4)
        self.assertEqual(preview_jobs.committed_units(), 2)
        self.assertIn("Length on screen is approximate", data["jobs"][1]["label"])
        self.assertEqual(data["jobs"][0]["size_check"]["status"], "size-reference")
        self.assertEqual(data["jobs"][1]["size_check"], {"status": "size-close",
                                                         "note": "Size check: renders in proportion to the pair bought."})
        self.assertIsNone(data["jobs"][1]["colour_check"])

    def test_a_necklace_needs_a_half_body_demo_person(self):
        sid = self.start(size="smaller")
        refused = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "face-a"})
        self.assertEqual(refused.json()["error"]["code"], "MODEL_NOT_SUITABLE")
        self.assertIn("necklaces and hair colour need one of the half-body demo models", refused.json()["error"]["message"])

    def test_the_recorded_necklaces_replay_on_model_a_for_no_units_and_say_what_is_not_covered(self):
        sid = self.start(size="smaller")
        data = self.previews(sid, mode="recorded")
        self.assertEqual(data["provider"], "replay")
        states = {j["sku"]: (j["state"], j["error_code"]) for j in data["jobs"]}
        self.assertEqual(states["NECK-AMETHYST-S55"], ("succeeded", ""))
        self.assertEqual(states["NECK-BLOOD-S40"], ("succeeded", ""))
        # The gold pendant's pilot result failed and was not published.
        self.assertEqual(states["NECK-LEAF-G40"], ("failed", "NOT_RECORDED"))
        leaf = next(j for j in data["jobs"] if j["sku"] == "NECK-LEAF-G40")
        self.assertIn("no recorded result for this necklace on this person", leaf["message"])
        bib = next(j for j in data["jobs"] if j["sku"] == "NECK-BLOOD-S40")
        self.assertIn("28 October 2026 necklace pilot", bib["label"])
        self.assertEqual(bib["size_check"]["status"], "size-close")
        self.assertEqual(preview_jobs.committed_units(), 0)
        other = self.start(size="smaller")
        elsewhere = self.previews(other, mode="recorded", model="model-b")
        self.assertEqual({j["error_code"] for j in elsewhere["jobs"]}, {"NOT_RECORDED"})

    def test_the_evidence_page_shows_the_necklace_comparison_and_the_failure(self):
        from django.contrib.staticfiles import finders
        html = self.client.get("/evidence").content.decode()
        for text in ("Necklace pilot", "Necklaces on Model A", "Amethyst Y Chain, the necklace bought", "size check: in proportion",
                     "pasted a beige block over the chest", "The other 1 result stays private"):
            self.assertIn(text, html)
        data = json.loads(Path(finders.find("exchange/evidence/necklace.json")).read_text())
        self.assertEqual([r["sku"] for r in data["results"]], ["NECK-AMETHYST-S55", "NECK-BLOOD-S40", "NECK-LAPIS-S45"])
        self.assertNotIn("task_id", json.dumps(data))
        for r in data["results"]:
            self.assertIsNotNone(finders.find(f"exchange/evidence/{r['result_file']}"))

    def test_the_necklace_pilot_runs_four_necklaces_on_model_a_for_four_units(self):
        out = io.StringIO()
        call_command("run_feasibility", "--set", "necklace", "--sources", str(REAL_SOURCES), stdout=out)
        self.assertIn("On YouCam these pairs cost 4 units: Necklace VTO 4.", out.getvalue())
        self.assertIn("1. model-a x NECK-AMETHYST-S55 (Amethyst Y Chain, silver drop, 550 mm) on Necklace VTO",
                      out.getvalue())
        fake = FakeYouCamTransport(result_image=jpeg((180, 150, 120)), units_per_task=1)
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=4, PREVIEW_DAILY_UNIT_CAP=4), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-necklace": lambda: YouCamNecklaceProvider("test-only-key", base_url=API, transport=fake)}):
            call_command("run_feasibility", "--set", "necklace", "--sources", str(REAL_SOURCES), "--execute",
                         "--confirm-units", "4", stdout=io.StringIO())
        report = json.loads((self.media / "feasibility" / "reports" / "necklace-pilot" / "report.json").read_text())
        self.assertEqual((report["summary"]["succeeded"], report["summary"]["units"]["observed"]), (4, 4))
        self.assertIn("hangs the necklace at the neck", report["summary"]["gate"])
        self.assertEqual(report["pairs"][0]["inputs"]["earring"]["category"], "necklace")
        self.assertEqual(PreviewJob.objects.filter(provider="youcam-necklace").count(), 4)
