"""Bounded calls for the new exchange agents. No retries; unknown calls keep their reservation.

Prices checked against Anthropic's official model table on 29 October 2026. Token preflight must be
under 30k. We reserve 90k input tokens plus the output cap, without caching discounts. The token
counter is free. The shared ledger serializes reservations across these two features and survives
session cleanup. It does not include the older interpretation/comparison helpers' separate ledger.
"""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ExchangeAIBudget, ExchangeAICall

PRICES = {"claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
          "claude-sonnet-5-5": (Decimal("2"), Decimal("10"))}
MAX_INPUT = 30_000
RESERVED_INPUT = 90_000
MAX_OUTPUT = 1000


class Unavailable(ValueError):
    pass


class RequestNotStarted(Unavailable):
    """Known to have stopped before the paid generation request."""


def price(model, inputs, outputs):
    if model not in PRICES:
        raise Unavailable("Model has no approved price entry.")
    a, b = PRICES[model]
    return ((a * inputs + b * outputs) / 1_000_000).quantize(Decimal("0.000001"))


def make_client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY,
                               timeout=settings.EXCHANGE_AI_TIMEOUT_SECONDS, max_retries=0)


def enabled():
    return settings.EXCHANGE_AI_PROVIDER == "claude"


def allowed(session):
    return enabled() and (session is None or not settings.EXCHANGE_AI_JUDGE_ONLY or session.judge)


def reserve(session, feature, model):
    if not allowed(session) or not settings.EXCHANGE_AI_APPROVAL or not settings.ANTHROPIC_API_KEY:
        raise Unavailable("Paid exchange AI is off.")
    maximum = price(model, RESERVED_INPUT, MAX_OUTPUT)
    cap = Decimal(str(settings.EXCHANGE_AI_MAX_USD))
    if not cap.is_finite() or cap <= 0:
        raise Unavailable("No approved USD allowance.")
    with transaction.atomic():
        ExchangeAIBudget.objects.get_or_create(key="exchange-ai")
        budget = ExchangeAIBudget.objects.select_for_update().get(key="exchange-ai")
        calls = ExchangeAICall.objects.all()
        if calls.filter(state="uncertain").exists():
            raise Unavailable("An earlier charge is uncertain; further paid requests are stopped.")
        day = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if calls.filter(created_at__gte=day).count() >= settings.EXCHANGE_AI_DAILY_CALLS:
            raise Unavailable("Daily exchange-AI call limit reached.")
        if session is not None and calls.filter(session=session).count() >= settings.EXCHANGE_AI_SESSION_CALLS:
            raise Unavailable("Session exchange-AI call limit reached.")
        if budget.charged_usd + budget.reserved_usd + maximum > cap:
            raise Unavailable("Cumulative exchange-AI USD allowance reached.")
        budget.reserved_usd += maximum
        budget.save(update_fields=["reserved_usd"])
        return ExchangeAICall.objects.create(session=session, feature=feature, model=model, reserved_usd=maximum)


def settle(call, response):
    usage = response.usage
    inputs, outputs = usage.input_tokens, usage.output_tokens
    if type(inputs) is not int or type(outputs) is not int or min(inputs, outputs) < 0:
        raise Unavailable("Uncertain token accounting.")
    if inputs > RESERVED_INPUT or outputs > MAX_OUTPUT:
        raise Unavailable("Usage exceeded the approved token envelope.")
    actual = price(call.model, inputs, outputs)
    with transaction.atomic():
        budget = ExchangeAIBudget.objects.select_for_update().get(key="exchange-ai")
        locked = ExchangeAICall.objects.select_for_update().get(pk=call.pk)
        if locked.state != "reserved":
            raise Unavailable("Call was already settled.")
        budget.reserved_usd -= locked.reserved_usd
        budget.charged_usd += actual
        budget.save(update_fields=["reserved_usd", "charged_usd"])
        locked.state = "succeeded"
        locked.charged_usd, locked.input_tokens, locked.output_tokens = actual, inputs, outputs
        locked.save(update_fields=["state", "charged_usd", "input_tokens", "output_tokens"])
    if actual > call.reserved_usd:
        raise Unavailable("Usage exceeded its conservative reservation; stop this run.")


def ask(session, feature, model, *, system, messages, tools=None, output_schema=None):
    if not allowed(session) or not settings.EXCHANGE_AI_APPROVAL or not settings.ANTHROPIC_API_KEY:
        raise RequestNotStarted("Paid exchange AI is off.")
    # Validate the model before sending any data to the provider.
    try:
        price(model, 0, 0)
    except Unavailable as exc:
        raise RequestNotStarted(str(exc)) from None
    client = make_client()
    args = {"model": model, "system": system, "messages": messages}
    if tools:
        args["tools"] = tools
    try:
        count = client.messages.count_tokens(**args).input_tokens
    except Exception:
        raise RequestNotStarted("Token preflight did not complete; no paid generation request started.") from None
    if type(count) is not int or not 0 <= count <= MAX_INPUT:
        raise RequestNotStarted("Input exceeds the approved token preflight limit.")
    try:
        call = reserve(session, feature, model)
    except Unavailable as exc:
        raise RequestNotStarted(str(exc)) from None
    try:
        kwargs = {**args, "max_tokens": MAX_OUTPUT}
        if output_schema:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": output_schema}}
        response = client.messages.create(**kwargs)
        settle(call, response)
        return response
    except Exception:
        # The request may have been billed. Never release an unknown reservation or retry it.
        ExchangeAICall.objects.filter(pk=call.pk, state="reserved").update(state="uncertain")
        raise
