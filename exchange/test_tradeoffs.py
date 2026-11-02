import io
import json

from django.core.management import call_command
from django.test import Client, TestCase

from exchange.models import EvidenceEvent, ExchangeSession, PreviewJob, ProductVariant, Quote


class TradeoffAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def send(self, method, path, data):
        return getattr(self.client, method)(path, json.dumps(data), content_type="application/json")

    def session(self, budget=0):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": "style"})
        self.prefs = {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": budget,
                      "note": "private shopper words"}
        self.send("put", f"/api/sessions/{sid}/preferences", self.prefs)
        return sid

    def test_read_only_budget_possibility_cannot_authorize_a_quote_or_preview(self):
        sid = self.session()
        before = ExchangeSession.objects.get(id=sid)
        events = EvidenceEvent.objects.count()
        data = self.client.get(f"/api/sessions/{sid}/tradeoffs?constraint=budget").json()
        plan, = data["possibilities"]
        self.assertEqual((plan["constraint"], plan["before"], plan["after"]), ("budget", 0, 200))
        self.assertTrue(plan["minimum_budget"])
        self.assertFalse(plan["confirmed"])
        self.assertEqual([c["sku"] for c in plan["candidates"]], ["CALM-07"])
        self.assertIn("Sleeves: long", plan["kept"])
        self.assertNotIn("private shopper words", json.dumps(data))
        after = ExchangeSession.objects.get(id=sid)
        self.assertEqual((after.preferences, after.preference_version, after.status),
                         (before.preferences, before.preference_version, before.status))
        self.assertEqual(EvidenceEvent.objects.count(), events)
        self.assertEqual((Quote.objects.count(), PreviewJob.objects.count()), (0, 0))
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"})
        self.assertEqual(quote.json()["error"]["code"], "INELIGIBLE")
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/candidates").json()["status"], "NO_MATCH")
        self.send("put", f"/api/sessions/{sid}/preferences", {**self.prefs, "max_top_up_minor": 200})
        self.assertEqual([c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]], ["CALM-07"])
        self.assertEqual(self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).status_code, 201)

    def test_a_possibility_does_not_bypass_stock_changes_before_confirmation(self):
        sid = self.session()
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/tradeoffs?constraint=budget").json()["status"], "POSSIBILITIES")
        ProductVariant.objects.filter(sku="CALM-07").update(stock_qty=0)
        self.send("put", f"/api/sessions/{sid}/preferences", {**self.prefs, "max_top_up_minor": 200})
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/candidates").json()["status"], "NO_MATCH")
        self.assertEqual(self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).status_code, 422)

    def test_other_browsers_closed_sessions_and_unsupported_constraints_are_refused(self):
        sid = self.session()
        url = f"/api/sessions/{sid}/tradeoffs?constraint=budget"
        self.assertEqual(Client().get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/tradeoffs?constraint=stock").json()["error"]["code"], "INVALID_CONSTRAINT")
        ExchangeSession.objects.filter(id=sid).update(status="CANCELLED")
        self.assertEqual(self.client.get(url).json()["error"]["code"], "SESSION_CLOSED")

    def test_existing_matches_and_newly_available_catalog_options_do_not_invite_relaxation(self):
        sid = self.session(budget=1000)
        data = self.client.get(f"/api/sessions/{sid}/tradeoffs?constraint=budget").json()
        self.assertEqual((data["status"], data["possibilities"]), ("MATCHES_EXIST", []))
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": "fit"})
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/tradeoffs?constraint=budget").json()["error"]["code"], "REASON_MISMATCH")
