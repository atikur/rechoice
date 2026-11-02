import json
from pathlib import Path
import shutil
import tempfile

from django.conf import settings
from django.test import SimpleTestCase

from exchange.heldout_evaluation import FOLDER, build_heldout


ROOT = Path(settings.BASE_DIR)


class HeldoutEvaluationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = build_heldout(ROOT)

    def public_copy(self, root):
        """Only the public tree; intentionally omit the database, .env and private photos."""
        shutil.copytree(ROOT / "assets", root / "assets")
        shutil.copytree(ROOT / "exchange/static", root / "exchange/static")
        names = list(json.loads((ROOT / "assets/heldout_v1.json").read_text())["input_sha256"])
        names.append("exchange/heldout_evaluation.py")
        for name in names:
            if name.startswith("private_media/") or (root / name).exists():
                continue
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, root / name)

    def test_committed_report_reproduces_without_private_files_or_database_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.public_copy(root)
            self.assertFalse((root / "private_media").exists())
            self.assertEqual(build_heldout(root), self.report)
        self.assertEqual(self.report, json.loads((ROOT / FOLDER / "report.json").read_text()))
        self.assertFalse(any("private_media" in name for name in self.report["input_sha256"]))

    def test_first_attempt_denominators_billing_and_unassessed_human_fields_are_separate(self):
        s = self.report["summary"]
        self.assertEqual((s["planned"], s["succeeded"], s["source_photos"]), (6, 6, 2))
        self.assertEqual((s["failed"], s["uncertain"], s["not_attempted"]), (0, 0, 0))
        self.assertEqual((s["plain_colour_planned"], s["plain_colour_measured"], s["plain_colour_close"]), (4, 4, 4))
        self.assertEqual((s["plain_colour_missing"], s["plain_colour_different"], s["patterned_references"]), (0, 0, 2))
        self.assertEqual((s["requested_difference_reviewed"], s["independent_human_reviews"]), (0, 0))
        self.assertEqual((s["units"], self.report["billing"]["balance_after"]), (12, 958))
        self.assertEqual([c["colour"]["delta"] for c in self.report["cases"]], [None, 0.9, 3.7, None, 5.5, 2.5])
        self.assertTrue(all(c["attempts"] == 1 and c["review"] is None for c in self.report["cases"]))
        observations = [c["observation"] for c in self.report["cases"] if c["observation"]]
        self.assertEqual(len(observations), 1)
        self.assertFalse(observations[0]["blinded"])

    def test_changed_output_or_frozen_measurement_code_is_rejected(self):
        for name, message in ((str(FOLDER / self.report["cases"][0]["result_file"]), "Output hash changed"),
                              ("exchange/colour_check.py", "Frozen input or measurement code changed")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.public_copy(root)
                with (root / name).open("ab") as stream:
                    stream.write(b"changed")
                with self.assertRaisesRegex(ValueError, message):
                    build_heldout(root)

    def test_omitted_or_duplicated_cases_cannot_disappear_from_the_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.public_copy(root)
            path = root / FOLDER / "observations.json"
            data = json.loads(path.read_text())
            cases = data["cases"]
            for changed in (cases[:-1], cases[:-1] + [cases[0]]):
                data["cases"] = changed
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "Every frozen case"):
                    build_heldout(root)

    def test_budget_overrun_or_unreconciled_balance_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.public_copy(root)
            path = root / FOLDER / "observations.json"
            original = path.read_text()
            data = json.loads(original)
            data["approved_unit_cap"] = 11
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "approved unit cap"):
                build_heldout(root)
            data = json.loads(original)
            data["billing"]["balance_after"] = 957
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "balance change"):
                build_heldout(root)

    def test_public_pages_keep_cohorts_separate_and_show_all_results_and_limits(self):
        response = self.client.get("/evaluation/heldout")
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        for phrase in ("6 planned renders on 2 synthetic photos", "Colour closeness is not overall fidelity",
                       "AI observation, not blinded", "pushed up", "Independent human review pending",
                       "evaluate_heldout --check", "a zero does not establish", "958 remaining"):
            self.assertIn(phrase, text)
        for case in self.report["cases"]:
            self.assertIn(f'id="{case["id"]}"', text)
        development = self.client.get("/evaluation").content.decode()
        self.assertIn('href="/evaluation/heldout"', development)
        self.assertIn("36 development outcomes.", development)
        self.assertNotIn("0 held-out outcomes", development)
