"""The published gate verdicts, grouped by what each render was meant to show, for the evaluation headline.

The gate report (evidence/gate-v1.json) counts every alternative render together: development renders the
print bound was set on, tops registered to fail, and repeat renders of earlier flags. Each group answers a
different question, so the headline keeps them apart. Tops that should pass, rendered after the thresholds were
fixed, ask whether a good preview gets through; tops registered to fail ask whether a bad one is held back;
repeats ask whether a flag comes back. This reads the published cases only and measures nothing itself.
"""

# Only the registered gate studies were rendered after this gate's thresholds were fixed.
# The earlier photo holdout remains held out for its original colour protocol, but its images
# were among the 16 known renders used to develop this later gate.
UNSEEN = ("live_study", "live_study_v2")


def _reasons(rows):
    """How many held-back renders each check flagged; one render can be flagged by more than one."""
    counts = {"colour": 0, "print": 0, "redrawn": 0}
    for row in rows:
        if (row.get("colour") or {}).get("status") == "differs":
            counts["colour"] += 1
        if (row.get("print") or {}).get("status") in ("similar", "busier"):
            counts["print"] += 1
        if row["stability"]["status"] == "changed":
            counts["redrawn"] += 1
    return counts


def outcomes(report):
    alternatives = [c for c in report.get("cases", []) if c.get("role") == "alternative"]
    unseen = [c for c in alternatives if c["split"] in UNSEEN]
    should_pass = [c for c in unseen if c.get("catalog_quieter")]
    built_to_fail = [c for c in unseen if not c.get("catalog_quieter")]
    held = [c for c in should_pass if c["verdict"] != "shows"]
    repeats = [c for c in alternatives if c["split"] == "repeat"]
    development = [c for c in alternatives if c["split"] in ("development", "held_out")]
    return {
        "people": len({c["model"] for c in unseen}),
        "should_pass": {"n": len(should_pass), "passed": len(should_pass) - len(held), "held": len(held),
                        "reasons": _reasons(held)},
        "built_to_fail": {"n": len(built_to_fail), "held": sum(1 for c in built_to_fail if c["verdict"] != "shows")},
        "repeats": {"n": len(repeats), "flagged": sum(1 for c in repeats if c["verdict"] == "concern")},
        "development": {"n": len(development), "verdicts": {v: sum(1 for c in development if c["verdict"] == v)
                                                           for v in ("shows", "check", "concern")}},
    }
