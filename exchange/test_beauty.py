import io
import json
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from exchange.models import EvidenceEvent, ExchangeRequest, OrderLine, PreviewJob, ProductVariant


class BeautyBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        patch = override_settings(PRIVATE_MEDIA_ROOT=media.name)
        patch.enable()
        self.addCleanup(patch.disable)

    def send(self, method, path, payload=None, **headers):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}),
                                            content_type="application/json", **headers)

    def start(self, token="DEMO-ORDER-001-LIP", reason="shade", **prefs):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        line = OrderLine.objects.get(token=token)
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/item", {"line_id": line.id}).status_code, 200)
        if reason:
            self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": reason}).status_code, 200)
        if prefs:
            response = self.send("put", f"/api/sessions/{sid}/preferences", {"max_top_up_minor": 500, **prefs})
            self.assertEqual(response.status_code, 200, response.content)
        return sid


class BeautyShadeTests(BeautyBase):
    def test_the_order_lists_every_item_and_each_category_offers_its_own_reasons(self):
        data = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()
        self.assertEqual([(i["category"], i["opened"]) for i in data["items"]],
                         [("tops", False), ("lip", True), ("foundation", True), ("nail", False), ("earrings", False), ("necklace", False), ("hair", True),
                          ("shoes", False), ("bag", False)])
        lipstick = data["items"][1]
        self.assertEqual((lipstick["shade_name"], lipstick["shade_hex"]), ("230 Terracotta", "#b5532f"))
        self.assertEqual([r["code"] for r in data["reasons"]], ["style", "fit", "damaged", "changed_mind"])
        chosen = self.send("put", f"/api/sessions/{data['session_id']}/item", {"line_id": lipstick["line_id"]}).json()
        self.assertEqual([r["code"] for r in chosen["reasons"]], ["shade", "reaction", "damaged", "changed_mind"])
        refused = self.send("put", f"/api/sessions/{data['session_id']}/reason", {"reason": "style"})
        self.assertEqual(refused.json()["error"]["code"], "REASON_NOT_OFFERED")
        other = OrderLine.objects.create(token="OTHER", fictional_order_id="SL-OTHER", retailer_id="secondlook-demo",
                                         variant=ProductVariant.objects.get(sku="CALM-01"), paid_minor=4000,
                                         eligible_credit_minor=4000)
        self.assertEqual(self.send("put", f"/api/sessions/{data['session_id']}/item",
                                   {"line_id": other.id}).status_code, 404)

    def test_a_reaction_goes_to_people_and_never_to_a_preview(self):
        sid = self.start(reason=None)
        data = self.send("put", f"/api/sessions/{sid}/reason", {"reason": "reaction"}).json()
        self.assertEqual(data["next"], "support")
        self.assertIn("will not suggest another product", data["message"])
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/preferences",
                                   {"tone": "cooler", "max_top_up_minor": 500}).status_code, 409)

    def test_a_too_orange_lipstick_gets_cooler_shades_from_its_own_line_because_it_was_opened(self):
        sid = self.start(tone="cooler")
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual([c["sku"] for c in data["candidates"]], ["LIP-VELVET-220", "LIP-VELVET-240"])
        first = data["candidates"][0]
        self.assertEqual((first["shade_name"], first["shade_hex"], first["product_line"]),
                         ("220 Rosewood", "#a04a55", "Velvet Matte Lipstick"))
        self.assertIn("same product line", first["reasons"])
        left_out = {x["code"]: x["count"] for x in data["exclusions"]}
        self.assertEqual(left_out["opened_same_line"], 4)
        self.assertNotIn("CALM", json.dumps(data))
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "LIP-VELVET-220"}).json()
        self.assertEqual((quote["kind"], quote["top_up_minor"]), ("shade", 0))
        satin = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "LIP-SATIN-20"})
        self.assertEqual(satin.json()["error"]["code"], "OPENED_SAME_LINE")
        draft = self.send("post", "/api/requests", {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                          HTTP_IDEMPOTENCY_KEY="shade")
        self.assertEqual(draft.status_code, 201, draft.content)
        self.assertEqual(ExchangeRequest.objects.get().quote.kind, "shade")

    def test_a_foundation_that_reads_too_orange_finds_the_cool_shade_beside_it(self):
        sid = self.start(token="DEMO-ORDER-001-FDN", tone="cooler")
        found = self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]
        self.assertEqual(found[0]["sku"], "FDN-VEIL-210C")

    def test_impossible_shade_requests_explain_themselves_and_relax_nothing(self):
        sid = self.start(lightness="lighter", tone="warmer", intensity="bolder")
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual(data["status"], "NO_MATCH")
        self.assertIn("No shade meets every limit", data["message"])
        self.assertTrue(data["exclusions"])
        self.assertEqual(EvidenceEvent.objects.filter(action="candidates_checked").last().facts["matches"], 0)
        from exchange.insights import summary
        unmet = summary()["unmet"]
        self.assertEqual(unmet[0][0], "shade: lighter, warmer, bolder · up to USD 5")
        self.assertIn("not light or dark enough", unmet[0][2])

    def test_shade_words_fill_the_shade_controls(self):
        sid = self.start(reason="shade")
        data = self.send("post", f"/api/sessions/{sid}/interpret",
                         {"text": "It looks too orange and a bit too dark on me. Nothing extra please."}).json()
        self.assertEqual(data["proposal"], {"lightness": "lighter", "tone": "cooler", "max_top_up_minor": 0})
        mixed = self.send("post", f"/api/sessions/{sid}/interpret", {"text": "lighter, or maybe darker"}).json()
        self.assertEqual(mixed["questions"][0]["field"], "lightness")

    def test_shade_previews_show_the_purchased_shade_and_two_alternatives(self):
        sid = self.start(tone="cooler")
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "face-a"}).json()["asset_id"]
        jobs = self.send("post", f"/api/sessions/{sid}/previews",
                         {"candidate_skus": ["LIP-VELVET-220", "LIP-VELVET-240"], "consent": True,
                          "asset_id": asset, "include_original": True}).json()["jobs"]
        self.assertEqual([(j["sku"], j["baseline"]) for j in jobs],
                         [("LIP-VELVET-230", True), ("LIP-VELVET-220", False), ("LIP-VELVET-240", False)])
        self.assertEqual(PreviewJob.objects.count(), 3)

    def test_the_item_cannot_change_once_the_exchange_has_started(self):
        sid = self.start(tone="cooler")
        nail = OrderLine.objects.get(token="DEMO-ORDER-001-NAIL")
        locked = self.send("put", f"/api/sessions/{sid}/item", {"line_id": nail.id})
        self.assertEqual((locked.status_code, locked.json()["error"]["code"]), (409, "ITEM_LOCKED"))


