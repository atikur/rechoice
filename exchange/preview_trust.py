"""Explain a preview without turning a catalog fact or an uncalibrated reading into image proof."""


def money(minor, currency):
    return f"{currency} {minor // 100}.{minor % 100:02d}"


def requested_changes(session):
    """Confirmed controls only; never the shopper's free text."""
    data = session.preferences
    category = session.order_line.variant.category
    asked = []
    if data.get("quieter_print"):
        asked.append("A quieter print")
    for key, label in (("sleeve_required", "Sleeves"), ("neckline_preference", "Neckline"),
                       ("lightness", "Lightness"), ("tone", "Tone"), ("intensity", "Intensity")):
        if data.get(key):
            asked.append(f"{label}: {data[key]}")
    if data.get("color_preferences"):
        asked.append("Colours: " + ", ".join(data["color_preferences"]))
    size = data.get("size")
    if size:
        labels = {"shoes": {"smaller": "Lower heel", "larger": "Higher heel"},
                  "necklace": {"smaller": "Shorter necklace", "larger": "Longer necklace"}}
        asked.append(labels.get(category, {}).get(size, f"Size: {size}"))
    if data.get("metal"):
        label = "Colour family" if category in ("shoes", "bag") else "Metal"
        asked.append(f"{label}: {data['metal']}")
    if "max_top_up_minor" in data:
        asked.append("Maximum extra: " + money(data["max_top_up_minor"], data["currency"]))
    return asked


def look_trust(session, items, *, recorded, visible):
    """A stage's measurements must never be presented as proof about the final composite."""
    return {"summary": "The combined image has not been automatically verified", "attention": False,
            "eligibility": "not_rechecked", "requested": requested_changes(session),
            "catalog_facts": ["Items added in order: " + "; ".join(item["title"] for item in items),
                              "These are catalog item names, not a detection of what appears in the final image. Review a current quote before choosing."],
            "checks": [{"label": "Combined look", "status": "not_measured",
                        "note": "Checks on an earlier step do not verify the final image. Each new step can change an earlier item."
                                if visible else "No current combined preview image is available."}],
            "limits": ["The final outfit, makeup and jewellery combination has not been automatically checked for fidelity.",
                       "Unrelated changes to the person, pose or background are not automatically verified.",
                       "The image cannot prove physical fit, fabric feel or exact colour."],
            "source": "Recorded YouCam steps; no new render or units used." if recorded else
                      "See the individual step labels for how this look was made.",
            "evaluation_url": "/evaluation"}


