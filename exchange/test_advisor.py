import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from exchange import advisor, assistant
from exchange.models import EvidenceEvent, ExchangeRequest, OrderLine
from exchange.test_assistant import ON, FakeClaude, reply


def good(skus):
    return {"summary": "The Ribbed Turtleneck is the closer match: long sleeves and a quieter catalog pattern, for "
                       "USD 2.00 extra.",
            "notes": [{"sku": sku, "note": "Long sleeves and a quieter pattern than the blouse."} for sku in skus]}


class AdvisorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        patch = override_settings(PRIVATE_MEDIA_ROOT=Path(media.name))
        patch.enable()
        self.addCleanup(patch.disable)

    def send(self, method, path, payload=None, client=None, **headers):
        return getattr(client or self.client, method)(path, data=json.dumps(payload or {}),
                                                      content_type="application/json", **headers)

    def tops_session(self):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": "style"})
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                              "max_top_up_minor": 1000, "note": "my private words"})
        return sid

    def skus(self, sid):
        return [c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]]

    def test_the_rules_compare_the_options_from_the_facts_until_claude_is_switched_on(self):
        sid = self.tops_session()
        data = self.send("post", f"/api/sessions/{sid}/compare").json()
        self.assertEqual(data["reader"], "rules")
        self.assertIn("2 options fit your limits; you asked for long sleeves, a quieter print, at most USD 10.00 extra.",
                      data["summary"])
        self.assertEqual(set(data["notes"]), set(self.skus(sid)))
        self.assertEqual(data["label"], "Put together from the catalog facts and what you asked for.")
        self.assertFalse(EvidenceEvent.objects.filter(action=advisor.COMPARE).exists())

    @override_settings(**ON)
    def test_claude_compares_from_facts_only_and_never_sees_the_note_or_a_photo(self):
        sid = self.tops_session()
        skus = self.skus(sid)
        fake = FakeClaude(reply(good(skus)))
        with mock.patch.object(assistant, "make_client", return_value=fake):
            data = self.send("post", f"/api/sessions/{sid}/compare").json()
        self.assertEqual((data["reader"], data["summary"]), ("claude", good(skus)["summary"]))
        self.assertIn("Written by Claude Haiku 5.5", data["label"])
        request = json.dumps(fake.requests[0])
        self.assertNotIn("my private words", request)
        self.assertNotIn("/api/assets/", request)
        self.assertIn("Never describe the shopper", fake.requests[0]["system"])
        schema = fake.requests[0]["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["notes"]["items"]["properties"]["sku"]["enum"], skus)
        event = EvidenceEvent.objects.get(action=advisor.COMPARE)
        self.assertEqual((event.facts["outcome"], event.facts["input_tokens"]), ("read", 1400))

    @override_settings(**ON)
    def test_a_reply_about_the_person_an_invented_number_or_a_missing_option_falls_back_to_the_rules(self):
        sid = self.tops_session()
        skus = self.skus(sid)
        person = {**good(skus), "summary": "The turtleneck will flatter your skin."}
        invented = {**good(skus), "summary": "The turtleneck is USD 9.99 extra."}
        missing = {**good(skus), "notes": good(skus)["notes"][:1]}
        for answer, why in ((person, "mentions the person"), (invented, "number not in the facts"),
                            (missing, "incomplete reply")):
            with self.subTest(why), mock.patch.object(assistant, "make_client", return_value=FakeClaude(reply(answer))):
                data = self.send("post", f"/api/sessions/{sid}/compare").json()
                self.assertEqual(data["reader"], "rules")
                self.assertEqual(EvidenceEvent.objects.filter(action=advisor.COMPARE).latest("at").facts["outcome"], why)
        # A person word that is the store's own wording, like a foundation called Skin Veil, is allowed.
        facts = {"options": [{"title": "Skin Veil Foundation, 230W Medium Warm"}]}
        self.assertEqual(advisor.check_text("Both are in the Skin Veil line.", facts), "")
        self.assertEqual(advisor.check_text("It suits your skin.", facts), "mentions the person")
        self.assertEqual(advisor.check_text("It suits your complexion.", facts), "mentions the person")
        # "$2" is the same number as "USD 2.00" in the facts.
        cheap = {**good(skus), "summary": "The turtleneck costs just $2 more and has long sleeves."}
        with mock.patch.object(assistant, "make_client", return_value=FakeClaude(reply(cheap))):
            self.assertEqual(self.send("post", f"/api/sessions/{sid}/compare").json()["reader"], "claude")

    @override_settings(**{**ON, "ASSISTANT_SESSION_READS": 2})
    def test_comparisons_and_reads_share_the_owner_approved_limits(self):
        sid = self.tops_session()
        skus = self.skus(sid)
        fake = FakeClaude(reply(good(skus)))
        with mock.patch.object(assistant, "make_client", return_value=fake):
            readers = [self.send("post", f"/api/sessions/{sid}/compare").json()["reader"] for _ in range(3)]
        self.assertEqual(readers, ["claude", "claude", "rules"])
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(EvidenceEvent.objects.filter(action=advisor.COMPARE).latest("at").facts["outcome"], "session limit")
        with mock.patch.object(assistant, "make_client", return_value=fake):
            self.assertEqual(self.send("post", f"/api/sessions/{sid}/interpret", {"text": "Too busy"}).json()["reader"],
                             "rules")

    def test_the_comparison_carries_each_previews_size_check(self):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/item", {"line_id": OrderLine.objects.get(token="DEMO-ORDER-001-EAR").id})
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": "look"})
        self.send("put", f"/api/sessions/{sid}/preferences", {"size": "smaller", "metal": "silver", "max_top_up_minor": 500})
        person = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "face-a"}).json()["asset_id"]
        self.send("post", f"/api/sessions/{sid}/previews", {"candidate_skus": self.skus(sid), "consent": True,
                  "asset_id": person, "include_original": True, "mode": "recorded"})
        with override_settings(**ON), mock.patch.object(assistant, "make_client",
                                                        return_value=FakeClaude(reply({"summary": "", "notes": []}))) as made:
            self.send("post", f"/api/sessions/{sid}/compare")
        facts = json.loads(made.return_value.requests[0]["messages"][0]["content"].split("<facts>\n")[1].split("\n</facts>")[0])
        crystal = next(o for o in facts["options"] if o["sku"] == "EAR-CRYSTAL-S38")
        self.assertIn("Rechoice resized it to its catalog length", crystal["checks"][0])
        self.assertEqual(facts["asked_for"], ["smaller", "silver", "at most USD 5.00 extra"])

    def draft(self):
        sid = self.tops_session()
        quote = self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).json()
        self.send("post", "/api/requests", {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True},
                  HTTP_IDEMPOTENCY_KEY="advisor")
        return ExchangeRequest.objects.get()

    def staff(self):
        reviewer = Client()
        reviewer.force_login(get_user_model().objects.create_user("staff", password="local-test-only", is_staff=True))
        return reviewer

    def test_staff_get_a_draft_message_to_edit_and_shoppers_cannot_ask_for_one(self):
        draft = self.draft()
        url = f"/api/staff/requests/{draft.id}/suggest"
        self.assertNotEqual(self.send("post", url, {"state": "REVIEWED"}).status_code, 200)
        staff = self.staff()
        reviewed = self.send("post", url, {"state": "REVIEWED"}, client=staff).json()
        self.assertEqual(reviewed["reader"], "rules")
        self.assertIn("Midnight Floral Blouse goes back and Ribbed Turtleneck comes to you", reviewed["message"])
        self.assertIn("USD 2.00 extra", reviewed["message"])
        self.assertEqual(self.send("post", url, {"state": "MAYBE"}, client=staff).status_code, 422)
        draft.refresh_from_db()
        self.assertEqual(draft.state, "DRAFT")
        with override_settings(**ON), mock.patch.object(assistant, "make_client", return_value=FakeClaude(
                reply({"message": "Would you like the turtleneck in the same size M?"}))) as made:
            asked = self.send("post", url, {"state": "NEEDS_CLARIFICATION"}, client=staff).json()
        self.assertEqual((asked["reader"], asked["message"]), ("claude", "Would you like the turtleneck in the same size M?"))
        self.assertNotIn("my private words", json.dumps(made.return_value.requests[0]))
        self.assertEqual(EvidenceEvent.objects.get(action=advisor.STAFF_NOTE).facts["outcome"], "read")


class AdvisorEvaluationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        patch = override_settings(PRIVATE_MEDIA_ROOT=self.media)
        patch.enable()
        self.addCleanup(patch.disable)

    def run_command(self, *args):
        out = io.StringIO()
        call_command("evaluate_advisor", *args, stdout=out)
        return out.getvalue()

    def test_the_dry_run_builds_every_scenario_and_spends_nothing(self):
        out = self.run_command()
        self.assertIn("Scenarios: 16 comparisons and 12 staff messages (1 skipped with no options).", out)
        self.assertFalse(EvidenceEvent.objects.filter(action="advisor_eval").exists())
        with self.assertRaisesMessage(Exception, "Add --max-usd"):
            self.run_command("--execute")

    @override_settings(**ON)
    def test_execute_checks_each_reply_judges_blind_and_stops_at_the_cap(self):
        def answer(**request):
            facts = json.loads(request["messages"][0]["content"].split("<facts>\n")[1].split("\n</facts>")[0])
            if "text_A" in facts:
                return reply({"a_accurate": True, "b_accurate": True, "more_helpful": "A", "reason": "Clearer."})
            if "options" in facts:
                return reply({"summary": "Both meet your request.",
                              "notes": [{"sku": o["sku"], "note": "Meets your request."} for o in facts["options"]]})
            return reply({"message": "Your exchange is noted. We'll be in touch."})
        fake = FakeClaude(reply({}))
        fake.create = answer
        with mock.patch.object(assistant, "make_client", return_value=fake):
            out = self.run_command("--execute", "--judge", "--max-usd", "1")
        self.assertIn("Claude: 28 of 28 usable", out)
        # A goes to Claude on odd scenarios and to the rules on even ones, so "A" every time splits the wins.
        self.assertRegex(out, r"Judge \(claude-sonnet-5-5\), 28 blind comparisons: Claude more helpful 1\d, rules 1\d")
        report = json.loads((self.media / "advisor-eval" / "report.json").read_text())
        self.assertEqual(len([s for s in report["scenarios"] if "judge" in s]), 28)
        with mock.patch.object(assistant, "make_client", return_value=fake):
            stopped = self.run_command("--execute", "--max-usd", "0.03")
        self.assertIn("Stopped before scenario", stopped)
