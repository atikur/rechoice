import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from exchange import assistant, preview_jobs
from exchange.models import OrderLine, PreviewJob
from secondlook.youcam_fake import API, FakeYouCamTransport
from secondlook.youcam_provider import BAG_PATH, SHOES_PATH, YouCamBagProvider, YouCamShoesProvider

REAL_SOURCES = Path(__file__).resolve().parents[1] / "assets" / "pixabay_sources.json"


def jpeg(colour=(150, 120, 110)):
    out = io.BytesIO()
    Image.new("RGB", (640, 900), colour).save(out, format="JPEG")
    return out.getvalue()


def png(size=800):
    out = io.BytesIO()
    Image.new("RGB", (size, size), (180, 160, 150)).save(out, format="PNG")
    return out.getvalue()


class AccessoryTests(TestCase):
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

    def start(self, token, **prefs):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        data = self.send("put", f"/api/sessions/{sid}/item", {"line_id": OrderLine.objects.get(token=token).id}).json()
        self.reasons = [r["code"] for r in data["reasons"]]
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": "look"}).json()["next"],
                         "jewellery_preferences")
        if prefs:
            response = self.send("put", f"/api/sessions/{sid}/preferences", {"max_top_up_minor": 500, **prefs})
            self.assertEqual(response.status_code, 200, response.content)
        return sid

    def previews(self, sid, model, mode="live"):
        skus = [c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]]
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model})
        self.assertEqual(asset.status_code, 201, asset.content)
        return self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": skus, "consent": True,
                         "asset_id": asset.json()["asset_id"], "include_original": True, "mode": mode}).json()

    def youcam(self, fake):
        options = {"base_url": API, "transport": fake}
        return mock.patch.dict(preview_jobs.PROVIDERS, {
            "youcam-shoes": lambda: YouCamShoesProvider("test-only-key", units_per_job=2, **options),
            "youcam-bag": lambda: YouCamBagProvider("test-only-key", units_per_job=2, **options)})

    def test_the_heels_are_too_high_and_too_loud_finds_the_cream_peep_toes(self):
        sid = self.start("DEMO-ORDER-001-SHOE")
        self.assertEqual(self.reasons, ["look", "damaged", "changed_mind"])
        words = self.send("post", f"/api/sessions/{sid}/interpret",
                          {"text": "The heels are too high and the red is too loud. Nothing extra."}).json()
        self.assertEqual(words["proposal"], {"size": "smaller", "metal": "neutral", "max_top_up_minor": 0})
        self.assertEqual(words["understood"], ["a lower heel", "a neutral colour", "nothing extra"])
        self.send("put", f"/api/sessions/{sid}/preferences", {"size": "smaller", "metal": "neutral", "max_top_up_minor": 500})
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual([c["sku"] for c in data["candidates"]], ["SHOE-CREAM-PEEP"])
        first = data["candidates"][0]
        self.assertEqual((first["details"], first["image_url"]),
                         ("Neutral colour · peep-toe · heel 50 mm", "/static/exchange/accessories/SHOE-CREAM-PEEP.jpg"))
        self.assertEqual(first["reasons"][:2], ["lower heel: heel 50 mm instead of 100 mm", "neutral colour"])
        self.assertEqual(sorted(x["code"] for x in data["exclusions"]), ["not_metal", "not_size"])
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "SHOE-CREAM-PEEP"}).json()
        self.assertEqual(quote["top_up_minor"], 0)

    def test_shoes_and_bags_take_a_colour_family_never_a_metal_and_jewellery_the_reverse(self):
        shoes = self.start("DEMO-ORDER-001-SHOE")
        refused = self.send("put", f"/api/sessions/{shoes}/preferences", {"metal": "gold", "max_top_up_minor": 500})
        self.assertEqual(refused.json()["error"]["message"], "Choose a colour this item comes in.")
        earrings = self.start("DEMO-ORDER-001-EAR")
        refused = self.send("put", f"/api/sessions/{earrings}/preferences", {"metal": "neutral", "max_top_up_minor": 500})
        self.assertEqual(refused.status_code, 422)

    def test_shoes_preview_on_the_full_length_model_through_shoes_v3_for_two_units_each(self):
        sid = self.start("DEMO-ORDER-001-SHOE", size="smaller")
        refused = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"})
        self.assertEqual(refused.json()["error"]["message"],
                         "Shoe previews need a full-length photo with the feet showing. Choose Model F.")
        fake = FakeYouCamTransport(result_image=jpeg(), units_per_task=2)
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                self.youcam(fake):
            data = self.previews(sid, "model-f")
        self.assertEqual(data["provider"], "youcam-shoes")
        self.assertEqual([(j["sku"], j["state"]) for j in data["jobs"]],
                         [("SHOE-RED-BOOT", "succeeded"), ("SHOE-GOLD-BROGUE", "succeeded"), ("SHOE-CREAM-PEEP", "succeeded")])
        self.assertEqual({path for path, _ in fake.tasks}, {SHOES_PATH})
        self.assertEqual(set(fake.tasks[0][1]), {"src_file_id", "ref_file_id"})
        self.assertEqual(preview_jobs.committed_units(), 6)
        self.assertIn("Heel height on screen is approximate.", data["jobs"][1]["label"])

    def test_bag_try_on_is_off_after_its_pilot_and_the_adapter_never_sends_a_photo_without_a_recorded_gender(self):
        sid = self.start("DEMO-ORDER-001-BAG", size="smaller", metal="neutral")
        self.assertEqual([c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]],
                         ["BAG-TAN-MESSENGER"])
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
        refused = self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": ["BAG-TAN-MESSENGER"],
                            "consent": True, "asset_id": asset, "include_original": True, "mode": "live"})
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (409, "PREVIEW_OFF"))
        self.assertIn("dresses the person in a new outfit and setting", refused.json()["error"]["message"])
        self.assertFalse(PreviewJob.objects.exists())
        # The adapter itself: a photo with no recorded gender is refused before any upload or task.
        from secondlook.providers import PreviewInput, ProviderRejected
        fake = FakeYouCamTransport(result_image=jpeg(), units_per_task=2)
        provider = YouCamBagProvider("test-only-key", base_url=API, transport=fake)
        job = PreviewInput("job", "BAG-TAN-MESSENGER", 1, "bag", jpeg(), consent=True, consent_version=1,
                           garment_image=jpeg(), params={"category": "bag", "gender": None})
        with self.assertRaisesMessage(ProviderRejected, "DEMO_PERSON_REQUIRED"):
            provider.start(job)
        self.assertEqual((fake.tasks, fake.uploads), ([], {}))
        demo = PreviewJob(person_asset=None)
        self.assertIsNone(preview_jobs.demo_bag_gender(demo.person_asset))

    def test_the_recorded_shoes_replay_on_model_f_for_no_units(self):
        sid = self.start("DEMO-ORDER-001-SHOE", size="smaller")
        data = self.previews(sid, "model-f", mode="recorded")
        self.assertEqual(data["provider"], "replay")
        self.assertEqual([(j["sku"], j["state"]) for j in data["jobs"]],
                         [("SHOE-RED-BOOT", "succeeded"), ("SHOE-GOLD-BROGUE", "succeeded"), ("SHOE-CREAM-PEEP", "succeeded")])
        self.assertIn("28 October 2026 shoes pilot", data["jobs"][1]["label"])
        self.assertEqual(preview_jobs.committed_units(), 0)
        self.assertIn('"shoes"', self.client.get("/").content.decode().split('id="recorded-categories"')[1][:300])

    def test_the_evidence_page_shows_the_shoes_and_why_bag_try_on_is_off(self):
        from django.contrib.staticfiles import finders
        html = self.client.get("/evidence").content.decode()
        for text in ("Shoes and bags pilot", "Shoes on Model F", "Red Heeled Ankle Boots, the pair bought",
                     "Why bag try-on is off", "a gallery background, a green dress", "new outfit, pose and background"):
            self.assertIn(text, html)
        data = json.loads(Path(finders.find("exchange/evidence/accessories.json")).read_text())
        self.assertEqual([r["sku"] for r in data["results"]],
                         ["SHOE-RED-BOOT", "SHOE-CREAM-PEEP", "SHOE-GOLD-BROGUE", "SHOE-NUDE-PUMP"])
        self.assertEqual((data["pilot"]["units"]["observed"], data["bag_probe"]["units"]["observed"]), (8, 2))
        self.assertNotIn("task_id", json.dumps(data))
        for name in [r["result_file"] for r in data["results"]] + [data["bag_probe"]["result_file"]]:
            self.assertIsNotNone(finders.find(f"exchange/evidence/{name}"))

    def test_claude_reads_shoes_and_bags_with_colour_families(self):
        instructions = assistant.instructions("accessories", "shoes")
        self.assertIn("a pair of shoes", instructions)
        self.assertIn("black, brown, tan, beige, cream and nude are neutral", instructions)
        schema = assistant.schema("accessories")
        self.assertEqual(schema["properties"]["proposal"]["properties"]["metal"]["enum"], ["", "neutral", "bright", "either"])

    def test_the_shoes_and_bags_pilots_plan_four_pairs_each_for_eight_units(self):
        for which, feature, first in (("shoes", "Shoes V3", "1. model-f x SHOE-RED-BOOT"),
                                      ("bags", "Bag V2", "1. model-a x BAG-RED-HOBO")):
            with self.subTest(which):
                out = io.StringIO()
                call_command("run_feasibility", "--set", which, "--sources", str(REAL_SOURCES), stdout=out)
                self.assertIn(f"On YouCam these pairs cost 8 units: {feature} 8.", out.getvalue())
                self.assertIn(first, out.getvalue())
        fake = FakeYouCamTransport(result_image=jpeg(), units_per_task=2)
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=8, PREVIEW_DAILY_UNIT_CAP=8), \
                self.youcam(fake):
            call_command("run_feasibility", "--set", "bags", "--sources", str(REAL_SOURCES), "--execute",
                         "--confirm-units", "8", stdout=io.StringIO())
        report = json.loads((self.media / "feasibility" / "reports" / "bags-pilot" / "report.json").read_text())
        self.assertEqual((report["summary"]["succeeded"], report["summary"]["units"]["observed"]), (4, 8))
        self.assertEqual({body["gender"] for _, body in fake.tasks}, {"female"})
        self.assertIn("carried", report["summary"]["gate"])

    def test_help_me_compare_describes_shoes_by_heel_and_colour(self):
        sid = self.start("DEMO-ORDER-001-SHOE", size="smaller")
        data = self.send("post", f"/api/sessions/{sid}/compare").json()
        self.assertEqual(data["reader"], "rules")
        self.assertIn("Metallic Platform Brogues: lower heel: heel 30 mm instead of 100 mm", data["notes"]["SHOE-GOLD-BROGUE"])
        from exchange import advisor
        from exchange.models import ProductVariant
        self.assertEqual(advisor.item_facts(ProductVariant.objects.get(sku="BAG-TAN-MESSENGER")),
                         ["neutral colour", "messenger style", "300 mm wide"])
