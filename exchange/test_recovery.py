import io
import json
import tempfile
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from exchange import preview_jobs
from exchange.models import EvidenceEvent, PreviewJob
from exchange.tasks import drive_preview_job, recover_stuck_previews
from exchange.test_deploy_config import run
from exchange.tests import fake_provider, provider
from secondlook.providers import FAILED, PENDING, RUNNING, SUCCEEDED, UNCERTAIN, PollResult

NOTHING = {"requeued": 0, "repolled": 0, "timed_out": 0, "gave_up": 0}


class RecoveryTests(TestCase):
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
        self.started, self.polled = [], []
        self.outcome = PollResult(SUCCEEDED, 2)
        self.cls = fake_provider(calls=self.started, poll=lambda task_id: self.polled.append(task_id) or self.outcome)

    def send(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json")

    def lost(self):
        """A paid preview whose worker task was lost before it ran."""
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000, "currency": "USD"})
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
        with self.paid(), mock.patch("exchange.views._enqueue"):
            data = self.send("post", f"/api/sessions/{sid}/previews",
                             {"candidate_skus": ["CALM-07"], "consent": True, "asset_id": asset}).json()
        return PreviewJob.objects.get(pk=data["jobs"][0]["job_id"])

    def running(self, driver="chain-a"):
        """A paid preview the provider is still working on, driven so far by one task chain."""
        job = self.lost()
        self.outcome = PollResult(RUNNING, 0)
        with self.paid():
            self.assertEqual(preview_jobs.advance(str(job.pk), driver), RUNNING)
        return job

    def paid(self):
        return provider(self.cls, ceiling=100)

    def age(self, job, seconds, *fields):
        """Move the job's clocks back as if this long had passed without progress."""
        past = timezone.now() - timedelta(seconds=seconds)
        PreviewJob.objects.filter(pk=job.pk).update(last_step_at=past, **{name: past for name in fields})

    def recover(self):
        queued = []
        with self.paid():
            counts = preview_jobs.recover_stuck_jobs(lambda *task: queued.append(task))
        return counts, queued

    def test_a_queued_preview_whose_task_was_lost_runs_once_it_has_stalled(self):
        job = self.lost()
        with self.paid():
            self.assertEqual(recover_stuck_previews(), NOTHING)
            self.age(job, settings.PREVIEW_STALL_SECONDS + 1)
            with self.assertLogs("exchange.preview_jobs", "WARNING") as logs:
                self.assertEqual(recover_stuck_previews(), {**NOTHING, "requeued": 1})
            self.assertIn(f"Preview job {job.pk} was stuck pending; requeued", logs.output[0])
            self.assertEqual(recover_stuck_previews(), NOTHING)
        job.refresh_from_db()
        self.assertEqual((job.state, job.observed_units, len(self.started)), (SUCCEEDED, 2, 1))
        event = EvidenceEvent.objects.get(action="preview_job_recovered")
        self.assertEqual((event.facts["job_id"], event.facts["how"]), (str(job.pk), "requeued"))

    def test_a_requeued_preview_still_faces_the_pause_switch(self):
        job = self.lost()
        call_command("preview_switch", "pause", reason="test", stdout=io.StringIO())
        self.age(job, settings.PREVIEW_STALL_SECONDS + 1)
        with self.paid(), self.assertLogs("exchange.preview_jobs", "WARNING"):
            recover_stuck_previews()
        job.refresh_from_db()
        self.assertEqual((job.state, job.error_code, self.started), (FAILED, "PREVIEWS_PAUSED", []))

    def test_a_preview_queued_too_long_ends_unsent_and_the_shopper_may_retry(self):
        job = self.lost()
        self.age(job, settings.PREVIEW_QUEUE_LIMIT_SECONDS + 1, "queued_at")
        counts, queued = self.recover()
        self.assertEqual((counts, queued), ({**NOTHING, "timed_out": 1}, []))
        job.refresh_from_db()
        self.assertEqual((job.state, job.error_code, job.reserved_units, self.started), (FAILED, "QUEUE_TIMEOUT", 0, []))
        shown = self.client.get(f"/api/previews/{job.pk}").json()
        self.assertEqual((shown["message"], shown["retry_allowed"]),
                         ("This preview took too long to start. You can try once more.", True))
        with self.paid():
            retried = self.send("post", f"/api/previews/{job.pk}/retry").json()
        self.assertEqual((retried["state"], retried["attempt"], len(self.started)), (SUCCEEDED, 2, 1))

    def test_a_reopened_refusal_is_queued_afresh(self):
        job = self.lost()
        past = timezone.now() - timedelta(hours=1)
        PreviewJob.objects.filter(pk=job.pk).update(state=FAILED, error_code="DAILY_CAP", queued_at=past,
                                                    last_step_at=past)
        job = preview_jobs.reopen_if_refused(PreviewJob.objects.get(pk=job.pk))
        self.assertEqual(job.state, PENDING)
        self.assertGreater(job.queued_at, timezone.now() - timedelta(seconds=5))
        self.assertEqual(self.recover(), (NOTHING, []))

    def test_only_the_chain_that_claimed_a_job_polls_it_until_recovery_hands_it_on(self):
        job = self.running()
        with self.paid():
            # A second task for the same job, from a repeated request, stops without polling.
            self.assertEqual(drive_preview_job(str(job.pk)), preview_jobs.SUPERSEDED)
        self.assertEqual((len(self.started), len(self.polled)), (1, 1))
        self.age(job, settings.PREVIEW_STALL_SECONDS + 1, "started_at")
        with self.assertLogs("exchange.preview_jobs", "WARNING"):
            counts, queued = self.recover()
        self.assertEqual(counts, {**NOTHING, "repolled": 1})
        [(job_id, attempt, driver)] = queued
        self.assertEqual(attempt, (settings.PREVIEW_STALL_SECONDS + 1) // settings.PREVIEW_POLL_SECONDS)
        self.outcome = PollResult(SUCCEEDED, 2)
        with self.paid():
            self.assertEqual(preview_jobs.advance(job_id, "chain-a"), preview_jobs.SUPERSEDED)
            self.assertEqual(drive_preview_job(job_id, attempt, driver), SUCCEEDED)
        job.refresh_from_db()
        self.assertEqual((job.state, job.observed_units, len(self.started), len(self.polled)), (SUCCEEDED, 2, 1, 2))

    def test_a_running_preview_past_its_time_limit_is_given_up_and_keeps_its_reservation(self):
        job = self.running()
        limit = settings.PREVIEW_MAX_POLLS * settings.PREVIEW_POLL_SECONDS + settings.PREVIEW_STALL_SECONDS
        self.age(job, limit + 1, "started_at")
        counts, queued = self.recover()
        self.assertEqual((counts, queued), ({**NOTHING, "gave_up": 1}, []))
        job.refresh_from_db()
        self.assertEqual((job.state, job.error_code, job.reserved_units), (UNCERTAIN, "POLL_TIMEOUT", 2))
        self.assertEqual((len(self.started), len(self.polled)), (1, 1))
        self.assertIn("will not be retried", self.client.get(f"/api/previews/{job.pk}").json()["message"])

    def test_a_preview_that_may_still_be_starting_is_never_started_again(self):
        job = self.running()
        PreviewJob.objects.filter(pk=job.pk).update(provider_task_id="")
        self.age(job, settings.PREVIEW_STALL_SECONDS + 1, "started_at")
        self.assertEqual(self.recover(), (NOTHING, []))
        limit = settings.PREVIEW_MAX_POLLS * settings.PREVIEW_POLL_SECONDS + settings.PREVIEW_STALL_SECONDS
        self.age(job, limit + 1, "started_at")
        counts, queued = self.recover()
        self.assertEqual((counts, queued), ({**NOTHING, "gave_up": 1}, []))
        job.refresh_from_db()
        self.assertEqual((job.state, job.error_code, job.reserved_units, len(self.started)),
                         (UNCERTAIN, "START_UNCERTAIN", 2, 1))

    def test_recovery_is_scheduled_every_minute_and_must_wait_longer_than_a_poll(self):
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE["recover-stuck-previews"],
                         {"task": "exchange.recover_stuck_previews", "schedule": 60.0})
        refused = run(["-c", "import config.settings"], PREVIEW_POLL_SECONDS="10", PREVIEW_STALL_SECONDS="15")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("PREVIEW_STALL_SECONDS must be at least twice PREVIEW_POLL_SECONDS", refused.stderr)
