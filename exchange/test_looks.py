import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from exchange import preview_jobs
from exchange.management.commands.run_feasibility import Command
from exchange.models import LookPreview, PreviewJob, PrivateAsset, ProductVariant
from exchange.replay import replay_key
from secondlook.providers import SUCCEEDED, ProviderRejected
from secondlook.youcam_fake import API, FakeYouCamTransport
from secondlook.youcam_provider import (
    NECKLACE_PATH, TASK_PATH, YouCamClothesProvider, YouCamEarringProvider, YouCamMakeupProvider,
    YouCamNecklaceProvider,
)

LIMITS = dict(PREVIEW_UNIT_CEILING=100, PREVIEW_DAILY_UNIT_CAP=100, PREVIEW_SESSION_UNIT_CAP=100,
              PUBLIC_LIVE_PREVIEWS=True)
TOP, LIP, EAR, NECK = "CALM-07", "LIP-VELVET-230", "EAR-PEARL-G58", "NECK-AMETHYST-S55"
REAL_SOURCES = Path(__file__).resolve().parents[1] / "assets" / "pixabay_sources.json"


def jpeg(colour):
    out = io.BytesIO()
    Image.new("RGB", (600, 800), colour).save(out, format="JPEG")
    return out.getvalue()


class NoFaceMakeup(YouCamMakeupProvider):
    def start(self, job):
        raise ProviderRejected("error_no_face")


class EarTooSmall(YouCamEarringProvider):
    def start(self, job):
        raise ProviderRejected("PHOTO_CHECK_INVALID")


