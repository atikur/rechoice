"""Claude writing from facts: a comparison of the options for the shopper, and a draft message for staff.

Both are written only from facts Rechoice already holds: what the shopper asked for (the confirmed
controls, never their own words), the store's catalog details, prices and policy, and the checks run on
the previews. Claude never sees a photo, a preview or anything about the person, and is told not to
describe how anything looks on them. Every reply is checked before it is shown: only the options given,
only numbers that appear in the facts, no words about the person's body, skin or face, and plain text
within length. Anything that fails, and anything past the assistant's limits, falls back to the rules'
version, built from the same facts. Staff always edit or send the message themselves.
"""
import json
import logging
import re

from django.conf import settings

from . import assistant
from .models import EvidenceEvent

log = logging.getLogger(__name__)

COMPARE, STAFF_NOTE = "assistant_compare", "assistant_staff_note"
# Claude may describe the items and the facts, never the person: no inference from appearance.
PERSONAL = re.compile(r"\b(skin|undertone|complexion|face shape|your face|your body|body shape|figure|"
                      r"flatter\w*|slimming|your hair colour|ethnic\w*|age\b|older|younger|weight)\b", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
COMPARE_LIMIT, NOTE_LIMIT, MESSAGE_LIMIT = 600, 180, 500
# What the store tells every shopper, so a text that repeats it is not making it up.
STORE_NOTES = ["Every option meets the shopper's hard limits; a colour or neckline wish only orders them.",
               "A try-on preview shows how an item looks, not fit, exact colour or feel."]
DECISIONS = {"REVIEWED": "approved by staff", "DECLINED": "declined by staff",
             "NEEDS_CLARIFICATION": "staff need to ask the shopper a question first"}
STAFF_PROCESS = ("After an approval or a decline, staff email the shopper the return instructions. "
                 "Rechoice itself never changes an order, stock or money.")

COMPARE_INSTRUCTIONS = """You help a shopper who is returning {item} to a fictional online store choose between the \
exchange options the store found. You get facts in JSON: what the shopper asked for, the item they bought, each \
option's catalog details and price difference, and the checks the store ran on the try-on previews.

Write for the shopper, in plain, warm English:
- "summary": two or three sentences on how the options differ against what they asked for, and which matches \
their request most closely and why. If the facts don't separate them, say so.
- "notes": one sentence per option, under 180 characters, saying what changes from the item bought.

Rules:
- Use only the facts given. Never invent a detail, a number or an option, and don't draw on outside \
knowledge, such as what a shade number means. Quote prices exactly as given.
- Never describe the shopper or how anything looks on them: no skin, face, body, hair, age or "flattering". \
You have not seen any photo and must not imply you have.
- A preview shows how an item looks, not fit, exact colour or feel; don't promise any of those.
- If a check says a preview was resized or may differ, mention it for that option.
- No markdown, no lists, no emoji."""

STAFF_INSTRUCTIONS = """You draft a short message from a fictional online store's staff to a shopper about their \
exchange request. You get the request's facts in JSON and the decision staff are about to make.

- When it is approved: confirm the exchange plainly: the item going back, the replacement and any top-up or credit, \
exactly as given. Say staff will send return instructions.
- When it is declined: explain briefly and kindly, using only the reason in the facts if there is one, and say the \
normal return is still open.
- When staff need to ask a question: say what is proposed, then ask one clear question about it that the facts \
leave open. Don't ask about, or offer, anything the facts don't mention, and don't say the exchange is agreed.

Rules: use only the facts; never invent stock, dates, prices or policies. Never comment on the shopper's \
appearance. Under 500 characters, plain text, no greeting name, no signature, no markdown."""


def _schema_compare(skus):
    return {"type": "object", "additionalProperties": False, "required": ["summary", "notes"], "properties": {
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                             "required": ["sku", "note"],
                                             "properties": {"sku": {"type": "string", "enum": skus},
                                                            "note": {"type": "string"}}}}}}


SCHEMA_NOTE = {"type": "object", "additionalProperties": False, "required": ["message"],
               "properties": {"message": {"type": "string"}}}


def _money(cents, currency="USD"):
    return f"{currency} {cents / 100:.2f}"


def wishes(preferences):
    """The shopper's confirmed controls, in words. Their own note is never included."""
    p, words = preferences or {}, []
    for key, phrase in (("lightness", "{} shade"), ("tone", "{} tone"), ("intensity", "{} intensity"),
                        ("size", "{}"), ("metal", "{}")):
        if p.get(key):
            words.append(phrase.format(p[key]))
    if p.get("sleeve_required"):
        words.append(f"{p['sleeve_required']} sleeves")
    if p.get("quieter_print"):
        words.append("a quieter print")
    if p.get("color_preferences"):
        words.append(" or ".join(p["color_preferences"]))
    if p.get("neckline_preference"):
        words.append(f"a {p['neckline_preference']} neckline")
    budget = p.get("max_top_up_minor")
    words.append("nothing extra to pay" if budget == 0 else f"at most {_money(budget)} extra" if budget else "any price")
    return words


