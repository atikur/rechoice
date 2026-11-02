import io
import json
import tempfile
from datetime import timedelta

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from exchange.models import OrderLine, PreviewJob, PrivateAsset, ProductVariant


class PreviewTrustTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(PRIVATE_MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def send(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json")

    def preview(self, *, category="tops", token="DEMO-ORDER-001", person="model-a", **preferences):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        if category != "tops":
            line = OrderLine.objects.get(token=token)
            self.send("put", f"/api/sessions/{sid}/item", {"line_id": line.pk})
            self.send("put", f"/api/sessions/{sid}/reason", {"reason": "shade" if category == "lip" else "look"})
        response = self.send("put", f"/api/sessions/{sid}/preferences", {"max_top_up_minor": 1000, **preferences})
        self.assertEqual(response.status_code, 200, response.content)
        candidates = self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": person}).json()["asset_id"]
        response = self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": [c["sku"] for c in candidates],
                             "asset_id": asset, "mode": "recorded", "consent": True, "include_original": True})
        self.assertEqual(response.status_code, 202, response.content)
        return sid, response.json()["jobs"]

    def test_confirmed_controls_catalog_and_measurements_are_separate_and_free_text_is_absent(self):
        _, jobs = self.preview(quieter_print=True, sleeve_required="long", note="PRIVATE-NOTE-DO-NOT-PUBLISH")
        original = next(j for j in jobs if j["baseline"])
        replacement = next(j for j in jobs if not j["baseline"])
        trust = replacement["trust"]
        self.assertEqual(trust["eligibility"], "eligible")
        self.assertIn("A quieter print", trust["requested"])
        self.assertIn("Sleeves: long", trust["requested"])
        self.assertIn("Maximum extra: USD 10.00", trust["requested"])
        self.assertTrue(any("stock, policy" in fact for fact in trust["catalog_facts"]))
        self.assertTrue(any(check["label"] == "Garment colour" for check in trust["checks"]))
        self.assertIn("Recorded YouCam result", trust["source"])
        self.assertIn("not automatically verified", " ".join(trust["limits"]))
        self.assertNotIn("PRIVATE-NOTE", json.dumps(jobs))
        self.assertEqual(original["trust"]["eligibility"], "reference")
        self.assertNotIn("eligible shortlist", " ".join(original["trust"]["catalog_facts"]))

    def test_later_stock_change_removes_eligibility_claim_even_if_the_image_is_still_ready(self):
        _, jobs = self.preview(quieter_print=True, sleeve_required="long")
        job = next(j for j in jobs if not j["baseline"])
        ProductVariant.objects.filter(sku=job["sku"]).update(stock_qty=0)
        current = self.client.get(f"/api/previews/{job['job_id']}").json()
        self.assertEqual(current["state"], "succeeded")
        self.assertEqual(current["trust"]["eligibility"], "refresh")
        self.assertNotIn("were rechecked", " ".join(current["trust"]["catalog_facts"]))

    def test_uncalibrated_beauty_never_receives_a_positive_colour_verdict_or_numeric_confidence(self):
        _, jobs = self.preview(category="lip", token="DEMO-ORDER-001-LIP", person="face-a", tone="cooler")
        for job in jobs:
            self.assertIsNone(job["colour_check"])
            self.assertEqual(job["trust"]["checks"][0]["status"], "uncalibrated")
            self.assertNotIn("delta", json.dumps(job["trust"]))
            self.assertNotIn("shade-close", json.dumps(job["trust"]))

    def test_resized_earring_explains_the_correction_and_keeps_the_original_switch(self):
        _, jobs = self.preview(category="earrings", token="DEMO-ORDER-001-EAR", person="face-a", size="smaller", metal="silver")
        resized = next(j for j in jobs if j["sku"] == "EAR-CRYSTAL-S38")
        self.assertTrue(resized["rendered_url"])
        self.assertTrue(resized["trust"]["attention"])
        self.assertTrue(any("resized by Rechoice" in text for text in resized["trust"]["limits"]))
        self.assertEqual(resized["trust"]["checks"][0]["status"], "size-resized")

    def test_expired_result_does_not_repeat_stale_image_measurements(self):
        _, jobs = self.preview(quieter_print=True, sleeve_required="long")
        item = next(j for j in jobs if not j["baseline"])
        job = PreviewJob.objects.get(pk=item["job_id"])
        PrivateAsset.objects.filter(pk=job.result_asset_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        current = self.client.get(f"/api/previews/{job.pk}").json()
        self.assertIsNone(current["image_url"])
        self.assertEqual(current["trust"]["checks"][0]["status"], "not_measured")
        self.assertNotIn("ΔE", json.dumps(current["trust"], ensure_ascii=False))
        self.assertEqual(Client().get(f"/api/previews/{job.pk}").status_code, 404)

    def test_unreviewed_colour_reference_is_not_described_as_a_verified_match(self):
        _, jobs = self.preview(quieter_print=True, sleeve_required="long")
        item = next(j for j in jobs if not j["baseline"])
        ProductVariant.objects.filter(sku=item["sku"]).update(reference_colour_status="proposed")
        current = self.client.get(f"/api/previews/{item['job_id']}").json()
        self.assertEqual(current["trust"]["checks"][0]["status"], "unreviewed")
        self.assertIsNone(current["colour_check"])
        self.assertIn("not a verified colour match", current["trust"]["checks"][0]["note"])

    def test_an_expired_correction_shows_the_original_without_claiming_it_was_resized(self):
        _, jobs = self.preview(category="earrings", token="DEMO-ORDER-001-EAR", person="face-a", size="smaller", metal="silver")
        item = next(j for j in jobs if j["sku"] == "EAR-CRYSTAL-S38")
        job = PreviewJob.objects.get(pk=item["job_id"])
        PrivateAsset.objects.filter(pk=job.sized_asset_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        current = self.client.get(f"/api/previews/{job.pk}").json()
        self.assertIsNone(current["rendered_url"])
        self.assertEqual(current["image_url"], f"/api/assets/{job.result_asset_id}")
        self.assertEqual(current["size_check"]["status"], "size-differs")
        self.assertIn("original render", current["size_check"]["note"])
        self.assertEqual(current["trust"]["checks"][0]["status"], "correction_unavailable")
        self.assertFalse(any("resized by Rechoice" in text for text in current["trust"]["limits"]))

    def test_a_combined_look_does_not_inherit_a_stage_colour_match_as_a_final_image_verdict(self):
        sid, jobs = self.preview(quieter_print=True, sleeve_required="long")
        item = next(j for j in jobs if not j["baseline"])
        asset = PreviewJob.objects.get(pk=item["job_id"]).person_asset_id
        response = self.send("post", f"/api/sessions/{sid}/looks", {"sku": item["sku"], "asset_id": str(asset),
                                                                 "mode": "recorded", "consent": True})
        self.assertEqual(response.status_code, 202, response.content)
        result = response.json()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["trust"]["checks"][0]["status"], "not_measured")
        self.assertIn("do not verify the final image", result["trust"]["checks"][0]["note"])
        self.assertNotIn("ΔE", json.dumps(result["trust"], ensure_ascii=False))
