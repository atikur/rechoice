import uuid
from django.db import models
from django.utils import timezone


class ExchangeAIBudget(models.Model):
    """Cumulative reservation ledger for the copilot and visual reviewer, separate from legacy helpers."""
    key = models.CharField(max_length=40, primary_key=True, default="exchange-ai")
    charged_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    reserved_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)


class ExchangeAICall(models.Model):
    session = models.ForeignKey("ExchangeSession", null=True, on_delete=models.SET_NULL)
    feature = models.CharField(max_length=30)
    model = models.CharField(max_length=60)
    state = models.CharField(max_length=20, default="reserved")
    reserved_usd = models.DecimalField(max_digits=12, decimal_places=6)
    charged_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)


class VisualReviewAttempt(models.Model):
    fingerprint = models.CharField(max_length=64, unique=True)
    model = models.CharField(max_length=60)
    status = models.CharField(max_length=20, default="pending")
    sources = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)


class ProductVariant(models.Model):
    retailer_id = models.CharField(max_length=40)
    sku = models.CharField(max_length=40)
    # Sizes of one garment share a style code and its catalog photo.
    style_code = models.CharField(max_length=40, blank=True)
    title = models.CharField(max_length=120)
    category = models.CharField(max_length=40, default="tops")
    size_label = models.CharField(max_length=20)
    currency = models.CharField(max_length=3, default="USD")
    price_minor = models.PositiveIntegerField()
    stock_qty = models.PositiveIntegerField()
    pattern_level = models.PositiveSmallIntegerField(null=True)
    color_tag = models.CharField(max_length=40, blank=True)
    sleeve = models.CharField(max_length=20, blank=True)
    neckline = models.CharField(max_length=30, blank=True)
    image_asset_id = models.CharField(max_length=180, blank=True)
    # Beauty products: each shade is its own variant in a product line, with its shade as a hex fact.
    product_line = models.CharField(max_length=80, blank=True)
    shade_name = models.CharField(max_length=60, blank=True)
    shade_hex = models.CharField(max_length=7, blank=True)
    finish = models.CharField(max_length=20, blank=True)
    # Earrings: drop length in millimetres and style (stud or drop); the metal is the colour tag.
    length_mm = models.PositiveSmallIntegerField(null=True, blank=True)
    # Earrings VTO is told this length, in mm: the part below the hook, which the engine scales before adding the
    # hook back. Measured on the 28 October 2026 pilot, where a drop told its whole length rendered 51% too long.
    scale_mm = models.PositiveSmallIntegerField(null=True, blank=True)
    form = models.CharField(max_length=12, blank=True)
    # The garment's colour for checking generated previews; "proposed" until the owner reviews it.
    reference_colour = models.CharField(max_length=7, blank=True)
    reference_colour_status = models.CharField(max_length=10, blank=True)
    verified_at = models.DateTimeField(default=timezone.now)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["retailer_id", "sku"], name="retailer_sku_unique")]


class PolicySnapshot(models.Model):
    retailer_id = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    allowed_category = models.CharField(max_length=40, default="tops")
    # All categories this policy covers; empty means only allowed_category.
    categories = models.JSONField(default=list, blank=True)
    # Opened beauty products can only be exchanged for another shade in the same product line.
    opened_same_line = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    fee_minor = models.PositiveIntegerField(null=True)
    exchange_allowed = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["retailer_id", "version"], name="retailer_policy_unique")]


class OrderLine(models.Model):
    token = models.CharField(max_length=80, unique=True)
    fictional_order_id = models.CharField(max_length=80)
    retailer_id = models.CharField(max_length=40)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    paid_minor = models.PositiveIntegerField()
    eligible_credit_minor = models.PositiveIntegerField(null=True)
    currency = models.CharField(max_length=3, default="USD")
    simple_pricing = models.BooleanField(default=True)
    policy_version = models.PositiveIntegerField(default=1)
    opened = models.BooleanField(default=False)


class ExchangeSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    browser_session_key = models.CharField(max_length=80)
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT)
    preferences = models.JSONField(default=dict)
    preference_version = models.PositiveIntegerField(default=0)
    consent_version = models.PositiveIntegerField(default=0)
    consent_given = models.BooleanField(default=False)
    # Why the shopper is sending the item back: style, fit, damaged or changed_mind. Blank means style.
    return_reason = models.CharField(max_length=20, blank=True)
    # Started by a browser that entered the judge access code; judges have their own live allowance.
    judge = models.BooleanField(default=False)
    # The batch currently shown, including pending/failed jobs; never infer it from old successes.
    active_preview_ids = models.JSONField(default=list, blank=True, null=True)
    status = models.CharField(max_length=32, default="STARTED")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class PrivateAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_session = models.ForeignKey(ExchangeSession, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    license_or_consent = models.CharField(max_length=180)
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=180)
    expires_at = models.DateTimeField()


