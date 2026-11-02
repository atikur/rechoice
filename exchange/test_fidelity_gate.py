import io
import json
import tempfile

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image, ImageDraw

from exchange import fidelity_gate as gate
from exchange.colour_check import hex_to_rgb
from exchange.models import PreviewJob, ProductVariant
from exchange.tests import fake_provider, provider
from secondlook.providers import SUCCEEDED, PollResult, StartResult


def jpeg(image):
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def person(size=(600, 800)):
    image = Image.new("RGB", size, "#3a3f44")
    ImageDraw.Draw(image).ellipse((230, 80, 370, 230), fill="#8d5a3c")
    return image


def dressed(colour, *, stripes=0, base=None, redraw_background=None):
    """The person with a top painted on; stripes add print detail, redraw_background changes the scene."""
    image = (base or person()).copy()
    draw = ImageDraw.Draw(image)
    if redraw_background:
        draw.rectangle((0, 0, image.width, image.height // 5), fill=redraw_background)
    draw.rectangle((150, 260, 450, 700), fill=colour)
    for n in range(stripes):
        y = 270 + n * (430 // max(stripes, 1))
        draw.line((150, y, 450, y), fill="#f4f0e0", width=4)
    return image


class GateMeasurementTests(SimpleTestCase):
    def test_a_plain_top_reads_quieter_than_a_striped_one_on_the_same_photo(self):
        photo = jpeg(person())
        busy = jpeg(dressed("#2b3a67", stripes=40))
        plain = jpeg(dressed("#607e77"))
        result = gate.print_check(photo, busy, plain)
        self.assertEqual(result.status, "quieter")
        self.assertLessEqual(result.value, gate.QUIET_RATIO)
        self.assertEqual(gate.print_check(photo, busy, jpeg(dressed("#2b3a67", stripes=40))).status, "busier")

    def test_print_needs_the_bought_top_on_the_same_photo(self):
        photo = jpeg(person())
        self.assertEqual(gate.print_check(photo, None, jpeg(dressed("#607e77"))).status, "not_measured")

    def test_stability_passes_a_garment_only_change_and_flags_a_redrawn_scene(self):
        photo = jpeg(person())
        self.assertEqual(gate.stability_check(photo, jpeg(dressed("#607e77"))).status, "stable")
        changed = gate.stability_check(photo, jpeg(dressed("#607e77", redraw_background="#c9d6e8")))
        self.assertEqual(changed.status, "changed")
        self.assertGreater(changed.value, gate.STABLE_PCT)
        reshaped = gate.stability_check(photo, jpeg(dressed("#607e77").resize((600, 600))))
        self.assertEqual(reshaped.status, "changed")
        self.assertIn("different shape", reshaped.note)

    def test_verdict_combines_the_checks(self):
        self.assertEqual(gate.verdict("close", "quieter", "stable", quieter_asked=True).status, "shows")
        self.assertEqual(gate.verdict("close", "busier", "stable", quieter_asked=True).status, "concern")
        self.assertEqual(gate.verdict("differs", "quieter", "stable", quieter_asked=False).status, "concern")
        self.assertEqual(gate.verdict("close", "quieter", "changed", quieter_asked=True).status, "concern")
        self.assertEqual(gate.verdict("close", "not_measured", "stable", quieter_asked=True).status, "check")
        self.assertEqual(gate.verdict("close", "not_measured", "stable", quieter_asked=False).status, "shows")
        self.assertEqual(gate.verdict("unchecked", "quieter", "stable", quieter_asked=True).status, "check")


class GateVisualTests(SimpleTestCase):
    @staticmethod
    def share(image, box, test):
        pixels = [image.getpixel((x, y)) for x in range(box[0], box[2], 4) for y in range(box[1], box[3], 4)]
        return sum(1 for p in pixels if test(p)) / len(pixels)

    def test_the_change_picture_marks_the_garment_blue_and_a_redrawn_scene_orange(self):
        from exchange.gate_visuals import change_overlay
        photo = person()
        orange = lambda p: p[0] > 170 and 70 < p[1] < 150 and p[2] < 90
        blue = lambda p: p[2] > p[0] + 50
        for redraw, outside in ((None, False), ("#d9d4c7", True)):
            drawn = Image.open(io.BytesIO(change_overlay(jpeg(photo), jpeg(dressed("#e0b04a", redraw_background=redraw)))))
            drawn = drawn.convert("RGB")
            self.assertEqual(drawn.size, (480, 640))
            w, h = drawn.size
            garment = (int(w * 0.35), int(h * 0.45), int(w * 0.65), int(h * 0.8))
            above = (int(w * 0.1), 4, int(w * 0.9), int(h * 0.18))
            self.assertGreater(self.share(drawn, garment, blue), 0.5)
            self.assertEqual(self.share(drawn, above, orange) > 0.5, outside)


class GateFlowTests(TestCase):
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

    def send(self, method, path, payload=None, **headers):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json", **headers)

    def previews(self, paint):
        """Run the bought blouse and both alternatives through a fake provider that paints each result."""
        results = {}

        def start(job):
            photo = Image.open(io.BytesIO(job.person_image)).convert("RGB")
            results[f"task-{job.sku}"] = jpeg(paint(job.sku, photo))
            return StartResult(f"task-{job.sku}", 2)

        cls = fake_provider(start=start, poll=lambda task: PollResult(SUCCEEDED, 2, image=results[task]))
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})
        with provider(cls, ceiling=100):
            asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
            jobs = self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True,
                                                                        "asset_id": asset, "include_original": True}).json()["jobs"]
        return sid, {j["sku"]: j for j in jobs}

    @staticmethod
    def torso(photo):
        return (photo.width // 4, photo.height // 3, photo.width * 3 // 4, photo.height * 9 // 10)

    def paint_plain_or_busy(self, sku, photo):
        colour = {sku: hex_to_rgb(v.reference_colour) for sku, v in
                  ((s, ProductVariant.objects.get(sku=s)) for s in ("CALM-07", "CALM-04"))}
        draw = ImageDraw.Draw(photo)
        box = self.torso(photo)
        if sku == "ORIGINAL":
            draw.rectangle(box, fill="#1f2a55")
            for y in range(box[1], box[3], 12):
                draw.line((box[0], y, box[2], y), fill="#f0d9a8", width=3)
        elif sku == "CALM-07":
            draw.rectangle(box, fill=colour[sku])
        else:
            # CALM-04 is drawn with the right colour but as busy as the blouse: a print the gate should catch.
            draw.rectangle(box, fill=colour[sku])
            for y in range(box[1], box[3], 12):
                draw.line((box[0], y, box[2], y), fill="#f0d9a8", width=3)
        return photo

    def test_the_gate_measures_each_alternative_against_the_bought_top_and_gates_the_quote(self):
        sid, jobs = self.previews(self.paint_plain_or_busy)
        self.assertIsNone(jobs["ORIGINAL"]["gate"])
        self.assertEqual(jobs["CALM-07"]["gate"]["status"], "shows")
        self.assertLessEqual(jobs["CALM-07"]["gate"]["print_ratio"], gate.QUIET_RATIO)
        self.assertEqual(jobs["CALM-04"]["gate"]["status"], "concern")
        self.assertIn("not quieter", jobs["CALM-04"]["gate"]["note"])
        trust = jobs["CALM-04"]["trust"]
        self.assertTrue(trust["attention"])
        self.assertEqual(trust["summary"], "This preview does not show what you asked for")
        self.assertTrue(any(c["label"].startswith("Print") for c in trust["checks"]))
        self.assertTrue(any(c["label"] == "Outside the garment" and c["status"] == "stable" for c in trust["checks"]))
        reference = PreviewJob.objects.get(variant__sku="ORIGINAL")
        self.assertEqual(reference.gate_status, "reference")
        self.assertFalse(any(job["gate_pending"] for job in jobs.values()))
        # The card draws the verdict on the preview and names the failing check.
        self.assertEqual((jobs["CALM-07"]["gate"]["badge"], jobs["CALM-07"]["gate"]["flags"]), ("Checks agree", []))
        self.assertEqual((jobs["CALM-04"]["gate"]["badge"], jobs["CALM-04"]["gate"]["flags"]), ("Flagged: print", ["print"]))
        # The catalog colour sits beside the colour measured on the preview; the patterned blouse has none.
        swatches = jobs["CALM-07"]["swatches"]
        self.assertEqual(swatches["product"], ProductVariant.objects.get(sku="CALM-07").reference_colour)
        self.assertRegex(swatches["preview"], r"^#[0-9a-f]{6}$")
        self.assertEqual(PreviewJob.objects.get(variant__sku="CALM-07").preview_colour, swatches["preview"])
        self.assertIsNone(jobs["ORIGINAL"]["swatches"])
        # What changed is drawn from the stability check, for this browser only.
        changes = jobs["CALM-04"]["changes"]
        self.assertEqual(changes["url"], f"/api/previews/{jobs['CALM-04']['job_id']}/changes")
        self.assertEqual(changes["stable_pct"], gate.STABLE_PCT)
        drawn = self.client.get(changes["url"])
        self.assertEqual((drawn.status_code, drawn["Content-Type"], drawn["Cache-Control"]), (200, "image/jpeg", "private, no-store"))
        self.assertEqual(Image.open(io.BytesIO(drawn.content)).format, "JPEG")
        self.assertEqual(self.client.get(jobs["ORIGINAL"]["changes"]["url"]).status_code, 200)
        from django.test import Client
        self.assertEqual(Client().get(changes["url"]).status_code, 404)
        # Before the worker writes the verdict, the page is told to ask again; the bought top never waits.
        PreviewJob.objects.filter(variant__sku="CALM-07").update(gate_status="", gate_note="")
        waiting = self.client.get(f"/api/previews/{jobs['CALM-07']['job_id']}").json()
        self.assertTrue(waiting["gate_pending"])
        self.assertIsNone(waiting["gate"])
        self.assertFalse(self.client.get(f"/api/previews/{jobs['ORIGINAL']['job_id']}").json()["gate_pending"])
        PreviewJob.objects.filter(variant__sku="CALM-07").update(gate_status="shows")

        refused = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-04"})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(refused.json()["error"]["code"], "PREVIEW_CONCERN")
        accepted = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-04", "accept_preview_concern": True,
                              "preview_concern_token": refused.json()["error"]["concern_token"]})
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).status_code, 201)

    def test_the_evaluation_headline_keeps_tops_that_should_pass_apart_from_tops_built_to_fail(self):
        from pathlib import Path
        from django.conf import settings
        from exchange.gate_outcomes import outcomes
        report = json.loads((Path(settings.BASE_DIR) / "exchange/static/exchange/evidence/gate-v1.json").read_text())
        o = outcomes(report)
        self.assertEqual((o["should_pass"]["passed"], o["should_pass"]["n"], o["people"]), (15, 26, 6))
        # Earlier photo-holdout images calibrated this later gate; do not count them as unseen.
        retrospective = {**report, "cases": [c for c in report["cases"] if c["split"] == "held_out"]}
        self.assertEqual(outcomes(retrospective)["should_pass"]["n"], 0)
        self.assertEqual(o["should_pass"]["reasons"], {"colour": 7, "print": 4, "redrawn": 4})
        self.assertEqual((o["built_to_fail"]["held"], o["built_to_fail"]["n"]), (4, 8))
        self.assertEqual((o["repeats"]["flagged"], o["repeats"]["n"]), (6, 6))
        # Every published alternative lands in exactly one group.
        groups = o["should_pass"]["n"] + o["built_to_fail"]["n"] + o["repeats"]["n"] + o["development"]["n"]
        self.assertEqual(groups, report["summary"]["alternatives"])
        page = self.client.get("/evaluation").content.decode()
        self.assertIn("<strong>15 / 26</strong><span>plain-top alternatives passed", page)
        self.assertIn("<strong>4 / 8</strong><span>patterned tops registered to fail were held back", page)

    def test_a_result_without_the_bought_top_is_marked_for_the_shopper_to_check(self):
        results = {}

        def start(job):
            photo = Image.open(io.BytesIO(job.person_image)).convert("RGB")
            ImageDraw.Draw(photo).rectangle(self.torso(photo), fill=hex_to_rgb(ProductVariant.objects.get(sku="CALM-07").reference_colour))
            results[f"task-{job.sku}"] = jpeg(photo)
            return StartResult(f"task-{job.sku}", 2)

        cls = fake_provider(start=start, poll=lambda task: PollResult(SUCCEEDED, 2, image=results[task]))
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})
        with provider(cls, ceiling=100):
            asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
            job, = self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": ["CALM-07"], "consent": True,
                                                                        "asset_id": asset, "include_original": False}).json()["jobs"]
        self.assertEqual(job["gate"]["status"], "check")
        self.assertIsNone(job["gate"]["print_ratio"])
        self.assertEqual(self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).status_code, 201)

    def test_the_published_gate_report_is_current(self):
        from django.conf import settings
        from pathlib import Path
        from exchange import gate_evaluation
        self.assertTrue(gate_evaluation.check(Path(settings.BASE_DIR)))


