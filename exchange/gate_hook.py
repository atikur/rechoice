"""Run the fidelity gate on finished top previews and store the verdict on each job.

Kept apart from preview_jobs.py, whose source is frozen by the registered held-out protocol. The task
layer calls gate_finished() after a job reaches a terminal state; the gate itself never fails a job.
"""
import logging

from PIL import Image

from secondlook.colour import rgb_to_hex

from .assets import read_private_image
from .colour_check import preview_colour
from .fidelity_gate import print_check, stability_check, verdict
from .models import PreviewJob

log = logging.getLogger(__name__)


def measured_colour(person, image, colour_status):
    """The garment colour the colour check measured, as hex, for the card's swatch; "" when it measured none."""
    if colour_status not in ("close", "differs") or not person or not image:
        return ""
    try:
        colour, _ = preview_colour(person, image)
    except (OSError, ValueError, Image.DecompressionBombError):
        return ""
    return rgb_to_hex(colour) if colour else ""


def gate_finished(job_id):
    """Measure every finished top preview on the same photo as this job, and gate the alternatives.

    The print check needs the top bought drawn on the same photo, so when that baseline finishes after an
    alternative, the alternative is measured again here. Never raises.
    """
    try:
        job = PreviewJob.objects.select_related("owner_session__order_line", "variant", "person_asset").get(pk=job_id)
        if job.state != "succeeded" or job.look_id or job.variant.category != "tops" or not job.result_asset_id:
            return
        siblings = list(PreviewJob.objects.filter(owner_session=job.owner_session, person_asset=job.person_asset,
                                                  state="succeeded", result_asset__isnull=False, look__isnull=True,
                                                  variant__category="tops").select_related("variant", "result_asset"))
        person = read_private_image(job.person_asset.storage_key) if job.person_asset else None
        bought_id = job.owner_session.order_line.variant_id
        bought = next((s for s in siblings if s.variant_id == bought_id), None)
        bought_image = read_private_image(bought.result_asset.storage_key) if bought else None
        quieter = bool(job.owner_session.preferences.get("quieter_print"))
        for sibling in siblings:
            if sibling.variant_id == bought_id:
                if not sibling.gate_status:
                    stability = stability_check(person, read_private_image(sibling.result_asset.storage_key))
                    PreviewJob.objects.filter(pk=sibling.pk).update(outside_change_pct=stability.value,
                                                                    gate_status="reference", gate_note=stability.note)
                continue
            # Already measured, and nothing new to measure it against.
            if sibling.gate_status and (sibling.print_ratio is not None or not bought_image or not quieter):
                continue
            image = read_private_image(sibling.result_asset.storage_key)
            printed = print_check(person, bought_image, image) if quieter else None
            stability = stability_check(person, image)
            result = verdict(sibling.colour_check, printed.status if printed else "not_measured", stability.status,
                             quieter_asked=quieter)
            PreviewJob.objects.filter(pk=sibling.pk).update(
                print_ratio=printed.value if printed else None, outside_change_pct=stability.value,
                gate_status=result.status, gate_note=result.note,
                preview_colour=measured_colour(person, image, sibling.colour_check))
    except Exception:
        log.warning("Fidelity gate failed for job %s", job_id, exc_info=True)