class BeautyPreviewTests(BeautyBase):
    def youcam(self, fake):
        from unittest import mock
        from exchange import preview_jobs
        from secondlook.youcam_fake import API
        from secondlook.youcam_provider import YouCamMakeupProvider, YouCamNailProvider
        return mock.patch.dict(preview_jobs.PROVIDERS, {
            "youcam-makeup": lambda: YouCamMakeupProvider("test-only-key", base_url=API, transport=fake),
            "youcam-nail": lambda: YouCamNailProvider("test-only-key", base_url=API, transport=fake)})

    def fake(self, colour):
        from PIL import Image
        from secondlook.youcam_fake import FakeYouCamTransport
        out = io.BytesIO()
        Image.new("RGB", (600, 600), colour).save(out, format="JPEG")
        return FakeYouCamTransport(result_image=out.getvalue(), units_per_task=1)

    def test_a_lipstick_comparison_sends_three_shades_to_makeup_vto_for_three_units(self):
        from exchange import preview_jobs
        fake = self.fake("#a04a55")
        sid = self.start(tone="cooler")
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                self.youcam(fake):
            asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "face-a"}).json()["asset_id"]
            data = self.send("post", f"/api/sessions/{sid}/previews",
                             {"candidate_skus": ["LIP-VELVET-220", "LIP-VELVET-240"], "consent": True,
                              "asset_id": asset, "include_original": True}).json()
        self.assertEqual(data["provider"], "youcam-makeup")
        self.assertEqual({j["state"] for j in data["jobs"]}, {"succeeded"})
        self.assertEqual([payload["effects"][0]["palettes"][0]["color"] for _, payload in fake.tasks],
                         ["#b5532f", "#a04a55", "#9c5a6e"])
        self.assertEqual({payload["effects"][0]["category"] for _, payload in fake.tasks}, {"lip_color"})
        self.assertEqual(preview_jobs.committed_units(), 3)
        self.assertIn("Shades on screen vary", data["jobs"][1]["label"])
        self.assertIsNone(data["jobs"][1]["colour_check"])
        self.assertTrue(PreviewJob.objects.filter(colour_check__in=("unchecked", "shade-close", "shade-differs")).exists())

    def test_each_item_is_previewed_on_the_demo_person_that_fits_it(self):
        from exchange import preview_jobs
        cases = (("DEMO-ORDER-001-NAIL", "hand-a", ("model-a", "face-a"), "Nail previews need a hand"),
                 ("DEMO-ORDER-001-LIP", "face-a", ("model-a", "hand-a"), "previews need a face"),
                 ("DEMO-ORDER-001-FDN", "face-b", ("hand-a",), "previews need a face"),
                 ("DEMO-ORDER-001-EAR", "face-a", ("model-a", "hand-a"), "earring previews need a face"),
                 ("DEMO-ORDER-001", "model-a", ("face-a", "hand-a"), "half-body demo models"))
        for token, fits, wrong, message in cases:
            with self.subTest(token=token):
                sid = self.start(token=token, reason=None)
                for name in wrong:
                    refused = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": name})
                    self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (422, "MODEL_NOT_SUITABLE"))
                    self.assertIn(message, refused.json()["error"]["message"])
                chosen = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": fits})
                self.assertEqual(chosen.status_code, 201, chosen.content)
                self.assertEqual(chosen.json()["label"], fits.replace("-", " ").title())
        self.assertEqual([preview_jobs.provider_name_for(c, "youcam") for c in ("tops", "lip", "foundation", "nail")],
                         ["youcam", "youcam-makeup", "youcam-makeup", "youcam-nail"])
        self.assertEqual([preview_jobs.provider_name_for(c, "replay") for c in ("tops", "lip")], ["replay", "replay"])

    def test_a_nail_comparison_paints_three_shades_on_the_demo_hand_for_three_units(self):
        from exchange import preview_jobs
        fake = self.fake("#b3203a")
        sid = self.start(token="DEMO-ORDER-001-NAIL", tone="cooler")
        skus = [c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]]
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                self.youcam(fake):
            asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "hand-a"}).json()["asset_id"]
            data = self.send("post", f"/api/sessions/{sid}/previews",
                             {"candidate_skus": skus, "consent": True, "asset_id": asset, "include_original": True}).json()
        self.assertEqual(data["provider"], "youcam-nail")
        self.assertEqual({j["state"] for j in data["jobs"]}, {"succeeded"})
        self.assertEqual([payload["effects"][0]["color"] for _, payload in fake.tasks],
                         ["#ef6f5a"] + [ProductVariant.objects.get(sku=s).shade_hex for s in skus])
        self.assertEqual({len(payload["effects"]) for _, payload in fake.tasks}, {5})
        self.assertEqual(preview_jobs.committed_units(), 3)

    def recorded(self, token, model, **prefs):
        sid = self.start(token=token, **prefs)
        skus = [c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]]
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model}).json()["asset_id"]
        return self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": skus, "consent": True,
                         "asset_id": asset, "include_original": True, "mode": "recorded"}).json()

    def test_recorded_beauty_comparisons_replay_each_shade_by_sku_for_no_units(self):
        from exchange import preview_jobs
        for token, model in (("DEMO-ORDER-001-LIP", "face-a"), ("DEMO-ORDER-001-FDN", "face-a"),
                             ("DEMO-ORDER-001-NAIL", "hand-a")):
            with self.subTest(token=token):
                data = self.recorded(token, model, tone="cooler")
                self.assertEqual(data["provider"], "replay")
                self.assertEqual([j["state"] for j in data["jobs"]], ["succeeded"] * 3)
                self.assertIn("27 October 2026 beauty pilot", data["jobs"][0]["label"])
                # Three shades of one product line share a style code; each must still get its own result.
                images = {PreviewJob.objects.get(id=j["job_id"]).result_asset.sha256 for j in data["jobs"]}
                self.assertEqual(len(images), 3)
        self.assertEqual(preview_jobs.committed_units(), 0)

    def test_a_shade_with_no_recorded_result_says_what_is_covered(self):
        data = self.recorded("DEMO-ORDER-001-LIP", "face-b", tone="cooler")
        self.assertEqual({j["error_code"] for j in data["jobs"]}, {"NOT_RECORDED"})
        self.assertIn("no recorded result for this shade on this face", data["jobs"][0]["message"])
        self.assertIn("Face A", data["jobs"][0]["message"])

    def test_the_evidence_page_shows_the_three_beauty_comparisons(self):
        from django.contrib.staticfiles import finders
        html = self.client.get("/evidence").content.decode()
        for text in ("Beauty pilot", "Lipstick on Face A", "Foundation on Face A", "Nail polish on Hand A",
                     "230 Terracotta, the shade bought", "04 Lilac", "too subtle for the shade check",
                     "The other 4 results stay private", "First reading by Claude"):
            self.assertIn(text, html)
        data = json.loads(Path(finders.find("exchange/evidence/beauty.json")).read_text())
        self.assertEqual([r["pair"] for r in data["results"]], [1, 2, 3, 7, 8, 9, 11, 12, 13])
        self.assertNotIn("task", json.dumps(data["results"]).replace("tests", ""))
        for r in data["results"]:
            self.assertIsNotNone(finders.find(f"exchange/evidence/{r['result_file']}"))

    def test_large_face_photos_are_scaled_to_the_makeup_limit(self):
        from PIL import Image
        from exchange.preview_jobs import _fit_long_side
        out = io.BytesIO()
        Image.new("RGB", (2400, 3200), "#cccccc").save(out, format="JPEG")
        self.assertEqual(Image.open(io.BytesIO(_fit_long_side(out.getvalue(), 1900))).size, (1425, 1900))
        small = io.BytesIO()
        Image.new("RGB", (800, 1000), "#cccccc").save(small, format="JPEG")
        self.assertEqual(_fit_long_side(small.getvalue(), 1900), small.getvalue())
