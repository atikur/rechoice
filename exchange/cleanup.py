"""Scheduled deletion of expired private images and closing of expired sessions.

Preview jobs are never deleted: they are the unit ledger that the spending limits rely on, and
they hold no images. Deleting a person photo leaves the job pointing at nothing.
"""
import logging
from importlib import import_module

from django.conf import settings
from django.utils import timezone

from .assets import delete_private_image, private_image_exists
from .models import EvidenceEvent, ExchangeRequest, ExchangeSession, PrivateAsset

log = logging.getLogger(__name__)


def _remove(asset):
    """Delete the image, confirm it is gone, then delete the record. True on success."""
    try:
        delete_private_image(asset.storage_key)
        if private_image_exists(asset.storage_key):
            return False
    except Exception:
        log.warning("Could not delete private image %s", asset.pk, exc_info=True)
        return False
    asset.delete()
    return True


def cleanup_expired(now=None):
    now = now or timezone.now()
    removed = failed = closed = 0
    for asset in PrivateAsset.objects.filter(expires_at__lte=now).iterator():
        if _remove(asset):
            removed += 1
        else:
            failed += 1
    with_drafts = set(ExchangeRequest.objects.values_list("owner_session_id", flat=True))
    for session in ExchangeSession.objects.filter(expires_at__lte=now).exclude(status="EXPIRED"):
        for asset in PrivateAsset.objects.filter(owner_session=session):
            if _remove(asset):
                removed += 1
            else:
                failed += 1
        preferences = dict(session.preferences or {})
        if session.id not in with_drafts and preferences.get("note"):
            preferences["note"] = ""
        session.preferences = preferences
        session.status = "EXPIRED"
        session.save(update_fields=["preferences", "status"])
        closed += 1
    import_module(settings.SESSION_ENGINE).SessionStore.clear_expired()
    result = {"assets_removed": removed, "assets_failed": failed, "sessions_closed": closed}
    if failed:
        log.error("Cleanup could not delete %s private images; they will be retried.", failed)
        EvidenceEvent.objects.create(actor="system", action="cleanup_failed", facts=result)
    elif removed or closed:
        EvidenceEvent.objects.create(actor="system", action="cleanup_ran", facts=result)
    return result