class RegisteredStudyTests(SimpleTestCase):
    def test_the_v2_study_is_frozen_with_its_people_and_code(self):
        import hashlib
        from pathlib import Path
        from django.conf import settings
        root = Path(settings.BASE_DIR)
        sha = lambda path: hashlib.sha256((root / path).read_bytes()).hexdigest()
        plan = json.loads((root / "assets/gate_study_v2.json").read_text())
        self.assertEqual(sha("assets/gate_study_v2.json"), (root / "assets/gate_study_v2.sha256").read_text().split()[0])
        for name, digest in {**plan["code_sha256"], **plan["sources"]}.items():
            self.assertEqual(sha(name), digest, name)
        for model in plan["models"]:
            self.assertEqual(sha("exchange/static/" + model["static_path"]), model["sha256"], model["name"])
            self.assertTrue(model["source"]["ai_generated"])
        self.assertEqual(plan["budget"]["estimated_units"], sum(c["estimated_units"] for c in plan["cases"]))
        self.assertLessEqual(plan["budget"]["estimated_units"], plan["budget"]["approved_cap_units"])
        roles = [c["role"] for c in plan["cases"]]
        self.assertEqual((roles.count("reference"), roles.count("plain"), roles.count("busy"), roles.count("repeat")), (4, 20, 8, 6))

    def test_the_v2_label_items_are_blind_and_the_agreement_is_recomputed_from_the_published_labels(self):
        from pathlib import Path
        from django.conf import settings
        from exchange import gate_evaluation, gate_labels
        root = Path(settings.BASE_DIR)
        evidence = root / "exchange/static/exchange/evidence"
        report = json.loads((evidence / "gate-study-v2/report.json").read_text())
        items = gate_labels.items(root, report, json.loads((evidence / "gate-study-v1/report.json").read_text()))
        self.assertEqual(len(items), 38)
        self.assertEqual(sorted({len(i["questions"]) for i in items}), [1, 2, 3])
        self.assertNotIn("sku", json.dumps([{k: v for k, v in i.items() if k != "images"} for i in items]).lower())
        labels = json.loads((evidence / "gate-study-v2/labels-ai.json").read_text())
        self.assertEqual(set(labels["answers"]), {i["id"] for i in items})
        published = json.loads((evidence / "gate-v1.json").read_text())
        self.assertEqual(gate_labels.agreement(published["cases"], labels), published["labels"][0]["agreement"])


