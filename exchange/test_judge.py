import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from exchange import preview_jobs
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from exchange.assets import private_path
from exchange.cleanup import cleanup_expired
from exchange.models import ExchangeSession, PreviewJob, PrivateAsset
from exchange.tests import fake_provider, png_bytes, provider
from secondlook.providers import FAILED, SUCCEEDED


class RecordedAndJudgeTests(TestCase):
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

    def session(self, client=None):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}, client).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000}, client)
        return sid

    def previews(self, sid, model="model-a", client=None, **extra):
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model}, client).json()["asset_id"]
        payload = {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True, "asset_id": asset, **extra}
        return self.send("post", f"/api/sessions/{sid}/previews", payload, client)

    def judge_client(self, code="judge-test-code"):
        client = Client()
        client.post("/judge", {"code": code})
        return client

    def test_recorded_results_are_real_labelled_checked_and_free_even_while_paused(self):
        call_command("preview_switch", "pause", reason="test", stdout=io.StringIO())
        data = self.previews(self.session(), mode="recorded", include_original=True).json()
        self.assertEqual(data["provider"], "replay")
        jobs = {j["sku"]: j for j in data["jobs"]}
        self.assertEqual({sku: j["state"] for sku, j in jobs.items()},
                         {"ORIGINAL": SUCCEEDED, "CALM-07": SUCCEEDED, "CALM-04": SUCCEEDED})
        for job in jobs.values():
            self.assertTrue(job["image_url"].startswith("/api/assets/"))
            self.assertIn("AI preview. Appearance may differ.", job["label"])
            self.assertIn("Recorded YouCam result from the 19 October 2026 pilot", job["label"])
        self.assertEqual({sku: j["colour_check"]["status"] for sku, j in jobs.items()},
                         {"ORIGINAL": "pattern", "CALM-07": "close", "CALM-04": "close"})
        self.assertEqual(preview_jobs.committed_units(), 0)
        live = self.previews(self.session(), mode="live")
        self.assertEqual(live.json()["error"]["code"], "PREVIEWS_PAUSED")

    def test_a_pair_that_was_never_recorded_says_so_and_needs_a_person(self):
        # Model B's suggested tops replay from the gate study; without it, Model B has none of them recorded.
        with mock.patch("exchange.replay.recorded_gate_study", return_value={"results": []}):
            jobs = self.previews(self.session(), model="model-b", mode="recorded").json()["jobs"]
        self.assertEqual({j["state"] for j in jobs}, {FAILED})
        self.assertIn("no recorded result for this top on this model", jobs[0]["message"])
        self.assertFalse(jobs[0]["retry_allowed"])
        sid = self.session()
        bare = self.send("post", f"/api/sessions/{sid}/previews",
                         {"candidate_skus": ["CALM-07"], "consent": True, "mode": "recorded"})
        self.assertEqual(bare.json()["error"]["code"], "PHOTO_REQUIRED")

    @override_settings(JUDGE_ACCESS_CODE="judge-test-code")
    def test_the_judge_code_opens_the_staff_pages_for_that_browser_only(self):
        self.assertEqual(self.client.get("/staff/").status_code, 302)
        wrong = self.client.post("/judge", {"code": "guess"}).content.decode()
        self.assertIn("That code is not right.", wrong)
        judge = self.judge_client()
        self.assertIn("Judge access is on for this browser", judge.get("/judge").content.decode())
        self.assertEqual(judge.get("/staff/").status_code, 200)
        self.assertEqual(judge.get("/staff/insights").status_code, 200)
        self.assertEqual(self.client.get("/staff/").status_code, 302)
        for _ in range(10):
            Client(REMOTE_ADDR="10.0.0.9").post("/judge", {"code": "guess"})
        self.assertIn("Too many requests", Client(REMOTE_ADDR="10.0.0.9").post("/judge", {"code": "judge-test-code"}).content.decode())

    def test_without_a_code_the_judge_page_explains_and_refuses(self):
        html = self.client.post("/judge", {"code": "anything"}).content.decode()
        self.assertIn("not set on this server", html)
        self.assertEqual(self.client.get("/staff/").status_code, 302)
        refused = self.send("post", "/api/judge-access", {"code": "anything"})
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (503, "JUDGE_CODE"))

    @override_settings(JUDGE_ACCESS_CODE="judge-test-code")
    def test_the_judges_page_keeps_an_order_already_open_in_that_browser(self):
        judge, other = Client(), Client()
        sid, elsewhere = self.session(judge), self.session(other)
        judge.post("/judge", {"code": "judge-test-code"})
        # The session key changes on the way in; the open order moves with it and gains judge access.
        self.assertEqual(judge.get(f"/api/sessions/{sid}/candidates").status_code, 200)
        self.assertTrue(ExchangeSession.objects.get(pk=sid).judge)
        self.assertEqual(judge.get(f"/api/sessions/{elsewhere}/candidates").status_code, 404)
        self.assertFalse(ExchangeSession.objects.get(pk=elsewhere).judge)
        self.assertEqual(other.get(f"/api/sessions/{elsewhere}/candidates").status_code, 200)

    @override_settings(JUDGE_ACCESS_CODE="judge-test-code", PUBLIC_LIVE_PREVIEWS=False, ALLOW_PHOTO_UPLOAD=False,
                       JUDGE_DAILY_UNIT_CAP=8)
    def test_a_judge_who_enters_the_code_mid_order_keeps_it_and_gains_live_previews(self):
        client = Client()
        sid = self.session(client)
        with provider(fake_provider(), ceiling=100):
            page = client.get("/").content.decode()
            self.assertIn('id="judgeUnlock"', page)
            self.assertIn('id="liveOption" hidden', page)
            refused = self.previews(sid, client=client, mode="live")
            self.assertEqual(refused.json()["error"]["code"], "LIVE_FOR_JUDGES")
            wrong = self.send("post", "/api/judge-access", {"code": "guess"}, client)
            self.assertEqual((wrong.status_code, wrong.json()["error"]["message"]), (403, "That code is not right."))
            self.assertFalse(ExchangeSession.objects.get(pk=sid).judge)
            key = client.session.session_key
            opened = self.send("post", "/api/judge-access", {"code": "judge-test-code"}, client)
            self.assertEqual(opened.status_code, 200)
            self.assertTrue(opened.json()["allow_upload"])
            self.assertTrue(opened.json()["live_label"])
            self.assertNotEqual(client.session.session_key, key)
            self.assertTrue(ExchangeSession.objects.get(pk=sid).judge)
            jobs = self.previews(sid, client=client, mode="live").json()["jobs"]
        self.assertEqual([j["state"] for j in jobs], [SUCCEEDED, SUCCEEDED])
        self.assertEqual(self.upload(sid, client).status_code, 201)
        # Entering the code again changes nothing, and the page no longer offers the form.
        self.assertEqual(self.send("post", "/api/judge-access", {"code": "whatever"}, client).status_code, 200)
        self.assertNotIn('id="judgeUnlock"', client.get("/").content.decode())
        for _ in range(10):
            self.send("post", "/api/judge-access", {"code": "guess"}, Client(REMOTE_ADDR="10.0.0.8"))
        limited = self.send("post", "/api/judge-access", {"code": "judge-test-code"}, Client(REMOTE_ADDR="10.0.0.8"))
        self.assertEqual(limited.status_code, 429)

    def test_the_app_can_be_installed_on_a_phone(self):
        home = self.client.get("/").content.decode()
        self.assertIn('rel="manifest" href="/static/exchange/manifest.json"', home)
        self.assertIn('rel="apple-touch-icon"', home)
        manifest = json.loads(Path(finders.find("exchange/manifest.json")).read_text())
        self.assertEqual((manifest["name"], manifest["start_url"], manifest["display"]), ("Rechoice", "/", "standalone"))
        for icon in manifest["icons"]:
            path = finders.find("exchange/" + icon["src"])
            self.assertIsNotNone(path, icon["src"])
            with Image.open(path) as image:
                self.assertEqual(f"{image.width}x{image.height}", icon["sizes"])

    def test_the_judge_page_links_each_path_and_names_every_youcam_feature(self):
        html = self.client.get("/judge").content.decode()
        for path in ("tops", "lip", "earrings", "foundation", "nail", "necklace", "hair", "shoes", "bag"):
            self.assertIn(f'href="/?return={path}"', html)
        for feature in ("Clothes V4", "Makeup VTO", "Nail VTO", "Earrings VTO", "Necklace VTO", "AI Hair Color",
                        "Shoes V3", "Bag V2"):
            self.assertIn(f"<td>{feature}</td>", html)
        self.assertLess(html.index('id="access"'), html.index('id="gate-path"'))
        home = self.client.get("/").content.decode()
        self.assertIn("Send it back for one you'll keep.", home)
        # The page leads with the exchange itself, and the gate's flagged render stays in view.
        self.assertIn('<a class="button" href="/?return=tops">Try the blouse exchange</a>', home)
        self.assertIn("exchange/evidence/gate-study-v1/02-model-b-CALM-04.jpg", home)

    def test_the_gate_studys_model_b_renders_replay_for_everyone(self):
        from exchange.replay import ReplayPreviewProvider
        results = ReplayPreviewProvider().results
        for sku in ("ORIGINAL", "CALM-07", "CALM-04"):
            self.assertTrue(finders.find("exchange/evidence/" + results[("model-b", sku)]["result_file"]), sku)
        # A pilot recording keeps its place where the study also rendered the pair.
        self.assertEqual(results[("model-c", "CALM-03")]["result_file"], "pilot-08-model-c-CALM-03.jpg")

    def test_a_replay_from_the_gate_study_is_labelled_as_the_study(self):
        jobs = {j["sku"]: j for j in self.previews(self.session(), model="model-b", mode="recorded",
                                                   include_original=True).json()["jobs"]}
        self.assertIn("30 October 2026 gate study", jobs["CALM-04"]["label"])
        self.assertNotIn("19 October", jobs["CALM-04"]["label"])
        model_a = self.previews(self.session(), model="model-a", mode="recorded").json()["jobs"][0]
        self.assertIn("19 October 2026 pilot", model_a["label"])

    @override_settings(JUDGE_ACCESS_CODE="judge-test-code", PUBLIC_LIVE_PREVIEWS=False,
                       JUDGE_DAILY_UNIT_CAP=2, PREVIEW_SESSION_UNIT_CAP=6)
    def test_live_previews_can_be_kept_for_judges_on_their_own_daily_cap(self):
        with provider(fake_provider(), ceiling=100):
            public = self.previews(self.session(), mode="live")
            self.assertEqual((public.status_code, public.json()["error"]["code"]), (403, "LIVE_FOR_JUDGES"))
            judge = self.judge_client()
            sid = self.session(judge)
            self.assertTrue(ExchangeSession.objects.get(pk=sid).judge)
            jobs = self.previews(sid, client=judge, mode="live").json()["jobs"]
        self.assertEqual([(j["state"], j["error_code"]) for j in jobs], [(SUCCEEDED, ""), (FAILED, "DAILY_CAP")])
        self.assertEqual(preview_jobs.committed_units(owner_session__judge=True), 2)
        self.assertEqual(PreviewJob.objects.filter(owner_session__judge=False).count(), 0)

    def upload(self, sid, client):
        image = SimpleUploadedFile("me.png", png_bytes(size=800), content_type="image/png")
        return client.post(f"/api/sessions/{sid}/assets", {"image": image, "adult_permission": "true"})

    @override_settings(JUDGE_ACCESS_CODE="judge-test-code", ALLOW_PHOTO_UPLOAD=False)
    def test_judges_can_preview_their_own_photo_and_it_is_deleted_within_a_day(self):
        public = self.client.get("/").content.decode()
        self.assertIn('id="uploadOption" data-allowed="" hidden', public)
        self.assertIn("Your own photo and live previews are open to judges", public)
        refused = self.upload(self.session(), self.client)
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (503, "UPLOAD_DISABLED"))
        self.assertIn("open to judges only", refused.json()["error"]["message"])
        judge = self.judge_client()
        page = judge.get("/").content.decode()
        self.assertIn('id="uploadOption" data-allowed="1">', page)
        self.assertNotIn('id="judgeUnlock"', page)
        # On a phone the second input opens the front camera directly.
        self.assertIn('id="photoCamera" accept="image/jpeg,image/png" capture="user"', page)
        self.assertIn("Recorded results cover the demo people only", page)
        self.assertIn("Own-photo controls use test mode", judge.get("/judge").content.decode())
        sid = self.session(judge)
        uploaded = self.upload(sid, judge)
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        asset = PrivateAsset.objects.get(id=uploaded.json()["asset_id"])
        self.assertLessEqual(asset.expires_at, timezone.now() + timedelta(hours=24))
        stored = private_path(asset.storage_key)
        self.assertTrue(stored.exists())
        cleanup_expired(timezone.now() + timedelta(hours=24, minutes=1))
        self.assertFalse(stored.exists())
        self.assertFalse(PrivateAsset.objects.filter(pk=asset.pk).exists())

    @override_settings(JUDGE_ACCESS_CODE="judge-test-code", PUBLIC_LIVE_PREVIEWS=False, JUDGE_DAILY_UNIT_CAP=4)
    def test_a_judges_own_photo_uses_a_live_preview_and_recorded_results_say_so(self):
        judge = self.judge_client()
        sid = self.session(judge)
        asset = self.upload(sid, judge).json()["asset_id"]
        recorded = self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": ["CALM-07"], "consent": True,
                             "asset_id": asset, "mode": "recorded"}, judge).json()["jobs"]
        self.assertEqual(recorded[0]["error_code"], "NOT_RECORDED")
        self.assertIn("Recorded results only cover the AI-generated demo people", recorded[0]["message"])
        with provider(fake_provider(), ceiling=100):
            live = self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": ["CALM-07"], "consent": True,
                             "asset_id": asset, "mode": "live"}, judge).json()["jobs"]
        self.assertEqual(live[0]["state"], SUCCEEDED)
        self.assertEqual(preview_jobs.committed_units(owner_session__judge=True), 2)

    def test_the_evidence_page_shows_every_recorded_result_and_pending_ratings(self):
        html = self.client.get("/evidence").content.decode()
        self.assertIn("What YouCam actually returned", html)
        self.assertEqual(html.count('class="pair"'), 10)
        self.assertIn("10 of 10 rated usable for a style comparison", html)
        self.assertIn("Rated by Claude (AI assistant) at the owner&#x27;s request", html)
        self.assertIn("Rated usable: pattern 2/2, sleeves 1/2, neckline 2/2, person stable.", html)
        self.assertIn("Passed the gate on 20 October 2026", html)
        self.assertIn("median 21.0 s", html)
        for name in ("pilot-03-model-a-CALM-07.jpg", "pilot-10-model-c-SHORT-03.jpg"):
            self.assertIsNotNone(finders.find(f"exchange/evidence/{name}"))
