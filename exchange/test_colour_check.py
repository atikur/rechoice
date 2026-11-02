import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from PIL import Image, ImageDraw

from exchange import preview_jobs
from exchange.colour_check import catalog_colour, check_preview, ciede2000, hex_to_rgb
from exchange.models import PreviewJob, ProductVariant
from exchange.tests import fake_provider, provider
from secondlook.providers import SUCCEEDED, PollResult, StartResult

PILOT = Path(settings.BASE_DIR) / "private_media/feasibility"


def jpeg(image):
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def person_and_result(shirt, size=(600, 800)):
    """A plain person photo, and the same photo with a shirt painted on the torso."""
    person = Image.new("RGB", size, "#3a3f44")
    ImageDraw.Draw(person).ellipse((230, 80, 370, 230), fill="#8d5a3c")
    result = person.copy()
    ImageDraw.Draw(result).rectangle((150, 260, 450, 700), fill=shirt)
    return jpeg(person), jpeg(result)


class ColourCheckTests(SimpleTestCase):
    def test_ciede2000_matches_the_published_reference_pair(self):
        self.assertAlmostEqual(ciede2000((50, 2.6772, -79.7751), (50, 0, -82.7485)), 2.0425, places=3)

    def test_a_preview_in_the_reference_colour_is_close(self):
        person, result = person_and_result("#607e77")
        check = check_preview(person, result, "#607e77", 0)
        self.assertEqual(check.status, "close")
        self.assertLess(check.delta, 3)
        self.assertEqual(check.note, "Colour check: close to the product photo.")

    def test_a_shifted_colour_is_flagged_with_its_direction(self):
        person, result = person_and_result("#9fbdb6")
        check = check_preview(person, result, "#607e77", 0)
        self.assertEqual(check.status, "differs")
        self.assertIn("looks lighter than the product. Trust the product photo", check.note)

    def test_patterns_and_unusable_inputs_get_no_colour_verdict(self):
        person, result = person_and_result("#607e77")
        self.assertEqual(check_preview(person, result, "#607e77", 2).status, "pattern")
        self.assertEqual(check_preview(None, result, "#607e77", 0).status, "unchecked")
        self.assertEqual(check_preview(person, result, "", 0).status, "unchecked")
        self.assertEqual(check_preview(person, b"not an image", "#607e77", 0).status, "unchecked")
        wide = jpeg(Image.new("RGB", (900, 500), "#607e77"))
        self.assertEqual(check_preview(person, wide, "#607e77", 0).status, "unchecked")

    @unittest.skipUnless((PILOT / "reports/pilot/report.json").exists(), "the private pilot results are not on this machine")
    def test_calibration_on_the_recorded_pilot(self):
        catalog = Path(settings.BASE_DIR) / "exchange/static/exchange/catalog"
        outcomes = {}
        for result in sorted((PILOT / "reports/pilot").glob("*.jpg")):
            model, sku = re.match(r"\d+-(model-[a-e])-(.+)", result.stem).groups()
            reference = "#%02x%02x%02x" % catalog_colour((catalog / f"{sku}.jpg").read_bytes())
            pattern = 2 if sku in ("ORIGINAL", "BOLD-01", "BOLD-02") else 0
            outcomes[sku] = check_preview((PILOT / f"{model}.jpg").read_bytes(), result.read_bytes(), reference, pattern).status
        self.assertEqual(outcomes, {"CALM-04": "close", "ORIGINAL": "pattern", "CALM-07": "close", "CALM-01": "close",
                                    "BOLD-01": "pattern", "OVER-01": "close", "BOLD-02": "pattern", "CALM-03": "close",
                                    "CALM-02": "close", "SHORT-03": "differs"})


