"""Read a bet slip with a local model, then check every answer against the slip.

The model returns each fact with the words it came from. A number only counts
when its quote is really on the slip and really contains that number, and odds
only count when the quote shows them exactly as printed. On top of that, the
slip's own arithmetic is checked: stake times odds is the return the slip
prints. Anything that fails is reported as unread, never guessed, and a few
plain keyword scans run on the slip itself to catch what the model missed.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field

NUMBERS = ("stake", "returns", "to_win", "rollover")
FLAGS = ("live", "odds_changed", "bonus")
TEXTS = ("event", "market", "bet_type", "total_odds")

_NUM = {"type": "object", "properties": {"value": {"type": ["number", "null"]}, "quote": {"type": "string"}},
        "required": ["value", "quote"]}
_FLAG = {"type": "object", "properties": {"present": {"type": "boolean"}, "quote": {"type": "string"}},
         "required": ["present", "quote"]}
_TEXT = {"type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
         "required": ["text", "quote"]}
_SELECTION = {"type": "object", "properties": {"pick": {"type": "string"}, "odds": {"type": "string"},
                                               "quote": {"type": "string"}}, "required": ["pick", "odds", "quote"]}
_MARKET = {"type": "object", "properties": {"prices": {"type": "array", "items": {"type": "string"}},
                                            "quote": {"type": "string"}}, "required": ["prices", "quote"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {"type": "array", "items": _SELECTION},
        "market_odds": _MARKET,
        **{name: _NUM for name in NUMBERS},
        **{name: _FLAG for name in FLAGS},
        **{name: _TEXT for name in TEXTS},
    },
    "required": ["selections", "market_odds", *NUMBERS, *FLAGS, *TEXTS],
}

# No example values in here on purpose: an example value in a prompt is a default
# the model reaches for when it cannot see.
SYSTEM = (
    "You read one sports bet slip or betting market, exactly as someone copied it from the page or captured it "
    "on screen. For every field, return what the slip says and the exact words it came from: the quote is copied "
    "character for character from the slip, at most 15 words, and it is one continuous piece of the slip: never "
    "join words from two places, never drop words from the middle. If the slip does not say it, the value is null "
    "(or present false, text empty, list empty) and the quote is empty. Never estimate, never calculate, never "
    "fill a gap.\n"
    "selections: every selection on the slip, one item each, in order. pick is what was picked; odds is that "
    "selection's price copied exactly as printed, in whatever format the slip uses (decimal, fractional or "
    "American); the quote contains those odds, and when the pick and its odds sit on different lines, the quote is the odds alone. A single bet has one selection. Prices of a market listing that "
    "were not picked are not selections.\n"
    "total_odds: the combined odds of an accumulator or parlay, as printed, if the slip shows them.\n"
    "stake: the amount staked, as a plain number without the currency sign. returns: the potential returns or "
    "payout, stake included. to_win: the potential profit, when the slip prints it as to win or profit. Copy "
    "amounts as printed; never compute one from another.\n"
    "market_odds: when the page shows every outcome of one market with its price (for example home, draw and "
    "away), the prices of that one market in order, copied as printed, and one quote that contains all of them; "
    "this quote may be up to 30 words. Otherwise an empty list.\n"
    "event: the match or event. market: the market name. bet_type: the slip's name for the bet (single, double, "
    "treble, accumulator, parlay, or a number of folds or legs).\n"
    "live: the bet is on an event already in play. odds_changed: the slip says the odds or the price changed. "
    "bonus: the bet uses a bonus, a free bet or promotional funds. rollover: how many times a bonus must be "
    "wagered before it can be withdrawn, as a number."
)

# Words that must surround a quote for a flag of that kind to count. Lists, not
# understanding: they stop a quote that talks about something else, nothing more.
FLAG_WORDS = {
    "live": r"\blive\b|in-?play|\d{1,3}(\+\d{1,2})?'|minute|half",
    "odds_changed": r"chang|moved|was|now|->|→",
    "bonus": r"bonus|free bet|promo|token|credit",
}

# The label a number must sit next to on the slip: 21.00 only counts as the
# return if "return" or "payout" is right there.
LABELS = {
    "stake": r"stake|wager|bet amount|amount|risk",
    # OCR reads "returns" as "retums" or "tums"; "potential" survives.
    "returns": r"return|retum|payout|pays|winnings|potential",
    "to_win": r"to win|profit|winnings",
    "rollover": r"wager|rollover|playthrough|turnover|times",
}

# A slip's name for how many selections it holds.
_FOLDS = {"single": 1, "double": 2, "treble": 3, "trixie": 3, "yankee": 4}
_FOLD_COUNT = re.compile(r"(\d+)\s*-?\s*(?:fold|legs?|selections?|team)\b")

# Keyword scans of the slip itself. If one hits while the model said nothing,
# the verdict asks you to look instead of trusting the silence.
SCANS = {
    "live": r"\bin-?play\b|\blive (bet|betting|now|odds|score|match)\b|(?<![\d.,'])\d{1,3}(\+\d{1,2})?'(?![\w'])",
    "odds_changed": r"\b(odds|prices?) (have |has )?(changed|moved)\b",
    "bonus": r"\b(free bets?|bonus (bets?|funds|cash|money)|wagering requirements?|rollover|playthrough)\b",
}

CURRENCY = re.compile(r"r\$|[£$€]")
CURRENCY_AFTER = re.compile(r"^\s?([£$€]|eur\b|usd\b|gbp\b|brl\b)")


class UnreadableAnswer(ValueError):
    """The model's answer is not the JSON the schema asks for."""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―−]", "-", text)
    text = re.sub(r"#(?=\d{3}\b)", "+", text)  # OCR reads the + of American odds as #
    return re.sub(r"\s+", " ", text).strip().casefold()


