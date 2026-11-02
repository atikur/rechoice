import uuid

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from secondlook.providers import PENDING, RUNNING, SUCCEEDED
from . import preview_jobs
from .gate_hook import gate_finished


@shared_task(name="exchange.drive_preview_job")
def drive_preview_job(job_id, attempt=0, driver=None):
    """Advance one preview job, then poll again later while it is still running.

    driver names this task chain. The chain that claims a job is the only one that keeps polling it.
    """
    driver = driver or uuid.uuid4().hex
    state = preview_jobs.advance(job_id, driver)
    if state == SUCCEEDED:
        # Measure a finished top preview against the request before the shopper sees it.
        gate_finished(job_id)
        # A look's next step starts from this result.
        nxt = preview_jobs.next_look_step(job_id)
        if nxt is not None and nxt.state == PENDING:
            drive_preview_job.apply_async((str(nxt.id),))
    if state != RUNNING:
        return state
    if attempt >= settings.PREVIEW_MAX_POLLS:
        return preview_jobs.give_up(job_id)
    drive_preview_job.apply_async((job_id, attempt + 1, driver), countdown=settings.PREVIEW_POLL_SECONDS)
    return state


@shared_task(name="exchange.recover_stuck_previews")
def recover_stuck_previews():
    """Beat sends this every minute to pick up previews whose worker task was lost."""
    return preview_jobs.recover_stuck_jobs(
        lambda job_id, attempt, driver: drive_preview_job.apply_async((job_id, attempt, driver)))


def _paid_provider():
    """The configured provider when it charges units and can report its balance, else None."""
    try:
        provider = preview_jobs.get_provider()
    except preview_jobs.ProviderUnavailable:
        return None
    return provider if provider.units_per_job and hasattr(provider, "balance") else None


@shared_task(name="exchange.refresh_provider_balance")
def refresh_provider_balance():
    provider = _paid_provider()
    if provider is None:
        return "skipped: no paid provider"
    return preview_jobs.refresh_balance(provider, force=True).balance_units


@shared_task(name="exchange.reconcile_provider_units")
def reconcile_provider_units():
    provider = _paid_provider()
    if provider is None:
        return "skipped: no paid provider"
    return preview_jobs.reconcile_units(provider.unit_history())


@shared_task(name="exchange.cleanup_expired")
def cleanup_expired():
    from .cleanup import cleanup_expired as run
    return run()


HEARTBEAT_KEY = "worker:heartbeat"


@shared_task(name="exchange.worker_heartbeat")
def worker_heartbeat():
    """Beat sends this every minute; the health check reads it from the shared cache."""
    stamp = timezone.now().isoformat()
    cache.set(HEARTBEAT_KEY, stamp, timeout=600)
    return stamp

