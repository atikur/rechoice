"""Current-photo checks must not use old photos or lose late concern acknowledgements."""
import io
import json
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from exchange import assistant
from exchange.models import ExchangeSession, PreviewJob
from exchange.test_assistant import ON, FakeClaude, reply


class PreviewContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(PRIVATE_MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{self.sid}/preferences", {
            "quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})

    def send(self, method, path, payload=None, **headers):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}),
                                            content_type="application/json", **headers)

    def previews(self, model, skus=None):
        person = self.send("post", f"/api/sessions/{self.sid}/demo-person", {"model": model}).json()["asset_id"]
        response = self.send("post", f"/api/sessions/{self.sid}/previews", {
            "candidate_skus": skus or ["CALM-04", "CALM-07"], "consent": True,
            "asset_id": person, "include_original": True, "mode": "recorded"})
        self.assertEqual(response.status_code, 202, response.content)
        return {j["sku"]: j for j in response.json()["jobs"]}, person

    def quote(self, job=None, accept=False):
        data = {"selected_sku": "CALM-04", "accept_preview_concern": accept}
        if job is not None:
            data["preview_job_id"] = job["job_id"]
        if accept:
            warning = self.send("post", f"/api/sessions/{self.sid}/quotes", {**data, "accept_preview_concern": False})
            data["preview_concern_token"] = warning.json().get("error", {}).get("concern_token", "")
        return self.send("post", f"/api/sessions/{self.sid}/quotes", data)

    def draft(self, quote, key="draft"):
        return self.send("post", "/api/requests", {"quote_id": quote["quote_id"],
                         "quote_version": 1, "confirmed": True}, HTTP_IDEMPOTENCY_KEY=key)

    def test_switching_photos_uses_current_batch_even_when_returning_to_reused_jobs(self):
        self.send("post", f"/api/sessions/{self.sid}/demo-person", {"model": "model-a"})
        first, _ = self.previews("model-b")
        self.assertEqual(self.quote(first["CALM-04"]).status_code, 422)
        second, _ = self.previews("model-a")
        self.assertEqual(self.quote(second["CALM-04"]).status_code, 201)
        self.assertEqual(self.quote(first["CALM-04"], accept=True).status_code, 409)
        again, _ = self.previews("model-b")
        self.assertEqual(again["CALM-04"]["job_id"], first["CALM-04"]["job_id"])
        self.assertEqual(self.quote(again["CALM-04"]).status_code, 422)

    def test_comparison_includes_gate_concern_even_if_writer_omits_it(self):
        jobs, _ = self.previews("model-b")
        warning = PreviewJob.objects.get(pk=jobs["CALM-04"]["job_id"]).gate_note
        self.assertTrue(warning)
        path = f"/api/sessions/{self.sid}/compare"
        self.assertIn(warning, self.send("post", path).json()["notes"]["CALM-04"])
        answer = {"summary": "Both options have long sleeves.", "notes": [
            {"sku": sku, "note": "Long sleeves."} for sku in ("CALM-04", "CALM-07")]}
        with override_settings(**ON), mock.patch.object(assistant, "make_client", return_value=FakeClaude(reply(answer))):
            data = self.send("post", path).json()
        self.assertEqual(data["reader"], "claude")
        self.assertIn(warning, data["notes"]["CALM-04"])

    def test_partial_failed_batch_never_reuses_another_photo(self):
        old, _ = self.previews("model-b")
        current, _ = self.previews("model-a", ["CALM-07"])
        PreviewJob.objects.filter(pk=current["CALM-07"]["job_id"]).update(state="failed", gate_note="STALE NOTE")
        data = self.send("post", f"/api/sessions/{self.sid}/compare").json()
        self.assertNotIn("Preview checks", data["notes"]["CALM-04"])
        self.assertNotIn("STALE NOTE", json.dumps(data))
        self.assertEqual(self.quote().status_code, 201)
        response = self.send("post", f"/api/sessions/{self.sid}/compare", {
            "preview_job_ids": [j["job_id"] for j in old.values()]})
        self.assertEqual(response.status_code, 409)

    def test_late_concern_requires_fresh_ack_and_draft_retries_stay_idempotent(self):
        jobs, _ = self.previews("model-a")
        job = jobs["CALM-04"]
        quote = self.quote(job).json()
        PreviewJob.objects.filter(pk=job["job_id"]).update(gate_status="concern", gate_note="Later check found a changed scene.")
        self.assertEqual(self.draft(quote).status_code, 409)
        self.assertEqual(self.quote(job).status_code, 422)
        accepted = self.quote(job, accept=True).json()
        self.assertEqual(self.draft(accepted).status_code, 201)
        self.assertTrue(self.draft(accepted).json()["duplicate"])

    def test_photo_deletion_removes_comparison_but_keeps_recorded_warning(self):
        jobs, person = self.previews("model-b")
        self.assertEqual(self.send("delete", f"/api/assets/{person}").status_code, 200)
        self.assertEqual(self.quote(jobs["CALM-04"]).status_code, 422)
        data = self.send("post", f"/api/sessions/{self.sid}/compare").json()
        self.assertNotIn("Preview checks", data["notes"]["CALM-04"])

    def test_unknown_ids_and_legacy_context_cannot_suppress_checks(self):
        jobs, _ = self.previews("model-b")
        self.assertEqual(self.quote({"job_id": "00000000-0000-0000-0000-000000000000"}, True).status_code, 409)
        self.assertEqual(self.quote({"job_id": "not-a-uuid"}, True).status_code, 422)
        ExchangeSession.objects.filter(pk=self.sid).update(active_preview_ids=None)
        self.assertEqual(self.quote(jobs["CALM-04"], True).status_code, 409)
        self.assertEqual(self.send("post", f"/api/sessions/{self.sid}/compare").status_code, 409)

    def test_pending_job_can_be_quoted_but_a_late_concern_blocks_draft(self):
        jobs, _ = self.previews("model-b")
        job = jobs["CALM-04"]
        PreviewJob.objects.filter(pk=job["job_id"]).update(state="pending")
        quote = self.quote(job).json()
        PreviewJob.objects.filter(pk=job["job_id"]).update(state="succeeded")
        self.assertEqual(self.draft(quote).status_code, 409)

    def test_old_warning_token_cannot_acknowledge_changed_warning_on_same_job(self):
        jobs, _ = self.previews("model-b")
        warning = self.quote(jobs["CALM-04"]).json()["error"]
        PreviewJob.objects.filter(pk=jobs["CALM-04"]["job_id"]).update(gate_note="A new concern arrived.")
        response = self.send("post", f"/api/sessions/{self.sid}/quotes", {
            "selected_sku": "CALM-04", "preview_job_id": jobs["CALM-04"]["job_id"],
            "accept_preview_concern": True, "preview_concern_token": warning["concern_token"]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["message"], "A new concern arrived.")

    def test_pending_baseline_blocks_draft_until_finished_but_failure_does_not(self):
        jobs, _ = self.previews("model-a")
        quote = self.quote(jobs["CALM-04"]).json()
        baseline = jobs["ORIGINAL"]["job_id"]
        PreviewJob.objects.filter(pk=baseline).update(state="pending")
        response = self.draft(quote)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PREVIEW_PENDING")
        PreviewJob.objects.filter(pk=baseline).update(state="failed")
        self.assertEqual(self.draft(quote).status_code, 201)

    def test_another_sessions_job_id_cannot_replace_the_current_warning(self):
        jobs, _ = self.previews("model-b")
        own_sid = self.sid
        self.sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{self.sid}/preferences", {
            "quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})
        other_jobs, _ = self.previews("model-a")
        self.sid = own_sid
        self.assertEqual(self.quote(other_jobs["CALM-04"], True).status_code, 409)
        self.assertEqual(self.quote(jobs["CALM-04"]).status_code, 422)

    def test_succeeded_preview_waits_for_its_gate_before_draft(self):
        jobs, _ = self.previews("model-a")
        job = jobs["CALM-04"]
        quote = self.quote(job).json()
        PreviewJob.objects.filter(pk=job["job_id"]).update(gate_status="")
        status = self.client.get(f"/api/previews/{job['job_id']}").json()
        self.assertTrue(status["gate_pending"])
        response = self.draft(quote)
        self.assertEqual(response.json()["error"]["code"], "PREVIEW_PENDING")
        PreviewJob.objects.filter(pk=job["job_id"]).update(gate_status="concern", gate_note="Late concern.")
        self.assertEqual(self.draft(quote).json()["error"]["code"], "QUOTE_CHANGED")