def _number(digits: str) -> float | None:
    """One written number, in either convention: 1,203.50 and 1.203,50 are the same amount, and a comma
    followed by one or two digits (21,00) is a decimal comma."""
    if "," in digits and "." in digits:
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
        digits = digits.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif "," in digits:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", digits):
            digits = digits.replace(",", "")
        elif re.fullmatch(r"\d+,\d{1,2}", digits):
            digits = digits.replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif digits.count(".") > 1:
        digits = digits.replace(".", "")
    try:
        return float(digits)
    except ValueError:
        return None


_AMOUNT = re.compile(r"(\d(?:[\d,.]*\d)?)(?:\s*([km])(?![a-z]))?", re.I)


def amounts(quote: str) -> list[float]:
    """Every number written in the quote, with 2K read as 2000."""
    found = []
    for digits, suffix in _AMOUNT.findall(quote or ""):
        value = _number(digits)
        if value is None:
            continue
        if suffix:
            value *= 1_000 if suffix.lower() == "k" else 1_000_000
        found.append(value)
    return found


def parse_odds(printed: str) -> float | None:
    """Decimal odds from odds written in any of the three formats: 2.10, 2,10, 11/10, +110, evens.
    None when the text is not odds."""
    t = normalize(printed).replace(" ", "")
    if t in ("evens", "evs", "even", "ev"):
        return 2.0
    if m := re.fullmatch(r"(\d+)/(\d+)", t):
        top, bottom = int(m.group(1)), int(m.group(2))
        return 1 + top / bottom if bottom else None
    if m := re.fullmatch(r"([+-])(\d{3,})", t):
        n = int(m.group(2))
        if n < 100:
            return None
        return 1 + n / 100 if m.group(1) == "+" else 1 + 100 / n
    if re.fullmatch(r"\d+(?:[.,]\d+)?", t):
        value = float(t.replace(",", "."))
        return value if value > 1 else None
    return None


def show_odds(decimal: float) -> str:
    return f"{decimal:.2f}"


def around(haystack: str | None, quote: str) -> str:
    """The slip around every place the quote appears, so a short quote is judged by what surrounds it."""
    needle = normalize(quote)
    if haystack is None or not needle or needle not in haystack:
        return needle
    pieces, start = [], haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        pieces.append(haystack[max(0, start - 60):end + 60])
        start = haystack.find(needle, end)
    return " | ".join(pieces)


def _close(a: float, b: float, share: float = 0.001) -> bool:
    return abs(a - b) <= max(0.005, abs(b) * share)


def _odds_in(printed: str, quote: str) -> bool:
    """The odds appear in the quote exactly as printed, as a price and not as part of an amount."""
    token, text = normalize(printed).replace(" ", ""), normalize(quote)
    for hit in re.finditer(rf"(?<![\d.,/]){re.escape(token)}(?![\d.,/]?\d)", text):
        if not CURRENCY.search(text[max(0, hit.start() - 2):hit.start()]) and not CURRENCY_AFTER.search(text[hit.end():]):
            return True
    return False


