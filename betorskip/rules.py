"""Your betting rules, applied by code to a checked reading.

A verdict is one of three words. SKIP names the rule the bet breaks. CHECK
means something a rule depends on could not be confirmed, or the slip
disagrees with itself, so you look before placing anything. BET means no rule
is broken; it comes with what the price implies (break-even, the house margin,
the market's fair odds).

None of this says a bet will win. It reads the slip right and holds you to
the rules you wrote down. The defaults are common starting points, not
advice. Every number lives in a TOML file you replace with your own.
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .read import LABELS, Reading, normalize, show_odds

# Where each wall input shows on a slip. If its label is nowhere on the slip, a
# value the model gave was invented, not misread: the slip does not show it.
SHOWN = {"stake": LABELS["stake"], "rollover": LABELS["rollover"]}


@dataclass
class Rules:
    bankroll: float = 0.0          # what you set aside for betting (0 = stake rules off)
    max_stake_pct: float = 5.0     # a stake above this share of the bankroll is a wall
    min_odds: float = 1.0          # odds below this are a wall (1.0 = off)
    max_odds: float = 0.0          # odds above this are a wall (0 = off)
    max_legs: int = 0              # more selections than this on one slip is a wall (0 = off)
    max_margin_pct: float = 10.0   # a house margin above this, when the whole market shows, is a wall
    never: list[str] = field(default_factory=list)   # markets you do not bet, as words on the slip
    live_is_wall: bool = False     # True if you do not bet in play
    max_rollover: float = 0.0      # a bonus that must be wagered more times than this is a wall (0 = off)
    min_edge_pct: float = 0.0      # with --prob: an expected value below this is a wall
    kelly_fraction: float = 0.25   # with --prob: the share of the Kelly stake to suggest

    @classmethod
    def load(cls, path: str | Path) -> "Rules":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        flat: dict = {}
        tables = [value for value in data.values() if isinstance(value, dict)]
        tables.append({key: value for key, value in data.items() if not isinstance(value, dict)})
        for table in tables:
            for key, value in table.items():
                if key in flat:
                    raise ValueError(f"{key} is set twice in {path}")
                flat[key] = value
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(flat) - known)
        if unknown:
            raise ValueError(f"unknown setting(s) in {path}: {', '.join(unknown)}")
        defaults = cls()
        for key, value in flat.items():
            want = type(getattr(defaults, key))
            if want is list:
                ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
            elif want is float:
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif want is int:
                ok = isinstance(value, int) and not isinstance(value, bool)
            else:
                ok = isinstance(value, want)
            if not ok:
                raise ValueError(f"{key} in {path} should be {'a list of words' if want is list else want.__name__}, "
                                 f"not {value!r}")
            if want in (int, float) and not math.isfinite(value):
                raise ValueError(f"{key} in {path} must be a number")
            if want in (int, float) and value < 0:
                raise ValueError(f"{key} in {path} cannot be negative")
            if want is list and any(not item.strip() for item in value):
                raise ValueError(f"{key} in {path} has an empty entry, which would match every slip")
        if flat.get("kelly_fraction", 0) > 1:
            raise ValueError(f"kelly_fraction in {path} is a share of the Kelly stake, at most 1")
        if flat.get("max_stake_pct", 0) > 100:
            raise ValueError(f"max_stake_pct in {path} is a percentage, at most 100")
        return cls(**flat)


@dataclass
class Verdict:
    kind: str                       # BET, SKIP or CHECK
    walls: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stake_pct: float | None = None  # the stake as a share of your bankroll
    break_even: float | None = None  # how often the bet must win to break even
    margin: float | None = None     # the house margin on the whole market, when it shows
    fair_odds: float | None = None  # the market's price for this pick with the margin taken out


def money(value: float, sign: str = "") -> str:
    return f"{sign}{value:,.2f}"


def percent(share: float) -> str:
    """The probability as you gave it: 52% stays 52%, 0.5% stays 0.5%."""
    return f"{round(share * 100, 4):g}%"


def decide(reading: Reading, rules: Rules, slip: str | None = None, prob: float | None = None) -> Verdict:
    v = reading.value
    verdict = Verdict("BET")
    walls, checks, notes = verdict.walls, verdict.checks, verdict.notes
    text = normalize(slip or "")
    sign = reading.currency
    legs = reading.selections()
    odds = reading.odds()
    stake = v("stake")

    # Markets you do not bet.
    for market in rules.never:
        if slip is not None and re.search(rf"(?<!\w){re.escape(normalize(market))}(?!\w)", text):
            walls.append(f'"{market}" is on your never list')

    # A rule resting on a number the slip itself disputes does not get to decide: that is a CHECK.
    # With no text to check an image reading against, every number is in doubt.
    doubt = set(reading.suspect)
    if not reading.source_checked:
        doubt |= {"odds", "stake", "legs", "rollover"}

    # How many selections ride on one slip.
    if rules.max_legs and len(legs) > rules.max_legs and "legs" not in doubt:
        walls.append(f"{len(legs)} selections on one slip (your max {rules.max_legs})")

    # The price.
    if odds is not None and "odds" not in doubt:
        verdict.break_even = 1 / odds
        if rules.max_odds and odds > rules.max_odds:
            walls.append(f"odds {show_odds(odds)} are above your max {show_odds(rules.max_odds)}")
        if odds < rules.min_odds:
            walls.append(f"odds {show_odds(odds)} are below your min {show_odds(rules.min_odds)}")

    # The stake against the bankroll.
    if stake is not None and rules.bankroll and "stake" not in doubt:
        verdict.stake_pct = stake / rules.bankroll
        if verdict.stake_pct * 100 > rules.max_stake_pct:
            walls.append(f"stake {money(stake, sign)} is {verdict.stake_pct:.1%} of your {money(rules.bankroll, sign)} "
                         f"bankroll (your max {rules.max_stake_pct:g}%)")

    # What the house keeps, when the whole market is on the page.
    market = v("market_odds")
    if market and "odds" not in doubt:  # the market's prices were read the same way as the pick's
        book = sum(1 / d for d in market)
        verdict.margin = book - 1
        if verdict.margin * 100 > rules.max_margin_pct:
            walls.append(f"the house keeps {verdict.margin:.1%} on this market (your max {rules.max_margin_pct:g}%)")
        if len(legs) == 1 and legs[0].ok and any(abs(d - legs[0].value) < 1e-9 for d in market):
            verdict.fair_odds = book * legs[0].value

    partial = reading.facts.get("market_odds")
    if verdict.margin is None and partial is not None and not partial.ok and len(partial.value or []) >= 2 \
            and len(legs) == 1 and legs[0].ok and any(d and abs(d - legs[0].value) < 1e-9 for d in partial.value):
        notes.append("house margin unknown: market prices are on the slip, but not one whole market could be "
                     "confirmed")

    # A bonus you would have to churn before you can take it out.
    rollover = v("rollover")
    if rollover is not None and rules.max_rollover and rollover > rules.max_rollover and "rollover" not in doubt:
        walls.append(f"the bonus must be wagered {rollover:g} times (your max {rules.max_rollover:g})")

    # In play.
    if v("live"):
        quote = reading.facts["live"].quote
        if rules.live_is_wall and reading.source_checked:
            walls.append(f'a live bet, and you do not bet in play: "{quote}"')
        else:
            notes.append(f'live: the price can move between reading and placing ("{quote}")')

    # Your own estimate, if you gave one.
    kelly_line = None
    if prob is not None and odds is not None and "odds" not in doubt:
        edge = prob * odds - 1
        if edge * 100 < rules.min_edge_pct:
            walls.append(f"at your {percent(prob)} the bet is worth {edge:+.1%} of the stake "
                         f"(your min {rules.min_edge_pct:+g}%)")
        elif edge > 0:
            kelly = edge / (odds - 1)
            kelly_line = (f"at your {percent(prob)} the bet is worth {edge:+.1%} of the stake; "
                          f"Kelly says {kelly:.1%} of bankroll")
            if rules.bankroll:
                suggested = kelly * rules.kelly_fraction * rules.bankroll
                cap = rules.bankroll * rules.max_stake_pct / 100
                kelly_line += f", {rules.kelly_fraction:g} Kelly is {money(min(suggested, cap), sign)}"
                if suggested > cap:
                    kelly_line += " (your max stake)"

    # Things the rules lean on that the reading could not confirm.
    if not legs and v("total_odds") is None:
        checks.append("no odds could be read from the slip")
    for problem in reading.problems:
        checks.append(problem)
    for fact in reading.unread():
        label = fact.name.replace("_", " ")
        if fact.name == "market_odds":
            continue  # no margin then; --explain shows why
        if fact.name in SHOWN and slip is not None and not re.search(SHOWN[fact.name], text):
            notes.append(f"the slip does not show a {label}; the reading said {fact.value!s}, ignored")
        elif fact.name in ("live", "odds_changed", "bonus"):
            if fact.name in reading.scans:
                checks.append(f'could not confirm {label} ({fact.problem}), and the slip says "{reading.scans[fact.name]}"')
            else:
                notes.append(f"ignored a {label} the slip does not back ({fact.problem})")
        elif fact.name in ("returns", "to_win"):
            # Only a cross-check on stake and odds, never an input to a rule.
            shown = f"{fact.value:g}" if isinstance(fact.value, (int, float)) else str(fact.value)
            notes.append(f"ignored {label} {shown}: {fact.problem}")
        elif fact.name in ("event", "market", "bet_type"):
            notes.append(f"could not confirm the {label}: {fact.problem}")
        else:
            checks.append(f"could not confirm {label}: {fact.problem}")
    if stake is None and rules.bankroll and reading.facts.get("stake") is not None and reading.facts["stake"].ok:
        if slip is not None and not re.search(LABELS["stake"], text):
            notes.append(f"no stake on the slip; your max is {money(rules.bankroll * rules.max_stake_pct / 100, sign)}")
        else:
            checks.append("the slip shows a stake and none could be read")
    if v("odds_changed"):
        checks.append(f'the slip says the price moved: "{reading.facts["odds_changed"].quote}"')
    if v("bonus") and v("rollover") is None:
        notes.append(f'bonus funds: read the terms before you place it ("{reading.facts["bonus"].quote}")')
    said = {"live": bool(v("live")), "odds_changed": bool(v("odds_changed")), "bonus": bool(v("bonus"))}
    for name, hit in reading.scans.items():
        if not said[name] and reading.facts.get(name) is not None and reading.facts[name].ok:
            checks.append(f'the slip says "{hit}" and the reading found no {name.replace("_", " ")}')
    if not reading.source_checked:
        checks.append("read from an image with no text to check the quotes against")
    for fact in reading.facts.values():
        if fact.ok and fact.note:
            notes.append(f"{fact.name}: {fact.note}")

    if kelly_line and not walls:
        notes.insert(0, kelly_line)
    if walls:
        verdict.kind = "SKIP"
    elif checks:
        verdict.kind = "CHECK"
    return verdict
