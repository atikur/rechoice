"""Turn a shopper's own words into a proposal for the preference controls.

The result is only a proposal. It fills form controls that the shopper reviews and confirms, and it
never decides eligibility, price or policy. The text is untrusted: it can only select from the same
enumerated values the controls offer. Anything unclear or contradictory becomes a question rather
than a guess. Standard library only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_TOP_UP_MINOR = 100000

COLOURS = {
    "navy": "navy", "blue": "blue", "denim": "blue", "green": "green", "sage": "green", "olive": "green",
    "red": "red", "burgundy": "burgundy", "maroon": "burgundy", "wine": "burgundy", "black": "black",
    "white": "white", "cream": "white", "ivory": "white", "sand": "sand", "beige": "sand", "khaki": "sand",
    "tan": "sand", "mauve": "mauve", "lilac": "mauve", "brown": "brown", "grey": "grey", "gray": "grey",
    "pink": "pink", "yellow": "yellow", "purple": "purple", "orange": "orange",
}
NECKLINES = [
    (r"turtle[- ]?neck|roll[- ]?neck|polo neck", "turtleneck"),
    (r"crew[- ]?neck|round[- ]?neck|crew\b", "crew"),
    (r"collar(?:ed)?|button[- ]?down", "collared"),
    (r"v[- ]?neck", "v-neck"),
]
QUIETER = [
    r"too (?:busy|loud|bold|bright|much|wild|flashy)", r"(?:print|pattern)s? (?:is |are |was |looks? )?(?:too )?(?:busy|loud|wild)",
    r"quieter|calmer|plainer|simpler|subtler", r"\bplain\b|\bsolid\b|\bsimple\b|\bminimal\b|\bsubtle\b|understated",
    r"less (?:busy|pattern|print|loud|bold)", r"not (?:so|as) (?:busy|loud|bold)", r"toned? down",
]
BOLDER = [r"bolder|busier|louder", r"more (?:pattern|print|colou?rful)"]
SLEEVES = [
    (r"long[- ]?sleeve[ds]?|full[- ]?sleeve[ds]?|sleeves? (?:must|should|need to) be long|keep (?:the )?(?:long )?sleeves(?: long)?", "long"),
    (r"short[- ]?sleeve[ds]?|sleeves? (?:must|should|need to) be short", "short"),
    (r"sleeveless|no sleeves", "sleeveless"),
]
NEGATION = r"(?:no|not|without|hate|dislike|don'?t (?:want|like)|avoid)\s+(?:\w+\s+){0,2}"
OTHER_CURRENCY = r"(?:bdt|tk|taka|৳|eur|€|£|gbp|inr|rs\.?|rupees?|euros?|pounds?)"
AMOUNT = r"(?:(?:\$|usd\s*|us\$\s*)(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:dollars?|usd|bucks)\b)"
BUDGET_CUES = r"extra|more|at most|up to|max(?:imum)?|no more than|budget|limit|top[- ]?up|spend|difference|pay"
PAST_PRICE = r"(?:paid|cost|costs|was|bought|spent)\s+(?:\w+\s+){0,2}$"
NOTHING_EXTRA = r"(?:nothing|no|zero|not a (?:cent|penny)) (?:extra|more)|(?:don'?t|do not|won'?t|will not|can'?t|cannot) (?:want to )?pay (?:any(?:thing)? )?(?:more|extra)"


@dataclass
class Interpretation:
    proposal: dict = field(default_factory=dict)
    understood: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"proposal": self.proposal, "understood": self.understood,
                "questions": self.questions, "notes": self.notes}


def _hits(text: str, patterns: list[str]) -> list[re.Match]:
    return [m for pattern in patterns for m in re.finditer(pattern, text)]


def _negated(text: str, match: re.Match) -> bool:
    return re.search(NEGATION + r"$", text[max(0, match.start() - 40):match.start()]) is not None


def _ask(result: Interpretation, field_name: str, question: str, options: list[tuple[str, object]]) -> None:
    result.questions.append({"field": field_name, "question": question,
                             "options": [{"label": label, "value": value} for label, value in options]})


def interpret(text: str) -> Interpretation:
    result = Interpretation()
    text = " ".join((text or "").lower().replace("’", "'").split())
    if not text:
        return result

    quieter, bolder = _hits(text, QUIETER), _hits(text, BOLDER)
    if quieter and bolder:
        _ask(result, "quieter_print", "Do you want a quieter print or a bolder one?",
             [("Quieter print", True), ("Any print", False)])
    elif quieter:
        result.proposal["quieter_print"] = True
        result.understood.append("a quieter print")
    elif bolder:
        result.proposal["quieter_print"] = False
        result.understood.append("no need for a quieter print")

    wanted, refused = set(), set()
    for pattern, value in SLEEVES:
        for match in re.finditer(pattern, text):
            (refused if _negated(text, match) else wanted).add(value)
    wanted -= refused
    if len(wanted) > 1:
        _ask(result, "sleeve_required", "You mentioned more than one sleeve length. Which do you need?",
             [("Long sleeves", "long"), ("Short sleeves", "short"), ("Any length", "")])
    elif wanted:
        value = wanted.pop()
        result.proposal["sleeve_required"] = value
        result.understood.append(f"{value} sleeves required" if value != "sleeveless" else "sleeveless")
    elif refused:
        _ask(result, "sleeve_required", "Which sleeve length do you need instead?",
             [(label, value) for label, value in (("Long sleeves", "long"), ("Short sleeves", "short"),
                                                  ("Any length", "")) if value not in refused])

    colours, avoided = [], []
    for word, tag in COLOURS.items():
        for match in re.finditer(rf"\b{word}\b", text):
            (avoided if _negated(text, match) else colours).append((match.start(), tag))
    preferred = [tag for _, tag in sorted(colours) if tag not in {t for _, t in avoided}]
    preferred = list(dict.fromkeys(preferred))
    if preferred:
        result.proposal["color_preferences"] = preferred[:3]
        result.understood.append("prefers " + " or ".join(preferred[:3]))
    if avoided:
        result.notes.append("Colours to avoid are not used to filter tops yet. Check the photos for colour.")

    necklines = list(dict.fromkeys(value for pattern, value in NECKLINES for m in re.finditer(pattern, text)
                                   if not _negated(text, m)))
    if len(necklines) > 1:
        _ask(result, "neckline_preference", "Which neckline do you prefer?",
             [(name.capitalize(), name) for name in necklines] + [("Any", "")])
    elif necklines:
        result.proposal["neckline_preference"] = necklines[0]
        result.understood.append(f"{necklines[0]} neckline preferred")

    _budget(text, result)
    return result


SHADE_LIGHTNESS = [
    (r"too (?:dark|deep)|(?:a |one |two |a few )?shades? lighter|\blighter\b|paler", "lighter"),
    (r"too (?:light|pale|fair)|(?:a |one |two |a few )?shades? darker|\bdarker\b|\bdeeper\b", "darker"),
]
SHADE_TONE = [
    (r"too (?:orange|yellow|warm|golden|brassy|brown)|less (?:orange|yellow|warm|golden)|\bcooler\b|pinker"
     r"|\bbrassy\b|(?:went|turned|looks?|is) (?:too )?(?:orange|yellow)", "cooler"),
    (r"too (?:pink|cool|ashy|grey|gray|purple)|less (?:pink|cool|ashy)|\bwarmer\b|more golden", "warmer"),
]
SHADE_INTENSITY = [
    (r"too (?:bright|strong|bold|loud|intense|vivid|much)|\bsofter\b|more natural|subtler|toned? down", "softer"),
    (r"too (?:dull|faint|subtle|sheer|weak|flat)|\bbolder\b|stronger|more vivid|more colou?r", "bolder"),
]


def interpret_shade(text: str) -> Interpretation:
    """Like interpret(), for a beauty shade: lighter or darker, warmer or cooler, softer or bolder.

    The direction comes only from the shopper's words; nothing about their own colouring is assumed.
    """
    result = Interpretation()
    text = " ".join((text or "").lower().replace("’", "'").split())
    if not text:
        return result
    for field_name, patterns, question, options in (
        ("lightness", SHADE_LIGHTNESS, "Should the new shade be lighter or darker?",
         [("Lighter", "lighter"), ("Darker", "darker"), ("Keep the depth", "")]),
        ("tone", SHADE_TONE, "Should it be warmer or cooler?",
         [("Warmer, more golden", "warmer"), ("Cooler, less orange", "cooler"), ("Keep the tone", "")]),
        ("intensity", SHADE_INTENSITY, "Should it be softer or bolder?",
         [("Softer", "softer"), ("Bolder", "bolder"), ("Keep the intensity", "")]),
    ):
        found = {value for pattern, value in patterns for m in re.finditer(pattern, text) if not _negated(text, m)}
        if len(found) > 1:
            _ask(result, field_name, question, options)
        elif found:
            value = found.pop()
            result.proposal[field_name] = value
            result.understood.append({"lighter": "a lighter shade", "darker": "a darker shade",
                                      "cooler": "cooler, less orange or yellow", "warmer": "warmer, more golden",
                                      "softer": "softer", "bolder": "bolder"}[value])
    _budget(text, result)
    return result


JEWELLERY_SIZE = [
    (r"too (?:big|long|large|heavy|dangly|much)|smaller|shorter|daintier|subtler|more subtle|less (?:dangly|showy)", "smaller"),
    (r"too (?:small|short|tiny|subtle|plain)|bigger|longer|larger|statement|more (?:dangly|showy)", "larger"),
]
JEWELLERY_METAL = [
    (r"silver|white gold|platinum|steel", "silver"),
    (r"(?<!white )\bgold(?:en|-tone)?\b", "gold"),
]


def interpret_jewellery(text: str, noun: str = "pair") -> Interpretation:
    """Like interpret(), for earrings or a necklace: smaller or larger, and gold or silver. Nothing is read from
    the face. noun is "pair" for earrings and "necklace" for a necklace, whose size reads as length."""
    result = Interpretation()
    text = " ".join((text or "").lower().replace("’", "'").split())
    if not text:
        return result
    smaller, larger = ("shorter", "longer") if noun == "necklace" else ("smaller", "larger")
    for field_name, patterns, question, options in (
        ("size", JEWELLERY_SIZE, f"Should the new {noun} be {smaller} or {larger}?",
         [(smaller.capitalize(), "smaller"), (larger.capitalize(), "larger"), ("Keep the size", "")]),
        ("metal", JEWELLERY_METAL, "Gold or silver?",
         [("Gold", "gold"), ("Silver", "silver"), ("Either metal", "")]),
    ):
        found = {value for pattern, value in patterns for m in re.finditer(pattern, text) if not _negated(text, m)}
        if field_name == "metal" and len(found) > 1:
            # "Gold looks wrong, I wanted silver": the metal named last after a wish word is the one wanted.
            wish = re.search(r"(?:want(?:ed)?|prefer(?:red)?|would like|rather|instead|in)\s+(?:\w+\s+){0,2}(silver|gold)", text)
            found = {wish.group(1)} if wish else found
        if len(found) > 1:
            _ask(result, field_name, question, options)
        elif found:
            value = found.pop()
            result.proposal[field_name] = value
            result.understood.append({"smaller": f"a {smaller} {noun}", "larger": f"a {larger} {noun}",
                                      "gold": "gold", "silver": "silver"}[value])
    _budget(text, result)
    return result


HEEL_SIZE = [
    (r"(?:heels?|heel height) (?:is |are )?too (?:high|tall)|too high|lower heels?|\bflats?\b|flatter|less height", "smaller"),
    (r"too (?:flat|low)|higher heels?|more height|taller heels?|dressier", "larger"),
]
BAG_SIZE = [
    (r"too (?:big|large|bulky|heavy)|smaller|more compact|\bcompact", "smaller"),
    (r"too (?:small|tiny)|bigger|larger|roomier|more room", "larger"),
]
COLOUR_FAMILY = [
    (r"too (?:bright|loud|flashy|colou?rful)|neutral|\bplain(?:er)? colou?r|goes with everything|"
     r"\b(?:black|brown|tan|beige|nude|cream)\b", "neutral"),
    (r"too (?:dull|plain|boring|dark|neutral)|brighter|\bcolou?rful|bold(?:er)? colou?r|pop of colou?r|more colou?r",
     "bright"),
]


def interpret_accessory(text: str, category: str) -> Interpretation:
    """Like interpret_jewellery(), for shoes or a bag: a lower or higher heel, or a smaller or larger bag, and a
    neutral or bright colour. Nothing is read from the shopper's feet or body."""
    result = Interpretation()
    text = " ".join((text or "").lower().replace("’", "'").split())
    if not text:
        return result
    shoes = category == "shoes"
    sizes = {"smaller": "a lower heel", "larger": "a higher heel"} if shoes else {
        "smaller": "a smaller bag", "larger": "a larger bag"}
    for field_name, patterns, question, options in (
        ("size", HEEL_SIZE if shoes else BAG_SIZE,
         "Should the new pair have a lower or a higher heel?" if shoes else "Should the new bag be smaller or larger?",
         [("Lower heel" if shoes else "Smaller", "smaller"), ("Higher heel" if shoes else "Larger", "larger"),
          ("Keep the size", "")]),
        ("metal", COLOUR_FAMILY, "A neutral or a bright colour?",
         [("Neutral", "neutral"), ("Bright", "bright"), ("Either colour", "")]),
    ):
        found = {value for pattern, value in patterns for m in re.finditer(pattern, text) if not _negated(text, m)}
        if len(found) > 1:
            _ask(result, field_name, question, options)
        elif found:
            value = found.pop()
            result.proposal[field_name] = value
            result.understood.append({**sizes, "neutral": "a neutral colour", "bright": "a bright colour"}[value])
    _budget(text, result)
    return result


