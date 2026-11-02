"""Run the fidelity gate over every published top render, from public files only; no database or network.

The development renders set the print threshold, so this is a retrospective check of the gate's behaviour
on known images, not an accuracy study. The live studies are the unseen tests: gate-study-v1 rendered plain
tops on two people; gate-study-v2 adds four new people, patterned sweaters registered as tops it should catch, and repeats
of the v1 colour flags. Blind labels for v2, when published, are compared with the gate here.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

from . import fidelity_gate as gate
from . import gate_labels
from .colour_check import check_preview

EVIDENCE = Path("exchange/static/exchange/evidence")
REPORT = EVIDENCE / "gate-v1.json"
CODE = ("exchange/fidelity_gate.py", "exchange/colour_check.py", "exchange/gate_evaluation.py", "exchange/gate_labels.py")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def catalog(root):
    """Pattern level and reference colour per SKU, from the catalog as seeded; no database needed."""
    from .management.commands.seed_demo import TOPS
    colours = json.loads((root / "assets/reference_colours.json").read_text())
    reviewed = {sku: row["hex"] for sku, row in colours["colours"].items() if row.get("status") == "reviewed"}
    return {sku: {"pattern_level": pattern, "title": title, "reference_colour": reviewed.get(sku, "")}
            for sku, title, _price, pattern, *_rest in TOPS}


def load_cases(root):
    """Every succeeded top render with its source photo: the clothes pilot, the held-out study and the bag probe."""
    models = {m["name"]: m for m in json.loads((root / "exchange/static/exchange/models/manifest.json").read_text())["models"]}
    cases = []
    pilot = json.loads((root / EVIDENCE / "pilot.json").read_text())
    for row in pilot["results"]:
        if row["state"] != "succeeded":
            continue
        cases.append({"id": f"pilot-{row['pair']:02d}", "split": "development", "model": row["model"], "sku": row["sku"],
                      "person_image": models[row["model"]]["static_path"], "result_image": "exchange/evidence/" + row["result_file"]})
    heldout = json.loads((root / EVIDENCE / "heldout-v1/report.json").read_text())
    for row in heldout["cases"]:
        if row["state"] != "succeeded":
            continue
        cases.append({"id": row["id"], "split": "held_out", "model": row["model"], "sku": row["sku"],
                      "person_image": row["person_image"], "result_image": row["result_image"]})
    study = root / EVIDENCE / "gate-study-v1/report.json"
    if study.exists():
        for row in json.loads(study.read_text())["cases"]:
            if row["state"] != "succeeded":
                continue
            cases.append({"id": row["id"], "split": "live_study", "model": row["model"], "sku": row["sku"],
                          "person_image": row["person_image"], "result_image": row["result_image"]})
    study2 = root / EVIDENCE / "gate-study-v2/report.json"
    if study2.exists():
        v1 = {(c["model"], c["sku"]): c["id"] for c in cases if c["split"] == "live_study"}
        for row in json.loads(study2.read_text())["cases"]:
            if row["state"] != "succeeded":
                continue
            case = {"id": row["id"], "split": "live_study_v2", "model": row["model"], "sku": row["sku"],
                    "study_role": row["role"], "person_image": row["person_image"], "result_image": row["result_image"]}
            if row["role"] == "repeat":
                # A repeat is measured against the v1 blouse render on the same photo, beside the v1 render it repeats.
                case.update({"split": "repeat", "bought_split": "live_study", "repeat_of": v1.get((row["model"], row["sku"]))})
            cases.append(case)
    cases.append({"id": "bag-probe", "split": "development", "model": "model-a", "sku": "BAG-RED-HOBO",
                  "person_image": models["model-a"]["static_path"],
                  "result_image": "exchange/evidence/bag-probe-model-a-BAG-RED-HOBO.jpg", "known_failure": True})
    return cases


def measure(root, cases):
    items = catalog(root)
    static = root / "exchange/static"
    images = {}
    for case in cases:
        images[case["id"]] = ((static / case["person_image"]).read_bytes(), (static / case["result_image"]).read_bytes())
    bought = {(c["split"], c["model"]): images[c["id"]][1] for c in cases if c["sku"] == "ORIGINAL"}
    out = []
    for case in cases:
        person, result = images[case["id"]]
        row = {**case, "person_sha256": digest(person), "result_sha256": digest(result)}
        stability = gate.stability_check(person, result)
        row["stability"] = {"status": stability.status, "outside_change_pct": stability.value}
        if case.get("known_failure"):
            row["title"] = "Red slouch shoulder bag (rejected probe)"
            row["verdict"] = "concern" if stability.status == "changed" else "missed"
            row["note"] = ("The gate flags the probe that redrew the whole scene." if stability.status == "changed"
                           else "The gate did not flag the known failure.")
            out.append(row)
            continue
        item = items[case["sku"]]
        row["title"] = item["title"]
        row["pattern_level"] = item["pattern_level"]
        colour = check_preview(person, result, item["reference_colour"], item["pattern_level"])
        row["colour"] = {"status": colour.status, "delta": colour.delta}
        if case["sku"] == "ORIGINAL":
            row["role"] = "reference"
            row["busyness"] = gate.busyness(person, result)
            out.append(row)
            continue
        row["role"] = "alternative"
        printed = gate.print_check(person, bought.get((case.get("bought_split", case["split"]), case["model"])), result)
        row["busyness"] = gate.busyness(person, result)
        row["print"] = {"status": printed.status, "ratio": printed.value}
        verdict = gate.verdict(colour.status, printed.status, stability.status, quieter_asked=True)
        row["verdict"], row["note"] = verdict.status, verdict.note
        # What the catalog says, so a reader can see where the print measurement and the catalog agree.
        row["catalog_quieter"] = item["pattern_level"] == 0
        out.append(row)
    return out


def share(n, d):
    return {"n": n, "of": d}


def summarize_v2(rows):
    """The registered outcomes of gate-study-v2, in the order the protocol lists them."""
    study = [r for r in rows if r["split"] == "live_study_v2"]
    if not study:
        return None
    plain = [r for r in study if r.get("study_role") == "plain"]
    busy = [r for r in study if r.get("study_role") == "busy"]
    references = {r["model"] for r in study if r.get("study_role") == "reference"}
    plain_ref = [r for r in plain if r["model"] in references]
    busy_ref = [r for r in busy if r["model"] in references]
    repeats = {}
    for r in rows:
        if r["split"] == "repeat":
            first = next((c for c in rows if c["id"] == r["repeat_of"]), None)
            entry = repeats.setdefault(r["repeat_of"], {"model": r["model"], "sku": r["sku"],
                                                         "first": first and first["colour"], "repeats": []})
            entry["repeats"].append({"id": r["id"], **r["colour"]})
    for entry in repeats.values():
        entry["flag_recurs_in_every_repeat"] = all(x["status"] == "differs" for x in entry["repeats"])
    return {
        "people": sorted({r["model"] for r in study}),
        "renders": len(study),
        "busy_not_shows": share(sum(r["verdict"] != "shows" for r in busy_ref), len(busy_ref)),
        "busy_print_above_quiet_ratio": share(sum((r["print"]["ratio"] or 0) > gate.QUIET_RATIO for r in busy_ref
                                                  if r["print"]["status"] != "not_measured"), len(busy_ref)),
        "plain_print_quieter": share(sum(r["print"]["status"] == "quieter" for r in plain_ref), len(plain_ref)),
        "plain_shows": share(sum(r["verdict"] == "shows" for r in plain), len(plain)),
        "plain_colour_close": share(sum(r["colour"]["status"] == "close" for r in plain), len(plain)),
        "renders_stable": share(sum(r["stability"]["status"] == "stable" for r in study), len(study)),
        "verdicts": {v: sum(1 for r in plain + busy if r["verdict"] == v) for v in ("shows", "check", "concern")},
        "repeats": list(repeats.values()),
    }


def summarize(rows):
    alternatives = [r for r in rows if r.get("role") == "alternative"]
    measured = [r for r in alternatives if r["print"]["status"] != "not_measured"]
    agree = [r for r in measured if (r["print"]["status"] == "quieter") == r["catalog_quieter"]]
    probe = next((r for r in rows if r.get("known_failure")), None)
    live = [r for r in alternatives if r["split"] == "live_study"]
    return {
        "live_study": {"alternatives": len(live), "shows": sum(1 for r in live if r["verdict"] == "shows"),
                       "print_quieter": sum(1 for r in live if r["print"]["status"] == "quieter"),
                       "print_measured": sum(1 for r in live if r["print"]["status"] != "not_measured"),
                       "colour_close": sum(1 for r in live if r["colour"]["status"] == "close"),
                       "stable": sum(1 for r in rows if r["split"] == "live_study" and r["stability"]["status"] == "stable"),
                       "renders": sum(1 for r in rows if r["split"] == "live_study")} if live else None,
        "live_study_v2": summarize_v2(rows),
        "renders": len(rows) - (1 if probe else 0),
        "references": sum(1 for r in rows if r.get("role") == "reference"),
        "alternatives": len(alternatives),
        "verdicts": {v: sum(1 for r in alternatives if r["verdict"] == v) for v in ("shows", "check", "concern")},
        "print_measured": len(measured),
        "print_agrees_with_catalog": len(agree),
        "print_not_measured": len(alternatives) - len(measured),
        "colour_concerns": sum(1 for r in alternatives if r["colour"]["status"] == "differs"),
        "stability_measured": sum(1 for r in rows if r["stability"]["status"] != "not_measured"),
        "stability_changed": sum(1 for r in rows if r["stability"]["status"] == "changed" and not r.get("known_failure")),
        "known_failure_flagged": bool(probe and probe["verdict"] == "concern"),
    }


def label_sets(root):
    """Every published set of blind labels for gate-study-v2, with its file."""
    return [(path, json.loads(path.read_text())) for path in sorted((root / EVIDENCE / "gate-study-v2").glob("labels-*.json"))]


def labels(root, rows):
    """Agreement between the gate and each published set of blind labels for gate-study-v2."""
    out = []
    for path, data in label_sets(root):
        out.append({"rater": data["rater"], "model": data.get("model"), "kind": data.get("kind", "ai"),
                    "independent": data.get("independent", False),
                    "file": f"exchange/evidence/gate-study-v2/{path.name}",
                    "sha256": digest(path.read_bytes()), "items": len(data["answers"]),
                    "agreement": gate_labels.agreement(rows, data)})
    return out


def human_consensus(rows, sets):
    """The answer most independent people gave on each item and question, and how often the gate agrees with it.

    Only people who did not build the gate count (assets/gate_labels_addendum.json). A tie, or a majority for
    unclear, gives no answer for that item; both are counted so nothing drops out unseen.
    """
    people = [data for data in sets if data.get("kind") == "human" and data.get("independent")]
    if len(people) < 2:
        return None
    majority, ties, unclear = {}, 0, 0
    for item_id in sorted(set().union(*(p["answers"] for p in people))):
        for question in ("print", "colour", "outside"):
            votes = Counter(p["answers"].get(item_id, {}).get(question) for p in people)
            votes.pop(None, None)
            if not votes:
                continue
            ranked = votes.most_common()
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                ties += 1
            elif ranked[0][0] == "unclear":
                unclear += 1
            else:
                majority.setdefault(item_id, {})[question] = ranked[0][0]
    return {"raters": len(people), "ties": ties, "unclear": unclear,
            "agreement": gate_labels.agreement(rows, {"answers": majority})}


def build(root):
    rows = measure(root, load_cases(root))
    sets = [data for _, data in label_sets(root)]
    humans = [data for data in sets if data.get("kind") == "human"]
    owner_note = ("The protocol named the owner as a second, human rater; the owner chose not to label, so only the AI rater is reported."
                  if not humans else
                  f"{len(humans)} people labelled the same items blind after the AI labels were published, under the "
                  "addendum in assets/gate_labels_addendum.json; it adds raters and changes nothing else in the study.")
    return {
        "version": gate.VERSION,
        "thresholds": {"colour_delta": 10.0, "quiet_ratio": gate.QUIET_RATIO, "not_quieter_ratio": gate.NOT_QUIETER_RATIO,
                       "garment_band": list(gate.GARMENT_BAND), "pixel_change": gate.CHANGE_THRESHOLD, "stable_pct": gate.STABLE_PCT},
        "threshold_origin": "The print bound was chosen on these development renders: plain tops scored 0.11 to 0.42 of the "
                            "bought blouse's edge detail and patterned tops 0.50 to 0.70. The stability box and pixel threshold "
                            "are the held-out study's frozen values. This report is therefore not an unseen test of the gate.",
        "code_sha256": {name: digest((root / name).read_bytes()) for name in CODE},
        "summary": summarize(rows),
        "labels": labels(root, rows),
        "human_raters": len(humans),
        "human_consensus": human_consensus(rows, sets),
        "cases": rows,
        "limits": [
            "Print busyness is mean edge strength inside the garment area; it is not a judgement of taste or a pattern classifier.",
            "The stability measurement uses a fixed garment band; hands, hair and shadows inside the band are not checked.",
            "Sleeves and neckline are not measured; they remain catalog facts.",
            "A verdict of shows means the three measurements agree with the request; it does not establish fit, fabric or exact colour.",
            "Colour is compared with the catalog colour without correcting for the light in the photo. In gate-study-v2 the blind "
            "AI rater accepted all ten renders the colour check flagged, allowing for the photo's lighting.",
            "Print busyness also counts construction detail. The utility shirt's pockets, buttons and seams read as not quieter "
            "on all four new photos, where the blind AI rater saw a plain shirt.",
            "The two patterned sweaters registered as tops that should fail were weak negatives: a yoke band and broad stripes are "
            "less busy than the dense floral, and the blind AI rater judged all eight quieter. A live test against a top as busy "
            "as the blouse is still to be run.",
            "On the full-length photo Clothes V4 also redrew trousers and waistlines in some renders; the stability band flagged "
            "seven of its eight renders, at 3.0 to 4.5 percent, including the blouse itself.",
            owner_note,
        ],
    }


def check(root):
    """True when the committed report matches a fresh run of the same code on the same files."""
    fresh = build(root)
    saved = json.loads((root / REPORT).read_text())
    return fresh == saved
