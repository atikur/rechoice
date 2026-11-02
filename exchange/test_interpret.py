import io
import json

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase

from exchange.models import ExchangeSession


class InterpretTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        self.sid = self.post("/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]

    def post(self, path, payload, client=None):
        return (client or self.client).post(path, data=json.dumps(payload), content_type="application/json")

    def test_words_become_a_proposal_that_is_never_saved(self):
        data = self.post(f"/api/sessions/{self.sid}/interpret",
                         {"text": "The print is too busy. Keep long sleeves. At most $10 extra."}).json()
        self.assertEqual(data["proposal"], {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})
        self.assertIn("do not decide anything", data["notice"])
        session = ExchangeSession.objects.get(pk=self.sid)
        self.assertEqual((session.preferences, session.preference_version), ({}, 0))

    def test_conflicting_words_ask_a_question_and_other_browsers_are_refused(self):
        data = self.post(f"/api/sessions/{self.sid}/interpret", {"text": "long sleeves or short sleeves"}).json()
        self.assertEqual((data["proposal"], data["questions"][0]["field"]), ({}, "sleeve_required"))
        self.assertEqual(self.post(f"/api/sessions/{self.sid}/interpret", {"text": "x" * 501}).status_code, 422)
        self.assertEqual(self.post(f"/api/sessions/{self.sid}/interpret", {"text": "plain"}, Client()).status_code, 404)
