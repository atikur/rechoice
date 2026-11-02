from django.contrib import admin
from .models import (
    EvidenceEvent, ExchangeRequest, ExchangeSession, OrderLine, PolicySnapshot,
    PreviewControl, PreviewJob, PrivateAsset, ProductVariant, Quote,
)
for model in (ProductVariant, PolicySnapshot, OrderLine, ExchangeSession,
              PrivateAsset, PreviewJob, Quote, ExchangeRequest, EvidenceEvent, PreviewControl):
    admin.site.register(model)
