import io
import json

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from exchange.models import EvidenceEvent, ExchangeRequest, ExchangeSession, ProductVariant


class ReturnReasonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()

    def send(self, method, path, payload=None, **headers):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}),
                                            content_type="application/json", **headers)

    def start(self, reason=None):
        data = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()
        if reason:
            self.assertEqual(self.send("put", f"/api/sessions/{data['session_id']}/reason",
                                       {"reason": reason}).status_code, 200)
        return data

    def draft(self, quote_id, key):
        return self.send("post", "/api/requests", {"quote_id": quote_id, "quote_version": 1, "confirmed": True},
                         HTTP_IDEMPOTENCY_KEY=key)

    def test_the_order_shows_its_size_window_and_the_reasons_to_choose_from(self):
        data = self.start()
        self.assertEqual(data["order"]["size_label"], "M")
        self.assertTrue(data["policy"]["exchange_allowed"])
        self.assertEqual([r["code"] for r in data["reasons"]], ["style", "fit", "damaged", "changed_mind"])

    def test_each_reason_leads_to_its_own_path(self):
        sid = self.start()["session_id"]
        expected = {"style": "preferences", "fit": "sizes", "damaged": "support", "changed_mind": "return"}
        for reason, step in expected.items():
            with self.subTest(reason=reason):
                data = self.send("put", f"/api/sessions/{sid}/reason", {"reason": reason}).json()
                self.assertEqual(data["next"], step)
                self.assertTrue(data["return_option"])
        self.assertEqual(self.send("put", f"/api/sessions/{sid}/reason", {"reason": "refund"}).status_code, 422)
        self.assertEqual(EvidenceEvent.objects.filter(action="return_reason_given").count(), 4)

    def test_a_fit_return_offers_other_sizes_and_never_style_preferences(self):
        sid = self.start("fit")["session_id"]
        refused = self.send("put", f"/api/sessions/{sid}/preferences", {"max_top_up_minor": 1000})
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (409, "REASON_MISMATCH"))
        data = self.client.get(f"/api/sessions/{sid}/sizes").json()
        self.assertEqual(data["current_size"], "M")
        self.assertEqual([(s["size_label"], s["available"], s["top_up_minor"]) for s in data["sizes"]],
                         [("S", True, 0), ("XL", True, 0), ("L", False, None)])
        self.assertEqual([s["note"] for s in data["sizes"]], ["In stock", "In stock", "Out of stock"])
        self.assertTrue(all(s["image_url"] == "/static/exchange/catalog/ORIGINAL.jpg" for s in data["sizes"]))

    def test_a_size_exchange_is_quoted_drafted_and_rechecked_like_any_other(self):
        sid = self.start("fit")["session_id"]
        other_top = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-01"})
        self.assertEqual((other_top.status_code, other_top.json()["error"]["code"]), (422, "INELIGIBLE"))
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "ORIGINAL-S"}).json()
        self.assertEqual((quote["kind"], quote["size_label"], quote["top_up_minor"]), ("size", "S", 0))
        ProductVariant.objects.filter(sku="ORIGINAL-S").update(stock_qty=0)
        self.assertEqual(self.draft(quote["quote_id"], "size-1").status_code, 409)
        ProductVariant.objects.filter(sku="ORIGINAL-S").update(stock_qty=3)
        created = self.draft(quote["quote_id"], "size-2")
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(ExchangeRequest.objects.get().quote.kind, "size")

    def test_sizes_are_only_for_a_fit_return_and_style_exchanges_never_list_them(self):
        sid = self.start("style")["session_id"]
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/sizes").status_code, 409)
        self.send("put", f"/api/sessions/{sid}/preferences",
                  {"quieter_print": False, "sleeve_required": "long", "max_top_up_minor": 5000})
        found = self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]
        self.assertTrue(found)
        self.assertFalse([item for item in found if item["sku"].startswith("ORIGINAL")])

    def test_choosing_the_normal_return_ends_the_flow_without_a_refund(self):
        sid = self.start("changed_mind")["session_id"]
        data = self.send("post", f"/api/sessions/{sid}/return").json()
        self.assertEqual(data["status"], "CANCELLED")
        self.assertIn("has not started a refund", data["message"])
        self.assertEqual(ExchangeSession.objects.get(pk=sid).status, "CANCELLED")
        self.assertEqual(EvidenceEvent.objects.get(action="return_chosen").facts, {"reason": "changed_mind"})
        self.assertEqual(self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-01"}).status_code, 409)
        self.assertEqual(self.send("post", f"/api/sessions/{sid}/return").status_code, 409)