def _nearest_label(text: str) -> tuple[int, set[str]]:
    """Where the last label in ``text`` ends, and which kinds of number it can name."""
    ends = {kind: max((m.end() for m in re.finditer(pattern, text)), default=-1) for kind, pattern in LABELS.items()}
    nearest = max(ends.values())
    return nearest, {kind for kind, end in ends.items() if end == nearest and nearest != -1}


def labelled(haystack: str, quote: str, name: str, value: float) -> bool:
    """The label closest before the number is this fact's label: "£45.00" after "Stake £20.00 Returns"
    belongs to the returns, not to the stake, whether the words before it are in the quote or on the slip."""
    needle = normalize(quote)
    for hit in _AMOUNT.finditer(needle):
        number = _number(hit.group(1))
        if number is None or not _close(float(value), number):
            continue
        end, kinds = _nearest_label(needle[:hit.start()])
        if kinds:
            return name in kinds
        start = haystack.find(needle)
        while start != -1:
            end, kinds = _nearest_label(haystack[max(0, start - 40):start])
            if name in kinds:
                return True
            start = haystack.find(needle, start + 1)
        return False
    return False


@dataclass
class Fact:
    name: str
    value: object = None
    quote: str = ""
    ok: bool = True
    problem: str = ""
    note: str = ""
    printed: str = ""   # odds as the slip prints them
    pick: str = ""      # what a selection picked


@dataclass
class Reading:
    facts: dict[str, Fact] = field(default_factory=dict)
    scans: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)   # the slip disagrees with itself
    suspect: set[str] = field(default_factory=set)      # what a problem calls into doubt: odds, stake, legs
    source_checked: bool = True
    currency: str = ""
    raw: dict = field(default_factory=dict)

    def value(self, name: str):
        fact = self.facts.get(name)
        return fact.value if fact is not None and fact.ok else None

    def selections(self) -> list[Fact]:
        return [fact for name, fact in self.facts.items() if name.startswith("selection ")]

    def odds(self) -> float | None:
        """The bet's decimal odds: the slip's total when it prints one, else the product of its legs.
        None unless every number it rests on was confirmed."""
        if self.value("total_odds") is not None:
            return self.value("total_odds")
        legs = self.selections()
        if not legs or not all(leg.ok for leg in legs):
            return None
        return math.prod(leg.value for leg in legs)

    def unread(self) -> list[Fact]:
        return [fact for fact in self.facts.values() if not fact.ok]