def preview_trust(job, *, visible, baseline, candidate=None, corrected=False):
    item = job.variant
    facts = []
    if baseline:
        facts.append("The item bought: the reference for this comparison, not a replacement recommendation.")
        eligibility = "reference"
    elif candidate is not None:
        reasons = [reason for reason in candidate.reasons if not reason.startswith(item.currency + " ")]
        if reasons:
            facts.append("; ".join(reasons))
        facts.append("Informational extra cost: " + money(candidate.top_up_minor, item.currency))
        facts.append("In the current eligible shortlist: stock, policy and your confirmed controls were rechecked.")
        eligibility = "eligible"
    else:
        facts.append("Not in the current eligible shortlist. Refresh your options before choosing.")
        eligibility = "refresh"
    if item.shade_hex:
        facts.append(f"Catalog shade: {item.shade_name}, {item.shade_hex}; {item.finish} finish.")
    elif item.category in ("earrings", "necklace"):
        length = f"{item.length_mm} mm" if item.category == "earrings" else f"{item.length_mm / 10:g} cm"
        facts.append(f"Catalog: {item.color_tag} {item.form}, {length} long.")
    elif item.category == "shoes":
        facts.append(f"Catalog: {item.color_tag} {item.form}, heel {item.length_mm} mm.")
    elif item.category == "tops":
        facts.append(f"Catalog: {item.color_tag}, {item.sleeve} sleeves, {item.neckline} neckline.")

    checks = []
    attention = False
    if visible:
        if item.category == "tops" and job.colour_check in ("close", "differs"):
            reviewed = item.reference_colour_status == "reviewed"
            checks.append({"label": "Garment colour", "status": job.colour_check if reviewed else "unreviewed",
                           "note": job.colour_note if reviewed else
                           "The catalog reference colour has not been reviewed; this is not a verified colour match.",
                           "value": f"ΔE {job.colour_delta:g}" if job.colour_delta is not None else None})
            attention = job.colour_check == "differs" and reviewed
        elif job.colour_check == "pattern":
            checks.append({"label": "Garment colour", "status": "not_measured",
                           "note": "A patterned garment has no single colour score. Compare its details with the product photo."})
        if item.category == "tops" and not baseline and job.gate_status and job.gate_status != "reference":
            asked_quieter = bool(job.owner_session.preferences.get("quieter_print"))
            if asked_quieter:
                ratio = job.print_ratio
                checks.append({"label": "Print, against the top you bought", "status": "measured" if ratio is not None else "not_measured",
                               "note": ("Quieter: " if ratio is not None and ratio <= 0.5 else "Not quieter: " if ratio is not None else "")
                                       + (f"{ratio:g}× the bought top's edge detail on the same photo." if ratio is not None
                                          else "the top you bought was not previewed on this photo, so the print was not compared."),
                               "value": None})
                attention = attention or (ratio is not None and ratio > 0.5)
            pct = job.outside_change_pct
            checks.append({"label": "Outside the garment", "status": "stable" if pct is not None and pct <= 3 else "changed" if pct is not None else "not_measured",
                           "note": (f"{pct:g}% of the image outside the garment band changed." if pct is not None
                                    else "The preview's shape differs from the photo, so the change outside the garment was not measured."),
                           "value": None})
            attention = attention or (pct is not None and pct > 3)
        if job.size_check:
            missing_correction = job.size_check == "size-resized" and not corrected
            checks.append({"label": "Relative rendered size",
                           "status": "correction_unavailable" if missing_correction else job.size_check,
                           "note": "The corrected copy is unavailable. Showing YouCam's original render; its relative size is not verified."
                                   if missing_correction else job.size_note})
            attention = attention or job.size_check in ("size-differs", "size-resized")
        if item.shade_hex:
            checks.append({"label": "Applied shade", "status": "uncalibrated",
                           "note": "The image shade check is not calibrated for shoppers. Use the catalog swatch; no colour-match verdict is given."})
    if not checks:
        checks.append({"label": "Image checks", "status": "not_measured",
                       "note": "No measured image verdict is available for this preview." if visible else
                       "No current preview image is available, so image checks are not shown."})

    limits = ["The image cannot prove physical fit, fabric feel or exact colour.",
              "Unrelated changes to the person, pose or background are not automatically verified for this preview."]
    if item.category == "tops":
        limits.append("Sleeve and neckline matches come from catalog facts; the render's sleeves and neckline are not automatically measured."
                      if job.gate_status and job.gate_status != "reference" else
                      "Print, sleeve and neckline matches come from catalog facts; those details are not automatically verified in the render.")
    if item.category in ("earrings", "necklace"):
        limits.append("A photo has no ruler: the size check compares proportions with the item bought, not absolute length on your body.")
    if item.category == "shoes":
        limits.append("The catalog heel height is not a measurement of the rendered shoe.")
    if corrected and visible:
        limits.append("The displayed earring was resized by Rechoice. Use the original-render switch to compare with YouCam's output.")
    source = ("Recorded YouCam result; no new render or units used." if job.provider == "replay" else
              "Mock provider; no YouCam image was generated." if job.provider == "mock" else
              "Generated through the preview provider; image checks have the limits below.")
    statuses = {check["status"] for check in checks}
    summary = ("This preview does not show what you asked for" if job.gate_status == "concern" and visible else
               "Size corrected; inspect both versions" if "size-resized" in statuses else
               "Preview colour differs from the product photo" if "differs" in statuses else
               "Size needs your attention" if attention else
               "Colour measured against the product photo" if "close" in statuses else
               "No calibrated shade verdict" if "uncalibrated" in statuses else
               "What has and has not been checked")
    return {"summary": summary,
            "attention": attention, "eligibility": eligibility, "requested": requested_changes(job.owner_session),
            "catalog_facts": facts, "checks": checks, "limits": limits, "source": source,
            "evaluation_url": "/evaluation"}
