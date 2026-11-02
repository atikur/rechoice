import io
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase

from exchange.models import EvidenceEvent, ExchangeRequest, OrderLine, ProductVariant


class StaffLoopTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        staff = get_user_model().objects.create_user("staff", password="local-test-only", is_staff=True)
        self.staff = Client()
        self.staff.force_login(staff)

    def send(self, method, path, payload=None, client=None, **headers):
        return getattr(client or self.client, method)(path, data=json.dumps(payload or {}),
                                                      content_type="application/json", **headers)

    def draft(self):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": "fit"})
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "ORIGINAL-S"}).json()
        return self.send("post", "/api/requests", {"quote_id": quote["quote_id"], "quote_version": 1,
                                                   "confirmed": True}, HTTP_IDEMPOTENCY_KEY="loop").json()["request_id"]

    def decide(self, request_id, state, message=""):
        return self.send("patch", f"/api/staff/requests/{request_id}", {"state": state, "message": message}, self.staff)

    def test_a_question_reaches_the_shopper_and_the_answer_returns_the_draft_to_staff(self):
        request_id = self.draft()
        stock = ProductVariant.objects.get(sku="ORIGINAL-S").stock_qty
        self.assertEqual(self.client.get(f"/api/requests/{request_id}").json()["state"], "DRAFT")
        self.assertEqual(self.decide(request_id, "NEEDS_CLARIFICATION").status_code, 422)
        self.assertEqual(self.decide(request_id, "NEEDS_CLARIFICATION", "Is the label size S or M?").status_code, 200)
        status = self.client.get(f"/api/requests/{request_id}").json()
        self.assertEqual((status["state"], status["message"], status["can_reply"]),
                         ("NEEDS_CLARIFICATION", "Is the label size S or M?", True))
        self.assertEqual(self.decide(request_id, "REVIEWED").json()["error"]["message"], "Waiting for the shopper's reply.")
        answered = self.send("post", f"/api/requests/{request_id}/reply", {"reply": "The label says M."}).json()
        self.assertEqual((answered["state"], answered["reply"], answered["can_reply"]), ("DRAFT", "The label says M.", False))
        self.assertEqual(self.send("post", f"/api/requests/{request_id}/reply", {"reply": "again"}).status_code, 409)
        html = self.staff.get("/staff/").content.decode()
        self.assertIn("Shopper's reply", html)
        self.assertIn("The label says M.", html)
        self.assertEqual(self.decide(request_id, "REVIEWED", "Thanks, size S is fine.").status_code, 200)
        final = self.client.get(f"/api/requests/{request_id}").json()
        self.assertEqual((final["state"], final["message"]), ("REVIEWED", "Thanks, size S is fine."))
        self.assertIn("changes no order", final["state_text"])
        self.assertEqual(self.decide(request_id, "DECLINED").status_code, 409)
        self.assertEqual(ProductVariant.objects.get(sku="ORIGINAL-S").stock_qty, stock)
        self.assertEqual(OrderLine.objects.get(token="DEMO-ORDER-001").paid_minor, 4000)
        self.assertEqual(EvidenceEvent.objects.filter(action="shopper_replied").count(), 1)

    def test_only_the_shoppers_browser_sees_or_answers_the_request(self):
        request_id = self.draft()
        self.decide(request_id, "NEEDS_CLARIFICATION", "Which colour?")
        other = Client()
        self.assertEqual(other.get(f"/api/requests/{request_id}").status_code, 404)
        self.assertEqual(self.send("post", f"/api/requests/{request_id}/reply", {"reply": "red"}, other).status_code, 404)
        self.assertEqual(ExchangeRequest.objects.get().shopper_reply, "")
        self.assertEqual(self.send("patch", f"/api/staff/requests/{request_id}", {"state": "REVIEWED"}).status_code, 302)

    def test_the_staff_page_offers_a_decision_form_for_open_drafts(self):
        request_id = self.draft()
        html = self.staff.get("/staff/").content.decode()
        self.assertIn(f'data-url="/api/staff/requests/{request_id}"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.decide(request_id, "DECLINED")
        self.assertNotIn('class="decision"', self.staff.get("/staff/").content.decode())
