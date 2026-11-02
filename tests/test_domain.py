import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from secondlook.domain import (
    DomainError, OrderLine, PatternLevel, Policy, Preferences, Variant,
    make_quote, revalidate_quote, shortlist, size_options,
)
from secondlook.mock_provider import MockPreviewProvider
from secondlook.providers import SUCCEEDED, PreviewInput, ProviderRejected


NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def fixture():
    line = OrderLine("demo", "ORIGINAL", 1, 4000, 4000, "USD", True, 1)
    policy = Policy(1, "tops", NOW + timedelta(days=1), 0, True)
    pref = Preferences(1000, quieter_than=PatternLevel.BUSY, sleeve_required="long")
    def v(sku, price=4500):
        return Variant("demo", sku, "tops", "M", "USD", price, 3,
                       PatternLevel.QUIET, "blue", "long", "crew", f"asset-{sku}", 1)
    return line, policy, pref, v


class DomainTests(unittest.TestCase):
    def test_two_deterministic_choices(self):
        line, policy, pref, v = fixture()
        found = shortlist(line, policy, pref, [v("C"), v("B"), v("A")], NOW)
        self.assertEqual([x.variant.sku for x in found], ["A", "B"])
        self.assertIn("long sleeves", found[0].reasons)

    def test_stock_and_missing_attributes_excluded(self):
        line, policy, pref, v = fixture()
        items = [replace(v("ZERO"), stock_qty=0),
                 replace(v("UNKNOWN"), sleeve=None),
                 replace(v("BUSY"), pattern_level=PatternLevel.BUSY)]
        self.assertEqual(shortlist(line, policy, pref, items, NOW), [])

    def test_budget_boundary_and_paid_credit(self):
        line, policy, pref, v = fixture()
        found = shortlist(line, policy, pref, [v("YES", 5000), v("NO", 5001)], NOW)
        self.assertEqual([x.variant.sku for x in found], ["YES"])
        self.assertEqual(found[0].top_up_minor, 1000)
        self.assertEqual(make_quote(line, v("FIVE"), policy, pref, NOW + timedelta(hours=1), NOW).top_up_minor, 500)

    def test_invalid_budget_and_currency(self):
        line, policy, pref, v = fixture()
        for bad in (-1, 10.5, True):
            with self.subTest(bad=bad), self.assertRaises(DomainError):
                shortlist(line, policy, replace(pref, max_top_up_minor=bad), [v("A")], NOW)
        self.assertEqual(shortlist(line, policy, pref, [replace(v("A"), currency="EUR")], NOW), [])

    def test_unknown_fee_and_cheaper_replacement(self):
        line, policy, pref, v = fixture()
        with self.assertRaises(DomainError) as ctx:
            make_quote(line, v("A"), replace(policy, fee_minor=None), pref, NOW + timedelta(hours=1), NOW)
        self.assertEqual(ctx.exception.code, "FEE_UNKNOWN")
        self.assertEqual(shortlist(line, policy, pref, [v("CHEAPER", 3500)], NOW), [])

    def test_quote_revalidation(self):
        line, policy, pref, v = fixture()
        variant = v("A")
        quote = make_quote(line, variant, policy, pref, NOW + timedelta(hours=1), NOW)
        revalidate_quote(quote, line, variant, policy, pref, NOW)
        for changed in (replace(variant, version=2), replace(variant, stock_qty=0)):
            with self.subTest(changed=changed), self.assertRaises(DomainError):
                revalidate_quote(quote, line, changed, policy, pref, NOW)

    def test_mock_provider_requires_consent(self):
        provider = MockPreviewProvider()
        job = PreviewInput("j1", "A", 1, "", None, consent=False, consent_version=0)
        with self.assertRaises(ProviderRejected) as ctx:
            provider.start(job)
        self.assertEqual(ctx.exception.code, "PHOTO_CONSENT_REQUIRED")
        started = provider.start(replace(job, consent=True, consent_version=1))
        self.assertEqual((started.provider_task_id, started.reserved_units), ("mock-j1", 0))
        result = provider.poll(started.provider_task_id)
        self.assertEqual((result.state, result.observed_units, result.image), (SUCCEEDED, 0, None))


if __name__ == "__main__":
    unittest.main()


class SizeExchangeTests(unittest.TestCase):
    def setUp(self):
        line, self.policy, self.pref, v = fixture()
        self.line = replace(line, style="ORIGINAL", size_label="M")
        self.size = lambda sku, size, stock=3: replace(v(sku, 4000), size_label=size, stock_qty=stock, style="ORIGINAL",
                                                       pattern_level=PatternLevel.BUSY)
        self.v = v

    def test_other_sizes_are_listed_with_unavailable_ones_explained(self):
        options = size_options(self.line, self.policy, [self.size("ORIGINAL-S", "S"), self.size("ORIGINAL-L", "L", 0),
                                                        self.size("ORIGINAL", "M"), self.v("CALM-01")], NOW)
        self.assertEqual([(o.variant.sku, o.available, o.top_up_minor) for o in options],
                         [("ORIGINAL-S", True, 0), ("ORIGINAL-L", False, None)])
        self.assertEqual((options[0].note, options[1].note), ("In stock", "Out of stock"))

    def test_sizes_of_the_same_top_are_never_style_alternatives(self):
        relaxed = Preferences(1000)
        found = shortlist(self.line, self.policy, relaxed, [self.size("ORIGINAL-S", "S"), self.v("CALM-01")], NOW)
        self.assertEqual([x.variant.sku for x in found], ["CALM-01"])

    def test_size_quotes_need_the_same_top_and_ignore_style_preferences(self):
        quote = make_quote(self.line, self.size("ORIGINAL-S", "S"), self.policy, None, NOW + timedelta(minutes=5), NOW, "size")
        self.assertEqual((quote.kind, quote.top_up_minor), ("size", 0))
        revalidate_quote(quote, self.line, self.size("ORIGINAL-S", "S"), self.policy, None, NOW)
        with self.assertRaises(DomainError) as other:
            make_quote(self.line, self.v("CALM-01"), self.policy, None, NOW + timedelta(minutes=5), NOW, "size")
        self.assertEqual(other.exception.code, "INELIGIBLE")
        with self.assertRaises(DomainError) as sold_out:
            revalidate_quote(quote, self.line, self.size("ORIGINAL-S", "S", 0), self.policy, None, NOW)
        self.assertEqual(sold_out.exception.code, "OUT_OF_STOCK")
        with self.assertRaises(DomainError) as no_prefs:
            make_quote(self.line, self.v("CALM-01"), self.policy, None, NOW + timedelta(minutes=5), NOW)
        self.assertEqual(no_prefs.exception.code, "PREFERENCES_REQUIRED")


class ExclusionTests(unittest.TestCase):
    def test_each_left_out_top_is_counted_once_by_its_first_failed_rule(self):
        from secondlook.domain import exclusions
        line, policy, pref, v = fixture()
        tops = [v("A", 6000), v("B", 5200), replace(v("C"), stock_qty=0), replace(v("D"), sleeve="short"),
                replace(v("E"), pattern_level=PatternLevel.BUSY), replace(v("F"), sleeve=None), v("G", 4400)]
        found = exclusions(line, policy, pref, tops, NOW)
        self.assertEqual([(x["code"], x["count"]) for x in found],
                         [("out_of_stock", 1), ("missing_details", 1), ("sleeves", 1), ("pattern", 1), ("over_budget", 2)])
        self.assertIn("closest would be USD 12.00 extra", found[-1]["text"])
        self.assertEqual(len(shortlist(line, policy, pref, tops, NOW)), 1)
