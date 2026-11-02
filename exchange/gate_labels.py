"""Blind labels for gate-study-v2: the items each rater sees, and how the gate's measurements compare.

Raters answer the registered questions (assets/gate_study_v2.json, "labels") from images alone: the photo,
the bought blouse drawn on the same photo, the replacement's product photo and the preview. They never see
the SKU, the catalog role or anything the gate measured. Items are shown in a fixed shuffled order.
"""
import json
import random
from pathlib import Path

PLAN = Path("assets/gate_study_v2.json")
SEED = 20261030
QUESTIONS = {"reference": ["outside"], "plain": ["print", "colour", "outside"], "repeat": ["print", "colour", "outside"],
             "busy": ["print", "outside"]}


def questions(root):
    return {q["key"]: q for q in json.loads((root / PLAN).read_text())["labels"]["questions"]}


def items(root, report, v1_report):
    """One item per succeeded render: public image paths only, keyed by case id, in the registered order."""
    v1_blouse = {r["model"]: r["result_image"] for r in v1_report["cases"] if r["sku"] == "ORIGINAL" and r.get("result_image")}
    blouse = {r["model"]: r["result_image"] for r in report["cases"]
              if r["role"] == "reference" and r["state"] == "succeeded"}
    out = []
    for row in report["cases"]:
        if row["state"] != "succeeded":
            continue
        images = {"photo": row["person_image"]}
        if row["role"] != "reference":
            bought = v1_blouse.get(row["model"]) if row["role"] == "repeat" else blouse.get(row["model"])
            if bought:
                images["bought"] = bought
            images["product"] = f"exchange/catalog/{row['sku']}.jpg"
        images["preview"] = row["result_image"]
        asked = [q for q in QUESTIONS[row["role"]] if q != "print" or "bought" in images]
        out.append({"id": row["id"], "images": images, "questions": asked})
    random.Random(SEED).shuffle(out)
    return out


MAPPING = {"print": {"quieter": "quieter", "similar": "not quieter", "busier": "not quieter"},
           "colour": {"close": "matches", "differs": "differs"},
           "outside": {"stable": "unchanged", "changed": "changed"}}


def gate_answer(row, question):
    """The gate's measurement in the raters' words, or None when the gate made no comparable call."""
    if question == "print":
        return MAPPING["print"].get((row.get("print") or {}).get("status"))
    if question == "colour":
        return MAPPING["colour"].get((row.get("colour") or {}).get("status"))
    return MAPPING["outside"].get(row["stability"]["status"])


def agreement(rows, labels):
    """Per question: how often the gate and this rater agree, over items both answered."""
    by_id = {r["id"]: r for r in rows}
    out = {}
    for question in ("print", "colour", "outside"):
        agree = answered = unclear = 0
        disagreements = []
        for item_id, answers in labels["answers"].items():
            if question not in answers or item_id not in by_id:
                continue
            gate = gate_answer(by_id[item_id], question)
            if answers[question] == "unclear":
                unclear += 1
                continue
            if gate is None:
                continue
            answered += 1
            if gate == answers[question]:
                agree += 1
            else:
                disagreements.append({"id": item_id, "gate": gate, "rater": answers[question]})
        out[question] = {"agree": agree, "answered": answered, "unclear": unclear, "disagreements": disagreements}
    return out
