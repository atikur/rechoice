import io
import json
from pathlib import Path

from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import TestCase

from exchange.colour_check import ciede2000, hex_to_rgb, rgb_to_lab


class AccuracyEvidenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def data(self):
        return json.loads(Path(finders.find("exchange/evidence/accuracy.json")).read_text())

    def test_the_measurements_cover_every_published_result_with_honest_denominators(self):
        data = self.data()
        published = {name: json.loads(Path(finders.find(f"exchange/evidence/{name}.json")).read_text())["results"]
                     for name in ("pilot", "beauty", "jewellery")}
        self.assertEqual([e["sku"] for e in data["earrings"]], [r["sku"] for r in published["jewellery"]])
        self.assertEqual([s["sku"] for s in data["shades"]], [r["sku"] for r in published["beauty"]])
        self.assertEqual([c["sku"] for c in data["clothes"]], [r["sku"] for r in published["pilot"]])
        # The pair bought is the yardstick, so it is never counted as in proportion.
        self.assertEqual(data["earrings"][0]["status"], "reference")
        self.assertEqual(data["summary"]["earrings"], {"compared": 2, "in_proportion": 1, "in_proportion_resized": 2})
        # Only the earring out of proportion is resized, and it is measured again afterwards.
        crystal = next(e for e in data["earrings"] if e["status"] == "longer")
        self.assertLessEqual(abs(crystal["resized_pct"]), 15)
        self.assertEqual([e["sku"] for e in data["earrings"] if "resized_share" in e], [crystal["sku"]])
        self.assertEqual(data["summary"]["shades"]["readable"],
                         sum(s["delta"] is not None for s in data["shades"]))
        self.assertEqual(data["summary"]["clothes"]["plain"] + data["summary"]["clothes"]["patterned"], 10)
        # Every ΔE shown is the difference between the two swatches it sits beside.
        rows = [(s["measured_hex"], s["requested_hex"], s["delta"]) for s in data["shades"]]
        rows += [(c["measured_hex"], c["reference_hex"], c["delta"]) for c in data["clothes"]]
        for measured_hex, reference_hex, delta in rows:
            if measured_hex:
                measured = ciede2000(rgb_to_lab(hex_to_rgb(measured_hex)), rgb_to_lab(hex_to_rgb(reference_hex)))
                self.assertAlmostEqual(measured, delta, delta=0.06)
        # Clothes were measured on the same stored files when published, so they match the published check.
        recorded = {r["sku"]: r["colour_check"]["delta"] for r in published["pilot"]}
        for row in data["clothes"]:
            if row["delta"] is not None:
                self.assertAlmostEqual(row["delta"], recorded[row["sku"]], delta=0.15)

    def test_the_evidence_page_opens_with_how_accurate_it_is_including_what_failed(self):
        html = self.client.get("/evidence").content.decode()
        self.assertLess(html.index("How accurate is it?"), html.index("Clothes pilot"))
        for text in ("6 of 7 plain tops are within ΔE 10", "6 of 8 readable results are within ΔE 5",
                     "1 of 2 earrings render in proportion",
                     "The 38 mm Blue Crystal Drops renders 51% longer than its length implies",
                     "does not control drop length", "resized, 2 of 2 are in proportion", "0.65 (-1%)", "too subtle", "patterned, not measured",
                     "Indigo Denim Shirt", "The correction uses catalog length ratios",
                     "it does not establish physical length or fit on a person"):
            self.assertIn(text, html)
        self.assertNotIn("Sizes follow the catalog lengths", html)
        self.assertIn("Size does not always follow the catalog length", html)