class ColourCheckFlowTests(TestCase):
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

    def send(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json")

    def test_a_successful_preview_records_and_shows_its_colour_check(self):
        reference = ProductVariant.objects.get(sku="CALM-07").reference_colour
        self.assertRegex(reference, r"^#[0-9a-f]{6}$")
        shirts = {}

        def start(job):
            person = Image.open(io.BytesIO(job.person_image)).convert("RGB")
            box = (person.width // 4, person.height // 3, person.width * 3 // 4, person.height * 9 // 10)
            draw = ImageDraw.Draw(person)
            draw.rectangle(box, fill=hex_to_rgb(reference))
            if job.sku == "ORIGINAL":
                # The bought blouse is a busy print; a plain alternative must read quieter than it for the gate.
                for y in range(box[1], box[3], 12):
                    draw.line((box[0], y, box[2], y), fill="#f0d9a8", width=3)
            shirts[f"task-{job.sku}"] = jpeg(person)
            return StartResult(f"task-{job.sku}", 2)

        cls = fake_provider(start=start, poll=lambda task: PollResult(SUCCEEDED, 2, image=shirts[task]))
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000})
        with provider(cls, ceiling=100):
            asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
            jobs = self.send("post", f"/api/sessions/{sid}/previews",
                             {"candidate_skus": ["CALM-07"], "consent": True, "asset_id": asset,
                              "include_original": True}).json()["jobs"]
        by_sku = {j["sku"]: j for j in jobs}
        self.assertEqual(by_sku["CALM-07"]["colour_check"]["status"], "close")
        self.assertEqual(by_sku["ORIGINAL"]["colour_check"]["status"], "pattern")
        job = PreviewJob.objects.get(variant__sku="CALM-07")
        self.assertEqual((job.state, job.colour_check), (SUCCEEDED, "close"))
        self.assertEqual(ProductVariant.objects.get(sku="CALM-07").reference_colour_status, "reviewed")
        ProductVariant.objects.filter(sku="CALM-07").update(reference_colour_status="proposed")
        staff = get_user_model().objects.create_user("staff", password="local-test-only", is_staff=True)
        reviewer = Client()
        reviewer.force_login(staff)
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).json()
        self.client.post("/api/requests", data=json.dumps({"quote_id": quote["quote_id"], "quote_version": 1,
                                                           "confirmed": True}),
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="colour-staff")
        html = reviewer.get("/staff/").content.decode()
        self.assertIn("close (ΔE", html)
        self.assertIn("reference not yet reviewed", html)

    def test_a_broken_check_never_fails_the_preview(self):
        cls = fake_provider(poll=lambda task: PollResult(SUCCEEDED, 2, image=jpeg(Image.new("RGB", (512, 700), "#335577"))))
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000})
        with provider(cls, ceiling=100), unittest.mock.patch("exchange.preview_jobs.check_preview", side_effect=RuntimeError), \
                self.assertLogs("exchange.preview_jobs", "WARNING"):
            asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
            job = self.send("post", f"/api/sessions/{sid}/previews",
                            {"candidate_skus": ["CALM-07"], "consent": True, "asset_id": asset}).json()["jobs"][0]
        self.assertEqual(job["state"], SUCCEEDED)
        self.assertEqual(PreviewJob.objects.get().colour_check, "unchecked")
        self.assertEqual(preview_jobs.committed_units(), 2)


class StatusLengthTests(SimpleTestCase):
    def test_every_check_status_fits_its_column(self):
        # Postgres enforces the column length that SQLite ignores; a longer status fails the job's save.
        import re
        from pathlib import Path
        from django.conf import settings
        from exchange.models import PreviewJob
        root = Path(settings.BASE_DIR) / "exchange"
        for source, kind, field in (("colour_check.py", "ColourCheck", "colour_check"), ("size_check.py", "SizeCheck", "size_check")):
            statuses = set(re.findall(kind + r'\("([a-z_-]+)"', (root / source).read_text()))
            self.assertTrue(statuses, source)
            limit = PreviewJob._meta.get_field(field).max_length
            self.assertEqual([s for s in statuses if len(s) > limit], [], field)
