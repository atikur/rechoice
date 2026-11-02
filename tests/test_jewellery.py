import unittest
from datetime import datetime, timedelta, timezone

from secondlook.domain import DomainError, OrderLine, Policy, Variant, make_quote
from secondlook.jewellery import JewelleryPreferences, jewellery_exclusions, jewellery_shortlist
from secondlook.preference_text import interpret_jewellery

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def pair(sku, metal, form, length, price=4200, stock=3):
    return Variant("demo", sku, "earrings", "", "USD", price, stock, None, metal, None, None, "", 1, sku, None,
                   length, form)


def fixture():
    # The shopper bought long gold drops and finds them too long.
    line = OrderLine("demo", "PEARL-58", 1, 4200, 4200, "USD", True, 1, "PEARL-58", "", "earrings", False, None,
                     58, "gold", "drop")
    policy = Policy(1, "tops", NOW + timedelta(days=30), 0, True, ("tops", "earrings"), False)
    pairs = [pair("PEARL-58", "gold", "drop", 58), pair("CRYSTAL-38", "silver", "drop", 38, 4400),
             pair("TOPAZ-12", "silver", "stud", 12), pair("ROPE-10", "gold", "stud", 10),
             pair("HOOP-50", "gold", "hoop", 50), pair("LONG-80", "gold", "drop", 80),
             pair("SOLD-20", "silver", "drop", 20, stock=0), pair("CHEAP-15", "silver", "stud", 15, 3000)]
    return line, policy, pairs


class JewelleryTests(unittest.TestCase):
    def test_smaller_and_silver_are_hard_filters_and_the_same_style_comes_first(self):
        line, policy, pairs = fixture()
        found = jewellery_shortlist(line, policy, JewelleryPreferences(500, "smaller", "silver"), pairs, NOW)
        self.assertEqual([c.variant.sku for c in found], ["CRYSTAL-38", "TOPAZ-12"])
        self.assertEqual(found[0].reasons[:3], ("smaller: 38 mm instead of 58 mm", "silver", "same style, drop"))
        self.assertIn("a stud instead of a drop", found[1].reasons)

    def test_a_size_change_must_be_at_least_a_quarter_and_the_metal_is_kept_unless_asked(self):
        line, policy, pairs = fixture()
        found = jewellery_shortlist(line, policy, JewelleryPreferences(500, "smaller"), pairs, NOW)
        # HOOP-50 is only 14% shorter, so it is not smaller enough; gold studs keep the metal.
        self.assertEqual([c.variant.sku for c in found], ["ROPE-10", "CRYSTAL-38"])
        larger = jewellery_shortlist(line, policy, JewelleryPreferences(500, "larger"), pairs, NOW)
        self.assertEqual([c.variant.sku for c in larger], ["LONG-80"])

    def test_exclusions_explain_every_pair_left_out_without_relaxing(self):
        line, policy, pairs = fixture()
        left_out = {x["code"]: x for x in jewellery_exclusions(
            line, policy, JewelleryPreferences(0, "smaller", "silver"), pairs, NOW)}
        self.assertEqual(left_out["out_of_stock"]["count"], 1)
        self.assertEqual(left_out["staff_review"]["count"], 1)
        self.assertEqual(left_out["over_budget"]["count"], 1)
        self.assertEqual(left_out["not_size"]["text"], "2 not clearly smaller")
        self.assertEqual(left_out["not_metal"]["text"], "1 not in silver")

    def test_a_change_must_be_stated_and_the_quote_rechecks_it(self):
        line, policy, pairs = fixture()
        for prefs, code in ((JewelleryPreferences(500), "CHANGE_REQUIRED"),
                            (JewelleryPreferences(500, metal="rose"), "INVALID_PREFERENCE")):
            with self.subTest(code=code), self.assertRaises(DomainError) as error:
                jewellery_shortlist(line, policy, prefs, pairs, NOW)
            self.assertEqual(error.exception.code, code)
        prefs = JewelleryPreferences(500, "smaller", "silver")
        quote = make_quote(line, pairs[1], policy, prefs, NOW + timedelta(minutes=10), NOW, "jewellery")
        self.assertEqual((quote.kind, quote.top_up_minor), ("jewellery", 200))
        with self.assertRaises(DomainError) as gold:
            make_quote(line, pairs[3], policy, prefs, NOW + timedelta(minutes=10), NOW, "jewellery")
        self.assertEqual(gold.exception.code, "INELIGIBLE")

    def test_the_shoppers_words_fill_size_metal_and_budget(self):
        result = interpret_jewellery("These are too long and I wanted silver. Nothing extra please.")
        self.assertEqual(result.proposal, {"size": "smaller", "metal": "silver", "max_top_up_minor": 0})
        self.assertEqual(interpret_jewellery("gold looks wrong on me, I would like silver instead").proposal,
                         {"metal": "silver"})
        self.assertEqual(interpret_jewellery("smaller, or maybe larger").questions[0]["field"], "size")
        self.assertEqual(interpret_jewellery("not too big at all, just not silver").proposal, {})


if __name__ == "__main__":
    unittest.main()
