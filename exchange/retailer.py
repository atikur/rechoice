"""How a store plugs Rechoice in: create an order and get its return link, then read the exchange requests back.

The store authenticates with RETAILER_API_KEY as a bearer token; blank turns the API off. The demo has one store,
so the key acts for the catalog seeded as "secondlook-demo". Requests are pulled, not pushed: Rechoice makes no
outbound calls to a store, and a real integration would add signed webhooks.
"""
import hmac
import re
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers

from .models import EvidenceEvent, ExchangeRequest, OrderLine, PolicySnapshot, ProductVariant
from .views import _error, _over_address_limit, _validated

RETAILER = "secondlook-demo"
ORDER_NUMBER = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,39}$")


class LineSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=40)
    paid_minor = serializers.IntegerField(min_value=0, max_value=10_000_000)
    opened = serializers.BooleanField(default=False)


class OrderSerializer(serializers.Serializer):
    order_number = serializers.CharField(max_length=40)
    lines = LineSerializer(many=True, allow_empty=False, max_length=10)

    def validate_order_number(self, value):
        if not ORDER_NUMBER.match(value):
            raise serializers.ValidationError("Use 3 to 40 capital letters, digits and dashes.")
        return value


def _authorized(request):
    key = settings.RETAILER_API_KEY
    given = request.headers.get("Authorization", "").removeprefix("Bearer ")
    return bool(key) and hmac.compare_digest(given.encode(), key.encode())


def retailer_api(view):
    def wrapped(request, *args, **kwargs):
        if not settings.RETAILER_API_KEY:
            return _error("RETAILER_API_OFF", "The retailer API is not configured on this server.", 404)
        if _over_address_limit(request, "retailer", 120):
            return _error("RATE_LIMITED", "Too many requests. Try again later.", 429)
        if not _authorized(request):
            return _error("UNAUTHORIZED", "Send the store's API key as a bearer token.", 401)
        return view(request, *args, **kwargs)
    # The store authenticates with its key, not a browser cookie, so there is no CSRF to guard against.
    return csrf_exempt(wrapped)


@retailer_api
def orders(request):
    if request.method != "POST":
        return _error("METHOD_NOT_ALLOWED", "POST is required.", 405)
    data, error = _validated(request, OrderSerializer)
    if error:
        return error
    variants = {v.sku: v for v in ProductVariant.objects.filter(
        retailer_id=RETAILER, sku__in=[line["sku"] for line in data["lines"]])}
    unknown = sorted({line["sku"] for line in data["lines"]} - set(variants))
    if unknown:
        return _error("UNKNOWN_SKU", "Not in the store's catalog: " + ", ".join(unknown) + ".")
    policy = PolicySnapshot.objects.filter(retailer_id=RETAILER).order_by("-version").first()
    if policy is None:
        return _error("POLICY_UNKNOWN", "The store has no return policy on file.", 422)
    if OrderLine.objects.filter(retailer_id=RETAILER, fictional_order_id=data["order_number"]).exists():
        return _error("ORDER_EXISTS", "An order with this number already has a return link.", 409)
    try:
        with transaction.atomic():
            lines = [OrderLine.objects.create(
                token=secrets.token_urlsafe(18), fictional_order_id=data["order_number"], retailer_id=RETAILER,
                variant=variants[line["sku"]], quantity=1, paid_minor=line["paid_minor"],
                eligible_credit_minor=line["paid_minor"], currency=variants[line["sku"]].currency,
                simple_pricing=True, policy_version=policy.version, opened=line["opened"])
                for line in data["lines"]]
    except IntegrityError:
        return _error("ORDER_EXISTS", "An order with this number already has a return link.", 409)
    EvidenceEvent.objects.create(actor="retailer", action="order_registered",
                                 facts={"order_number": data["order_number"], "lines": len(lines)})
    return JsonResponse({
        "order_number": data["order_number"],
        "return_url": request.build_absolute_uri(reverse("home") + "?order=" + lines[0].token),
        "store_page_url": request.build_absolute_uri(reverse("store") + "?order=" + lines[0].token),
        "lines": [{"line_token": line.token, "sku": line.variant.sku, "title": line.variant.title} for line in lines],
    }, status=201)


def _request_json(draft):
    line, quote = draft.owner_session.order_line, draft.quote
    return {
        "request_id": str(draft.id), "state": draft.state, "order_number": line.fictional_order_id,
        "returned": {"sku": line.variant.sku, "title": line.variant.title, "line_token": line.token},
        "replacement": {"sku": quote.variant.sku, "title": quote.variant.title},
        "kind": "another size" if quote.kind == "size" else "a different item",
        "currency": quote.currency, "credit_minor": quote.credit_minor, "replacement_minor": quote.replacement_minor,
        "fee_minor": quote.fee_minor, "top_up_minor": quote.top_up_minor,
        "confirmed_at": draft.customer_confirmed_at.isoformat(),
        "reviewed_at": draft.reviewed_at.isoformat() if draft.reviewed_at else None,
    }


def _requests():
    return ExchangeRequest.objects.select_related(
        "quote__variant", "owner_session__order_line__variant").filter(owner_session__order_line__retailer_id=RETAILER)


@retailer_api
def requests(request):
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    drafts = _requests()
    if since := request.GET.get("since"):
        when = parse_datetime(since)
        if when is None:
            return _error("INVALID_INPUT", "Give since as an ISO 8601 time, such as 2026-10-28T00:00:00Z.")
        drafts = drafts.filter(customer_confirmed_at__gt=when)
    return JsonResponse({"requests": [_request_json(d) for d in drafts.order_by("customer_confirmed_at")[:100]]})


@retailer_api
def request_detail(request, request_id):
    if request.method != "GET":
        return _error("METHOD_NOT_ALLOWED", "GET is required.", 405)
    draft = _requests().filter(id=request_id).first()
    if draft is None:
        return _error("NOT_FOUND", "Request not found.", 404)
    return JsonResponse(_request_json(draft))