class LookPreview(models.Model):
    """One person wearing a chosen replacement with the rest of the order.

    Each item is one preview job, run in turn on the result of the one before: the top, then the
    lipstick, then the necklace. A look may continue an earlier one: its first steps are that look's,
    and its own steps start on the last of them.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_session = models.ForeignKey(ExchangeSession, on_delete=models.CASCADE)
    fingerprint = models.CharField(max_length=64, unique=True)
    # The items in the order they are applied: [{"sku", "key", "category", "provider", "version", "units"}, ...].
    # key is what a recorded result is filed under.
    plan = models.JSONField()
    person_asset = models.ForeignKey(PrivateAsset, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    person_asset_sha256 = models.CharField(max_length=64, blank=True)
    consent_version = models.PositiveIntegerField(default=0)
    continues = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    continued_steps = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class PreviewJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_session = models.ForeignKey(ExchangeSession, on_delete=models.CASCADE)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64, unique=True)
    provider = models.CharField(max_length=30, default="mock")
    provider_version = models.CharField(max_length=30, default="mock-v1")
    provider_task_id = models.CharField(max_length=180, blank=True)
    state = models.CharField(max_length=20, default="pending")
    error_code = models.CharField(max_length=40, blank=True)
    result_asset = models.ForeignKey(PrivateAsset, null=True, blank=True, on_delete=models.SET_NULL)
    person_asset = models.ForeignKey(PrivateAsset, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    person_asset_sha256 = models.CharField(max_length=64, blank=True)
    consent_version = models.PositiveIntegerField(default=0)
    # A shopper's manual retry is a new job that points at the failed one; nothing is ever restarted.
    retry_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="retry")
    observed_units = models.PositiveIntegerField(default=0)
    reserved_units = models.PositiveIntegerField(default=0)
    # The colour check on a generated result: close, differs, pattern or unchecked. Blank without a result.
    colour_check = models.CharField(max_length=20, blank=True)
    colour_delta = models.FloatField(null=True, blank=True)
    colour_note = models.CharField(max_length=160, blank=True)
    # The garment colour the colour check measured on a top preview, as hex, so the card can show it beside the
    # catalog colour. Written with the gate verdict; blank when the colour was not measured.
    preview_colour = models.CharField(max_length=7, blank=True)
    # The size check on a generated earring result: the rendered height in pixels, and how it compares with the
    # pair bought on the same photo: size-close, size-differs or unchecked. Blank without a result.
    size_px = models.PositiveIntegerField(null=True, blank=True)
    size_check = models.CharField(max_length=20, blank=True)
    size_note = models.CharField(max_length=160, blank=True)
    # An earring resized to its catalog length; result_asset stays as YouCam drew it.
    sized_asset = models.ForeignKey(PrivateAsset, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # The fidelity gate on a top preview (exchange/fidelity_gate.py): print busyness relative to the bought top on the
    # same photo, the share of the image outside the garment band that changed, and the verdict: shows, check or
    # concern. Blank until the checks have run.
    print_ratio = models.FloatField(null=True, blank=True)
    outside_change_pct = models.FloatField(null=True, blank=True)
    gate_status = models.CharField(max_length=12, blank=True)
    gate_note = models.CharField(max_length=220, blank=True)
    started_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Recovery bookkeeping. queued_at is when the job last became pending, last_step_at is its last
    # sign of progress, and driver names the one task chain allowed to poll it once it runs.
    queued_at = models.DateTimeField(default=timezone.now)
    last_step_at = models.DateTimeField(default=timezone.now)
    driver = models.CharField(max_length=32, blank=True)
    # A step of a look, counted from 0. A later step's person_asset is the result of the step before.
    look = models.ForeignKey(LookPreview, null=True, blank=True, on_delete=models.CASCADE, related_name="steps")
    look_step = models.PositiveSmallIntegerField(null=True, blank=True)


class Quote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_session = models.ForeignKey(ExchangeSession, on_delete=models.CASCADE)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    kind = models.CharField(max_length=10, default="style")
    # Exact preview concern acknowledged for this quote, rechecked when saving its draft.
    preview_concern_ack = models.CharField(max_length=64, blank=True)
    currency = models.CharField(max_length=3)
    credit_minor = models.PositiveIntegerField()
    replacement_minor = models.PositiveIntegerField()
    fee_minor = models.PositiveIntegerField()
    top_up_minor = models.PositiveIntegerField()
    variant_version = models.PositiveIntegerField()
    policy_version = models.PositiveIntegerField()
    preference_version = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class ExchangeRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_session = models.ForeignKey(ExchangeSession, on_delete=models.PROTECT)
    quote = models.OneToOneField(Quote, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=100)
    state = models.CharField(max_length=30, default="DRAFT")
    customer_confirmed_at = models.DateTimeField()
    reviewed_at = models.DateTimeField(null=True)
    # Staff's latest message to the shopper (required when asking for clarification) and the reply.
    message_to_shopper = models.CharField(max_length=500, blank=True)
    shopper_reply = models.CharField(max_length=500, blank=True)
    replied_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner_session", "idempotency_key"], name="session_draft_key_unique")]


class EvidenceEvent(models.Model):
    at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=30)
    action = models.CharField(max_length=60)
    session = models.ForeignKey(ExchangeSession, null=True, on_delete=models.SET_NULL)
    facts = models.JSONField(default=dict)
    correlation_id = models.UUIDField(default=uuid.uuid4)


class PreviewControl(models.Model):
    """One row (id 1): the switch that pauses new previews, and the latest provider balance reading."""
    paused = models.BooleanField(default=False)
    reason = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    balance_units = models.FloatField(null=True, blank=True)
    balance_checked_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row

