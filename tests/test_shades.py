import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from secondlook.domain import DomainError, OrderLine, Policy, Variant, make_quote, revalidate_quote, shortlist
from secondlook.shades import ShadePreferences, shade_exclusions, shade_moves, shade_shortlist

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
PURCHASED = "#b5532f"  # an orange-brown lipstick the shopper finds too orange


def shade(sku, hex_value, line="LIP-VELVET", price=1800, stock=4, category="lip"):
    return Variant("demo", sku, category, "", "USD", price, stock, None, None, None, None, "", 1, line, hex_value)


def fixture(opened=True):
    line = OrderLine("demo", "LIP-230", 1, 1800, 1800, "USD", True, 1, "LIP-VELVET", "", "lip", opened, PURCHASED)
    policy = Policy(1, "tops", NOW + timedelta(days=30), 0, True, ("tops", "lip", "nail"), True)
    shades = [shade("LIP-230", PURCHASED), shade("LIP-220", "#a04a55"), shade("LIP-240", "#9c5a6e"),
              shade("LIP-225", "#9a3b2e"), shade("LIP-235", "#e0654a"), shade("LIP-210", "#c98a84"),
              shade("LIP-250", "#7e2a44", stock=0), shade("SATIN-10", "#a64d63", line="LIP-SATIN"),
              shade("NAIL-1", "#a04a55", line="NAIL-GLOSS", category="nail")]
    return line, policy, shades


class ShadeTests(unittest.TestCase):
    def test_moves_measure_lightness_warmth_and_intensity_from_the_purchased_shade(self):
        moves = shade_moves(PURCHASED, "#c98a84")
        self.assertGreater(moves["lightness"], 10)
        self.assertLess(moves["tone"], -10)

    def test_only_shades_that_move_the_stated_way_are_offered_nearest_first(self):
        line, policy, shades = fixture()
        cooler = ShadePreferences(1000, tone="cooler")
        found = shade_shortlist(line, policy, cooler, shades, NOW)
        self.assertEqual([c.variant.sku for c in found], ["LIP-220", "LIP-240"])
        self.assertIn("same product line", found[0].reasons)
        self.assertTrue(any("cooler" in r for r in found[0].reasons))
        lighter_and_cooler = shade_shortlist(line, policy, ShadePreferences(1000, lightness="lighter", tone="cooler"),
                                             shades, NOW)
        self.assertEqual([c.variant.sku for c in lighter_and_cooler], ["LIP-210"])

    def test_an_opened_product_stays_in_its_line_and_other_categories_never_appear(self):
        line, policy, shades = fixture(opened=True)
        found = shade_shortlist(line, policy, ShadePreferences(1000, tone="cooler"), shades, NOW)
        self.assertNotIn("SATIN-10", [c.variant.sku for c in found])
        unopened = shade_shortlist(replace(line, opened=False), policy, ShadePreferences(1000, tone="cooler"), shades, NOW)
        self.assertIn("SATIN-10", [c.variant.sku for c in unopened])
        self.assertFalse([c for c in found if c.variant.category != "lip"])

    def test_exclusions_explain_every_left_out_shade_without_relaxing(self):
        line, policy, shades = fixture()
        left_out = {x["code"]: x for x in shade_exclusions(line, policy, ShadePreferences(1000, tone="cooler"), shades, NOW)}
        self.assertEqual(left_out["out_of_stock"]["count"], 1)
        self.assertEqual(left_out["opened_same_line"]["count"], 1)
        self.assertIn("not cooler enough", left_out["not_tone"]["text"])
        self.assertNotIn("NAIL", str(left_out))

    def test_a_shade_change_must_be_stated_and_valid(self):
        line, policy, shades = fixture()
        for prefs, code in ((ShadePreferences(1000), "SHADE_CHANGE_REQUIRED"),
                            (ShadePreferences(1000, tone="pinker"), "INVALID_PREFERENCE"),
                            (ShadePreferences(-1, tone="cooler"), "INVALID_MONEY")):
            with self.subTest(code=code), self.assertRaises(DomainError) as error:
                shade_shortlist(line, policy, prefs, shades, NOW)
            self.assertEqual(error.exception.code, code)

    def test_shade_quotes_are_made_and_rechecked_against_the_stated_change(self):
        line, policy, shades = fixture()
        prefs = ShadePreferences(1000, tone="cooler")
        choice = next(s for s in shades if s.sku == "LIP-220")
        quote = make_quote(line, choice, policy, prefs, NOW + timedelta(minutes=10), NOW, "shade")
        self.assertEqual((quote.kind, quote.top_up_minor), ("shade", 0))
        revalidate_quote(quote, line, choice, policy, prefs, NOW)
        with self.assertRaises(DomainError) as wrong_way:
            make_quote(line, next(s for s in shades if s.sku == "LIP-235"), policy, prefs,
                       NOW + timedelta(minutes=10), NOW, "shade")
        self.assertEqual(wrong_way.exception.code, "INELIGIBLE")
        with self.assertRaises(DomainError) as sold_out:
            revalidate_quote(quote, line, replace(choice, stock_qty=0), policy, prefs, NOW)
        self.assertEqual(sold_out.exception.code, "OUT_OF_STOCK")

    def test_style_shortlists_never_mix_in_beauty(self):
        line = OrderLine("demo", "TOP", 1, 4000, 4000, "USD", True, 1, "TOP", "M", "tops")
        policy = Policy(1, "tops", NOW + timedelta(days=30), 0, True, ("tops", "lip"), True)
        from secondlook.domain import PatternLevel, Preferences
        top = Variant("demo", "TOP-2", "tops", "M", "USD", 4200, 3, PatternLevel.QUIET, "blue", "long", "crew", "", 1)
        found = shortlist(line, policy, Preferences(1000), [top, shade("LIP-1", "#a04a55", price=4100)], NOW)
        self.assertEqual([c.variant.sku for c in found], ["TOP-2"])


if __name__ == "__main__":
    unittest.main()
