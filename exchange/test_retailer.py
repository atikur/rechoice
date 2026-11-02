import io
import json

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from exchange.models import EvidenceEvent, OrderLine

KEY = "test-only-retailer-key-0123456789abcdef"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {KEY}"}
ORDER = {"order_number": "DS-20481", "lines": [{"sku": "ORIGINAL", "paid_minor": 4000},
                                               {"sku": "LIP-VELVET-230", "paid_minor": 1800, "opened": True}]}


@override_settings(RETAILER_API_KEY=KEY)
class RetailerApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()

    def post(self, path, payload, **headers):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def register(self, order=ORDER):
        return self.post("/api/retailer/orders", order, **AUTH)

    def test_a_store_registers_an_order_and_its_return_link_opens_it(self):
        created = self.register()
        self.assertEqual(created.status_code, 201, created.content)
        data = created.json()
        self.assertEqual([line["sku"] for line in data["lines"]], ["ORIGINAL", "LIP-VELVET-230"])
        token = data["lines"][0]["line_token"]
        self.assertEqual(data["return_url"], f"http://testserver/?order={token}")
        self.assertEqual(len(token), 24)
        lip = OrderLine.objects.get(token=data["lines"][1]["line_token"])
        self.assertEqual((lip.fictional_order_id, lip.paid_minor, lip.eligible_credit_minor, lip.opened),
                         ("DS-20481", 1800, 1800, True))
        # The shopper's link starts a session on that order, with both items to pick from.
        session = self.post("/api/sessions", {"order_token": token}).json()
        self.assertEqual([item["sku"] for item in session["items"]], ["ORIGINAL", "LIP-VELVET-230"])
        self.assertEqual(session["order"]["fictional_order_id"], "DS-20481")
        page = self.client.get(f"/store?order={token}").content.decode()
        self.assertIn("Order DS-20481", page)
        self.assertIn(f'href="/?order={token}"', page)
        self.assertEqual(EvidenceEvent.objects.get(action="order_registered").facts,
                         {"order_number": "DS-20481", "lines": 2})

    def test_the_store_reads_back_the_exchange_request_without_the_shoppers_words(self):
        token = self.register().json()["lines"][0]["line_token"]
        sid = self.post("/api/sessions", {"order_token": token}).json()["session_id"]
        self.client.put(f"/api/sessions/{sid}/reason", data=json.dumps({"reason": "style"}), content_type="application/json")
        self.client.put(f"/api/sessions/{sid}/preferences", data=json.dumps(
            {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000, "note": "my private words"}),
            content_type="application/json")
        quote = self.post(f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).json()
        draft = self.post("/api/requests", {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                          HTTP_IDEMPOTENCY_KEY="retailer").json()
        listed = self.client.get("/api/retailer/requests?since=2026-01-01T00:00:00Z", **AUTH).json()["requests"]
        self.assertEqual(len(listed), 1)
        row = listed[0]
        self.assertEqual((row["request_id"], row["state"], row["order_number"]), (draft["request_id"], "DRAFT", "DS-20481"))
        self.assertEqual((row["returned"]["sku"], row["replacement"]["sku"], row["kind"]), ("ORIGINAL", "CALM-07", "a different item"))
        self.assertEqual((row["credit_minor"], row["top_up_minor"]), (4000, quote["top_up_minor"]))
        self.assertNotIn("my private words", json.dumps(row))
        one = self.client.get(f"/api/retailer/requests/{draft['request_id']}", **AUTH).json()
        self.assertEqual(one, row)
        self.assertEqual(self.client.get("/api/retailer/requests?since=2099-01-01T00:00:00Z", **AUTH).json()["requests"], [])
        self.assertEqual(self.client.get("/api/retailer/requests?since=yesterday", **AUTH).status_code, 422)

    def test_the_key_is_required_and_bad_orders_are_refused(self):
        self.assertEqual(self.post("/api/retailer/orders", ORDER).status_code, 401)
        self.assertEqual(self.post("/api/retailer/orders", ORDER, HTTP_AUTHORIZATION="Bearer wrong").status_code, 401)
        self.assertEqual(self.client.get("/api/retailer/requests").status_code, 401)
        unknown = self.register({"order_number": "DS-1", "lines": [{"sku": "NOT-A-SKU", "paid_minor": 100}]})
        self.assertEqual(unknown.status_code, 422)
        bad_number = self.register({"order_number": "ds 1 <script>", "lines": [{"sku": "ORIGINAL", "paid_minor": 100}]})
        self.assertEqual(bad_number.json()["error"]["code"], "INVALID_INPUT")
        empty = self.register({"order_number": "DS-2", "lines": []})
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().json()["error"]["code"], "ORDER_EXISTS")
        self.assertEqual(OrderLine.objects.filter(fictional_order_id="DS-20481").count(), 2)

    @override_settings(RETAILER_API_KEY="")
    def test_the_api_is_off_without_a_key(self):
        self.assertEqual(self.register().json()["error"]["code"], "RETAILER_API_OFF")

    def test_the_store_page_shows_the_demo_order_and_says_it_is_fictional(self):
        page = self.client.get("/store").content.decode()
        self.assertIn("Order SL-DEMO-1001", page)
        self.assertIn("A FICTIONAL STORE", page)
        self.assertIn('href="/?order=DEMO-ORDER-001"', page)
        self.assertEqual(self.client.get("/store?order=nope").status_code, 404)