def _budget(text: str, result: Interpretation) -> None:
    if re.search(rf"{OTHER_CURRENCY}\s*\d|\d[\d,.]*\s*{OTHER_CURRENCY}", text):
        _ask(result, "max_top_up_minor", "Prices here are in US dollars. What is the most extra you would pay in USD?", [])
        return
    amounts = set()
    for match in re.finditer(AMOUNT, text):
        before = text[max(0, match.start() - 30):match.start()]
        after = text[match.end():match.end() + 30]
        if re.search(PAST_PRICE, before):
            continue
        if re.search(BUDGET_CUES, before) or re.search(BUDGET_CUES, after):
            value = match.group(1) or match.group(2)
            amounts.add(round(float(value) * 100))
    if re.search(NOTHING_EXTRA, text):
        amounts.add(0)
    if len(amounts) > 1:
        _ask(result, "max_top_up_minor", "You mentioned more than one amount. What is the most extra you would pay?",
             [(f"USD {cents / 100:.2f}", cents) for cents in sorted(amounts)])
    elif amounts:
        cents = amounts.pop()
        if cents > MAX_TOP_UP_MINOR:
            _ask(result, "max_top_up_minor", f"The most this demo accepts is USD {MAX_TOP_UP_MINOR / 100:.2f} extra. "
                 "What is your limit?", [])
        else:
            result.proposal["max_top_up_minor"] = cents
            result.understood.append("nothing extra" if cents == 0 else f"up to USD {cents / 100:.2f} extra")