def item_facts(variant):
    """What the catalog says about an item, in words: what the texts may describe it by."""
    if variant.shade_hex:
        return [f"shade {variant.shade_name}"] + ([f"{variant.finish} finish"] if variant.finish else [])
    if variant.category in ("earrings", "necklace"):
        return [f"{variant.color_tag} metal", f"{variant.form} style", f"{variant.length_mm} mm long"]
    if variant.category == "shoes":
        return [f"{variant.color_tag} colour", f"{variant.form} style", f"heel {variant.length_mm} mm"]
    if variant.category == "bag":
        return [f"{variant.color_tag} colour", f"{variant.form} style", f"{variant.length_mm} mm wide"]
    pattern = {0: "quiet", 1: "moderate", 2: "busy"}.get(variant.pattern_level)
    return ([f"{variant.sleeve} sleeves"] if variant.sleeve else []) + ([f"{variant.neckline} neckline"] if variant.neckline else []) \
        + ([f"{pattern} print"] if pattern else []) + ([f"{variant.color_tag} colour"] if variant.color_tag else [])


def compare_facts(session, candidates, items, jobs):
    """Everything the comparison may use, as plain data."""
    line = session.order_line
    checks = {}
    for job in jobs:
        notes = list(dict.fromkeys(note for note in (
            job.gate_note, job.size_note, job.colour_note if not job.colour_check.startswith("shade-") else "") if note))
        if notes:
            checks[job.variant.sku] = notes
    return {
        "category": line.variant.category,
        "asked_for": wishes(session.preferences),
        "bought": {"title": line.variant.title, "details": item_facts(line.variant),
                   "price": _money(line.paid_minor, line.currency), "opened": line.opened,
                   "checks": checks.get(line.variant.sku, [])},
        "options": [{"sku": c.variant.sku, "title": items[c.variant.sku].title,
                     "details": item_facts(items[c.variant.sku]), "compared_with_bought": list(c.reasons),
                     "extra_to_pay": _money(c.top_up_minor, items[c.variant.sku].currency),
                     "checks": checks.get(c.variant.sku, [])} for c in candidates],
        "store_notes": STORE_NOTES,
    }


def rules_compare(facts):
    notes = {o["sku"]: f"{o['title']}: " + ", ".join(o["compared_with_bought"]) + "." for o in facts["options"]}
    cost = lambda o: float(o["extra_to_pay"].split()[1])  # noqa: E731
    lowest = min(cost(o) for o in facts["options"])
    cheapest = [o for o in facts["options"] if cost(o) == lowest]
    count = len(facts["options"])
    if count == 1:
        price = f"It costs {cheapest[0]['extra_to_pay']} extra."
    elif len(cheapest) == count:
        price = f"{'Both' if count == 2 else 'All'} cost {cheapest[0]['extra_to_pay']} extra."
    else:
        price = f"The lowest extra cost is {cheapest[0]['extra_to_pay']}, for " + " and ".join(o["title"] for o in cheapest) + "."
    summary = (f"{count} option{'s fit' if count != 1 else ' fits'} your limits; you asked for "
               + ", ".join(facts["asked_for"]) + f". {price} A preview shows how an item looks, not fit, exact colour or feel.")
    return {"summary": summary, "notes": notes}


def staff_facts(draft):
    line, quote, session = draft.owner_session.order_line, draft.quote, draft.owner_session
    return {
        "returned": line.variant.title, "returned_details": item_facts(line.variant),
        "replacement": quote.variant.title, "replacement_details": item_facts(quote.variant),
        "kind": "another size" if quote.kind == "size" else "a different item",
        # The shopper's reason for sending the item back, never staff's reason for a decision.
        "shopper_return_reason": session.return_reason or "style",
        "asked_for": wishes(session.preferences) if quote.kind != "size" else [],
        "credit": _money(quote.credit_minor, quote.currency), "replacement_price": _money(quote.replacement_minor, quote.currency),
        "top_up": _money(quote.top_up_minor, quote.currency), "fee": _money(quote.fee_minor, quote.currency),
        "shopper_reply": draft.shopper_reply,
        "process": STAFF_PROCESS,
    }


def rules_staff_note(facts, state):
    if state == "REVIEWED":
        extra = "" if facts["top_up"].endswith(" 0.00") else f" You'll pay {facts['top_up']} extra."
        return (f"Your exchange is approved: {facts['returned']} goes back and {facts['replacement']} comes to you."
                f"{extra} We'll email return instructions.")
    if state == "DECLINED":
        return ("We can't make this exchange. The normal return is still open to you; "
                "we'll email the instructions if you'd like to use it.")
    return f"Before we confirm {facts['replacement']}, is there anything about it you'd like us to check?"


def _numbers(text):
    """The numbers in a text, as values, so "$2" matches "USD 2.00" and "1,000" matches "1000"."""
    return {float(n.replace(",", "")) for n in NUMBER.findall(text)}


def _clean(text, limit):
    text = re.sub(r"[*_`#>]+", "", str(text or "")).strip()
    return " ".join(text.split())[:limit]


