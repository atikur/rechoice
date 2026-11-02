from rest_framework import serializers


class StartSerializer(serializers.Serializer):
    order_token = serializers.CharField(max_length=80)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=["style", "fit", "shade", "look", "reaction", "damaged", "changed_mind"])


class ItemSerializer(serializers.Serializer):
    line_id = serializers.IntegerField(min_value=1)


class ShadePreferenceSerializer(serializers.Serializer):
    lightness = serializers.ChoiceField(choices=["lighter", "darker"], allow_blank=True, default="")
    tone = serializers.ChoiceField(choices=["warmer", "cooler"], allow_blank=True, default="")
    intensity = serializers.ChoiceField(choices=["softer", "bolder"], allow_blank=True, default="")
    max_top_up_minor = serializers.IntegerField(min_value=0, max_value=100000)
    currency = serializers.ChoiceField(choices=["USD"], default="USD")
    note = serializers.CharField(max_length=500, allow_blank=True, default="")

    def validate(self, data):
        if not (data["lightness"] or data["tone"] or data["intensity"]):
            raise serializers.ValidationError("Say how the shade should change.")
        return data


class JewelleryPreferenceSerializer(serializers.Serializer):
    size = serializers.ChoiceField(choices=["smaller", "larger"], allow_blank=True, default="")
    # The metal for jewellery; for shoes and bags, the colour family.
    metal = serializers.ChoiceField(choices=["gold", "silver", "neutral", "bright"], allow_blank=True, default="")
    max_top_up_minor = serializers.IntegerField(min_value=0, max_value=100000)
    currency = serializers.ChoiceField(choices=["USD"], default="USD")
    note = serializers.CharField(max_length=500, allow_blank=True, default="")

    def validate(self, data):
        if not (data["size"] or data["metal"]):
            raise serializers.ValidationError("Say what should change: the size, or the metal or colour.")
        return data


class JudgeCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=100)


class InterpretSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=500, allow_blank=True)


class PreferenceSerializer(serializers.Serializer):
    quieter_print = serializers.BooleanField(default=False)
    sleeve_required = serializers.ChoiceField(choices=["long", "short", "sleeveless"], allow_blank=True, default="")
    color_preferences = serializers.ListField(child=serializers.CharField(max_length=40), default=list)
    neckline_preference = serializers.CharField(max_length=30, allow_blank=True, default="")
    max_top_up_minor = serializers.IntegerField(min_value=0, max_value=100000)
    currency = serializers.ChoiceField(choices=["USD"], default="USD")
    note = serializers.CharField(max_length=500, allow_blank=True, default="")


class PreviewSerializer(serializers.Serializer):
    candidate_skus = serializers.ListField(child=serializers.CharField(max_length=40),
                                           min_length=1, max_length=2)
    consent = serializers.BooleanField()
    asset_id = serializers.UUIDField(required=False)
    # Also preview the purchased top on the same person, so every preview is made the same way.
    include_original = serializers.BooleanField(default=False)
    # "recorded" replays real results from the pilot; "live" uses the site's preview provider.
    mode = serializers.ChoiceField(choices=["live", "recorded"], default="live")


class LookSerializer(serializers.Serializer):
    # One of the shopper's eligible choices, or the item they bought, to see what they have now.
    sku = serializers.CharField(max_length=40)
    consent = serializers.BooleanField()
    asset_id = serializers.UUIDField()
    mode = serializers.ChoiceField(choices=["live", "recorded"], default="live")


class CompareSerializer(serializers.Serializer):
    preview_job_ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=30)


class QuoteSerializer(serializers.Serializer):
    preview_job_id = serializers.UUIDField(required=False, allow_null=True)
    preview_concern_token = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    selected_sku = serializers.CharField(max_length=40)
    # True when the shopper chooses an option whose preview failed the fidelity gate, having seen why.
    accept_preview_concern = serializers.BooleanField(required=False, default=False)


class DraftSerializer(serializers.Serializer):
    quote_id = serializers.UUIDField()
    quote_version = serializers.IntegerField(min_value=1)
    confirmed = serializers.BooleanField()


class StaffDecisionSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["REVIEWED", "DECLINED", "NEEDS_CLARIFICATION"])
    message = serializers.CharField(max_length=500, allow_blank=True, default="")

    def validate(self, data):
        if data["state"] == "NEEDS_CLARIFICATION" and not data["message"].strip():
            raise serializers.ValidationError("Write the question for the shopper.")
        return data


class ReplySerializer(serializers.Serializer):
    reply = serializers.CharField(max_length=500)