def check(raw: dict, slip: str | None) -> Reading:
    """Turn the model's JSON into facts, keeping only what the slip backs up.

    ``slip`` is the text to check quotes against. ``None`` means there is no
    independent text (an image read without OCR): quotes are then kept as the
    model gave them and the reading says it is unchecked.
    """
    reading = Reading(source_checked=slip is not None, raw=raw)
    haystack = normalize(slip) if slip is not None else None

    def in_slip(quote: str) -> bool:
        return haystack is None or (bool(normalize(quote)) and normalize(quote) in haystack)

    def odds_fact(name: str, printed: str, quote: str, pick: str = "") -> Fact:
        fact = Fact(name, parse_odds(printed), quote, printed=printed.strip(), pick=pick.strip())
        bare = re.sub(r"[.,]", "", fact.printed)
        if fact.value is not None and not _odds_in(printed, quote) and re.fullmatch(r"\d{3,}", bare) \
                and _odds_in(bare, quote):
            # The text says 172 and the model wrote 1.72: keep what the text says, and let the
            # slip's arithmetic decide where the point goes.
            fact.printed, fact.value = bare, float(bare)
        if fact.value is None:
            fact.ok, fact.problem = False, f"{printed!r} is not odds"
        elif not in_slip(quote):
            fact.ok, fact.problem = False, "quote is not on the slip"
        elif not _odds_in(fact.printed, quote):
            fact.ok, fact.problem = False, f"{printed!r} is not the price in the quote"
        elif haystack is not None and not _odds_in(fact.printed, haystack):
            fact.ok, fact.problem = False, f"{printed!r} only appears on the slip as an amount of money"
        return fact

    for index, item in enumerate(raw.get("selections") or [], start=1):
        printed = str(item.get("odds") or "")
        reading.facts[f"selection {index}"] = odds_fact(f"selection {index}", printed,
                                                        (item.get("quote") or "").strip(), item.get("pick") or "")

    for name in NUMBERS:
        item = raw.get(name) or {}
        value, quote = item.get("value"), (item.get("quote") or "").strip()
        fact = Fact(name, value, quote)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            fact.ok, fact.problem = False, f"{value!r} is not a number"
        elif value is not None:
            if not in_slip(quote):
                fact.ok, fact.problem = False, "quote is not on the slip"
            elif not any(_close(float(value), found) for found in amounts(quote)):
                fact.ok, fact.problem = False, f"{value:g} is not the number in the quote"
            elif haystack is not None and not labelled(haystack, quote, name, float(value)):
                fact.ok, fact.problem = False, f"the slip does not label {quote!r} that way"
            elif float(value) < 0:
                fact.ok, fact.problem = False, f"{value:g} cannot be a {name.replace('_', ' ')}"
        reading.facts[name] = fact
        if name == "stake" and fact.ok and quote and (sign := CURRENCY.search(normalize(quote))):
            reading.currency = {"r$": "R$"}.get(sign.group(0), sign.group(0))

    for name in FLAGS:
        item = raw.get(name) or {}
        present, quote = bool(item.get("present")), (item.get("quote") or "").strip()
        fact = Fact(name, present, quote)
        if present:
            if not in_slip(quote):
                fact.ok, fact.problem = False, "quote is not on the slip"
            elif not re.search(FLAG_WORDS[name], around(haystack, quote)):
                fact.ok, fact.problem = False, "the slip does not say that there"
        reading.facts[name] = fact

    for name in TEXTS:
        item = raw.get(name) or {}
        text, quote = (item.get("text") or "").strip(), (item.get("quote") or "").strip()
        if name == "total_odds":
            reading.facts[name] = odds_fact(name, text, quote) if text else Fact(name)
            continue
        fact = Fact(name, text or None, quote)
        if text and not in_slip(quote):
            fact.ok, fact.problem = False, "quote is not on the slip"
        reading.facts[name] = fact

    market = raw.get("market_odds") or {}
    prices, quote = [str(p) for p in market.get("prices") or [] if str(p).strip()], (market.get("quote") or "").strip()
    if prices:
        fact = Fact("market_odds", [parse_odds(p) for p in prices], quote, printed=" / ".join(prices))
        book = sum(1 / d for d in fact.value) if all(fact.value) else None
        if not in_slip(quote):
            fact.ok, fact.problem = False, "quote is not on the slip"
        elif not all(fact.value) or not all(_odds_in(p, quote) for p in prices):
            fact.ok, fact.problem = False, "not every price is in the quote as printed"
        elif len(prices) < 2 or not 1.0 < book < 1.4:
            fact.ok, fact.problem = False, f"not one whole market (the prices add up to {book * 100:.0f}%)"
        reading.facts["market_odds"] = fact

    _collapse_single(reading)
    if haystack is not None:
        for name, pattern in SCANS.items():
            hit = re.search(pattern, haystack)
            if hit:
                reading.scans[name] = hit.group(0)
    _arithmetic(reading)
    return reading


def _collapse_single(reading: Reading) -> None:
    """A single quoted twice (once on the slip, once in the market listing below it) is one selection."""
    legs = reading.selections()
    named = normalize(reading.value("bet_type") or "")
    if len(legs) > 1 and re.search(r"\bsingle\b", named) and all(leg.ok for leg in legs) \
            and len({leg.value for leg in legs}) == 1:
        for leg in legs[1:]:
            del reading.facts[leg.name]


