import io
import json
import tempfile
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from . import ai_client, copilot, visual_review
from .models import ExchangeAIBudget, ExchangeAICall, ExchangeSession, PreviewJob, Quote, VisualReviewAttempt

AI_SETTINGS = dict(EXCHANGE_AI_PROVIDER="claude", EXCHANGE_AI_APPROVAL="test-only approval",
                   EXCHANGE_AI_JUDGE_ONLY=False,
                   EXCHANGE_AI_MAX_USD="2", EXCHANGE_AI_DAILY_CALLS=100, EXCHANGE_AI_SESSION_CALLS=20,
                   ANTHROPIC_API_KEY="fake-key-never-used")


def response(*blocks, stop="end_turn", inputs=1000, outputs=100):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=inputs, output_tokens=outputs))


def text_review(sleeves="supports"):
    return response(SimpleNamespace(type="text", text=json.dumps({"checks": [
        {"attribute": "sleeves", "finding": sleeves, "observation": "Sleeves appear raised above the cuffs." if sleeves == "concern" else "Sleeves extend to the cuffs."},
        {"attribute": "print", "finding": "supports", "observation": "The knit is plainer than the bought floral print."}]})))


class Tool:
    type = "tool_use"

    def __init__(self, name, number, arguments=None):
        self.name, self.id, self.input = name, str(number), arguments or {}

    def model_dump(self, **kwargs):
        return {"type": self.type, "name": self.name, "id": self.id, "input": self.input}


def client_for(*responses):
    client = MagicMock()
    client.messages.count_tokens.return_value.input_tokens = 1000
    client.messages.create.side_effect = list(responses)
    return client


def unpublished_reviews():
    actual = visual_review.recorded
    return patch("exchange.visual_review.recorded", side_effect=lambda name: {"results": []}
                 if name == "exchange/evidence/ai-reviews-v1.json" else actual(name))


@override_settings(**AI_SETTINGS)
class AIAccountingTests(TestCase):
    def ask(self, client, model="claude-haiku-5-5"):
        with patch("exchange.ai_client.make_client", return_value=client):
            return ai_client.ask(None, "test", model, system="test", messages=[{"role": "user", "content": "test"}])

    def test_off_and_unknown_models_never_create_a_client(self):
        with patch("exchange.ai_client.make_client") as factory:
            with override_settings(EXCHANGE_AI_PROVIDER="off"), self.assertRaises(ai_client.Unavailable):
                ai_client.ask(None, "test", "claude-haiku-5-5", system="", messages=[])
            with self.assertRaises(ai_client.Unavailable):
                ai_client.ask(None, "test", "unpriced-model", system="", messages=[])
            factory.assert_not_called()

    def test_preflight_over_limit_never_starts_a_paid_request(self):
        client = client_for()
        client.messages.count_tokens.return_value.input_tokens = 30001
        with self.assertRaises(ai_client.Unavailable):
            self.ask(client)
        client.messages.create.assert_not_called()
        self.assertEqual(ExchangeAICall.objects.count(), 0)

    def test_usd_reservations_prevent_another_call_before_settlement(self):
        with override_settings(EXCHANGE_AI_MAX_USD="0.01"):
            first = ai_client.reserve(None, "copilot", "claude-haiku-5-5")
            self.assertEqual(first.reserved_usd, Decimal("0.009500"))
            with self.assertRaises(ai_client.Unavailable):
                ai_client.reserve(None, "copilot", "claude-haiku-5-5")
        self.assertEqual(ExchangeAICall.objects.count(), 1)

    def test_success_settles_actual_usage_without_caching_discounts(self):
        self.ask(client_for(response()))
        budget = ExchangeAIBudget.objects.get()
        self.assertEqual((budget.reserved_usd, budget.charged_usd), (Decimal("0"), Decimal("0.000150")))
        self.assertEqual(ExchangeAICall.objects.get().state, "succeeded")

    def test_unknown_billing_retains_reservation_and_sdk_does_not_retry(self):
        client = client_for(TimeoutError("unknown outcome"))
        with self.assertRaises(TimeoutError):
            self.ask(client)
        self.assertEqual(client.messages.create.call_count, 1)
        self.assertEqual(ExchangeAICall.objects.get().state, "uncertain")
        self.assertEqual(ExchangeAIBudget.objects.get().reserved_usd, Decimal("0.009500"))
        with self.assertRaises(ai_client.Unavailable):
            ai_client.reserve(None, "visual_review", "claude-sonnet-5-5")
        with patch("anthropic.Anthropic") as sdk:
            ai_client.make_client()
            self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)

    def test_daily_limit_counts_uncertain_attempts(self):
        with override_settings(EXCHANGE_AI_DAILY_CALLS=1):
            ai_client.reserve(None, "copilot", "claude-haiku-5-5")
            with self.assertRaises(ai_client.Unavailable):
                ai_client.reserve(None, "visual_review", "claude-sonnet-5-5")

    def test_out_of_envelope_usage_retains_the_conservative_reservation(self):
        with self.assertRaises(ai_client.Unavailable):
            self.ask(client_for(response(inputs=90001)))
        self.assertEqual(ExchangeAICall.objects.get().state, "uncertain")
        self.assertEqual(ExchangeAIBudget.objects.get().reserved_usd, Decimal("0.009500"))


class ExchangeAgentAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(PRIVATE_MEDIA_ROOT=self.media.name, EXCHANGE_AI_PROVIDER="off")
        self.override.enable()
        self.addCleanup(self.override.disable)
        evidence = unpublished_reviews()
        evidence.start()
        self.addCleanup(evidence.stop)

    def send(self, method, path, data):
        return getattr(self.client, method)(path, json.dumps(data), content_type="application/json")

    def start(self, budget=None):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": "style"})
        if budget is not None:
            self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                      "max_top_up_minor": budget, "note": "private note must not reach the tool planner"})
        return sid

    def plan(self, sid, **data):
        return self.send("post", f"/api/sessions/{sid}/copilot", data)

    def job(self):
        sid = self.start(200)
        asset = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
        r = self.send("post", f"/api/sessions/{sid}/previews", {"asset_id": asset, "mode": "recorded",
                      "candidate_skus": ["CALM-07"], "consent": True})
        self.assertEqual(r.status_code, 202, r.content)
        return PreviewJob.objects.select_related("owner_session__order_line__variant", "variant", "person_asset", "result_asset").get(owner_session_id=sid)

    def test_clarification_and_proposals_do_not_confirm_preferences(self):
        sid = self.start()
        data = self.plan(sid, intent="Keep long sleeves and short sleeves. At most $2 extra.").json()
        self.assertEqual(data["status"], "CLARIFY")
        self.assertTrue(data["questions"])
        self.assertEqual(ExchangeSession.objects.get(pk=sid).preferences, {})
        self.assertFalse(data["confirmed"])

    def test_chosen_limit_is_the_only_tradeoff_and_it_cannot_authorize_a_quote(self):
        sid = self.start(0)
        self.assertEqual(self.plan(sid).json()["status"], "CHOOSE_LIMIT")
        data = self.plan(sid, revisit="budget").json()
        self.assertEqual((data["status"], data["possibilities"][0]["after"]), ("REVIEW_TRADEOFF", 200))
        self.assertNotIn("private note", json.dumps(data))
        self.assertEqual(ExchangeSession.objects.get(pk=sid).preferences["max_top_up_minor"], 0)
        self.assertEqual((Quote.objects.count(), PreviewJob.objects.count(), ExchangeAICall.objects.count()), (0, 0, 0))
        self.assertEqual(self.send("post", f"/api/sessions/{sid}/quotes", {"selected_sku": "CALM-07"}).status_code, 422)

    def test_method_ownership_closure_and_input_checks(self):
        sid = self.start(0)
        url = f"/api/sessions/{sid}/copilot"
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client().post(url, "{}", content_type="application/json").status_code, 404)
        for data in ({"revisit": "stock"}, {"intent": "x" * 501}, {"confirm": True}, []):
            self.assertEqual(self.send("post", url, data).status_code, 422)
        ExchangeSession.objects.filter(pk=sid).update(status="CANCELLED")
        self.assertEqual(self.plan(sid).status_code, 409)

    @override_settings(**AI_SETTINGS)
    def test_model_uses_real_tools_with_private_notes_excluded(self):
        sid = self.start(0)
        client = client_for(response(Tool("inspect_request", 1), Tool("find_candidates", 2), stop="tool_use"),
                            response(Tool("explore_limit", 3), stop="tool_use"), response(Tool("finish", 4), stop="tool_use"))
        with patch("exchange.ai_client.make_client", return_value=client):
            data = self.plan(sid, revisit="budget").json()
        self.assertEqual((data["reader"], data["status"]), ("claude", "REVIEW_TRADEOFF"))
        self.assertEqual(ExchangeAICall.objects.count(), 3)
        self.assertNotIn("private note", str(client.messages.create.call_args_list))
        self.assertEqual(ExchangeSession.objects.get(pk=sid).preferences["max_top_up_minor"], 0)

    @override_settings(**AI_SETTINGS)
    def test_invented_write_tool_falls_back_without_mutating_anything(self):
        sid = self.start(0)
        client = client_for(response(Tool("confirm_preferences", 1, {"max_top_up_minor": 9999}), stop="tool_use"))
        with patch("exchange.ai_client.make_client", return_value=client):
            data = self.plan(sid).json()
        self.assertEqual((data["reader"], data["fallback"], data["status"]), ("rules", True, "CHOOSE_LIMIT"))
        self.assertEqual(ExchangeSession.objects.get(pk=sid).preferences["max_top_up_minor"], 0)
        self.assertEqual(client.messages.create.call_count, 1)

    @override_settings(**AI_SETTINGS)
    def test_tool_rounds_are_bounded_and_a_changed_request_invalidates_the_result(self):
        sid = self.start(0)
        client = client_for(*(response(Tool("inspect_request", i), stop="tool_use") for i in range(copilot.MAX_ROUNDS)))
        with patch("exchange.ai_client.make_client", return_value=client):
            self.assertTrue(self.plan(sid).json()["fallback"])
        self.assertEqual(client.messages.create.call_count, copilot.MAX_ROUNDS)
        original = copilot.Tools.complete
        def changed(tools):
            result = original(tools)
            ExchangeSession.objects.filter(pk=sid).update(preference_version=99)
            return result
        with override_settings(EXCHANGE_AI_PROVIDER="off"), patch.object(copilot.Tools, "complete", changed):
            self.assertEqual(self.plan(sid).json()["status"], "STALE")

    def test_recording_integrity_and_off_mode_do_not_start_vision_calls(self):
        job = self.job()
        bundle = visual_review.bundle_for_job(job)
        self.assertEqual(set(bundle["images"]), {"bought_catalog", "replacement_catalog", "displayed_preview"})
        with patch("exchange.ai_client.make_client") as factory:
            data = self.send("post", f"/api/previews/{job.id}/visual-review", {}).json()
            self.assertEqual(data["status"], "NOT_ASSESSED")
            factory.assert_not_called()
        self.assertEqual(VisualReviewAttempt.objects.count(), 0)
        job.result_asset.sha256 = "0" * 64
        with self.assertRaises(visual_review.NotReviewable):
            visual_review.bundle_for_job(job)

    @override_settings(**AI_SETTINGS)
    def test_visual_review_is_ai_labelled_cached_and_scoped_to_the_owner(self):
        job = self.job()
        client = client_for(text_review("concern"))
        url = f"/api/previews/{job.id}/visual-review"
        with patch("exchange.ai_client.make_client", return_value=client):
            first = self.send("post", url, {}).json()
            second = self.send("post", url, {}).json()
        self.assertEqual((first["status"], first["assessment"], first["human_reviewed"]), ("ASSESSED", "concern", False))
        self.assertIn("AI", first["label"])
        self.assertEqual(second["assessment"], "concern")
        self.assertEqual(client.messages.create.call_count, 1)
        self.assertEqual(Client().post(url, "{}", content_type="application/json").status_code, 404)
        with override_settings(EXCHANGE_AI_PROVIDER="off"):
            self.assertEqual(self.plan(str(job.owner_session_id)).json()["status"], "REVIEW_PREVIEW")

    @override_settings(**AI_SETTINGS)
    def test_uncertain_visual_attempt_never_retries_automatically(self):
        job = self.job()
        client = client_for(TimeoutError("unknown"))
        with patch("exchange.ai_client.make_client", return_value=client):
            self.assertEqual(visual_review.review(job)["status"], "NOT_ASSESSED")
            self.assertEqual(visual_review.review(job)["status"], "NOT_ASSESSED")
        self.assertEqual(client.messages.create.call_count, 1)
        self.assertEqual(VisualReviewAttempt.objects.get().status, "uncertain")
        self.assertGreater(ExchangeAIBudget.objects.get().reserved_usd, 0)

    def test_review_rejects_person_claims_confidence_and_incomplete_attributes(self):
        asked = {"sleeves": "long"}
        for observation in ("This fits perfectly.", "Confidence 99 percent.", "Her body looks slimmer."):
            with self.assertRaises(ValueError):
                visual_review.accept({"checks": [{"attribute": "sleeves", "finding": "supports", "observation": observation}]}, asked)
        with self.assertRaises(ValueError):
            visual_review.accept({"checks": []}, asked)

    @override_settings(**AI_SETTINGS)
    def test_judge_only_mode_prevents_nonjudge_model_calls(self):
        sid = self.start(0)
        with override_settings(EXCHANGE_AI_JUDGE_ONLY=True), patch("exchange.ai_client.make_client") as factory:
            self.assertEqual(self.plan(sid).json()["reader"], "rules")
            job = self.job()
            self.assertEqual(visual_review.review(job)["status"], "NOT_ASSESSED")
            factory.assert_not_called()

    def test_review_tool_cannot_run_before_the_candidate_query(self):
        sid = self.start(200)
        tools = copilot.Tools(ExchangeSession.objects.get(pk=sid))
        with self.assertRaises(copilot.InvalidPlan):
            tools.dispatch("inspect_visual_reviews", {})

    def test_public_agent_report_is_separate_from_human_accuracy(self):
        with patch("exchange.ai_client.make_client") as factory:
            result = self.client.get("/evaluation/agents")
            self.assertEqual(result.status_code, 200)
            self.assertContains(result, "independent human review remains")
            self.assertContains(result, "not an accuracy score" if b"Run 1" in result.content else "not model accuracy")
            factory.assert_not_called()

    def test_catalog_change_during_plan_returns_stale_instead_of_recommending_it(self):
        from .models import ProductVariant
        sid = self.start(200)
        original = copilot.plan
        def change(session, **kwargs):
            result = original(session, **kwargs)
            ProductVariant.objects.filter(sku="CALM-07").update(stock_qty=0)
            return result
        with patch("exchange.copilot.plan", side_effect=change):
            self.assertEqual(self.plan(sid).json()["status"], "STALE")

    def test_an_assessment_for_old_preferences_is_not_presented_as_current(self):
        job = self.job()
        def change(current):
            ExchangeSession.objects.filter(pk=current.owner_session_id).update(preference_version=99)
            return {"status": "ASSESSED", "assessment": "supports", "checks": []}
        with patch("exchange.visual_review.review", side_effect=change):
            data = self.send("post", f"/api/previews/{job.id}/visual-review", {}).json()
        self.assertEqual(data["status"], "NOT_ASSESSED")
        self.assertIn("changed", data["message"])

    @override_settings(**AI_SETTINGS)
    def test_a_known_allowance_block_does_not_poison_the_review_cache(self):
        job = self.job()
        client = client_for(text_review())
        with patch("exchange.ai_client.make_client", return_value=client):
            with override_settings(EXCHANGE_AI_DAILY_CALLS=0):
                self.assertEqual(visual_review.review(job)["status"], "NOT_ASSESSED")
            self.assertEqual(VisualReviewAttempt.objects.get().status, "not_attempted")
            client.messages.create.assert_not_called()
            self.assertEqual(visual_review.review(job)["status"], "ASSESSED")
        self.assertEqual(client.messages.create.call_count, 1)
