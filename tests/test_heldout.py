import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_heldout import check_catalog, check_hashes, digest, validate_design

ROOT = Path(__file__).resolve().parents[1]


class HeldOutFreezeTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "assets/heldout_v1.json").read_text())

    def test_planned_cases_do_not_become_completed_evidence(self):
        summary = validate_design(self.plan)
        self.assertEqual(summary["completed_held_out_outcomes"], 0)
        self.assertEqual((summary["planned_outcomes"], summary["distinct_source_photos"], summary["proposed_units"]), (6, 2, 12))
        self.plan["cases"][0]["result"] = "generated.jpg"
        with self.assertRaisesRegex(ValueError, "Completed results"):
            validate_design(self.plan)

    def test_reusing_a_development_subject_or_duplicate_case_is_rejected(self):
        self.plan["known_development_models"].append(self.plan["models"][0]["name"])
        with self.assertRaisesRegex(ValueError, "development subject"):
            validate_design(self.plan)
        self.plan["known_development_models"].pop()
        self.plan["cases"].append(copy.deepcopy(self.plan["cases"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_design(self.plan)

    def test_input_drift_and_manifest_drift_are_detectable(self):
        path = ROOT / "assets/heldout_v1.json"
        self.assertEqual(digest(path.read_bytes()), (ROOT / "assets/heldout_v1.sha256").read_text().split()[0])
        self.assertNotEqual(digest(path.read_bytes() + b" "), digest(path.read_bytes()))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "photo.jpg").write_bytes(b"original bytes")
            expected = {"photo.jpg": digest(b"original bytes")}
            check_hashes(root, expected)
            (root / "photo.jpg").write_bytes(b"changed bytes")
            with self.assertRaisesRegex(ValueError, "Frozen input"):
                check_hashes(root, expected)

    def test_current_database_must_keep_the_same_product_inputs(self):
        frozen = self.plan["catalog_snapshot"]
        check_catalog(copy.deepcopy(frozen), frozen)
        changed = copy.deepcopy(frozen)
        changed["CALM-04"]["image_asset_id"] = "exchange/catalog/different-product.jpg"
        with self.assertRaisesRegex(ValueError, "Catalog facts changed"):
            check_catalog(changed, frozen)


if __name__ == "__main__":
    unittest.main()
