from dataclasses import replace
import unittest

from secondlook.domain import DomainError, PatternLevel
from secondlook.tradeoffs import explore
from tests.test_domain import fixture, NOW


class TradeoffTests(unittest.TestCase):
    def test_budget_is_the_minimum_feasible_amount_with_all_other_limits_preserved(self):
        line, policy, prefs, variant = fixture()
        prefs = replace(prefs, max_top_up_minor=0, color_preferences=("navy",))
        items = [replace(variant("SOLD_OUT", 4000), stock_qty=0), variant("CHEAPER", 3900),
                 replace(variant("SHORT", 4100), sleeve="short"), variant("RED", 4200),
                 replace(variant("NAVY", 4400), color_tag="navy"), variant("MORE", 4300)]
        plans = explore(line, policy, prefs, items, "budget", NOW)
        self.assertEqual(len(plans), 1)
        self.assertEqual((plans[0].before, plans[0].after), (0, 200))
        self.assertEqual([c.variant.sku for c in plans[0].candidates], ["RED"])
        self.assertEqual(prefs.max_top_up_minor, 0)
        self.assertEqual(explore(line, policy, prefs, list(reversed(items)), "budget", NOW), plans)

    def test_budget_includes_the_policy_fee_and_respects_the_supported_control_ceiling(self):
        line, policy, prefs, variant = fixture()
        prefs = replace(prefs, max_top_up_minor=0)
        self.assertEqual(explore(line, replace(policy, fee_minor=150), prefs, [variant("A", 4200)], "budget", NOW)[0].after, 350)
        self.assertEqual(explore(line, policy, prefs, [variant("TOO_MUCH", 104001)], "budget", NOW), [])

    def test_one_change_cannot_also_relax_another_limit(self):
        line, policy, prefs, variant = fixture()
        prefs = replace(prefs, max_top_up_minor=0)
        items = [replace(variant("SHORT_EXPENSIVE", 4300), sleeve="short")]
        for constraint in ("budget", "sleeves", "print"):
            self.assertEqual(explore(line, policy, prefs, items, constraint, NOW), [])

    def test_sleeve_possibilities_keep_the_budget_and_quieter_print_requirement(self):
        line, policy, prefs, variant = fixture()
        items = [replace(variant("SHORT", 4200), sleeve="short"),
                 replace(variant("BUSY", 4100), sleeve="short", pattern_level=PatternLevel.BUSY),
                 replace(variant("EXPENSIVE", 5100), sleeve="sleeveless")]
        plan, = explore(line, policy, prefs, items, "sleeves", NOW)
        self.assertEqual((plan.before, plan.after), ("long", "short"))
        self.assertEqual([c.variant.sku for c in plan.candidates], ["SHORT"])

    def test_print_change_must_be_selected_and_does_not_relax_sleeves_or_budget(self):
        line, policy, prefs, variant = fixture()
        items = [replace(variant("BUSY"), pattern_level=PatternLevel.BUSY)]
        self.assertEqual(explore(line, policy, prefs, items, "sleeves", NOW), [])
        plan, = explore(line, policy, prefs, items, "print", NOW)
        self.assertEqual((plan.before, plan.after), (True, False))
        self.assertEqual(plan.candidates[0].variant.sku, "BUSY")

    def test_existing_matches_do_not_trigger_a_suggestion_to_relax_anything(self):
        line, policy, prefs, variant = fixture()
        for constraint in ("budget", "sleeves", "print"):
            self.assertEqual(explore(line, policy, prefs, [variant("MATCH")], constraint, NOW), [])

    def test_policy_and_input_errors_are_never_turned_into_tradeoffs(self):
        line, policy, prefs, variant = fixture()
        for changed in (replace(policy, fee_minor=None), replace(policy, exchange_allowed=False)):
            with self.assertRaises(DomainError):
                explore(line, changed, prefs, [variant("A")], "budget", NOW)
        with self.assertRaisesRegex(DomainError, "Choose budget"):
            explore(line, policy, prefs, [variant("A")], "stock", NOW)
