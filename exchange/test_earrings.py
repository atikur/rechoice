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
from exchange.models import ExchangeRequest, OrderLine, PreviewJob
from exchange.size_check import rendered_height
from secondlook.youcam_fake import API, FakeYouCamTransport
from secondlook.youcam_provider import YouCamEarringProvider

REAL_SOURCES = Path(__file__).resolve().parents[1] / "assets" / "pixabay_sources.json"


def jpeg(colour):
    out = io.BytesIO()
    Image.new("RGB", (640, 640), colour).save(out, format="JPEG")
    return out.getvalue()


class EarringTests(TestCase):
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
        line = OrderLine.objects.get(token="DEMO-ORDER-001-EAR")
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/item", {"line_id": line.id}).status_code, 200)
        if reason:
            self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": reason}).status_code, 200)
        if prefs:
            response = self.send("put", f"/api/sessions/{sid}/preferences", {"max_top_up_minor": 500, **prefs})
            self.assertEqual(response.status_code, 200, response.content)
        return sid

    def previews(self, sid, mode="live", model="face-a"):
        skus = [c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]]
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model}).json()["asset_id"]
        return self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": skus, "consent": True,
                         "asset_id": asset, "include_original": True, "mode": mode}).json()

    def test_too_long_and_wanted_silver_finds_two_silver_pairs_and_saves_a_draft(self):
        sid = self.start(reason=None)
        reasons = self.send("put", f"/api/sessions/{sid}/item",
                            {"line_id": OrderLine.objects.get(token="DEMO-ORDER-001-EAR").id}).json()["reasons"]
        self.assertEqual([r["code"] for r in reasons], ["look", "reaction", "damaged", "changed_mind"])
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": "look"}).json()["next"],
                         "jewellery_preferences")
        words = self.send("post", f"/api/sessions/{sid}/interpret",
                          {"text": "These are too long and I wanted silver. Nothing extra please."}).json()
        self.assertEqual(words["proposal"], {"size": "smaller", "metal": "silver", "max_top_up_minor": 0})
        self.send("put", f"/api/sessions/{sid}/preferences", {"size": "smaller", "metal": "silver", "max_top_up_minor": 500})
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual([c["sku"] for c in data["candidates"]], ["EAR-CRYSTAL-S38", "EAR-TOPAZ-S12"])
        first = data["candidates"][0]
        self.assertEqual((first["details"], first["image_url"]),
                         ("Silver · drop · 38 mm", "/static/exchange/jewellery/EAR-CRYSTAL-S38.jpg"))
        self.assertIn("one silver drop earring, 38 mm long", first["image_alt"])
        self.assertEqual(data["exclusions"], [{"code": "not_metal", "count": 1, "text": "1 not in silver"}])
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "EAR-CRYSTAL-S38"}).json()
        self.assertEqual((quote["kind"], quote["top_up_minor"]), ("jewellery", 200))
        gold = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "EAR-ROPE-G10"})
        self.assertEqual(gold.json()["error"]["code"], "INELIGIBLE")
        draft = self.send("post", "/api/requests", {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                          HTTP_IDEMPOTENCY_KEY="earrings")
        self.assertEqual(draft.status_code, 201, draft.content)
        self.assertEqual(ExchangeRequest.objects.get().quote.kind, "jewellery")

    def test_a_request_the_catalog_cannot_meet_is_explained_and_counted_for_the_merchant(self):
        sid = self.start(size="larger", metal="silver")
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual(data["status"], "NO_MATCH")
        self.assertIn("No pair meets every limit", data["message"])
        self.assertEqual(data["exclusions"][0]["text"], "3 not clearly larger")
        from exchange.insights import summary
        self.assertEqual(summary()["unmet"][0][0], "jewellery: larger, silver · up to USD 5")

    def test_an_earring_comparison_sends_each_product_photo_at_its_own_size_for_three_units(self):
        fake = FakeYouCamTransport(result_image=jpeg((180, 150, 120)), units_per_task=1)
        sid = self.start(size="smaller", metal="silver")
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-earring": lambda: YouCamEarringProvider("test-only-key", base_url=API, transport=fake)}):
            data = self.previews(sid)
        self.assertEqual(data["provider"], "youcam-earring")
        self.assertEqual([(j["sku"], j["state"]) for j in data["jobs"]],
                         [("EAR-PEARL-G58", "succeeded"), ("EAR-CRYSTAL-S38", "succeeded"),
                          ("EAR-TOPAZ-S12", "succeeded")])
        # Each earring is told the length below its hook: the crystal drop's photo shows a lever-back over the top
        # 38% of its height, so it is told 24 mm, not 38.
        self.assertEqual([payload["object_infos"][0]["parameter"]["earring_scale"] for _, payload in fake.tasks],
                         [5.8, 2.4, 1.2])
        anchors = [payload["object_infos"][0]["parameter"]["earring_anchor_point"] for _, payload in fake.tasks]
        self.assertTrue(all(len(a) == 2 and a[1] < 40 for a in anchors), anchors)
        # One face and one product photo per task.
        self.assertEqual(len(fake.uploads), 6)
        self.assertEqual(preview_jobs.committed_units(), 3)
        self.assertIn("Size on screen is measured against the pair you bought", data["jobs"][1]["label"])
        self.assertIsNone(data["jobs"][1]["colour_check"])

    def test_every_earring_is_measured_against_the_pair_bought_on_the_same_photo(self):
        from PIL import ImageDraw
        face = Image.open(Path(__file__).resolve().parent / "static/exchange/models/face-a.jpg").convert("RGB")

        def drawn(height):
            out = io.BytesIO()
            image = face.copy()
            ImageDraw.Draw(image).rectangle((930, 520, 960, 520 + height), fill=(20, 20, 20))
            image.save(out, format="JPEG", quality=92)
            return out.getvalue()
        # The pair bought renders 142 px; the 38 mm crystal 50% too long, as on the pilot; the 12 mm stud in proportion.
        images = [drawn(142), drawn(140), drawn(29)]
        fake = FakeYouCamTransport(result_image=lambda: images.pop(0), units_per_task=1)
        sid = self.start(size="smaller", metal="silver")
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-earring": lambda: YouCamEarringProvider("test-only-key", base_url=API, transport=fake)}):
            data = self.previews(sid)
        checks = {j["sku"]: j["size_check"] for j in data["jobs"]}
        self.assertEqual(checks["EAR-PEARL-G58"]["status"], "size-reference")
        # The crystal is resized to its catalog length; YouCam's own render stays a click away.
        self.assertEqual(checks["EAR-CRYSTAL-S38"], {"status": "size-resized", "note": "Size check: YouCam drew it 50% "
                         "longer; Rechoice resized it to its catalog length, in proportion to the pair bought."})
        self.assertEqual(checks["EAR-TOPAZ-S12"], {"status": "size-close",
                                                   "note": "Size check: renders in proportion to the pair bought."})
        heights = {j.variant.sku: j.size_px for j in PreviewJob.objects.filter(provider="youcam-earring")}
        self.assertEqual(heights, {"EAR-PEARL-G58": 143, "EAR-CRYSTAL-S38": 141, "EAR-TOPAZ-S12": 30})
        self.assertIsNone(data["jobs"][1]["colour_check"])
        crystal = PreviewJob.objects.get(variant__sku="EAR-CRYSTAL-S38")
        jobs = {j["sku"]: j for j in data["jobs"]}
        self.assertEqual((jobs["EAR-CRYSTAL-S38"]["image_url"], jobs["EAR-CRYSTAL-S38"]["rendered_url"]),
                         (f"/api/assets/{crystal.sized_asset_id}", f"/api/assets/{crystal.result_asset_id}"))
        self.assertIsNone(jobs["EAR-TOPAZ-S12"]["rendered_url"])
        person = (self.media / crystal.person_asset.storage_key).read_bytes()
        resized = self.client.get(jobs["EAR-CRYSTAL-S38"]["image_url"])
        self.assertEqual(resized.status_code, 200)
        # 38/58 of the pair bought's 143 px is 94 px.
        self.assertAlmostEqual(rendered_height(person, resized.content), 94, delta=3)
        # Units are unchanged: the resize is done here, not by YouCam.
        self.assertEqual(preview_jobs.committed_units(), 3)
        # Withdrawing the photo deletes the resized copy with the rest.
        sized = crystal.sized_asset.storage_key
        self.assertEqual(self.client.delete(f"/api/assets/{crystal.person_asset_id}").status_code, 200)
        self.assertFalse((self.media / sized).exists())

    def test_the_recorded_earrings_comparison_replays_each_pair_for_no_units(self):
        sid = self.start(size="smaller", metal="silver")
        data = self.previews(sid, mode="recorded")
        self.assertEqual(data["provider"], "replay")
        self.assertEqual([(j["sku"], j["state"]) for j in data["jobs"]],
                         [("EAR-PEARL-G58", "succeeded"), ("EAR-CRYSTAL-S38", "succeeded"),
                          ("EAR-TOPAZ-S12", "succeeded")])
        self.assertIn("28 October 2026 earrings pilot", data["jobs"][0]["label"])
        self.assertIn("Size on screen is measured against the pair you bought", data["jobs"][0]["label"])
        images = {PreviewJob.objects.get(id=j["job_id"]).result_asset.sha256 for j in data["jobs"]}
        self.assertEqual(len(images), 3)
        self.assertEqual(preview_jobs.committed_units(), 0)
        # The recorded crystal, drawn half as long again as its length implies, is resized on replay too.
        checks = {j["sku"]: j["size_check"]["status"] for j in data["jobs"]}
        self.assertEqual(checks, {"EAR-PEARL-G58": "size-reference", "EAR-CRYSTAL-S38": "size-resized",
                                  "EAR-TOPAZ-S12": "size-close"})
        self.assertIn("YouCam drew it 50% longer", data["jobs"][1]["size_check"]["note"])

    def test_earrings_without_a_recording_say_what_is_covered(self):
        sid = self.start(size="smaller", metal="silver")
        data = self.previews(sid, mode="recorded", model="face-b")
        self.assertEqual({j["error_code"] for j in data["jobs"]}, {"NOT_RECORDED"})
        self.assertIn("no recorded results for these earrings", data["jobs"][0]["message"])

    def test_the_evidence_page_shows_the_earrings_comparison(self):
        from django.contrib.staticfiles import finders
        html = self.client.get("/evidence").content.decode()
        for text in ("Earrings pilot", "Earrings on Face A", "Baroque Pearl Drops, the pair bought",
                     "silver drop, 38 mm", "The other 1 result stays private", "replaces Face A&#x27;s own studs"):
            self.assertIn(text, html)
        data = json.loads(Path(finders.find("exchange/evidence/jewellery.json")).read_text())
        self.assertEqual([r["sku"] for r in data["results"]], ["EAR-PEARL-G58", "EAR-CRYSTAL-S38", "EAR-TOPAZ-S12"])
        self.assertNotIn("task_id", json.dumps(data))
        for r in data["results"]:
            self.assertIsNotNone(finders.find(f"exchange/evidence/{r['result_file']}"))

    def test_the_page_offers_recorded_results_only_for_items_that_have_them(self):
        html = self.client.get("/").content.decode()
        self.assertIn('<script id="recorded-categories" type="application/json">'
                      '["earrings", "foundation", "hair", "lip", "nail", "necklace", "shoes", "tops"]</script>',
                      html)
        self.assertIn('id="jewelPrefs"', html)

    def test_the_jewellery_pilot_runs_four_earrings_on_face_a_for_four_units(self):
        out = io.StringIO()
        call_command("run_feasibility", "--set", "jewellery", "--sources", str(REAL_SOURCES), stdout=out)
        self.assertIn("On YouCam these pairs cost 4 units: Earrings VTO 4.", out.getvalue())
        fake = FakeYouCamTransport(result_image=jpeg((180, 150, 120)), units_per_task=1)
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=4, PREVIEW_DAILY_UNIT_CAP=4), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-earring": lambda: YouCamEarringProvider("test-only-key", base_url=API, transport=fake)}):
            call_command("run_feasibility", "--set", "jewellery", "--sources", str(REAL_SOURCES), "--execute",
                         "--confirm-units", "4", stdout=io.StringIO())
        report = json.loads((self.media / "feasibility" / "reports" / "jewellery-pilot" / "report.json").read_text())
        self.assertEqual((report["summary"]["succeeded"], report["summary"]["units"]["observed"]), (4, 4))
        self.assertIn("believable size", report["summary"]["gate"])
        self.assertEqual(report["pairs"][0]["inputs"]["earring"]["length_mm"], 58)
        self.assertIsNone(report["pairs"][0]["review"]["placement"])
        self.assertEqual(PreviewJob.objects.filter(provider="youcam-earring").count(), 4)