class HumanLabelTests(SimpleTestCase):
    """The labels addendum: complete sets publish under pseudonyms, and the majority of independent people is compared."""

    def setUp(self):
        from pathlib import Path
        from django.conf import settings
        from exchange import gate_labels
        self.root = Path(settings.BASE_DIR)
        evidence = self.root / "exchange/static/exchange/evidence"
        self.items = gate_labels.items(self.root, json.loads((evidence / "gate-study-v2/report.json").read_text()),
                                       json.loads((evidence / "gate-study-v1/report.json").read_text()))
        self.cases = json.loads((evidence / "gate-v1.json").read_text())["cases"]
        self.gate = {c["id"]: c for c in self.cases}

    def answers(self, agree=True):
        """Every question answered, as the gate measured it or the other way round."""
        from exchange.gate_labels import gate_answer
        other = {"quieter": "not quieter", "not quieter": "quieter", "matches": "differs", "differs": "matches",
                 "unchanged": "changed", "changed": "unchanged", None: "unclear"}
        out = {}
        for item in self.items:
            for q in item["questions"]:
                gate = gate_answer(self.gate[item["id"]], q)
                out.setdefault(item["id"], {})[q] = (gate or "unclear") if agree else other[gate]
        return out

    def test_the_addendum_is_frozen_before_any_human_label(self):
        import hashlib
        data = (self.root / "assets/gate_labels_addendum.json").read_bytes()
        frozen = (self.root / "assets/gate_labels_addendum.sha256").read_text().split()[0]
        self.assertEqual(hashlib.sha256(data).hexdigest(), frozen)
        self.assertIn("whatever it shows", json.loads(data)["commitment"])

    def test_complete_sets_publish_under_pseudonyms_and_an_incomplete_one_is_refused(self):
        from django.core.management.base import CommandError
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as dest:
            from pathlib import Path
            for rater, agree in (("ann-k", True), ("bo", False), ("owner", True)):
                (Path(source) / f"gate-study-v2-labels-{rater}.json").write_text(json.dumps(
                    {"rater": rater, "blind_to": "gate output", "order_seed": 20261030,
                     "updated_at_utc": "2026-10-31T09:00:00+00:00", "answers": self.answers(agree)}))
            partial = self.answers()
            partial.pop(self.items[0]["id"])
            (Path(source) / "gate-study-v2-labels-cy.json").write_text(json.dumps({"rater": "cy", "answers": partial}))
            with self.assertRaisesMessage(CommandError, "Rater cy has"):
                call_command("publish_human_labels", "cy", source=source, dest=dest, stdout=io.StringIO())
            call_command("publish_human_labels", "ann-k", "bo", "owner", source=source, dest=dest, stdout=io.StringIO())
            written = {p.name: json.loads(p.read_text()) for p in Path(dest).glob("labels-*.json")}
        self.assertEqual(sorted(written), ["labels-owner.json", "labels-person-1.json", "labels-person-2.json"])
        self.assertEqual(written["labels-person-1.json"]["rater"], "Person 1 (human)")
        self.assertTrue(written["labels-person-2.json"]["independent"])
        self.assertFalse(written["labels-owner.json"]["independent"])
        self.assertEqual(written["labels-person-1.json"]["protocol"], "assets/gate_labels_addendum.json")
        # A rater's own id never reaches the published file.
        self.assertNotIn("ann-k", json.dumps(written))
        self.assertEqual(written["labels-person-1.json"]["answers"], self.answers(True))

    def test_the_majority_of_independent_people_is_compared_and_ties_are_counted(self):
        from exchange.gate_evaluation import human_consensus
        person = lambda answers, independent=True: {"kind": "human", "independent": independent, "answers": answers}
        ai = {"kind": "ai", "answers": self.answers(False)}
        self.assertIsNone(human_consensus(self.cases, [person(self.answers())]))
        self.assertIsNone(human_consensus(self.cases, [person(self.answers()), person(self.answers(), False), ai]))
        agree = human_consensus(self.cases, [person(self.answers()), person(self.answers()), person(self.answers(False))])
        self.assertEqual(agree["raters"], 3)
        for question in ("print", "colour", "outside"):
            self.assertEqual(agree["agreement"][question]["agree"], agree["agreement"][question]["answered"], question)
        split = human_consensus(self.cases, [person(self.answers()), person(self.answers(False))])
        self.assertGreater(split["ties"], 0)
        self.assertEqual(sum(split["agreement"][q]["answered"] for q in ("print", "colour", "outside")), 0)
