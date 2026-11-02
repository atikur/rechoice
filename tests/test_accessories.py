import unittest
from datetime import datetime, timedelta, timezone

from secondlook.domain import DomainError, OrderLine, Policy, Variant
from secondlook.jewellery import JewelleryPreferences, jewellery_exclusions, jewellery_shortlist
from secondlook.preference_text import interpret_accessory

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
POLICY = Policy(1, "tops", NOW + timedelta(days=30), 0, True, ("tops", "shoes", "bag"), False)


def item(category, sku, family, form, size, price):
    return Variant("demo", sku, category, "", "USD", price, 3, None, family, None, None, "", 1, sku, None, size, form)


def shoes():
    # Red heeled ankle boots, 100 mm heel, bought for USD 78.
    line = OrderLine("demo", "BOOT", 1, 7800, 7800, "USD", True, 1, "BOOT", "", "shoes", False, None, 100, "bright",
                     "ankle boot")
    return line, [item("shoes", "BOOT", "bright", "ankle boot", 100, 7800),
                  item("shoes", "PEEP", "neutral", "peep-toe", 50, 7800),
                  item("shoes", "PUMP", "neutral", "pump", 110, 7900),
                  item("shoes", "BROGUE", "bright", "brogue", 30, 8200),
                  item("bag", "TOTE", "neutral", "tote", 50, 7800)]


def bags():
    line = OrderLine("demo", "HOBO", 1, 6400, 6400, "USD", True, 1, "HOBO", "", "bag", False, None, 420, "bright",
                     "shoulder bag")
    return line, [item("bag", "HOBO", "bright", "shoulder bag", 420, 6400),
                  item("bag", "CROSS", "bright", "crossbody", 200, 6400),
                  item("bag", "TOTE", "bright", "tote", 260, 6600),
                  item("bag", "MESSENGER", "neutral", "messenger", 300, 6800)]


class AccessoryTests(unittest.TestCase):
    def test_a_lower_heel_in_a_neutral_colour_finds_the_peep_toes_only(self):
        line, variants = shoes()
        found = jewellery_shortlist(line, POLICY, JewelleryPreferences(500, "smaller", "neutral"), variants, NOW)
        self.assertEqual([c.variant.sku for c in found], ["PEEP"])
        self.assertEqual(found[0].reasons, ("lower heel: heel 50 mm instead of 100 mm", "neutral colour",
                                            "a peep-toe instead of an ankle boot", "USD 0.00 extra"))
        left = {x["code"]: x["text"] for x in jewellery_exclusions(line, POLICY, JewelleryPreferences(500, "smaller", "neutral"),
                                                                   variants, NOW)}
        self.assertEqual(left, {"not_size": "1 not clearly lower heel", "not_metal": "1 not in a neutral colour"})

    def test_a_lower_heel_alone_keeps_the_colour_first_and_never_shows_another_category(self):
        line, variants = shoes()
        found = jewellery_shortlist(line, POLICY, JewelleryPreferences(500, "smaller"), variants, NOW)
        self.assertEqual([c.variant.sku for c in found], ["BROGUE", "PEEP"])

    def test_a_smaller_neutral_bag_is_the_messenger_and_smaller_alone_keeps_bright(self):
        line, variants = bags()
        found = jewellery_shortlist(line, POLICY, JewelleryPreferences(500, "smaller", "neutral"), variants, NOW)
        self.assertEqual([(c.variant.sku, c.reasons[0]) for c in found],
                         [("MESSENGER", "smaller: 300 mm wide instead of 420 mm")])
        found = jewellery_shortlist(line, POLICY, JewelleryPreferences(500, "smaller"), variants, NOW)
        self.assertEqual([c.variant.sku for c in found], ["CROSS", "TOTE"])

    def test_an_unknown_colour_family_is_refused(self):
        with self.assertRaises(DomainError):
            JewelleryPreferences(500, None, "purple").validate()

    def test_the_shoppers_words_fill_in_heel_and_colour_and_nothing_about_their_feet(self):
        read = interpret_accessory("The heels are too high and the red is too loud. Nothing extra.", "shoes")
        self.assertEqual(read.proposal, {"size": "smaller", "metal": "neutral", "max_top_up_minor": 0})
        self.assertEqual(read.understood, ["a lower heel", "a neutral colour", "nothing extra"])
        bag = interpret_accessory("Too big and bulky, I'd like something brighter", "bag")
        self.assertEqual(bag.proposal, {"size": "smaller", "metal": "bright"})
        self.assertEqual(bag.understood, ["a smaller bag", "a bright colour"])
        self.assertEqual(interpret_accessory("I want flats", "shoes").proposal, {"size": "smaller"})
        both = interpret_accessory("Something neutral or maybe brighter", "bag")
        self.assertEqual([q["field"] for q in both.questions], ["metal"])


if __name__ == "__main__":
    unittest.main()