class LookTests(TestCase):
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

    def send(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json")

    def session(self, model="model-a"):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000, "currency": "USD"})
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model}).json()["asset_id"]
        return sid, asset

    def look(self, sid, asset, sku=TOP, mode="live"):
        return self.send("post", f"/api/sessions/{sid}/looks", {"sku": sku, "consent": True, "asset_id": asset,
                                                                "mode": mode})

    @contextlib.contextmanager
    def youcam(self, fake, makeup=YouCamMakeupProvider, **limits):
        options = {"base_url": API, "transport": fake}
        with override_settings(PREVIEW_PROVIDER="youcam", **{**LIMITS, **limits}), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam": lambda: YouCamClothesProvider("test-only-key", **options),
                    "youcam-makeup": lambda: makeup("test-only-key", **options),
                    "youcam-earring": lambda: YouCamEarringProvider("test-only-key", **options),
                    "youcam-necklace": lambda: YouCamNecklaceProvider("test-only-key", **options)}):
            yield

    def test_a_look_adds_the_top_the_lipstick_and_the_necklace_each_on_the_step_before_for_four_units(self):
        sid, asset = self.session()
        fake = FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)
        with self.youcam(fake):
            response = self.look(sid, asset)
            # Asking again returns the same look and calls nothing.
            again = self.look(sid, asset).json()
        self.assertEqual(response.status_code, 202, response.content)
        data = response.json()
        self.assertEqual(data["state"], "succeeded")
        self.assertEqual([(i["sku"], i["feature"], i["state"]) for i in data["items"]],
                         [(TOP, "Clothes V4", "succeeded"), (LIP, "Makeup VTO", "succeeded"),
                          (NECK, "Necklace VTO", "succeeded")])
        self.assertEqual([path for path, _ in fake.tasks], [TASK_PATH, "/s2s/v2.0/task/makeup-vto", NECKLACE_PATH])
        self.assertEqual(fake.tasks[1][1]["effects"][0]["palettes"][0]["color"], "#b5532f")
        self.assertEqual(fake.tasks[2][1]["object_infos"][0]["parameter"], {"necklace_need_remove_background": True})
        steps = list(LookPreview.objects.get().steps.order_by("look_step"))
        # Each step is drawn on the result of the one before; only the first sees the demo photo itself.
        self.assertEqual(str(steps[0].person_asset_id), asset)
        self.assertEqual([s.person_asset_id for s in steps[1:]], [s.result_asset_id for s in steps[:2]])
        self.assertEqual(data["image_url"], f"/api/assets/{steps[2].result_asset_id}")
        self.assertEqual(preview_jobs.committed_units(), 4)
        self.assertIn("Each item was added in turn to the same photo.", data["message"])
        self.assertIn("Shades on screen vary", data["message"])
        self.assertIn("Length on screen is approximate", data["message"])
        self.assertFalse(data["baseline"])
        self.assertEqual(data["left_out"], [
            "Skin Veil Foundation, 220W Light Warm: foundation barely shows on a photo of this size.",
            "Gloss Lacquer Nail Polish, 01 Coral Crush: nail polish needs a photo of a hand.",
            "Baroque Pearl Drops: earrings need a close photo of the ear.",
            "Tone Permanent Hair Colour, 7.3 Golden Blonde: hair colour is not part of a look yet."])
        self.assertEqual(self.client.get(data["image_url"]).status_code, 200)
        self.assertEqual((again["look_id"], len(fake.tasks)), (data["look_id"], 3))

    def test_the_item_bought_makes_the_what_you_have_now_look(self):
        sid, asset = self.session()
        with self.youcam(FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)):
            data = self.look(sid, asset, sku="ORIGINAL").json()
        self.assertTrue(data["baseline"])
        self.assertEqual([i["sku"] for i in data["items"]], ["ORIGINAL", LIP, NECK])

    def test_a_look_that_cannot_fit_under_the_session_limit_is_refused_before_any_call(self):
        sid, asset = self.session()
        fake = FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)
        with self.youcam(fake, PREVIEW_SESSION_UNIT_CAP=3):
            response = self.look(sid, asset)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (429, "PREVIEW_LIMIT"))
        self.assertIn("This look needs 4 preview units", response.json()["error"]["message"])
        self.assertEqual(fake.calls, [])
        self.assertFalse(PreviewJob.objects.exists())

    def test_a_failed_step_ends_the_look_and_the_steps_after_it_never_start(self):
        sid, asset = self.session()
        fake = FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)
        with self.youcam(fake, makeup=NoFaceMakeup):
            data = self.look(sid, asset).json()
        self.assertEqual(data["state"], "failed")
        self.assertEqual([i["state"] for i in data["items"]], ["succeeded", "failed", "waiting"])
        self.assertEqual(data["message"], "Step 2 of 3, Velvet Matte Lipstick, 230 Terracotta: This photo did not work "
                                          "for a preview. Try a different photo or a demo model.")
        self.assertIsNone(data["image_url"])
        self.assertEqual([path for path, _ in fake.tasks], [TASK_PATH])
        self.assertEqual(preview_jobs.committed_units(), 2)
        failed = PreviewJob.objects.get(look_step=1)
        retry = self.send("post", f"/api/previews/{failed.id}/retry")
        self.assertEqual(retry.status_code, 409, retry.content)

    def test_test_mode_runs_every_step_with_no_image_and_no_units(self):
        sid, asset = self.session()
        data = self.look(sid, asset).json()
        self.assertEqual((data["state"], data["image_url"]), ("succeeded", None))
        self.assertEqual(data["message"], "Mock only. No generated preview or YouCam call.")
        steps = PreviewJob.objects.filter(look__isnull=False).order_by("look_step")
        self.assertEqual([(s.variant.sku, s.provider, str(s.person_asset_id)) for s in steps],
                         [(TOP, "mock", asset), (LIP, "mock", asset), (NECK, "mock", asset)])
        self.assertEqual(preview_jobs.committed_units(), 0)

    def test_recorded_looks_replay_each_step_on_model_a_for_no_units_and_only_there(self):
        sid, asset = self.session()
        self.assertIn('data-recorded="yes"', self.client.get("/").content.decode())
        for sku, first in ((TOP, "look-01-step1-model-a-CALM-07.jpg"), ("CALM-04", "look-02-step1-model-a-CALM-04.jpg"),
                           ("ORIGINAL", "look-03-step1-model-a-ORIGINAL.jpg")):
            with self.subTest(sku):
                data = self.look(sid, asset, sku=sku, mode="recorded").json()
                self.assertEqual(data["state"], "succeeded")
                self.assertEqual(data["baseline"], sku == "ORIGINAL")
                # The top and lipstick were recorded first; the necklace was added to each look later.
                self.assertEqual([i["sku"] for i in data["items"]], [sku, LIP, NECK])
                self.assertIn("Recorded YouCam results from the 28 October 2026 looks pilot", data["message"])
                self.assertEqual(self.client.get(data["image_url"]).status_code, 200)
                steps = PreviewJob.objects.filter(look_id=data["look_id"]).order_by("look_step")
                self.assertEqual([s.provider_task_id for s in steps],
                                 [f"replay:model-a:look:{sku}", f"replay:model-a:look:{sku}+{LIP}",
                                  f"replay:model-a:look:{sku}+{LIP}+{NECK}"])
        self.assertEqual(preview_jobs.committed_units(), 0)
        other_sid, other = self.session(model="model-b")
        missing = self.look(other_sid, other, mode="recorded").json()
        self.assertEqual(missing["state"], "failed")
        self.assertIn("There is no recorded look for this choice on this person", missing["message"])

    def test_the_published_looks_and_the_evidence_page_show_every_step_of_each(self):
        from django.contrib.staticfiles import finders
        data = json.loads(Path(finders.find("exchange/evidence/looks.json")).read_text())
        self.assertEqual([(r["look"], r["keys"], r["from_probe"]) for r in data["results"]],
                         [(1, [TOP, LIP, NECK], True), (2, ["CALM-04", LIP, NECK], False),
                          (3, ["ORIGINAL", LIP, NECK], False)])
        self.assertEqual((data["pilot"]["units"]["observed"], data["pilot"]["units"]["billing"]),
                         (12, "reconciled from unit history"))
        self.assertNotIn("task_id", json.dumps(data))
        for look in data["results"]:
            self.assertEqual([s["feature"] for s in look["steps"]], ["Clothes V4", "Makeup VTO", "Necklace VTO"])
            for step in look["steps"]:
                self.assertIsNotNone(finders.find(f"exchange/evidence/{step['result_file']}"))
        html = self.client.get("/evidence").content.decode()
        for text in ("Looks pilot", "Look 1: Ribbed Turtleneck with Velvet Matte Lipstick, 230 Terracotta",
                     "Look 3: Midnight Floral Blouse with Velvet Matte Lipstick, 230 Terracotta and Amethyst Y Chain, "
                     "what the shopper has now",
                     "Step 2, Makeup VTO: adds Velvet Matte Lipstick", "Step 3, Necklace VTO: adds Amethyst Y Chain",
                     "from the probe", "PHOTO_CHECK_INVALID", "sits below the rolled collar",
                     "small pink smudge on the jeans"):
            self.assertIn(text, html)

    def test_a_recorded_look_also_covers_a_look_of_its_first_items(self):
        from exchange import replay
        three = {"results": [{"model": "model-a", "keys": ["CALM-07", "LIP-VELVET-230", "NECK-AMETHYST-S55"],
                              "state": "succeeded", "steps": []}]}
        with mock.patch.object(replay, "recorded_looks", return_value=three):
            self.assertEqual(replay.recorded_look_length("model-a", ["CALM-07", "LIP-VELVET-230"]), 2)
            self.assertEqual(replay.recorded_look_length("model-a", ["CALM-07", "LIP-VELVET-230", "NECK-AMETHYST-S55"]), 3)
            self.assertEqual(replay.recorded_look_length("model-a", ["CALM-04", "LIP-VELVET-230"]), 0)
            self.assertEqual(replay.recorded_look_length("model-b", ["CALM-07"]), 0)

    def test_deleting_the_photo_deletes_every_step_made_from_it(self):
        sid, asset = self.session()
        with self.youcam(FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)):
            self.look(sid, asset)
        results = list(PrivateAsset.objects.filter(kind="preview"))
        self.assertEqual(len(results), 3)
        self.assertEqual(self.send("delete", f"/api/assets/{asset}").status_code, 200)
        self.assertFalse(PrivateAsset.objects.exists())
        self.assertEqual(list(self.media.glob("*.jpg")), [])

    def test_only_a_choice_or_the_item_bought_can_be_shown_and_only_on_a_person_photo(self):
        sid, asset = self.session()
        self.assertEqual(self.look(sid, asset, sku="BOLD-01").json()["error"]["code"], "INELIGIBLE")
        result = PrivateAsset.objects.create(owner_session_id=sid, kind="preview", sha256="0" * 64,
                                             storage_key="x.jpg", license_or_consent="generated",
                                             expires_at=PrivateAsset.objects.get(id=asset).expires_at)
        self.assertEqual(self.look(sid, str(result.id)).json()["error"]["code"], "ASSET_NOT_FOUND")

    def test_the_status_poll_starts_a_next_step_whose_task_was_lost(self):
        sid, asset = self.session()
        with mock.patch("exchange.tasks.preview_jobs.next_look_step", return_value=None):
            data = self.look(sid, asset).json()
        self.assertEqual(data["state"], "running")
        data = self.client.get(f"/api/looks/{data['look_id']}").json()
        self.assertEqual([i["state"] for i in data["items"]], ["succeeded", "succeeded", "succeeded"])
        self.assertEqual(data["state"], "succeeded")

    def test_staff_see_each_look_step_in_the_provenance(self):
        sid, asset = self.session()
        self.look(sid, asset)
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": TOP}).json()
        draft = self.client.post("/api/requests", data=json.dumps(
            {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True}),
            content_type="application/json", HTTP_IDEMPOTENCY_KEY="look-draft")
        self.assertEqual(draft.status_code, 201, draft.content)
        self.client.force_login(get_user_model().objects.create_user("staff", password="unused-test", is_staff=True))
        html = self.client.get("/staff/").content.decode()
        for text in (f"{TOP} (look step 1)", f"{LIP} (look step 2)", f"{NECK} (look step 3)"):
            self.assertIn(text, html)


class LookPilotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        patch = override_settings(PRIVATE_MEDIA_ROOT=self.media, PREVIEW_POLL_SECONDS=0)
        patch.enable()
        self.addCleanup(patch.disable)

    def run_command(self, *args):
        out = io.StringIO()
        call_command("run_feasibility", "--set", "looks", "--sources", str(REAL_SOURCES), *args, stdout=out)
        return out.getvalue()

    def test_the_dry_run_plans_three_looks_on_model_a_for_twelve_units(self):
        output = self.run_command()
        self.assertIn("Looks: 3. Unit estimate: 0.", output)
        self.assertIn("On YouCam these looks cost 12 units: Clothes V4 6, Makeup VTO 3, Necklace VTO 3.", output)
        self.assertIn("1. model-a: CALM-07 on Clothes V4, then LIP-VELVET-230 (230 Terracotta #b5532f, matte) on "
                      "Makeup VTO, then NECK-AMETHYST-S55 (silver drop, 550 mm) on Necklace VTO: ", output)
        self.assertIn("Dry run. Nothing was written.", output)
        self.assertFalse(PreviewJob.objects.exists())

    @contextlib.contextmanager
    def youcam(self, fake, earring=YouCamEarringProvider, ceiling=20):
        options = {"base_url": API, "transport": fake}
        limits = {"PREVIEW_UNIT_CEILING": ceiling, "PREVIEW_DAILY_UNIT_CAP": ceiling}
        with override_settings(PREVIEW_PROVIDER="youcam", **limits), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam": lambda: YouCamClothesProvider("test-only-key", **options),
                    "youcam-makeup": lambda: YouCamMakeupProvider("test-only-key", **options),
                    "youcam-earring": lambda: earring("test-only-key", **options),
                    "youcam-necklace": lambda: YouCamNecklaceProvider("test-only-key", **options)}):
            yield

    def report(self):
        return json.loads((self.media / "feasibility" / "reports" / "looks-pilot" / "report.json").read_text())

    def test_the_first_look_then_the_others_run_on_the_fake_api_for_exactly_twelve_units(self):
        fake = FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)
        with self.youcam(fake, ceiling=12):
            with self.assertRaisesMessage(Exception, "--confirm-units 12"):
                self.run_command("--execute")
            self.assertEqual(fake.calls, [])
            self.run_command("--execute", "--pairs", "1", "--confirm-units", "4")
            self.assertEqual(len(fake.tasks), 3)
            self.assertEqual((self.report()["summary"]["succeeded"], self.report()["summary"]["not_attempted"]), (1, 2))
            self.run_command("--execute", "--pairs", "2,3", "--confirm-units", "8")
        summary = self.report()["summary"]
        self.assertEqual((summary["set"], summary["succeeded"], summary["units"]["observed"]), ("looks", 3, 12))
        self.assertIn("keeps the top's design", summary["gate"])
        first = self.report()["pairs"][0]
        self.assertEqual([s["feature"] for s in first["steps"]], ["Clothes V4", "Makeup VTO", "Necklace VTO"])
        self.assertTrue(all(s["result_file"] for s in first["steps"]))
        self.assertEqual(first["result_file"], first["steps"][-1]["result_file"])
        self.assertIn("shade_check", first["steps"][1])
        self.assertEqual(preview_jobs.committed_units(), 12)
        self.assertEqual(LookPreview.objects.count(), 3)

    def test_the_first_look_reuses_the_first_steps_of_a_longer_probe(self):
        """A probe planned with earrings too: its first three steps succeeded before the earrings were refused."""
        fake = FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)
        command = Command()
        item = command.look_plan(json.loads(REAL_SOURCES.read_text()))[0]
        with self.youcam(fake, earring=EarTooSmall):
            session = command.session_for("looks-pilot", item)
            variants = item["variants"] + [ProductVariant.objects.get(sku=EAR)]
            probe, job = preview_jobs.create_look(session, variants, preview_jobs.look_plan(variants),
                                                  command.asset_for(session, item))
            while job is not None:
                state = command.drive(job)
                job = preview_jobs.next_look_step(job.id) if state == SUCCEEDED else None
            self.assertEqual([s.state for s in preview_jobs.look_steps(probe)],
                             ["succeeded", "succeeded", "succeeded", "failed"])
            self.assertEqual(preview_jobs.committed_units(), 4)
            self.run_command("--execute", "--pairs", "2,3", "--confirm-units", "8")
            again = self.run_command("--execute", "--pairs", "1", "--confirm-units", "4")
        self.assertIn("1. model-a x CALM-07+LIP-VELVET-230+NECK-AMETHYST-S55: succeeded, from an earlier run", again)
        self.assertEqual(len(fake.tasks), 9)
        first = self.report()["pairs"][0]
        self.assertEqual(first["state"], "succeeded")
        self.assertEqual([s["job_id"] for s in first["steps"]], [str(s.id) for s in preview_jobs.look_steps(probe)[:3]])
        self.assertIn(f"the first 3 steps of look {probe.id}, planned as "
                      "CALM-07+LIP-VELVET-230+NECK-AMETHYST-S55+EAR-PEARL-G58", first["reused_from"])
        self.assertEqual(self.report()["summary"]["succeeded"], 3)
        self.assertEqual(preview_jobs.committed_units(), 12)
        self.assertEqual(LookPreview.objects.count(), 3)

    def make_look(self, command, item, variants, fake):
        """Run a look outside the plan, as the 28 October pilot did before the necklace was added."""
        session = command.session_for("looks-pilot", item)
        look, job = preview_jobs.create_look(session, variants, preview_jobs.look_plan(variants),
                                             command.asset_for(session, item))
        while job is not None:
            state = command.drive(job)
            job = preview_jobs.next_look_step(job.id) if state == SUCCEEDED else None
        return look

    def test_the_necklace_is_added_to_the_recorded_top_and_lipstick_looks_for_one_unit_each(self):
        """As recorded: look 1 is the probe's top and lipstick, whose earrings were refused; looks 2 and 3 are
        top-and-lipstick looks. Adding the necklace continues each from its lipstick result."""
        fake = FakeYouCamTransport(result_image=jpeg((150, 120, 110)), units_per_task=1)
        command = Command()
        plan = command.look_plan(json.loads(REAL_SOURCES.read_text()))
        with self.youcam(fake, earring=EarTooSmall, ceiling=20):
            probe = self.make_look(command, plan[0], plan[0]["variants"][:2] + [ProductVariant.objects.get(sku=EAR)],
                                   fake)
            shorter = [self.make_look(command, item, item["variants"][:2], fake) for item in plan[1:]]
            self.assertEqual([s.state for s in preview_jobs.look_steps(probe)], ["succeeded", "succeeded", "failed"])
            before, tasks = preview_jobs.committed_units(), len(fake.tasks)
            dry = self.run_command()
            self.assertIn("Unit estimate: 3.", dry)
            self.assertIn("Already made: look 1 steps 1 to 2, look 2 steps 1 to 2, look 3 steps 1 to 2.", dry)
            with self.assertRaisesMessage(Exception, "--confirm-units 3"):
                self.run_command("--execute", "--confirm-units", "12")
            self.run_command("--execute", "--confirm-units", "3")
            again = self.run_command("--execute")
        self.assertIn("Unit estimate: 0.", again)
        self.assertIn("succeeded, from an earlier run", again)
        # Exactly one Necklace VTO task per look, each on the lipstick step's result.
        self.assertEqual(len(fake.tasks) - tasks, 3)
        self.assertEqual(preview_jobs.committed_units() - before, 3)
        for base in [probe, *shorter]:
            look = LookPreview.objects.get(continues=base)
            steps = preview_jobs.look_steps(look)
            self.assertEqual([s.variant.sku for s in steps][2], "NECK-AMETHYST-S55")
            self.assertEqual(steps[2].person_asset, steps[1].result_asset)
            self.assertEqual(steps[:2], preview_jobs.look_steps(base)[:2])
        report = self.report()
        self.assertEqual(report["summary"]["succeeded"], 3)
        self.assertEqual([[s["feature"] for s in r["steps"]] for r in report["pairs"]],
                         [["Clothes V4", "Makeup VTO", "Necklace VTO"]] * 3)
        self.assertIn(f"steps 1 to 2 of look {probe.id}, planned as CALM-07+LIP-VELVET-230+EAR-PEARL-G58",
                      report["pairs"][0]["continued_from"])
        # Withdrawing the photo deletes the necklace steps too: each was made from a result made from it.
        person = probe.person_asset
        preview_jobs.discard_person_asset(person)
        self.assertFalse(PreviewJob.objects.filter(variant__sku="NECK-AMETHYST-S55", result_asset__isnull=False).exists())
