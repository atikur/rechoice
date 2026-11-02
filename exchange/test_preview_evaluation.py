import io
import json
from pathlib import Path
import shutil
import tempfile

from django.conf import settings
from django.test import SimpleTestCase
from PIL import Image, ImageDraw

from exchange.preview_evaluation import (build_evaluation, outside_change, review_template, summarize,
                                         validate_reviews)

ROOT = Path(settings.BASE_DIR)


class PreviewEvaluationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report, cls.corrections = build_evaluation(ROOT)

    def test_the_committed_report_and_corrections_reproduce_without_private_media_or_database_data(self):
        self.assertEqual(self.report, json.loads((ROOT / "exchange/static/exchange/evidence/evaluation.json").read_text()))
        for name, content in self.corrections.items():
            self.assertEqual((ROOT / "exchange/static" / name).read_bytes(), content)
        self.assertEqual(self.report["summary"]["outcomes"], 36)
        self.assertEqual(self.report["summary"]["development"], 36)
        self.assertEqual(self.report["summary"]["held_out"], 0)
        self.assertFalse(any("private_media" in key for key in self.report["input_sha256"]))

    def test_denominators_exclude_reference_earring_patterns_and_unassessed_reviews(self):
        summary = self.report["summary"]
        self.assertEqual((summary["plain_colour_measured"], summary["plain_colour_close"]), (7, 6))
        self.assertEqual((summary["relative_size_compared"], summary["relative_size_close"]), (2, 1))
        self.assertEqual((summary["size_warnings"], summary["size_corrections_passed"]), (1, 1))
        self.assertEqual(summary["patterned_not_scored"], 3)
        self.assertEqual(summary["uncalibrated_shades"], 12)
        self.assertEqual(summary["prior_ai_reviews"], 10)
        self.assertEqual(summary["requested_difference_reviewed"], 0)
        self.assertEqual(summary["new_complete_reviews"], 0)
        cases = {c["id"]: c for c in self.report["cases"]}
        self.assertTrue(cases["accessories-bag-probe"]["finding"])
        self.assertIn("corrected_image", cases["jewellery-02"])

    def test_future_held_out_counts_are_not_pooled_with_development(self):
        held_out = dict(self.report["cases"][0], split="held_out")
        development = [c for c in self.report["cases"] if c["id"] != held_out["id"]]
        self.assertEqual(summarize([held_out])["plain_colour_measured"], 1)
        self.assertEqual(summarize(development)["plain_colour_measured"], 6)
        self.assertEqual(self.report["split_metrics"]["held_out"]["outcomes"], 0)

    def test_review_provenance_and_rubric_are_required_and_unknown_cases_are_rejected(self):
        review = review_template(self.report["cases"])
        ids = {c["id"] for c in self.report["cases"]}
        with self.assertRaisesRegex(ValueError, "named reviewer"):
            validate_reviews(review, ids)
        for malformed in ([], "review", {"reviewer": 42}, {"reviewer": None}):
            with self.subTest(malformed=malformed), self.assertRaisesRegex(ValueError, "named reviewer"):
                validate_reviews(malformed, ids)
        review["reviewer"] = "Test reviewer, not an actual evaluation"
        self.assertEqual(len(validate_reviews(review, ids)), 36)
        review["ratings"]["not-a-case"] = review["ratings"]["pilot-01"]
        with self.assertRaisesRegex(ValueError, "unknown case"):
            validate_reviews(review, ids)
        review["ratings"].pop("not-a-case")
        review["ratings"]["pilot-01"]["product_detail"] = "perfect"
        with self.assertRaisesRegex(ValueError, "invalid product_detail"):
            validate_reviews(review, ids)
        case = dict(self.report["cases"][0], review={key: "not_assessed" for key in review["ratings"]["pilot-01"]})
        self.assertEqual(summarize([case])["new_complete_reviews"], 0)

    def test_pixel_change_only_counts_pixels_outside_the_declared_region_and_is_not_a_pass_fail(self):
        def data(image):
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            return stream.getvalue()
        original = Image.new("RGB", (100, 100), "white")
        changed = original.copy()
        ImageDraw.Draw(changed).rectangle((30, 30, 70, 70), fill="black")
        region = [[0.2, 0.2, 0.8, 0.8]]
        self.assertEqual(outside_change(data(original), data(changed), region, 40), 0)
        ImageDraw.Draw(changed).rectangle((0, 0, 15, 15), fill="black")
        self.assertGreater(outside_change(data(original), data(changed), region, 40), 0)
        self.assertIsNone(outside_change(data(original), data(Image.new("RGB", (200, 100))), region, 40))

    def test_public_page_shows_the_limits_failed_probe_method_and_every_case(self):
        response = self.client.get("/evaluation")
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        for phrase in ("36 development outcomes.", "not an independent accuracy benchmark",
                       "Rejected pilot result", "No combined pass rate", "evaluate_previews --check",
                       "New rubric review pending", "Prior AI review, not blinded", "not a preservation verdict"):
            self.assertIn(phrase, text)
        for case in self.report["cases"]:
            self.assertIn(f'id="{case["id"]}"', text)

    def test_changing_a_committed_input_changes_its_hash_and_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "assets", root / "assets")
            shutil.copytree(ROOT / "exchange/static", root / "exchange/static")
            for name in ("preview_evaluation.py", "colour_check.py", "size_check.py"):
                shutil.copy2(ROOT / "exchange" / name, root / "exchange" / name)
            image = root / "exchange/static/exchange/evidence/pilot-01-model-a-CALM-04.jpg"
            with Image.open(image) as opened:
                changed = opened.convert("RGB")
            ImageDraw.Draw(changed).rectangle((100, 300, changed.width - 100, changed.height - 20), fill="red")
            changed.save(image, quality=90)
            fresh, _ = build_evaluation(root)
            key = str(image.relative_to(root))
            self.assertNotEqual(fresh["input_sha256"][key], self.report["input_sha256"][key])
            self.assertNotEqual(fresh["cases"][0]["checks"], self.report["cases"][0]["checks"])
