import contextlib
import io
import json
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from exchange import preview_jobs
from exchange.models import PreviewJob
from exchange.tests import fake_provider, png_bytes
from secondlook.providers import FAILED, SUCCEEDED, PollResult

LIMITS = dict(PREVIEW_UNIT_CEILING=100, PREVIEW_DAILY_UNIT_CAP=100, PREVIEW_SESSION_UNIT_CAP=100)
AI_LABEL = "AI preview. Appearance may differ. Check the product details."


def polls(*results):
    """A poll function returning each result in turn, then repeating the last."""
    queue = list(results)
    return lambda task_id: queue.pop(0) if len(queue) > 1 else queue[0]


class PreviewStepTests(TestCase):
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

    def send(self, method, path, payload=None, client=None):
        return getattr(client or self.client, method)(path, data=json.dumps(payload or {}),
                                                      content_type="application/json")

    def session(self):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000, "currency": "USD"})
        return sid

    def person(self, sid, model="model-b"):
        return self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model}).json()["asset_id"]

    def preview(self, sid, skus=("CALM-07",), asset_id=None):
        payload = {"candidate_skus": list(skus), "consent": True, **({"asset_id": asset_id} if asset_id else {})}
        return self.send("post", f"/api/sessions/{sid}/previews", payload)

    def retry(self, job_id, client=None):
        return self.send("post", f"/api/previews/{job_id}/retry", {}, client)

    @contextlib.contextmanager
    def paid(self, provider_cls, **limits):
        with override_settings(PREVIEW_PROVIDER="fake", **{**LIMITS, **limits}), \
                mock.patch.dict(preview_jobs.PROVIDERS, {"fake": provider_cls}):
            yield

    def test_live_mode_requires_a_person_but_the_mock_does_not(self):
        sid = self.session()
        with self.paid(fake_provider()):
            refused = self.preview(sid)
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (422, "PHOTO_REQUIRED"))
        self.assertFalse(PreviewJob.objects.exists())
        mock_job = self.preview(sid).json()["jobs"][0]
        self.assertEqual((mock_job["state"], mock_job["attempt"], mock_job["retry_allowed"]), ("succeeded", 1, False))
        self.assertIn("No generated preview", mock_job["message"])

    def test_a_failed_preview_can_be_retried_once_and_the_retry_is_shown_from_then_on(self):
        result = png_bytes("#557799", 600)
        provider = fake_provider(poll=polls(PollResult(FAILED, 2, "unknown_internal_error"),
                                            PollResult(SUCCEEDED, 2, image=result)))
        sid = self.session()
        with self.paid(provider):
            asset_id = self.person(sid)
            first = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
            self.assertEqual((first["state"], first["retry_allowed"]), ("failed", True))
            self.assertEqual(first["message"], "The preview failed. You can try once more.")
            second = self.retry(first["job_id"])
            self.assertEqual(second.status_code, 201)
            retried = second.json()
            self.assertEqual((retried["state"], retried["attempt"], retried["label"]), ("succeeded", 2, AI_LABEL))
            self.assertTrue(retried["image_url"].startswith("/api/assets/"))
            again = self.retry(first["job_id"])
            self.assertEqual((again.status_code, again.json()["error"]["message"]),
                             (409, "This preview was already tried again."))
            self.assertEqual(self.retry(retried["job_id"]).status_code, 409)
            shown = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
            self.assertEqual(shown["job_id"], retried["job_id"])
        self.assertEqual(PreviewJob.objects.count(), 2)
        self.assertEqual(preview_jobs.committed_units(), 4)

    def test_unknown_outcomes_and_photo_problems_are_not_retried(self):
        def lost(job):
            raise TimeoutError("connection dropped")
        sid = self.session()
        with self.paid(fake_provider(start=lost)), self.assertLogs("exchange.preview_jobs", "ERROR"):
            unclear = self.preview(sid, asset_id=self.person(sid)).json()["jobs"][0]
        self.assertEqual((unclear["state"], unclear["retry_allowed"]), ("uncertain", False))
        self.assertIn("will not be retried", unclear["message"])
        self.assertEqual(self.retry(unclear["job_id"]).json()["error"]["code"], "RETRY_NOT_ALLOWED")
        sid = self.session()
        with self.paid(fake_provider(poll=polls(PollResult(FAILED, 2, "error_pose")))):
            photo = self.preview(sid, asset_id=self.person(sid)).json()["jobs"][0]
            refused = self.retry(photo["job_id"])
        self.assertEqual(photo["message"], "This photo did not work for a preview. Try a different photo or a demo model.")
        self.assertEqual((refused.status_code, refused.json()["error"]["message"]),
                         (409, "This photo did not work. Try a different photo or a demo model instead."))

    def test_retries_respect_the_session_cap_the_pause_and_ownership(self):
        provider = fake_provider(poll=polls(PollResult(FAILED, 2, "unknown_internal_error")))
        sid = self.session()
        with self.paid(provider, PREVIEW_SESSION_UNIT_CAP=4):
            jobs = self.preview(sid, ["CALM-07", "CALM-04"], self.person(sid)).json()["jobs"]
            self.assertEqual([j["state"] for j in jobs], ["failed", "failed"])
            capped = self.retry(jobs[0]["job_id"])
            self.assertEqual((capped.status_code, capped.json()["error"]["code"]), (429, "PREVIEW_LIMIT"))
            self.assertEqual(self.retry(jobs[0]["job_id"], client=Client()).status_code, 404)
            call_command("preview_switch", "pause", "--reason", "test", stdout=io.StringIO())
            self.assertEqual(self.retry(jobs[0]["job_id"]).json()["error"]["code"], "PREVIEWS_PAUSED")
        self.assertEqual(PreviewJob.objects.count(), 2)

    def test_no_retry_once_the_photo_is_removed(self):
        provider = fake_provider(poll=polls(PollResult(FAILED, 2, "unknown_internal_error")))
        sid = self.session()
        with self.paid(provider):
            asset_id = self.person(sid)
            job = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
            self.assertEqual(self.client.delete(f"/api/assets/{asset_id}").status_code, 200)
            refused = self.retry(job["job_id"])
        self.assertEqual((refused.status_code, refused.json()["error"]["message"]),
                         (409, "The photo for this preview was removed."))

    def test_staff_page_shows_preview_provenance_without_photos(self):
        provider = fake_provider(poll=polls(PollResult(FAILED, 2, "unknown_internal_error"),
                                            PollResult(SUCCEEDED, 2, image=png_bytes())))
        sid = self.session()
        with self.paid(provider):
            asset_id = self.person(sid, "model-b")
            first = self.preview(sid, asset_id=asset_id).json()["jobs"][0]
            self.retry(first["job_id"])
        # The uniform square test image fails the fidelity gate (its shape differs from the photo), so the
        # shopper chooses it knowingly, as the page allows.
        warning = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"})
        self.assertEqual(warning.status_code, 422)
        quoted = self.send("post", f"/api/sessions/{sid}/quotes", {
            "selected_sku": "CALM-07", "accept_preview_concern": True,
            "preview_concern_token": warning.json()["error"]["concern_token"]})
        self.assertEqual(quoted.status_code, 201)
        quote = quoted.json()
        draft = self.client.post("/api/requests", data=json.dumps({"quote_id": quote["quote_id"], "quote_version": 1,
                                                           "confirmed": True}),
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="staff-provenance")
        self.assertEqual(draft.status_code, 201)
        staff = get_user_model().objects.create_user("staff", password="local-test-only", is_staff=True)
        reviewer = Client()
        reviewer.force_login(staff)
        html = reviewer.get("/staff/").content.decode()
        for text in ("Preview provenance", "Demo model-b", "fake fake-v1", "failed · unknown_internal_error",
                     "succeeded", "Retry", "2 / 2", "Photo consent: given"):
            self.assertIn(text, html)
        self.assertNotIn("/api/assets/", html)

    def test_the_current_top_is_previewed_on_the_same_person_beside_the_alternatives(self):
        sid = self.session()
        asset_id = self.person(sid)
        payload = {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True, "asset_id": asset_id,
                   "include_original": True}
        jobs = self.send("post", f"/api/sessions/{sid}/previews", payload).json()["jobs"]
        self.assertEqual([(j["sku"], j["baseline"]) for j in jobs],
                         [("ORIGINAL", True), ("CALM-07", False), ("CALM-04", False)])
        self.assertEqual(len({j.person_asset_id for j in PreviewJob.objects.all()}), 1)
        again = self.send("post", f"/api/sessions/{sid}/previews", payload).json()["jobs"]
        self.assertEqual([j["job_id"] for j in again], [j["job_id"] for j in jobs])
        self.assertEqual(PreviewJob.objects.count(), 3)

    def test_the_baseline_counts_against_the_session_cap_and_is_never_chosen_by_the_request(self):
        sid = self.session()
        with self.paid(fake_provider(), PREVIEW_SESSION_UNIT_CAP=4):
            asset_id = self.person(sid)
            payload = {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True, "asset_id": asset_id,
                       "include_original": True}
            refused = self.send("post", f"/api/sessions/{sid}/previews", payload)
            self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (429, "PREVIEW_LIMIT"))
            self.assertFalse(PreviewJob.objects.exists())
            direct = self.send("post", f"/api/sessions/{sid}/previews",
                               {**payload, "candidate_skus": ["ORIGINAL"], "include_original": False})
            self.assertEqual(direct.json()["error"]["code"], "INELIGIBLE")
        with self.paid(fake_provider(), PREVIEW_SESSION_UNIT_CAP=6):
            jobs = self.send("post", f"/api/sessions/{sid}/previews", payload).json()["jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertEqual(preview_jobs.committed_units(), 6)
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).json()
        self.client.post("/api/requests", data=json.dumps({"quote_id": quote["quote_id"], "quote_version": 1,
                                                           "confirmed": True}),
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="baseline-staff")
        staff = get_user_model().objects.create_user("staff", password="local-test-only", is_staff=True)
        reviewer = Client()
        reviewer.force_login(staff)
        self.assertIn("ORIGINAL (current top)", reviewer.get("/staff/").content.decode())