def _arithmetic(reading: Reading) -> None:
    """Check the slip against itself: stake times odds against its return, legs against the count it
    names, legs against its total odds. A decimal point the OCR lost is put back when the slip's own
    arithmetic says where it was; anything else that disagrees goes to ``problems``, and what it calls
    into doubt goes to ``suspect``."""
    odds, stake = reading.odds(), reading.value("stake")
    payout, profit = (lambda o, s: o * s), (lambda o, s: (o - 1) * s)
    for name, expect_of, other, other_of in (("returns", payout, "to_win", profit),
                                             ("to_win", profit, "returns", payout)):
        shown = reading.value(name)
        if odds is None or stake is None or shown is None:
            continue
        if _close(expect_of(odds, stake), shown, share=0.01):
            continue
        if reading.facts[other].value is None and _close(other_of(odds, stake), shown, share=0.01):
            # Slips word this differently ("winnings" may include the stake); the arithmetic says which it is.
            moved = reading.facts.pop(name)
            reading.facts[other] = Fact(other, moved.value, moved.quote,
                                        note=f"{moved.quote!r} is the {other.replace('_', ' ')}, by the slip's arithmetic")
            continue
        if _restore_point(reading, name, expect_of):
            odds, stake = reading.odds(), reading.value("stake")
            continue
        sign = reading.currency
        reading.problems.append(
            f"the slip's numbers disagree: {sign}{stake:,.2f} at {show_odds(odds)} "
            f"{'returns' if name == 'returns' else 'wins'} {sign}{expect_of(odds, stake):,.2f}, "
            f"the slip shows {sign}{shown:,.2f}")
        reading.suspect |= {"odds", "stake"}

    legs = reading.selections()
    named = reading.value("bet_type")
    if named and legs:
        t = normalize(named)
        count = next((n for word, n in _FOLDS.items() if re.search(rf"\b{word}\b", t)), None)
        if m := _FOLD_COUNT.search(t):
            count = int(m.group(1))
        if count is not None and count != len(legs):
            reading.problems.append(f"the slip says {named!r} and {len(legs)} selection(s) were read")
            reading.suspect |= {"odds", "legs"}

    total = reading.facts.get("total_odds")
    if total is not None and total.ok and total.value is not None and legs and all(leg.ok for leg in legs):
        product = math.prod(leg.value for leg in legs)
        if not _close(total.value, product, share=0.02):
            reading.problems.append(f"total odds {total.printed} and the legs multiplied ({product:.2f}) disagree")
            reading.suspect.add("odds")

    # Decimal slips print prices with decimals. A bare 210 is a 2.10 that lost its point, even when
    # the return lost its point too and the arithmetic still agrees (10 x 210 = 2100).
    for fact in [*legs, total]:
        if fact is not None and fact.ok and re.fullmatch(r"\d{3,}", fact.printed):
            reading.problems.append(f"odds printed as {fact.printed} with no decimal point")
            reading.suspect.add("odds")


def _restore_point(reading: Reading, name: str, expect_of) -> bool:
    """OCR loses the small decimal point: 2.10 comes out as 210, 21.00 as 2100. When exactly one
    number written with no point becomes consistent once divided by 100, put the point back and
    say so."""
    candidates = []
    stake, shown = reading.value("stake"), reading.value(name)
    legs = reading.selections()
    total = reading.facts.get("total_odds")
    if total is not None and total.ok and total.value is not None:
        if re.fullmatch(r"\d{3,}", total.printed) and _close(expect_of(total.value / 100, stake), shown, share=0.01):
            candidates.append((total, total.value / 100))
    else:
        for leg in legs:
            if re.fullmatch(r"\d{3,}", leg.printed):
                fixed = leg.value / 100
                others = math.prod(other.value for other in legs if other is not leg)
                if fixed > 1 and _close(expect_of(fixed * others, stake), shown, share=0.01):
                    candidates.append((leg, fixed))
    odds = reading.odds()
    for key, test in (("stake", lambda v: _close(expect_of(odds, v), shown, share=0.01)),
                      (name, lambda v: _close(expect_of(odds, stake), v, share=0.01))):
        fact = reading.facts[key]
        if re.search(r"\d", fact.quote) and not re.search(r"\d[.,]\d", fact.quote) and test(fact.value / 100):
            candidates.append((fact, fact.value / 100))
    if len(candidates) != 1:
        return False
    fact, fixed = candidates[0]
    was = fact.printed or f"{fact.value:g}"
    fact.note = f"the text has {was} with no decimal point; the slip's arithmetic says {fixed:.2f}"
    fact.value = fixed
    if fact.printed:
        fact.printed = f"{fixed:.2f}"
    return True


def _parse(answer: str, slip: str | None) -> Reading:
    try:
        raw = json.loads(answer)
        if not isinstance(raw, dict):
            raise TypeError("not a JSON object")
        return check(raw, slip)
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        raise UnreadableAnswer(f"the model's answer could not be read ({error})") from error


def read_slip(ask, slip: str) -> Reading:
    """Ask the model about a pasted slip and check the answer against it."""
    return _parse(ask(SYSTEM, slip, schema=SCHEMA), slip)


def read_image(ask, image: bytes) -> Reading:
    """Last resort for a screenshot when there is no OCR: the model reads the image itself.

    With no text to check the quotes against, nothing stops an invented number,
    so the reading is marked unchecked and no rule gets to decide on it: the
    verdict is CHECK.
    """
    return _parse(ask(SYSTEM, "The bet slip is in the attached screenshot.", schema=SCHEMA, images=[image]), None)
