"""Recorded YouCam results, replayed for the demo with no new AI run and no units.

The clothes pilot on 19 October 2026 produced real cloth-v4 results for ten model-and-top pairs.
The beauty pilot on 27 October 2026 produced Makeup VTO and Nail VTO results, three comparisons of
which are published, and the earrings pilot on 28 October 2026 one Earrings VTO comparison. The registered
gate study on 30 October 2026 rendered the blouse and three plain tops live on Model B and Model C; those
renders replay too, so anyone can watch the fidelity gate flag a real render without a judge code or units.
This provider returns the recorded result when a session asks for one of those pairs with the same demo
person, and refuses anything else. Every replayed image is labelled as a recorded result.

Tops are looked up by style code, so every size of a top shares its recorded result. Shades are
looked up by SKU, because every shade in a product line shares one style code. A look's steps are looked
up by the items added so far, so each step replays the image that followed it in the looks pilot.
"""
import json
from pathlib import Path

from django.contrib.staticfiles import finders

from secondlook.providers import FAILED, SUCCEEDED, PollResult, ProviderRejected, StartResult
from secondlook.shades import BEAUTY

EVIDENCE = "exchange/evidence/pilot.json"
BEAUTY_EVIDENCE = "exchange/evidence/beauty.json"
JEWELLERY_EVIDENCE = "exchange/evidence/jewellery.json"
LOOKS_EVIDENCE = "exchange/evidence/looks.json"
NECKLACE_EVIDENCE = "exchange/evidence/necklace.json"
HAIR_EVIDENCE = "exchange/evidence/hair.json"
ACCESSORIES_EVIDENCE = "exchange/evidence/accessories.json"
GATE_STUDY_EVIDENCE = "exchange/evidence/gate-study-v1/report.json"


def recorded(name=EVIDENCE):
    path = finders.find(name)
    return json.loads(Path(path).read_text()) if path else {"results": []}


def recorded_beauty():
    return recorded(BEAUTY_EVIDENCE)


def recorded_jewellery():
    return recorded(JEWELLERY_EVIDENCE)


def recorded_necklace():
    return recorded(NECKLACE_EVIDENCE)


def recorded_hair():
    return recorded(HAIR_EVIDENCE)


def recorded_accessories():
    return recorded(ACCESSORIES_EVIDENCE)


def recorded_gate_study():
    """The gate study's live renders, filed like pilot results: by demo person and top, image under evidence/."""
    report = recorded(GATE_STUDY_EVIDENCE)
    return {"results": [{**case, "result_file": case["result_image"].removeprefix("exchange/evidence/")}
                        for case in report.get("cases", []) if case.get("state") == "succeeded" and case.get("result_image")]}


def recorded_looks():
    return recorded(LOOKS_EVIDENCE)


def recorded_look_length(model, keys):
    """How many of these items, in order, a recorded look on this demo person covers; 0 when none."""
    # A recorded look's first steps are themselves a recorded look of fewer items.
    recorded = {(look["model"], tuple(look["keys"][:n])) for look in recorded_looks().get("results", [])
                if look["state"] == "succeeded" for n in range(1, len(look["keys"]) + 1)}
    for n in range(len(keys), 0, -1):
        if (model, tuple(keys[:n])) in recorded:
            return n
    return 0


def look_key(keys):
    """What a recorded look's step is filed under: the keys of the items added up to that step."""
    return "look:" + "+".join(keys)


def replay_key(variant):
    """What a recorded result is filed under: the SKU for a shade, the style code for a top."""
    return variant.sku if variant.category in BEAUTY else (variant.style_code or variant.sku)


class ReplayPreviewProvider:
    name = "replay"
    version = "pilots-recorded"
    units_per_job = 0

    def __init__(self):
        self.results = {(r["model"], r["sku"]): r for evidence in (recorded(), recorded_beauty(), recorded_jewellery(),
                                                                      recorded_necklace(), recorded_hair(),
                                                                      recorded_accessories())
                        for r in evidence["results"] if r["state"] == "succeeded"}
        # The pilots keep their own recordings; the gate study fills in pairs they never rendered.
        for r in recorded_gate_study()["results"]:
            self.results.setdefault((r["model"], r["sku"]), r)
        for look in recorded_looks()["results"]:
            if look["state"] == "succeeded":
                for n, step in enumerate(look["steps"]):
                    self.results[(look["model"], look_key(look["keys"][:n + 1]))] = step

    def start(self, job):
        from .models import PreviewJob
        record = PreviewJob.objects.select_related("person_asset", "variant", "look__person_asset").filter(
            pk=job.job_id).first()
        # A look's later steps start from the step before, so the demo person is the look's own photo.
        person = (record.look.person_asset if record.look_id else record.person_asset) if record else None
        note = person.license_or_consent if person else ""
        model = note.split()[3] if note.startswith("AI-generated demo model ") else ""
        if record and record.look_id:
            key = look_key([entry["key"] for entry in record.look.plan[:record.look_step + 1]])
        else:
            key = replay_key(record.variant) if record else job.sku
        if (model, key) not in self.results:
            raise ProviderRejected("NOT_RECORDED")
        return StartResult(f"replay:{model}:{key}", 0)

    def poll(self, task_id):
        _, model, style = task_id.split(":", 2)
        record = self.results.get((model, style))
        path = finders.find(f"exchange/evidence/{record['result_file']}") if record else None
        if not path:
            return PollResult(FAILED, 0, "NOT_RECORDED")
        return PollResult(SUCCEEDED, 0, image=Path(path).read_bytes())
