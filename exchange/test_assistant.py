import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from exchange import assistant
from exchange.models import EvidenceEvent, OrderLine

ON = dict(ASSISTANT_PROVIDER="claude", ANTHROPIC_API_KEY="test-only-key", ASSISTANT_APPROVAL="owner-2026-10-28",
          ASSISTANT_DAILY_READS=100, ASSISTANT_SESSION_READS=5, ASSISTANT_MODEL="claude-haiku-5-5")


def reply(data, stop_reason="end_turn", tokens=(1400, 300)):
    """A Messages API response as the SDK returns it: a thinking block first, then the JSON text."""
    text = data if isinstance(data, str) else json.dumps(data)
    usage = SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1])
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(stop_reason=stop_reason, usage=usage, content=content)


class FakeClaude:
    """Stands in for anthropic.Anthropic: records each request and answers from a queue. Nothing is sent."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def apparel(**proposal):
    base = {"quieter_print": "", "sleeve_required": "", "neckline_preference": "", "colours": [],
            "budget": {"stated": False, "usd": 0}}
    return {"proposal": {**base, **proposal}, "understood": [], "questions": [], "notes": []}


class AssistantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()

    def session(self, token="DEMO-ORDER-001"):
        sid = self.client.post("/api/sessions", {"order_token": "DEMO-ORDER-001"},
                               content_type="application/json").json()["session_id"]
        if token != "DEMO-ORDER-001":
            line = OrderLine.objects.get(token=token)
            self.client.put(f"/api/sessions/{sid}/item", {"line_id": line.id}, content_type="application/json")
        return sid

    def interpret(self, sid, text):
        return self.client.post(f"/api/sessions/{sid}/interpret", {"text": text},
                                content_type="application/json").json()

    def test_the_rules_read_the_note_until_the_owner_switches_claude_on(self):
        sid = self.session()
        with mock.patch.object(assistant, "make_client") as client:
            data = self.interpret(sid, "The print is too busy. Keep long sleeves. At most $10 extra.")
        client.assert_not_called()
        self.assertEqual(data["reader"], "rules")
        self.assertEqual(data["proposal"], {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})
        self.assertEqual(data["notice"], "Check the controls before confirming. Your words do not decide anything.")
        self.assertFalse(EvidenceEvent.objects.filter(action="assistant_read").exists())
        self.assertNotIn("read by Claude", self.client.get("/").content.decode())

    @override_settings(**ON)
    def test_claude_reads_a_note_into_the_controls_with_a_structured_low_effort_request(self):
        sid = self.session()
        fake = FakeClaude(reply({**apparel(quieter_print="quieter", sleeve_required="long", colours=["navy", "green"],
                                           budget={"stated": True, "usd": 20}),
                                 "understood": ["a quieter print", "long sleeves", "up to USD 20.00 extra"]}))
        with mock.patch.object(assistant, "make_client", return_value=fake):
            data = self.interpret(sid, "Too loud, long sleeves, navy or green, up to twenty dollars extra.")
        self.assertEqual(data["reader"], "claude")
        self.assertEqual(data["proposal"], {"quieter_print": True, "sleeve_required": "long",
                                            "color_preferences": ["navy", "green"], "max_top_up_minor": 2000})
        self.assertEqual(data["understood"], ["a quieter print", "long sleeves", "up to USD 20.00 extra"])
        self.assertTrue(data["notice"].startswith("Read by Claude Haiku 5.5, Anthropic's AI, from your words alone."))
        request = fake.requests[0]
        self.assertEqual((request["model"], request["max_tokens"]), ("claude-haiku-5-5", 2000))
        self.assertEqual(request["output_config"]["effort"], "low")
        self.assertEqual(request["output_config"]["format"],
                         {"type": "json_schema", "schema": assistant.schema("apparel")})
        self.assertIn("about a top they are returning", request["system"])
        self.assertIn("Too loud, long sleeves", request["messages"][0]["content"])
        self.assertFalse({"temperature", "top_p", "top_k", "thinking"} & set(request))
        facts = EvidenceEvent.objects.get(action="assistant_read").facts
        self.assertEqual(facts, {"model": "claude-haiku-5-5", "outcome": "read", "called": True, "input_tokens": 1400,
                                 "output_tokens": 300, "cost_usd": 0.00029, "approval": "owner-2026-10-28"})
        self.assertNotIn("navy", json.dumps(facts))
        self.assertIn("Your words are read by Claude, Anthropic's AI", self.client.get("/").content.decode())

    def test_only_the_controls_own_answers_survive_a_reply(self):
        data = assistant.accept("apparel", {
            "proposal": {"quieter_print": "louder", "sleeve_required": "any", "neckline_preference": "boat",
                         "colours": ["navy", "teal", "navy"], "budget": {"stated": True, "usd": 5000}},
            "understood": ["a" * 81, " long   sleeves "],
            "questions": [{"field": "neckline_preference", "question": "Crew or collared?",
                           "options": [{"label": "Crew", "value": "crew"}, {"label": "Boat", "value": "boat"}]},
                          {"field": "skin", "question": "What is your skin tone?", "options": []},
                          {"field": "budget", "question": "Your limit in US dollars?",
                           "options": [{"label": "USD 5", "value": "$5"}, {"label": "Lots", "value": "lots"}]}],
            "notes": ["x" * 201]})
        self.assertEqual(data["proposal"], {"sleeve_required": "", "color_preferences": ["navy"]})
        self.assertEqual(data["questions"], [
            {"field": "neckline_preference", "question": "Crew or collared?",
             "options": [{"label": "Crew", "value": "crew"}]},
            {"field": "max_top_up_minor", "question": "Your limit in US dollars?",
             "options": [{"label": "USD 5", "value": 500}]}])
        self.assertEqual((data["understood"], data["notes"]), (["long sleeves"], []))
        # A control being asked about is never also filled in.
        asked = assistant.accept("jewellery", {"proposal": {"size": "smaller", "metal": "gold",
                                                            "budget": {"stated": False, "usd": 0}},
                                               "questions": [{"field": "metal", "question": "Gold or silver?",
                                                              "options": []}], "understood": [], "notes": []})
        self.assertEqual(asked["proposal"], {"size": "smaller"})
        with self.assertRaises(assistant.Unusable):
            assistant.accept("beauty", ["not", "an", "object"])

    @override_settings(**ON)
    def test_the_rules_answer_after_a_refusal_a_cut_off_reply_an_unreadable_one_or_an_error(self):
        note = "Gold looks wrong on me, silver instead."
        for answer, outcome in ((reply({}, stop_reason="refusal"), "refusal"),
                                (reply("{\"proposal\": ", stop_reason="max_tokens"), "max_tokens"),
                                (reply("not json"), "unreadable reply"),
                                (ConnectionError("network down"), "error: ConnectionError")):
            with self.subTest(outcome):
                sid = self.session("DEMO-ORDER-001-EAR")
                with mock.patch.object(assistant, "make_client", return_value=FakeClaude(answer)):
                    data = self.interpret(sid, note)
                self.assertEqual(data["reader"], "rules")
                self.assertTrue(data["notice"].startswith("Read by the store's rules."))
                event = EvidenceEvent.objects.filter(action="assistant_read").latest("at").facts
                self.assertEqual(event["outcome"], outcome)
                self.assertEqual(event["output_tokens"], 0 if outcome.startswith("error") else 300)

    @override_settings(**{**ON, "ASSISTANT_SESSION_READS": 2, "ASSISTANT_DAILY_READS": 3})
    def test_the_session_and_daily_limits_hand_the_note_back_to_the_rules(self):
        fake = FakeClaude(reply(apparel(quieter_print="quieter")))
        with mock.patch.object(assistant, "make_client", return_value=fake):
            first = self.session()
            readers = [self.interpret(first, "Too busy.")["reader"] for _ in range(3)]
            second = self.session()
            readers += [self.interpret(second, "Too busy.")["reader"] for _ in range(2)]
        self.assertEqual(readers, ["claude", "claude", "rules", "claude", "rules"])
        self.assertEqual(len(fake.requests), 3)
        outcomes = [e.facts["outcome"] for e in EvidenceEvent.objects.filter(action="assistant_read").order_by("at")]
        self.assertEqual(outcomes, ["read", "read", "session limit", "read", "daily limit"])

    @override_settings(**ON)
    def test_a_shade_note_is_read_with_the_shade_controls_and_never_about_the_shopper(self):
        sid = self.session("DEMO-ORDER-001-FDN")
        question = {"field": "tone", "question": "Should the new shade be warmer or cooler?",
                    "options": [{"label": "Warmer", "value": "warmer"}, {"label": "Cooler", "value": "cooler"}]}
        fake = FakeClaude(reply({"proposal": {"lightness": "", "tone": "", "intensity": "",
                                              "budget": {"stated": False, "usd": 0}},
                                 "understood": [], "questions": [question], "notes": []}))
        with mock.patch.object(assistant, "make_client", return_value=fake):
            data = self.interpret(sid, "I have warm undertones, what should I get?")
        self.assertEqual((data["proposal"], data["questions"]), ({}, [question]))
        request = fake.requests[0]
        self.assertIn("about a foundation they are returning", request["system"])
        self.assertIn("Never infer, guess or mention the shopper's skin", request["system"])
        fields = set(assistant.schema("beauty")["properties"]["proposal"]["properties"])
        self.assertEqual(fields, {"lightness", "tone", "intensity", "budget"})

    def test_every_schema_fits_structured_outputs(self):
        """Objects close their properties and require them all; no keyword structured outputs refuse is used."""
        refused = {"minimum", "maximum", "minLength", "maxLength", "maxItems", "pattern", "anyOf", "oneOf"}

        def walk(node):
            self.assertFalse(refused & set(node))
            if node.get("type") == "object":
                self.assertIs(node["additionalProperties"], False)
                self.assertEqual(set(node["required"]), set(node["properties"]))
                for child in node["properties"].values():
                    walk(child)
            if node.get("type") == "array":
                walk(node["items"])
        for group in assistant.CONTROLS:
            with self.subTest(group):
                walk(assistant.schema(group))

    @override_settings(**ON)
    def test_staff_see_reads_fallbacks_and_spend_without_the_notes(self):
        sid = self.session()
        fake = FakeClaude(reply(apparel(quieter_print="quieter")), ConnectionError())
        with mock.patch.object(assistant, "make_client", return_value=fake):
            self.interpret(sid, "Too busy, nothing extra.")
            self.interpret(sid, "Too busy, nothing extra.")
        self.client.force_login(get_user_model().objects.create_user("staff", password="unused-test", is_staff=True))
        html = self.client.get("/staff/insights").content.decode()
        for text in ("<h2>Claude</h2>", "Notes read by Claude", "Comparisons written by Claude", "error: ConnectionError 1",
                     "USD 0.0003"):
            self.assertIn(text, html)
        self.assertNotIn("Too busy", html)

    def test_the_instructions_name_the_store_colours_and_put_white_gold_with_silver(self):
        tops = assistant.instructions("apparel", "tops")
        self.assertIn("sand (also beige, khaki, tan)", tops)
        self.assertIn("Beige, khaki and tan are all sand.", tops)
        earrings = assistant.instructions("jewellery", "earrings")
        rules = earrings.split("How to fill them in:")[1].splitlines()
        self.assertEqual(rules[1], '- White gold, platinum and steel are silver metals: answer "silver" for any of '
                                   'them, never "gold".')
        self.assertNotIn("white gold", assistant.instructions("beauty", "lip").lower())

    def test_settings_keep_claude_off_until_the_key_approval_and_limits_are_set(self):
        base = {**os.environ, "DATABASE_URL": "sqlite:///unused.sqlite3"}
        for key in ("ASSISTANT_PROVIDER", "ANTHROPIC_API_KEY", "ASSISTANT_APPROVAL", "ASSISTANT_DAILY_READS"):
            base.pop(key, None)

        def load(**env):
            return subprocess.run([sys.executable, "-c", "import config.settings"], cwd=settings.BASE_DIR,
                                  env={**base, **env}, capture_output=True, text=True, timeout=60)
        self.assertIn("Unknown ASSISTANT_PROVIDER", load(ASSISTANT_PROVIDER="gpt").stderr)
        bare = load(ASSISTANT_PROVIDER="claude")
        for name in ("ASSISTANT_APPROVAL", "ANTHROPIC_API_KEY", "ASSISTANT_DAILY_READS"):
            self.assertIn(name, bare.stderr)
        ready = load(ASSISTANT_PROVIDER="claude", ANTHROPIC_API_KEY="test-only-key", ASSISTANT_APPROVAL="owner",
                     ASSISTANT_DAILY_READS="100")
        self.assertEqual(ready.returncode, 0, ready.stderr)


class EvaluationTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        patch = override_settings(PRIVATE_MEDIA_ROOT=self.media)
        patch.enable()
        self.addCleanup(patch.disable)

    def run_command(self, *args):
        out = io.StringIO()
        call_command("evaluate_assistant", *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_scores_the_rules_and_prices_claude_without_calling_it(self):
        with mock.patch.object(assistant, "make_client") as client:
            output = self.run_command()
        client.assert_not_called()
        self.assertIn("Notes: 60. Model: claude-haiku-5-5. With Claude, about USD 0.0240 and at most USD 0.0690.",
                      output)
        self.assertIn("Rules: 45 of 60 right (apparel 13/20, beauty 16/20, jewellery 16/20).", output)
        report = json.loads((self.media / "assistant-eval" / "report.json").read_text())
        self.assertEqual(len(report["notes"]), 60)
        self.assertNotIn("claude", report["notes"][0])

    @override_settings(ANTHROPIC_API_KEY="test-only-key")
    def test_reading_with_claude_needs_a_spend_cap_and_stops_before_it(self):
        with self.assertRaisesMessage(CommandError, "Add --max-usd"):
            self.run_command("--execute")
        fake = FakeClaude(reply(apparel(quieter_print="quieter", sleeve_required="long",
                                        budget={"stated": True, "usd": 10})))
        with mock.patch.object(assistant, "make_client", return_value=fake):
            # Each read here costs USD 0.00029, and the next may cost up to USD 0.00115, so the cap allows ten.
            output = self.run_command("--execute", "--max-usd", "0.004")
        self.assertEqual(len(fake.requests), 10)
        self.assertIn("Stopped before note 11", output)
        self.assertIn("Claude: 1 of 10 right (apparel 1/10); USD 0.0029 this run.", output)
        self.assertAlmostEqual(sum(e.facts["cost_usd"] for e in EvidenceEvent.objects.filter(action="assistant_eval")),
                               0.0029)
        with mock.patch.object(assistant, "make_client", return_value=fake):
            again = self.run_command("--execute", "--max-usd", "0.004")
        # The cap counts every run together, so a second run stops before its first read.
        self.assertEqual(len(fake.requests), 10)
        self.assertIn("Stopped before note 1:", again)

    @override_settings(ANTHROPIC_API_KEY="test-only-key")
    def test_several_runs_are_reported_one_by_one_and_averaged(self):
        fake = FakeClaude(reply(apparel(quieter_print="quieter", sleeve_required="long",
                                        budget={"stated": True, "usd": 10})))
        with mock.patch.object(assistant, "make_client", return_value=fake):
            output = self.run_command("--execute", "--max-usd", "1", "--runs", "2")
        self.assertEqual(len(fake.requests), 120)
        self.assertIn("Claude run 1: 1 of 60 right", output)
        self.assertIn("Claude run 2: 1 of 60 right", output)
        self.assertIn("Claude, average of 2 complete runs: 1.0 of 60 (apparel 1.0, beauty 0.0, jewellery 0.0).", output)
