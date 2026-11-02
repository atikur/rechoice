import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signals import request_finished
from django.test import Client, TestCase, override_settings
from django.db import close_old_connections
from django.utils import timezone
from PIL import Image

from kombu.exceptions import OperationalError as BrokerError

from exchange import preview_jobs
from exchange.assets import private_path
from exchange.models import (
    EvidenceEvent, ExchangeRequest, ExchangeSession, OrderLine, PolicySnapshot, PreviewJob,
    PrivateAsset, ProductVariant, Quote,
)
from secondlook.providers import RUNNING, SUCCEEDED, PollResult, ProviderRejected, StartResult
from secondlook.youcam_fake import API as YOUCAM_API, FakeYouCamTransport
from secondlook.youcam_provider import TransportError as YouCamTransportError, YouCamClothesProvider


def fake_provider(*, units=2, start=None, poll=None, calls=None):
    """A stand-in paid provider. It records every start so tests can prove nothing was sent."""
    class FakeProvider:
        name = "fake"
        version = "fake-v1"
        units_per_job = units

        def start(self, job):
            if calls is not None:
                calls.append(job)
            return start(job) if start else StartResult(f"fake-{job.job_id}", units)

        def poll(self, task_id):
            return poll(task_id) if poll else PollResult(SUCCEEDED, observed_units=units)
    return FakeProvider


@contextlib.contextmanager
def provider(cls, ceiling=10, **extra):
    with override_settings(PREVIEW_PROVIDER="fake", PREVIEW_UNIT_CEILING=ceiling,
                           PREVIEW_DAILY_UNIT_CAP=ceiling, **extra), \
            mock.patch.dict(preview_jobs.PROVIDERS, {"fake": cls}):
        yield


def png_bytes(color="#789aab", size=512):
    out = io.BytesIO()
    Image.new("RGB", (size, size), color).save(out, format="PNG")
    return out.getvalue()


class FlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        patch = override_settings(PRIVATE_MEDIA_ROOT=media.name)
        patch.enable()
        self.addCleanup(patch.disable)

    def send(self, method, path, payload=None, client=None, **headers):
        client = client or self.client
        return getattr(client, method.lower())(
            path, data=json.dumps(payload or {}), content_type="application/json", **headers)

    def start(self, client=None):
        response = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}, client)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["session_id"]

    def prefs(self, session_id, client=None, **overrides):
        payload = {"quieter_print": True, "sleeve_required": "long",
                   "color_preferences": [], "neckline_preference": "",
                   "max_top_up_minor": 1000, "currency": "USD", "note": ""}
        payload.update(overrides)
        return self.send("put", f"/api/sessions/{session_id}/preferences", payload, client)

    def choice(self, session_id, sku="CALM-01", client=None):
        return self.send("post", f"/api/sessions/{session_id}/quotes",
                         {"selected_sku": sku}, client)

    def demo_asset(self, sid, client=None, model="model-a"):
        return self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model}, client).json()["asset_id"]

    def preview(self, sid, skus=("CALM-07",), client=None, **extra):
        if "asset_id" not in extra:
            extra["asset_id"] = self.demo_asset(sid, client)
        payload = {"candidate_skus": list(skus), "consent": True, **extra}
        return self.send("post", f"/api/sessions/{sid}/previews", payload, client)

    def ready(self):
        sid = self.start()
        self.assertEqual(self.prefs(sid).status_code, 200)
        return sid

    def test_01_two_catalog_choices_and_reasons(self):
        sid = self.ready()
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual(len(data["candidates"]), 2)
        self.assertIn("long sleeves", data["candidates"][0]["reasons"])

    def test_02_unknown_and_wrong_retailer_skus_rejected(self):
        sid = self.ready()
        wrong = ProductVariant.objects.get(sku="CALM-01")
        wrong.retailer_id = "other"
        wrong.save()
        for sku in ("UNKNOWN", "CALM-01"):
            with self.subTest(sku=sku):
                self.assertEqual(self.choice(sid, sku).status_code, 422)
        self.assertFalse(Quote.objects.exists())

    def test_03_other_browser_cannot_read_session(self):
        sid = self.ready()
        other = Client()
        self.assertEqual(other.get(f"/api/sessions/{sid}/candidates").status_code, 404)

    def test_04_out_of_stock_excluded(self):
        sid = self.ready()
        item = ProductVariant.objects.get(sku="CALM-01")
        item.stock_qty = 0
        item.save()
        results = self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]
        self.assertNotIn("CALM-01", [x["sku"] for x in results])

    def test_05_missing_required_metadata_excluded(self):
        sid = self.ready()
        item = ProductVariant.objects.get(sku="CALM-01")
        item.pattern_level = None
        item.save()
        results = self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]
        self.assertNotIn("CALM-01", [x["sku"] for x in results])

    def test_06_invalid_money_rejected(self):
        sid = self.start()
        for invalid in (-1, "10.5", 2.2):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.prefs(sid, max_top_up_minor=invalid).status_code, 422)

    def test_07_currency_mismatch_has_no_quote(self):
        sid = self.ready()
        item = ProductVariant.objects.get(sku="CALM-01")
        item.currency = "EUR"
        item.save()
        self.assertEqual(self.choice(sid).status_code, 422)
        self.assertFalse(Quote.objects.exists())

    def test_08_budget_boundary(self):
        sid = self.ready()
        item = ProductVariant.objects.get(sku="CALM-01")
        item.price_minor = 5000
        item.save()
        self.assertEqual(self.choice(sid).status_code, 201)
        item.price_minor = 5001
        item.save()
        self.assertEqual(self.choice(sid).status_code, 422)

    def test_09_uses_eligible_credit_not_list_price(self):
        sid = self.ready()
        data = self.choice(sid).json()
        self.assertEqual(data["credit_minor"], 4000)
        self.assertEqual(data["top_up_minor"], 500)

    def test_10_unknown_fee_blocks_quote(self):
        sid = self.ready()
        policy = PolicySnapshot.objects.get(retailer_id="secondlook-demo")
        policy.fee_minor = None
        policy.save()
        self.assertEqual(self.choice(sid).status_code, 422)

    def test_11_cheaper_replacement_uses_staff_path(self):
        sid = self.ready()
        item = ProductVariant.objects.get(sku="CALM-01")
        item.price_minor = 3900
        item.save()
        self.assertEqual(self.choice(sid).status_code, 422)

    def test_12_partial_quantity_refused(self):
        sid = self.ready()
        line = OrderLine.objects.get(token="DEMO-ORDER-001")
        line.quantity = 2
        line.save()
        self.assertEqual(self.choice(sid).status_code, 422)

    def test_13_expired_policy_blocks_candidates(self):
        sid = self.ready()
        policy = PolicySnapshot.objects.get(retailer_id="secondlook-demo")
        policy.expires_at = timezone.now() - timedelta(seconds=1)
        policy.save()
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/candidates").status_code, 422)

    def test_14_no_match_does_not_relax(self):
        sid = self.start()
        self.prefs(sid, max_top_up_minor=0)
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual(data["status"], "NO_MATCH")
        self.assertEqual(data["candidates"], [])
        self.assertTrue(data["return_option"])

    def test_15_malformed_json_cannot_set_preferences(self):
        sid = self.start()
        response = self.client.put(f"/api/sessions/{sid}/preferences", data="{bad",
                                   content_type="application/json")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/candidates").status_code, 422)

    def test_16_consent_required(self):
        sid = self.ready()
        response = self.send("post", f"/api/sessions/{sid}/previews",
                             {"candidate_skus": ["CALM-07"], "consent": False})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(PreviewJob.objects.exists())

    def test_19_mock_previews_disclose_source(self):
        sid = self.ready()
        data = self.send("post", f"/api/sessions/{sid}/previews",
                         {"candidate_skus": ["CALM-07"], "consent": True}).json()
        self.assertEqual(data["provider"], "mock")
        self.assertIn("No generated preview", data["jobs"][0]["label"])
        self.assertEqual(PreviewJob.objects.get().observed_units, 0)

    def test_21_two_mock_jobs_are_reused_on_refresh(self):
        sid = self.ready()
        payload = {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True}
        one = self.send("post", f"/api/sessions/{sid}/previews", payload).json()
        two = self.send("post", f"/api/sessions/{sid}/previews", payload).json()
        self.assertEqual(one["jobs"], two["jobs"])
        self.assertEqual(PreviewJob.objects.count(), 2)

    def test_20_uncertain_start_is_not_duplicated(self):
        sid = self.ready()
        payload = {"candidate_skus": ["CALM-07"], "consent": True}
        first = self.send("post", f"/api/sessions/{sid}/previews", payload).json()
        PreviewJob.objects.filter(id=first["jobs"][0]["job_id"]).update(state="uncertain")
        second = self.send("post", f"/api/sessions/{sid}/previews", payload).json()
        self.assertEqual(second["jobs"][0]["state"], "uncertain")
        self.assertEqual(PreviewJob.objects.count(), 1)

    def test_21_partial_preview_failure_is_disclosed(self):
        sid = self.ready()
        payload = {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True}
        first = self.send("post", f"/api/sessions/{sid}/previews", payload).json()
        PreviewJob.objects.filter(id=first["jobs"][0]["job_id"]).update(state="failed")
        second = self.send("post", f"/api/sessions/{sid}/previews", payload).json()
        self.assertEqual([x["state"] for x in second["jobs"]], ["failed", "succeeded"])

    def test_22_price_change_invalidates_draft(self):
        sid = self.ready()
        quote = self.choice(sid).json()
        item = ProductVariant.objects.get(sku="CALM-01")
        item.price_minor += 100
        item.save()
        response = self.send("post", "/api/requests",
                             {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                             HTTP_IDEMPOTENCY_KEY="price-change")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ExchangeRequest.objects.exists())

    def test_23_expired_quote_invalidates_draft(self):
        sid = self.ready()
        quote = self.choice(sid).json()
        Quote.objects.filter(id=quote["quote_id"]).update(expires_at=timezone.now()-timedelta(seconds=1))
        response = self.send("post", "/api/requests",
                             {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                             HTTP_IDEMPOTENCY_KEY="expired")
        self.assertEqual(response.status_code, 409)

    def test_24_duplicate_confirm_is_one_draft(self):
        sid = self.ready()
        quote = self.choice(sid).json()
        payload = {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True}
        one = self.send("post", "/api/requests", payload, HTTP_IDEMPOTENCY_KEY="same-key")
        two = self.send("post", "/api/requests", payload, HTTP_IDEMPOTENCY_KEY="same-key")
        self.assertEqual(one.status_code, 201)
        self.assertEqual(two.status_code, 200)
        self.assertEqual(one.json()["request_id"], two.json()["request_id"])
        self.assertEqual(ExchangeRequest.objects.count(), 1)

    def test_25_other_browser_cannot_access_quote_or_preview(self):
        sid = self.ready()
        quote = self.choice(sid).json()
        job = self.send("post", f"/api/sessions/{sid}/previews",
                        {"candidate_skus": ["CALM-07"], "consent": True}).json()["jobs"][0]
        other = Client()
        self.assertEqual(other.get(f"/api/previews/{job['job_id']}").status_code, 404)
        response = self.send("post", "/api/requests",
                             {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                             other, HTTP_IDEMPOTENCY_KEY="other")
        self.assertEqual(response.status_code, 404)

    def test_17_and_18_file_type_and_remote_url_rejected(self):
        sid = self.ready()
        with override_settings(ALLOW_PHOTO_UPLOAD=True):
            bad_file = SimpleUploadedFile("bad.gif", b"GIF89a", content_type="image/gif")
            response = self.client.post(f"/api/sessions/{sid}/assets",
                                        {"image": bad_file, "adult_permission": "true"})
            self.assertEqual(response.status_code, 422)
            response = self.client.post(f"/api/sessions/{sid}/assets",
                                        {"url": "http://127.0.0.1/private", "adult_permission": "true"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "URL_NOT_ALLOWED")
            self.assertFalse(PrivateAsset.objects.exists())

    def test_26_private_asset_expiry_and_staff_review(self):
        sid = self.ready()
        image = Image.new("RGB", (512, 512), "#789aab")
        out = io.BytesIO()
        image.save(out, format="PNG")
        with tempfile.TemporaryDirectory() as directory, override_settings(
            ALLOW_PHOTO_UPLOAD=True, PRIVATE_MEDIA_ROOT=directory
        ):
            upload = SimpleUploadedFile("synthetic.png", out.getvalue(), content_type="image/png")
            response = self.client.post(f"/api/sessions/{sid}/assets",
                                        {"image": upload, "adult_permission": "true"})
            self.assertEqual(response.status_code, 201, response.content)
            asset = PrivateAsset.objects.get()
            path = private_path(asset.storage_key)
            self.assertTrue(path.exists())
            PrivateAsset.objects.filter(pk=asset.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
            call_command("cleanup_private_assets", verbosity=0)
            self.assertFalse(path.exists())
            self.assertFalse(PrivateAsset.objects.exists())
        quote = self.choice(sid).json()
        draft = self.send("post", "/api/requests",
                          {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                          HTTP_IDEMPOTENCY_KEY="staff-test").json()
        line = OrderLine.objects.get(token="DEMO-ORDER-001")
        original_stock = ProductVariant.objects.get(sku="CALM-01").stock_qty
        staff = get_user_model().objects.create_user("staff", password="local-test-only", is_staff=True)
        staff_client = Client()
        staff_client.force_login(staff)
        response = self.send("patch", f"/api/staff/requests/{draft['request_id']}",
                             {"state": "REVIEWED"}, staff_client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ExchangeRequest.objects.get().state, "REVIEWED")
        self.assertEqual(ProductVariant.objects.get(sku="CALM-01").stock_qty, original_stock)
        self.assertEqual(OrderLine.objects.get(pk=line.pk).paid_minor, line.paid_minor)

    def test_27_closed_session_refuses_new_quotes_previews_and_drafts(self):
        sid = self.ready()
        quote = self.choice(sid).json()
        payload = {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True}
        first = self.send("post", "/api/requests", payload, HTTP_IDEMPOTENCY_KEY="first")
        self.assertEqual(first.status_code, 201)
        second_quote = self.choice(sid, "CALM-04")
        self.assertEqual(second_quote.status_code, 409)
        self.assertEqual(second_quote.json()["error"]["code"], "SESSION_CLOSED")
        preview = self.send("post", f"/api/sessions/{sid}/previews",
                            {"candidate_skus": ["CALM-07"], "consent": True})
        self.assertEqual(preview.status_code, 409)
        other_key = self.send("post", "/api/requests", payload, HTTP_IDEMPOTENCY_KEY="second")
        self.assertEqual(other_key.status_code, 409)
        replay = self.send("post", "/api/requests", payload, HTTP_IDEMPOTENCY_KEY="first")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["request_id"], first.json()["request_id"])
        self.assertEqual(Quote.objects.count(), 1)
        self.assertEqual(ExchangeRequest.objects.count(), 1)
        self.assertFalse(PreviewJob.objects.exists())

    def test_28_session_status_only_moves_forward(self):
        sid = self.ready()
        self.client.get(f"/api/sessions/{sid}/candidates")
        self.assertEqual(self.choice(sid).status_code, 201)
        self.client.get(f"/api/sessions/{sid}/candidates")
        self.send("post", f"/api/sessions/{sid}/previews",
                  {"candidate_skus": ["CALM-07"], "consent": True})
        self.assertEqual(ExchangeSession.objects.get(id=sid).status, "QUOTE_READY")
        self.assertEqual(self.prefs(sid).status_code, 200)
        self.assertEqual(ExchangeSession.objects.get(id=sid).status, "PREFERENCES_CONFIRMED")

    def test_29_mock_job_runs_through_lifecycle(self):
        sid = self.ready()
        job = self.preview(sid).json()["jobs"][0]
        self.assertEqual(job["state"], "succeeded")
        record = PreviewJob.objects.get(id=job["job_id"])
        self.assertTrue(record.provider_task_id.startswith("mock-"))
        self.assertIsNotNone(record.started_at)
        self.assertIsNotNone(record.ended_at)
        self.assertEqual((record.reserved_units, record.observed_units), (0, 0))
        self.assertEqual(EvidenceEvent.objects.filter(action="preview_job_ended").count(), 1)
        status = self.client.get(f"/api/previews/{job['job_id']}").json()
        self.assertEqual((status["state"], status["image_url"]), ("succeeded", None))
        self.assertIn("No generated preview", status["label"])

    def test_30_rejected_start_fails_once_and_costs_nothing(self):
        calls = []

        def reject(job):
            raise ProviderRejected("GARMENT_UNSUPPORTED")
        sid = self.ready()
        with provider(fake_provider(start=reject, calls=calls)):
            first = self.preview(sid).json()["jobs"][0]
            again = self.preview(sid).json()["jobs"][0]
        self.assertEqual((first["state"], first["error_code"]), ("failed", "GARMENT_UNSUPPORTED"))
        self.assertEqual((again["job_id"], again["state"]), (first["job_id"], "failed"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(preview_jobs.committed_units(), 0)

    def test_31_unknown_start_outcome_is_uncertain_and_keeps_reservation(self):
        calls = []

        def lost(job):
            raise TimeoutError("connection dropped")
        sid = self.ready()
        with provider(fake_provider(start=lost, calls=calls)), \
                self.assertLogs("exchange.preview_jobs", "ERROR"):
            first = self.preview(sid).json()["jobs"][0]
            again = self.preview(sid).json()["jobs"][0]
        self.assertEqual((first["state"], first["error_code"]), ("uncertain", "START_UNCERTAIN"))
        self.assertEqual(again["state"], "uncertain")
        self.assertEqual(len(calls), 1)
        self.assertEqual(preview_jobs.committed_units(), 2)

    def test_32_unit_ceiling_blocks_paid_starts(self):
        calls = []
        sid = self.ready()
        with provider(fake_provider(calls=calls), ceiling=3):
            jobs = self.preview(sid, ("CALM-07", "CALM-04")).json()["jobs"]
        self.assertEqual([j["state"] for j in jobs], ["succeeded", "failed"])
        self.assertEqual(jobs[1]["error_code"], "BUDGET_CAP")
        self.assertEqual([c.sku for c in calls], ["CALM-07"])
        self.assertEqual(preview_jobs.committed_units(), 2)
        other = self.ready()
        with provider(fake_provider(calls=calls), ceiling=0):
            blocked = self.preview(other).json()["jobs"][0]
        self.assertEqual((blocked["state"], blocked["error_code"]), ("failed", "BUDGET_CAP"))
        self.assertEqual(len(calls), 1)

    def test_33_consent_change_before_start_blocks_transmission(self):
        calls = []
        sid = self.ready()
        with provider(fake_provider(calls=calls)), \
                mock.patch("exchange.views.drive_preview_job.delay"):
            job = self.preview(sid).json()["jobs"][0]
            self.assertEqual(job["state"], "pending")
            session = ExchangeSession.objects.get(id=sid)
            session.consent_given = False
            session.consent_version += 1
            session.save()
            self.assertEqual(preview_jobs.advance(job["job_id"]), "failed")
        self.assertEqual(PreviewJob.objects.get().error_code, "CONSENT_CHANGED")
        self.assertEqual(calls, [])

    def test_34_queue_outage_leaves_job_pending_for_the_next_request(self):
        sid = self.ready()
        with mock.patch("exchange.views.drive_preview_job.delay", side_effect=BrokerError("down")), \
                self.assertLogs("exchange.views", "WARNING"):
            job = self.preview(sid).json()["jobs"][0]
        self.assertEqual(job["state"], "pending")
        again = self.preview(sid).json()["jobs"][0]
        self.assertEqual((again["job_id"], again["state"]), (job["job_id"], "succeeded"))
        self.assertEqual(PreviewJob.objects.count(), 1)

    def test_35_generated_preview_is_private_and_deleted_with_the_photo(self):
        calls = []
        result = png_bytes("#c36a4d", 400)
        sid = self.ready()
        with tempfile.TemporaryDirectory() as directory, override_settings(
            ALLOW_PHOTO_UPLOAD=True, PRIVATE_MEDIA_ROOT=directory
        ), provider(fake_provider(calls=calls, poll=lambda task: PollResult(SUCCEEDED, 2, image=result))):
            upload = SimpleUploadedFile("synthetic.png", png_bytes(), content_type="image/png")
            asset_id = self.client.post(f"/api/sessions/{sid}/assets",
                                        {"image": upload, "adult_permission": "true"}).json()["asset_id"]
            job = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
            self.assertEqual(job["state"], "succeeded")
            self.assertTrue(job["image_url"].startswith("/api/assets/"))
            self.assertEqual(job["label"], "AI preview. Appearance may differ. Check the product details.")
            person = PrivateAsset.objects.get(id=asset_id)
            self.assertEqual(calls[0].person_image, private_path(person.storage_key).read_bytes())
            image = self.client.get(job["image_url"])
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image["Cache-Control"], "private, no-store")
            self.assertTrue(image.content.startswith(b"\xff\xd8"))
            # Closing a streamed response fires request_finished, whose close_old_connections would drop the
            # test's Postgres connection mid-transaction; the file is still closed, the connection kept.
            request_finished.disconnect(close_old_connections)
            try:
                image.close()
            finally:
                request_finished.connect(close_old_connections)
            self.assertEqual(Client().get(job["image_url"]).status_code, 404)
            generated = PrivateAsset.objects.get(kind="preview")
            generated_path = private_path(generated.storage_key)
            self.assertTrue(generated_path.exists())
            self.assertEqual(self.client.delete(f"/api/assets/{asset_id}").status_code, 200)
            self.assertFalse(generated_path.exists())
            self.assertFalse(PrivateAsset.objects.exists())
            self.assertFalse(ExchangeSession.objects.get(id=sid).consent_given)
            status = self.client.get(f"/api/previews/{job['job_id']}").json()
            self.assertIsNone(status["image_url"])

    def test_36_poll_timeout_marks_uncertain_and_keeps_reservation(self):
        sid = self.ready()
        with provider(fake_provider(poll=lambda task: PollResult(RUNNING, 1)),
                      PREVIEW_MAX_POLLS=2, PREVIEW_POLL_SECONDS=0):
            job = self.preview(sid).json()["jobs"][0]
        self.assertEqual((job["state"], job["error_code"]), ("uncertain", "POLL_TIMEOUT"))
        record = PreviewJob.objects.get()
        self.assertEqual((record.reserved_units, record.observed_units), (2, 1))
        self.assertEqual(preview_jobs.committed_units(), 2)

    def test_37_live_provider_is_still_refused_by_settings(self):
        env = {**os.environ, "PREVIEW_PROVIDER": "youcam", "DATABASE_URL": "sqlite:///unused.sqlite3"}
        result = subprocess.run([sys.executable, "-c", "import config.settings"],
                                cwd=settings.BASE_DIR, env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Live provider is disabled", result.stderr)

    def test_38_seed_links_every_catalog_photo(self):
        tops = ProductVariant.objects.filter(category="tops")
        self.assertEqual(tops.values("style_code").distinct().count(), 13)
        self.assertEqual(tops.count(), 16)
        # Beauty shades are hex swatches with no photo; jewellery, shoes and bags have their own product photos.
        self.assertFalse(ProductVariant.objects.exclude(category__in=("tops", "earrings", "necklace", "shoes", "bag"))
                         .exclude(image_asset_id="").exists())
        for item in ProductVariant.objects.filter(category__in=("shoes", "bag")):
            self.assertEqual(item.image_asset_id, f"exchange/accessories/{item.sku}.jpg")
            self.assertIsNotNone(finders.find(item.image_asset_id))
        for item in ProductVariant.objects.filter(category__in=("earrings", "necklace")):
            self.assertEqual(item.image_asset_id, f"exchange/jewellery/{item.sku}.jpg")
            self.assertIsNotNone(finders.find(item.image_asset_id))
        for item in tops:
            with self.subTest(sku=item.sku):
                self.assertEqual(item.image_asset_id, f"exchange/catalog/{item.style_code}.jpg")
                self.assertIsNotNone(finders.find(item.image_asset_id))

    def test_39_order_and_candidates_show_labelled_catalog_photos(self):
        start = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()
        self.assertEqual(start["order"]["image_url"], "/static/exchange/catalog/ORIGINAL.jpg")
        self.assertIn("busy pattern", start["order"]["image_alt"])
        sid = start["session_id"]
        self.assertEqual(self.prefs(sid).status_code, 200)
        found = self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]
        self.assertEqual([item["sku"] for item in found], ["CALM-07", "CALM-04"])
        for item in found:
            with self.subTest(sku=item["sku"]):
                self.assertEqual(item["image_url"], f"/static/exchange/catalog/{item['sku']}.jpg")
                self.assertTrue(item["image_alt"].startswith(f"Photo of {item['title']}"))
                self.assertIn("long-sleeve", item["image_alt"])
                self.assertIn("quiet pattern", item["image_alt"])
                self.assertIn("Pixabay", item["image_label"])

    def test_40_manifest_matches_shipped_photos_sources_and_seed(self):
        directory = Path(finders.find("exchange/catalog/manifest.json")).parent
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(manifest["license"]["name"], "Pixabay Content License")
        entries = {entry["sku"]: entry for entry in manifest["images"]}
        variants = {item.style_code: item for item in ProductVariant.objects.filter(category="tops")}
        self.assertEqual(set(entries), set(variants))
        self.assertEqual({p.name for p in directory.iterdir() if p.suffix in (".jpg", ".png")},
                         {entry["file"] for entry in entries.values()})
        self.assertEqual({sku for sku, entry in entries.items() if entry["retouched"]},
                         {"ORIGINAL", "BOLD-02", "CALM-01", "CALM-02"})
        for sku, entry in entries.items():
            with self.subTest(sku=sku):
                data = (directory / entry["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                with Image.open(io.BytesIO(data)) as image:
                    self.assertEqual(image.size, (entry["width"], entry["height"]))
                self.assertTrue(entry["source"]["page"].startswith("https://pixabay.com/"))
                self.assertEqual(len(entry["source"]["source_sha256"]), 64)
                self.assertFalse(entry["source"]["ai_generated"])
                item = variants[sku]
                self.assertEqual(
                    (entry["attributes"]["color_tag"], entry["attributes"]["sleeve"],
                     entry["attributes"]["neckline"]),
                    (item.color_tag, item.sleeve, item.neckline))
                self.assertEqual(entry["attributes"]["pattern"],
                                 {0: "quiet", 1: "moderate", 2: "busy"}[item.pattern_level])

    def test_41_missing_image_falls_back_to_placeholder(self):
        ProductVariant.objects.filter(sku="CALM-07").update(image_asset_id="")
        sid = self.ready()
        found = {x["sku"]: x for x in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]}
        self.assertEqual((found["CALM-07"]["image_url"], found["CALM-07"]["image_label"]), (None, None))
        self.assertIsNotNone(found["CALM-04"]["image_url"])

    def test_42_try_on_models_are_synthetic_private_and_paired(self):
        sources = json.loads((Path(settings.BASE_DIR) / "assets" / "pixabay_sources.json").read_text())
        names = {model["name"] for model in sources["models"]}
        self.assertTrue(all(model["source"]["ai_generated"] for model in sources["models"]))
        static_dir = Path(finders.find("exchange/catalog/manifest.json")).parent.parent
        public = {p.stem: p.parent.name for p in static_dir.rglob("*") if p.stem in names}
        demo = {m["name"] for m in sources["models"] if m["use"] == "main"}
        self.assertEqual(public, {name: "models" for name in demo})
        catalog = {entry["sku"] for entry in sources["catalog"]}
        mains = {model["name"] for model in sources["models"] if model["use"] == "main"}
        self.assertGreaterEqual(len(mains), 2)
        self.assertTrue(8 <= len(sources["pairs"]) <= 12)
        for pair in sources["pairs"]:
            with self.subTest(pair=pair):
                self.assertIn(pair["model"], mains)
                self.assertIn(pair["sku"], catalog)

    def test_43_reseeding_retires_stale_demo_tops(self):
        ProductVariant.objects.create(retailer_id="secondlook-demo", sku="CALM-05", title="Old top",
                                      size_label="M", price_minor=4700, stock_qty=5)
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())
        self.assertFalse(ProductVariant.objects.filter(sku="CALM-05").exists())
        self.assertEqual(ProductVariant.objects.filter(category="tops").count(), 16)

    @contextlib.contextmanager
    def youcam(self, fake, ceiling=10):
        adapter = lambda: YouCamClothesProvider("test-only-key", base_url=YOUCAM_API, transport=fake)
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=ceiling,
                               PREVIEW_DAILY_UNIT_CAP=ceiling), \
                mock.patch.dict(preview_jobs.PROVIDERS, {"youcam": adapter}):
            yield

    def upload_person(self, sid):
        upload = SimpleUploadedFile("synthetic.png", png_bytes(), content_type="image/png")
        response = self.client.post(f"/api/sessions/{sid}/assets", {"image": upload, "adult_permission": "true"})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["asset_id"]

    def test_44_youcam_needs_a_key_and_is_refused_without_one(self):
        sid = self.ready()
        with override_settings(PREVIEW_PROVIDER="youcam", YOUCAM_API_KEY=""):
            response = self.preview(sid)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "PREVIEW_UNAVAILABLE")
        self.assertFalse(PreviewJob.objects.exists())
        with override_settings(YOUCAM_API_KEY="test-only-key"):
            built = preview_jobs.get_provider("youcam")
        self.assertEqual((built.name, built.version, built.units_per_job), ("youcam", "cloth-v4", 2))

    def test_45_zero_unit_ceiling_stops_youcam_before_any_request(self):
        fake = FakeYouCamTransport()
        sid = self.ready()
        with tempfile.TemporaryDirectory() as directory, override_settings(
            ALLOW_PHOTO_UPLOAD=True, PRIVATE_MEDIA_ROOT=directory
        ), self.youcam(fake, ceiling=0):
            asset_id = self.upload_person(sid)
            job = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
        self.assertEqual((job["state"], job["error_code"]), ("failed", "BUDGET_CAP"))
        self.assertEqual(fake.calls, [])

    def test_46_youcam_job_runs_end_to_end_against_the_fake(self):
        fake = FakeYouCamTransport(result_image=png_bytes("#7a5c8e", 600))
        sid = self.ready()
        with tempfile.TemporaryDirectory() as directory, override_settings(
            ALLOW_PHOTO_UPLOAD=True, PRIVATE_MEDIA_ROOT=directory
        ), self.youcam(fake):
            asset_id = self.upload_person(sid)
            job = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
            self.assertEqual(job["state"], "succeeded")
            self.assertEqual(job["label"], "AI preview. Appearance may differ. Check the product details.")
            self.assertTrue(job["image_url"].startswith("/api/assets/"))
            person = PrivateAsset.objects.get(id=asset_id)
            self.assertEqual(fake.uploads["file-1"], private_path(person.storage_key).read_bytes())
            catalog_photo = Path(finders.find("exchange/catalog/CALM-07.jpg")).read_bytes()
            self.assertEqual(fake.uploads["file-2"], catalog_photo)
            record = PreviewJob.objects.get()
            self.assertEqual((record.provider, record.provider_version, record.provider_task_id),
                             ("youcam", "cloth-v4", "task-1"))
            self.assertEqual((record.reserved_units, record.observed_units), (2, 2))
            self.assertEqual(PrivateAsset.objects.filter(kind="preview").count(), 1)
        self.assertEqual(len(fake.api_calls("POST", "/s2s/v2.0/task/cloth-v4")), 1)

    def test_47_lost_youcam_task_request_is_uncertain_and_never_resent(self):
        fake = FakeYouCamTransport(task=YouCamTransportError("TimeoutError"))
        sid = self.ready()
        with tempfile.TemporaryDirectory() as directory, override_settings(
            ALLOW_PHOTO_UPLOAD=True, PRIVATE_MEDIA_ROOT=directory
        ), self.youcam(fake), self.assertLogs("exchange.preview_jobs", "ERROR"):
            asset_id = self.upload_person(sid)
            first = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
            again = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
        self.assertEqual((first["state"], first["error_code"]), ("uncertain", "TASK_REQUEST_LOST"))
        self.assertEqual((again["job_id"], again["state"]), (first["job_id"], "uncertain"))
        self.assertEqual(len(fake.api_calls("POST", "/s2s/v2.0/task/cloth-v4")), 1)
        self.assertEqual(preview_jobs.committed_units(), 2)
