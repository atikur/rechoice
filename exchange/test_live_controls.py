import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from exchange import preview_jobs, tasks
from exchange.models import EvidenceEvent, PreviewControl, PreviewJob, PrivateAsset
from exchange.tests import fake_provider, png_bytes
from secondlook.youcam_fake import API, FakeYouCamTransport, reply, task_status
from secondlook.youcam_provider import CREDIT_PATH, TASK_PATH, YouCamClothesProvider

LIMITS = dict(PREVIEW_UNIT_CEILING=100, PREVIEW_DAILY_UNIT_CAP=100, PREVIEW_SESSION_UNIT_CAP=100)


class LiveControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        patch = override_settings(ALLOW_PHOTO_UPLOAD=True, PRIVATE_MEDIA_ROOT=media.name, PREVIEW_POLL_SECONDS=0)
        patch.enable()
        self.addCleanup(patch.disable)

    def send(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json")

    def session(self, sleeve="long"):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": sleeve,
                                                             "max_top_up_minor": 1000, "currency": "USD"})
        return sid

    def photo(self, sid):
        upload = SimpleUploadedFile("adult.png", png_bytes(), content_type="image/png")
        return self.client.post(f"/api/sessions/{sid}/assets", {"image": upload, "adult_permission": "true"}).json()["asset_id"]

    def preview(self, sid, skus, asset_id=None):
        if asset_id is None:
            asset_id = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
        payload = {"candidate_skus": list(skus), "consent": True, "asset_id": asset_id}
        return self.send("post", f"/api/sessions/{sid}/previews", payload)

    @contextlib.contextmanager
    def youcam(self, fake, **limits):
        adapter = lambda: YouCamClothesProvider("test-only-key", base_url=API, transport=fake)
        with override_settings(PREVIEW_PROVIDER="youcam", **{**LIMITS, **limits}), \
                mock.patch.dict(preview_jobs.PROVIDERS, {"youcam": adapter}):
            yield

    @contextlib.contextmanager
    def paid_fake(self, **limits):
        with override_settings(PREVIEW_PROVIDER="fake", **{**LIMITS, **limits}), \
                mock.patch.dict(preview_jobs.PROVIDERS, {"fake": fake_provider()}):
            yield

    def test_settings_refuse_live_mode_until_every_requirement_is_set(self):
        base = {**os.environ, "DATABASE_URL": "sqlite:///unused.sqlite3", "DJANGO_ALLOWED_HOSTS": "rechoice.example"}
        for key in ("PREVIEW_LIVE_APPROVAL", "YOUCAM_API_KEY", "PREVIEW_UNIT_CEILING", "PREVIEW_DAILY_UNIT_CAP",
                    "YOUCAM_BALANCE_FLOOR", "CELERY_TASK_ALWAYS_EAGER", "DJANGO_DEBUG", "CACHE_URL", "PRIVATE_STORAGE"):
            base.pop(key, None)

        def load(**env):
            return subprocess.run([sys.executable, "-c", "import config.settings"], cwd=settings.BASE_DIR,
                                  env={**base, **env}, capture_output=True, text=True, timeout=60)
        unknown = load(PREVIEW_PROVIDER="youcam-v9")
        self.assertIn("Unknown PREVIEW_PROVIDER", unknown.stderr)
        bare = load(PREVIEW_PROVIDER="youcam", DJANGO_DEBUG="0", DJANGO_SECRET_KEY="test-only-settings-check-key-0123456789abcdefghijklmnop")
        self.assertNotEqual(bare.returncode, 0)
        for name in ("PREVIEW_LIVE_APPROVAL", "YOUCAM_API_KEY", "PREVIEW_UNIT_CEILING", "PREVIEW_DAILY_UNIT_CAP",
                     "YOUCAM_BALANCE_FLOOR", "CELERY_TASK_ALWAYS_EAGER=0", "CACHE_URL"):
            self.assertIn(name, bare.stderr)
        ready = load(PREVIEW_PROVIDER="youcam", DJANGO_DEBUG="0", DJANGO_SECRET_KEY="test-only-settings-check-key-0123456789abcdefghijklmnop",
                     PREVIEW_LIVE_APPROVAL="owner-2026-10-20", YOUCAM_API_KEY="test-only-key",
                     PREVIEW_UNIT_CEILING="300", PREVIEW_DAILY_UNIT_CAP="20", YOUCAM_BALANCE_FLOOR="700",
                     CELERY_TASK_ALWAYS_EAGER="0", CACHE_URL="redis://127.0.0.1:6379/1", PRIVATE_STORAGE="local")
        self.assertEqual(ready.returncode, 0, ready.stderr)

    def test_pause_refuses_new_previews_and_resume_lets_refused_jobs_run(self):
        sid = self.session()
        with mock.patch("exchange.views.drive_preview_job.delay"):
            job_id = self.preview(sid, ["CALM-07"]).json()["jobs"][0]["job_id"]
        call_command("preview_switch", "pause", "--reason", "provider incident", stdout=io.StringIO())
        refused = self.preview(sid, ["CALM-04"])
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (503, "PREVIEWS_PAUSED"))
        self.assertEqual(preview_jobs.advance(job_id), "failed")
        self.assertEqual(PreviewJob.objects.get(id=job_id).error_code, "PREVIEWS_PAUSED")
        self.assertEqual(self.client.get("/api/health").json()["previews"], "paused")
        call_command("preview_switch", "resume", stdout=io.StringIO())
        again = self.preview(sid, ["CALM-07"]).json()["jobs"][0]
        self.assertEqual((again["job_id"], again["state"]), (job_id, "succeeded"))
        self.assertEqual(EvidenceEvent.objects.filter(action__in=["previews_paused", "previews_resumed"]).count(), 2)
        with self.assertRaisesMessage(CommandError, "--reason"):
            call_command("preview_switch", "pause", stdout=io.StringIO())

    def test_a_job_already_at_the_provider_still_finishes_while_paused(self):
        fake = FakeYouCamTransport(result_image=png_bytes("#335577", 600), polls=[task_status("running"),
                                   task_status("success", url="https://storage.invalid/result/r.jpg")])
        sid = self.session()
        with self.youcam(fake), mock.patch("exchange.views.drive_preview_job.delay"):
            asset_id = self.photo(sid)
            job_id = self.preview(sid, ["CALM-07"], asset_id).json()["jobs"][0]["job_id"]
            self.assertEqual(preview_jobs.advance(job_id), "running")
            call_command("preview_switch", "pause", "--reason", "test", stdout=io.StringIO())
            self.assertEqual(preview_jobs.advance(job_id), "succeeded")
        self.assertTrue(PrivateAsset.objects.filter(kind="preview").exists())

    def test_daily_cap_refuses_until_the_next_day(self):
        with self.paid_fake(PREVIEW_DAILY_UNIT_CAP=4):
            first = self.preview(self.session(), ["CALM-07", "CALM-04"]).json()["jobs"]
            self.assertEqual([j["state"] for j in first], ["succeeded", "succeeded"])
            sid = self.session()
            blocked = self.preview(sid, ["CALM-07"]).json()["jobs"][0]
            self.assertEqual((blocked["state"], blocked["error_code"]), ("failed", "DAILY_CAP"))
            PreviewJob.objects.filter(state="succeeded").update(started_at=timezone.now() - timedelta(days=1))
            later = self.preview(sid, ["CALM-07"]).json()["jobs"][0]
            self.assertEqual((later["job_id"], later["state"]), (blocked["job_id"], "succeeded"))

    def test_session_cap_refuses_before_creating_jobs(self):
        with self.paid_fake(PREVIEW_SESSION_UNIT_CAP=4):
            sid = self.session()
            self.assertEqual([j["state"] for j in self.preview(sid, ["CALM-07", "CALM-04"]).json()["jobs"]],
                             ["succeeded", "succeeded"])
            self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "",
                                                                 "max_top_up_minor": 1000, "currency": "USD"})
            response = self.preview(sid, ["SHORT-04"])
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (429, "PREVIEW_LIMIT"))
            self.assertEqual(PreviewJob.objects.count(), 2)
            replay = self.preview(sid, ["CALM-07"])
            self.assertEqual(replay.status_code, 202)
            self.assertEqual(PreviewJob.objects.count(), 2)

    def test_balance_floor_stops_paid_starts(self):
        fake = FakeYouCamTransport(result_image=png_bytes(), balance=10.0)
        sid = self.session()
        with self.youcam(fake), override_settings(YOUCAM_BALANCE_FLOOR=8.0):
            asset_id = self.photo(sid)
            jobs = self.preview(sid, ["CALM-07", "CALM-04"], asset_id).json()["jobs"]
        self.assertEqual([(j["state"], j["error_code"]) for j in jobs], [("succeeded", ""), ("failed", "BALANCE_FLOOR")])
        self.assertEqual(len(fake.api_calls("POST", TASK_PATH)), 1)
        self.assertEqual(PreviewControl.load().balance_units, 10.0)

    def test_unreadable_balance_refuses_paid_starts(self):
        fake = FakeYouCamTransport(balance_reply=reply(500))
        sid = self.session()
        with self.youcam(fake), override_settings(YOUCAM_BALANCE_FLOOR=8.0), self.assertLogs("exchange.preview_jobs", "WARNING"):
            asset_id = self.photo(sid)
            job = self.preview(sid, ["CALM-07"], asset_id).json()["jobs"][0]
        self.assertEqual((job["state"], job["error_code"]), ("failed", "BALANCE_UNKNOWN"))
        self.assertEqual(len(fake.api_calls("GET", CREDIT_PATH)), 1)
        self.assertEqual(fake.api_calls("POST", TASK_PATH), [])

    def test_late_result_is_discarded_after_the_photo_is_withdrawn(self):
        fake = FakeYouCamTransport(result_image=png_bytes("#884422", 600), polls=[task_status("running"),
                                   task_status("success", url="https://storage.invalid/result/r.jpg")])
        sid = self.session()
        with self.youcam(fake), mock.patch("exchange.views.drive_preview_job.delay"):
            asset_id = self.photo(sid)
            job_id = self.preview(sid, ["CALM-07"], asset_id).json()["jobs"][0]["job_id"]
            self.assertEqual(preview_jobs.advance(job_id), "running")
            self.assertEqual(self.client.delete(f"/api/assets/{asset_id}").status_code, 200)
            self.assertEqual(preview_jobs.advance(job_id), "failed")
        job = PreviewJob.objects.get(id=job_id)
        self.assertEqual((job.error_code, job.observed_units, job.result_asset_id), ("CONSENT_WITHDRAWN", 2, None))
        self.assertFalse(PrivateAsset.objects.exists())
        self.assertIsNone(self.client.get(f"/api/previews/{job_id}").json()["image_url"])

    def test_reconcile_records_charges_and_flags_outside_spend(self):
        outside = {"target_id": "playground-task:000000000001", "action": "api_cloth_v4",
                   "info": {"credits": [{"amount_dec": -4.0}]}}
        fake = FakeYouCamTransport(result_image=png_bytes(), extra_history=[outside])
        sid = self.session()
        with self.youcam(fake):
            asset_id = self.photo(sid)
            self.preview(sid, ["CALM-07", "CALM-04"], asset_id)
            PreviewJob.objects.update(observed_units=9)
            out = io.StringIO()
            with self.assertLogs("exchange.preview_jobs", "WARNING"):
                call_command("reconcile_units", stdout=out)
        self.assertIn("Matched 2 tasks; 0 had no matching charge", out.getvalue())
        self.assertIn("4 units were charged to tasks this app did not create.", out.getvalue())
        self.assertEqual(set(PreviewJob.objects.values_list("observed_units", flat=True)), {2})
        self.assertEqual(EvidenceEvent.objects.get(action="units_reconciled").facts,
                         {"matched": 2, "unmatched": 0, "outside_units": 4})

    def test_reconcile_nets_a_refund_against_its_charge(self):
        sid = self.session()
        with self.paid_fake():
            self.preview(sid, ["CALM-07", "CALM-04"])
        kept, refunded = PreviewJob.objects.order_by("created_at")
        for job in (kept, refunded):
            PreviewJob.objects.filter(pk=job.pk).update(provider_task_id=f"task-{job.pk}", observed_units=1)
        entries = [{"target_id": f"task-{kept.pk}:1", "info": {"credits": [{"amount_dec": -1.0}]}},
                   {"target_id": f"task-{refunded.pk}:1", "info": {"credits": [{"amount_dec": -1.0}]}},
                   {"target_id": f"task-{refunded.pk}:1", "info": {"credits": [{"amount_dec": 0.0}, {"amount_dec": 1.0}]}}]
        self.assertEqual(preview_jobs.reconcile_units(entries), {"matched": 2, "unmatched": 0, "outside_units": 0})
        self.assertEqual([j.observed_units for j in PreviewJob.objects.order_by("created_at")], [1, 0])

    def test_reconcile_without_a_paid_provider_needs_a_saved_file(self):
        with self.assertRaisesMessage(CommandError, "--history FILE"):
            call_command("reconcile_units", stdout=io.StringIO())

    def test_scheduled_tasks_refresh_balance_and_reconcile_only_for_paid_providers(self):
        self.assertLessEqual({"exchange.refresh_provider_balance", "exchange.reconcile_provider_units"},
                             {e["task"] for e in settings.CELERY_BEAT_SCHEDULE.values()})
        self.assertTrue(tasks.refresh_provider_balance().startswith("skipped"))
        self.assertTrue(tasks.reconcile_provider_units().startswith("skipped"))
        fake = FakeYouCamTransport(balance=987.5)
        with self.youcam(fake):
            self.assertEqual(tasks.refresh_provider_balance(), 987.5)
            self.assertEqual(tasks.reconcile_provider_units(), {"matched": 0, "unmatched": 0, "outside_units": 0})
        self.assertEqual(fake.api_calls("POST", TASK_PATH), [])

    def test_paid_jobs_record_the_owner_approval(self):
        with self.paid_fake(), override_settings(PREVIEW_LIVE_APPROVAL="owner-2026-10-20"):
            self.preview(self.session(), ["CALM-07"])
        self.assertEqual(EvidenceEvent.objects.get(action="preview_job_ended").facts["approval"], "owner-2026-10-20")
