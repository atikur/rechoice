import io
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase

from exchange.models import EvidenceEvent, ExchangeRequest


class InsightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()

    def send(self, method, path, payload=None, **headers):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}),
                                            content_type="application/json", **headers)

    def session(self, reason="style", **prefs):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": reason})
        if reason == "style":
            self.send("put", f"/api/sessions/{sid}/preferences",
                      {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000, **prefs})
        return sid

    def test_no_match_says_which_rules_left_tops_out_and_relaxes_nothing(self):
        sid = self.session(max_top_up_minor=50, note="my private words")
        data = self.client.get(f"/api/sessions/{sid}/candidates").json()
        self.assertEqual(data["status"], "NO_MATCH")
        self.assertIn("Nothing was relaxed", data["message"])
        codes = {x["code"]: x for x in data["exclusions"]}
        self.assertEqual(set(codes), {"out_of_stock", "staff_review", "sleeves", "pattern", "over_budget"})
        self.assertIn("closest would be USD 2.00 extra", codes["over_budget"]["text"])
        event = EvidenceEvent.objects.get(action="candidates_checked")
        self.assertEqual(event.facts["matches"], 0)
        self.assertNotIn("note", event.facts["preferences"])
        self.assertNotIn("my private words", json.dumps(event.facts))

    def test_staff_insights_count_outcomes_unmet_demand_and_previews(self):
        for _ in range(2):
            self.client.get(f"/api/sessions/{self.session(max_top_up_minor=0)}/candidates")
        sid = self.session()
        self.client.get(f"/api/sessions/{sid}/candidates")
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
        self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True,
                                                            "asset_id": asset, "include_original": True})
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).json()
        self.send("post", "/api/requests", {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                  HTTP_IDEMPOTENCY_KEY="insights")
        fit = self.session("fit")
        size = self.send("post", f"/api/sessions/{fit}/quotes", {"selected_sku": "ORIGINAL-S"}).json()
        self.send("post", "/api/requests", {"quote_id": size["quote_id"], "quote_version": 1, "confirmed": True},
                  HTTP_IDEMPOTENCY_KEY="insights-size")
        self.send("post", f"/api/sessions/{self.session('changed_mind')}/return")
        self.assertEqual(self.client.get("/staff/insights").status_code, 302)
        staff = get_user_model().objects.create_user("staff", password="local-test-only", is_staff=True)
        reviewer = Client()
        reviewer.force_login(staff)
        html = reviewer.get("/staff/insights").content.decode()
        for text in ("<td>Sessions started</td><td class=\"num\">5</td>",
                     "<td>Exchange drafts: a different item</td><td class=\"num\">1</td>",
                     "<td>Exchange drafts: another size</td><td class=\"num\">1</td>",
                     "<td>Chose the normal return</td><td class=\"num\">1</td>",
                     "<td>long sleeves · quieter print · nothing extra</td><td class=\"num\">2</td>",
                     "over budget (2)", "<td>Doesn&#x27;t fit</td><td class=\"num\">1</td>",
                     "<td>Baseline previews of the current top</td><td class=\"num\">1</td>",
                     "do not show that returns fell",
                     # The style session ended in an exchange, the changed-mind one in a return, which is not a looks reason.
                     "<td>Returns that didn&#x27;t look right</td><td class=\"num\">3</td>",
                     "<td>Of those decided, kept as an exchange</td><td class=\"num\">1 of 1 (100%)</td>",
                     "<td>Tops</td><td class=\"num\">5</td><td class=\"num\">2</td><td class=\"num\">1</td>",
                     "19.3% of online sales are expected to be returned in 2025", "not a forecast"):
            self.assertIn(text, html)
        credit = sum(r.quote.credit_minor for r in ExchangeRequest.objects.select_related("quote"))
        self.assertIn(f"<td>Credit kept in the store instead of refunded</td><td class=\"num\">USD {credit / 100:,.2f}</td>", html)