def check_text(text, facts):
    """Why a written text can't be shown, or "" if it can: no person words, and only numbers from the facts.

    The facts never describe the person, so a person word inside a phrase the facts use is the store's own
    wording, such as a foundation called Skin Veil, and is allowed; "your skin" is not.
    """
    known = json.dumps(facts).lower()
    for match in PERSONAL.finditer(text):
        before = re.findall(r"(\w+)\s+$", text[:match.start()])
        after = re.findall(r"^\s+(\w+)", text[match.end():])
        phrases = [f"{word} {match.group(0)}" for word in before] + [f"{match.group(0)} {word}" for word in after]
        if not any(phrase.lower() in known for phrase in phrases):
            return "mentions the person"
    unknown = _numbers(text) - _numbers(json.dumps(facts))
    if unknown:
        return "number not in the facts"
    return ""


def accept_compare(data, facts):
    if not isinstance(data, dict):
        raise assistant.Unusable("unreadable reply")
    summary = _clean(data.get("summary"), COMPARE_LIMIT)
    skus = {o["sku"] for o in facts["options"]}
    notes = {}
    for item in data.get("notes") or []:
        if isinstance(item, dict) and item.get("sku") in skus:
            notes[item["sku"]] = _clean(item.get("note"), NOTE_LIMIT)
    if not summary or set(notes) != skus or not all(notes.values()):
        raise assistant.Unusable("incomplete reply")
    for text in (summary, *notes.values()):
        if problem := check_text(text, facts):
            raise assistant.Unusable(problem)
    return {"summary": summary, "notes": notes}


def accept_note(data, facts):
    message = _clean(data.get("message") if isinstance(data, dict) else "", MESSAGE_LIMIT)
    if not message:
        raise assistant.Unusable("incomplete reply")
    if problem := check_text(message, facts):
        raise assistant.Unusable(problem)
    return message


def ask(system, facts, schema, model=None):
    """One structured call, in the assistant's shape: the JSON reply and its (input, output) tokens."""
    response = assistant.make_client().messages.create(
        model=model or settings.ASSISTANT_MODEL, max_tokens=settings.ASSISTANT_MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": "The facts:\n<facts>\n" + json.dumps(facts, indent=1) + "\n</facts>"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}})
    usage = (response.usage.input_tokens, response.usage.output_tokens)
    if response.stop_reason != "end_turn":
        exc = assistant.Unusable(response.stop_reason or "no stop reason")
        exc.usage = usage
        raise exc
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        return json.loads(text), usage
    except ValueError:
        exc = assistant.Unusable("unreadable reply")
        exc.usage = usage
        raise exc from None


def _written(action, session, fallback, call, accept_reply, limited=True):
    """Claude's version when it is switched on, within the limits and passes the checks; otherwise the rules'."""
    if settings.ASSISTANT_PROVIDER != "claude":
        return fallback, "rules"
    if limit := assistant.limit_reached(session if limited else None):
        assistant.record(session, limit, called=False, action=action)
        return fallback, "rules"
    usage = None
    try:
        data, usage = call()
        result = accept_reply(data)
    except assistant.Unusable as exc:
        assistant.record(session, str(exc), usage=getattr(exc, "usage", None) or usage, action=action)
        return fallback, "rules"
    except Exception as exc:  # Any failure of the call leaves the rules' version.
        log.warning("Advisor call failed: %s", type(exc).__name__)
        assistant.record(session, f"error: {type(exc).__name__}", action=action)
        return fallback, "rules"
    assistant.record(session, "read", usage=usage, action=action)
    return result, "claude"


def compare(session, facts):
    item = assistant.ITEMS.get(facts["category"], "an item")
    result, reader = _written(
        COMPARE, session, rules_compare(facts),
        lambda: ask(COMPARE_INSTRUCTIONS.format(item=item), facts, _schema_compare([o["sku"] for o in facts["options"]])),
        lambda data: accept_compare(data, facts))
    # Measured warnings are part of the result even when the writer omits them.
    notes = dict(result["notes"])
    for option in facts["options"]:
        checks = option["checks"]
        if checks:
            notes[option["sku"]] += " Preview checks: " + " ".join(checks)
    return {**result, "notes": notes, "reader": reader}


def staff_note(draft, state):
    facts = {**staff_facts(draft), "decision": DECISIONS[state]}
    # Staff notes count against the daily limit only: the shopper's session limit is theirs.
    message, reader = _written(
        STAFF_NOTE, draft.owner_session, rules_staff_note(facts, state),
        lambda: ask(STAFF_INSTRUCTIONS, facts, SCHEMA_NOTE), lambda data: accept_note(data, facts), limited=False)
    return {"message": message, "reader": reader}


def spend_by_action():
    """Calls and spend per writing feature, for merchant insights."""
    rows = {}
    for action, facts in EvidenceEvent.objects.filter(action__in=(COMPARE, STAFF_NOTE)).values_list("action", "facts"):
        row = rows.setdefault(action, [0, 0.0])
        row[0] += facts.get("outcome") == "read"
        row[1] += facts.get("cost_usd", 0)
    return rows
