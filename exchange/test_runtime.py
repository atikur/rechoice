import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from exchange import preview_jobs, tasks
from exchange.assets import private_path
from exchange.models import EvidenceEvent, ExchangeRequest, ExchangeSession, PreviewJob, PrivateAsset
from exchange.tests import fake_provider
from secondlook.providers import FAILED, PollResult

LIMITS = dict(PREVIEW_UNIT_CEILING=100, PREVIEW_DAILY_UNIT_CAP=100, PREVIEW_SESSION_UNIT_CAP=100)


class RuntimeTests(TestCase):
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

    def send(self, method, path, payload=None, client=None, **headers):
        return getattr(client or self.client, method)(path, data=json.dumps(payload or {}),
                                                      content_type="application/json", **headers)

    def session(self, client=None):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}, client).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000, "currency": "USD",
                                                             "note": "the print is loud"}, client)
        return sid

    def person(self, sid):
        return self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]

    def preview(self, sid, asset_id, skus=("CALM-07",)):
        return self.send("post", f"/api/sessions/{sid}/previews",
                         {"candidate_skus": list(skus), "consent": True, "asset_id": asset_id})

    def expire(self, sid):
        ExchangeSession.objects.filter(id=sid).update(expires_at=timezone.now() - timedelta(minutes=1))

    def test_cleanup_removes_expired_images_and_closes_expired_sessions_but_keeps_the_ledger(self):
        live = self.session()
        live_asset = PrivateAsset.objects.get(id=self.person(live))
        old = self.session()
        with override_settings(PREVIEW_PROVIDER="fake", **LIMITS), \
                mock.patch.dict(preview_jobs.PROVIDERS, {"fake": fake_provider()}):
            self.preview(old, self.person(old))
        old_files = [private_path(a.storage_key) for a in PrivateAsset.objects.filter(owner_session_id=old)]
        self.assertEqual(len(old_files), 1)
        self.expire(old)
        units_before = preview_jobs.committed_units()
        call_command("cleanup_private_assets", stdout=io.StringIO())
        self.assertFalse(any(path.exists() for path in old_files))
        self.assertFalse(PrivateAsset.objects.filter(owner_session_id=old).exists())
        self.assertTrue(private_path(live_asset.storage_key).exists())
        session = ExchangeSession.objects.get(id=old)
        self.assertEqual((session.status, session.preferences["note"]), ("EXPIRED", ""))
        self.assertEqual(PreviewJob.objects.filter(owner_session_id=old).count(), 1)
        self.assertEqual(preview_jobs.committed_units(), units_before)
        self.assertEqual(EvidenceEvent.objects.get(action="cleanup_ran").facts,
                         {"assets_removed": 1, "assets_failed": 0, "sessions_closed": 1})
        self.assertEqual(self.send("post", f"/api/sessions/{old}/quotes", {"selected_sku": "CALM-07"}).status_code, 404)

    def test_cleanup_keeps_the_note_when_staff_still_need_it(self):
        sid = self.session()
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).json()
        self.client.post("/api/requests", data=json.dumps({"quote_id": quote["quote_id"], "quote_version": 1,
                                                           "confirmed": True}),
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="keep-note")
        self.assertTrue(ExchangeRequest.objects.exists())
        self.expire(sid)
        tasks.cleanup_expired()
        self.assertEqual(ExchangeSession.objects.get(id=sid).preferences["note"], "the print is loud")

    def test_failed_deletions_are_kept_reported_and_retried(self):
        sid = self.session()
        asset = PrivateAsset.objects.get(id=self.person(sid))
        path = private_path(asset.storage_key)
        path.unlink()
        path.mkdir()
        PrivateAsset.objects.filter(pk=asset.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertLogs("exchange.cleanup", "ERROR"):
            result = tasks.cleanup_expired()
        self.assertEqual(result["assets_failed"], 1)
        self.assertTrue(PrivateAsset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(EvidenceEvent.objects.get(action="cleanup_failed").facts["assets_failed"], 1)
        path.rmdir()
        self.assertEqual(tasks.cleanup_expired()["assets_removed"], 1)

    def test_cleanup_prunes_expired_browser_sessions_and_is_scheduled(self):
        Session.objects.create(session_key="old-browser", session_data="", expire_date=timezone.now() - timedelta(days=1))
        tasks.cleanup_expired()
        self.assertFalse(Session.objects.filter(session_key="old-browser").exists())
        scheduled = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertLessEqual({"exchange.cleanup_expired", "exchange.worker_heartbeat"}, scheduled)

    @override_settings(SESSION_LIMIT_PER_ADDRESS=2)
    def test_starting_sessions_is_limited_per_address(self):
        for _ in range(2):
            self.assertEqual(self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).status_code, 201)
        refused = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}, Client())
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (429, "RATE_LIMITED"))

    @override_settings(PREVIEW_REQUEST_LIMIT_PER_ADDRESS=2)
    def test_preview_requests_and_retries_share_one_address_limit(self):
        provider = fake_provider(poll=lambda task: PollResult(FAILED, 2, "unknown_internal_error"))
        sid = self.session()
        with override_settings(PREVIEW_PROVIDER="fake", **LIMITS), \
                mock.patch.dict(preview_jobs.PROVIDERS, {"fake": provider}):
            asset_id = self.person(sid)
            job = self.preview(sid, asset_id).json()["jobs"][0]
            self.assertEqual(self.send("post", f"/api/previews/{job['job_id']}/retry").status_code, 201)
            refused = self.preview(sid, asset_id, ["CALM-04"])
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (429, "RATE_LIMITED"))

    @override_settings(SESSION_LIMIT_PER_ADDRESS=1)
    def test_forwarded_addresses_count_only_behind_trusted_proxies(self):
        start = {"order_token": "DEMO-ORDER-001"}
        with override_settings(TRUSTED_PROXY_COUNT=1):
            self.assertEqual(self.send("post", "/api/sessions", start, Client(), HTTP_X_FORWARDED_FOR="203.0.113.5").status_code, 201)
            self.assertEqual(self.send("post", "/api/sessions", start, Client(), HTTP_X_FORWARDED_FOR="198.51.100.7").status_code, 201)
            self.assertEqual(self.send("post", "/api/sessions", start, Client(),
                                       HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.5").status_code, 429)
        cache.clear()
        self.assertEqual(self.send("post", "/api/sessions", start, Client(), HTTP_X_FORWARDED_FOR="203.0.113.9").status_code, 201)
        self.assertEqual(self.send("post", "/api/sessions", start, Client(), HTTP_X_FORWARDED_FOR="198.51.100.8").status_code, 429)

    def test_cache_url_selects_redis(self):
        env = {**os.environ, "DATABASE_URL": "sqlite:///unused.sqlite3", "CACHE_URL": "redis://127.0.0.1:6399/1"}
        code = "import config.settings as s; print(s.CACHES['default']['BACKEND'])"
        result = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), "django.core.cache.backends.redis.RedisCache")

    def test_health_inline_mode_is_ok_and_reveals_no_secrets(self):
        with override_settings(YOUCAM_API_KEY="test-only-secret", CELERY_BROKER_URL="redis://user:pw@host:6379/0"):
            response = self.client.get("/api/health")
        body = response.json()
        self.assertEqual((response.status_code, body["status"]), (200, "ok"))
        self.assertEqual(body["checks"], {"database": "ok", "cache": "ok", "queue": "inline", "worker": "inline"})
        for secret in ("test-only-secret", "redis://", "pw@"):
            self.assertNotIn(secret, response.content.decode())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_health_reports_queue_and_worker_problems(self):
        with mock.patch("exchange.views._broker_state", return_value="ok"):
            stale = self.client.get("/api/health")
            self.assertEqual((stale.status_code, stale.json()["checks"]["worker"]), (503, "stale"))
            tasks.worker_heartbeat()
            fresh = self.client.get("/api/health")
            self.assertEqual((fresh.status_code, fresh.json()["status"]), (200, "ok"))
        with mock.patch("exchange.views._broker_state", return_value="down"):
            down = self.client.get("/api/health")
        self.assertEqual((down.status_code, down.json()["checks"]["queue"]), (503, "down"))

    def test_health_shows_balance_freshness_in_live_mode(self):
        with override_settings(PREVIEW_PROVIDER="youcam"):
            self.assertEqual(self.client.get("/api/health").json()["checks"]["balance"], "unknown")
            control = preview_jobs.control()
            control.balance_units, control.balance_checked_at = 900.0, timezone.now()
            control.save()
            body = self.client.get("/api/health").json()
        self.assertEqual((body["checks"]["balance"], body["previews"]), ("fresh", "live"))
