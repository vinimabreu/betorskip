"""One screen per slip: the verdict first, then what it rests on."""

from __future__ import annotations

from .read import Reading, show_odds
from .rules import Verdict, money


def _head(verdict: Verdict) -> str:
    if verdict.kind == "SKIP":
        return f"SKIP · {verdict.walls[0]}"
    if verdict.kind == "CHECK":
        return f"CHECK · {verdict.checks[0]}"
    parts = ["BET"]
    if verdict.stake_pct is not None:
        parts.append(f"stake {verdict.stake_pct:.1%} of your bankroll")
    if verdict.break_even is not None:
        parts.append(f"break-even {verdict.break_even:.1%}")
    return " · ".join(parts)


def _bet(reading: Reading) -> str:
    v, sign = reading.value, reading.currency
    legs = reading.selections()
    parts = []
    kind = v("bet_type") or ("single" if len(legs) == 1 else f"{len(legs)} selections" if legs else "")
    if kind:
        parts.append(kind.lower() if kind.lower() == "single" else kind)
    if len(legs) == 1 and legs[0].ok:
        leg = legs[0]
        price = leg.printed if leg.printed == show_odds(leg.value) else f"{leg.printed} ({show_odds(leg.value)})"
        parts.append(f"{leg.pick} @ {price}" if leg.pick else f"odds {price}")
    elif reading.odds() is not None:
        parts.append(f"odds {show_odds(reading.odds())}")
    if v("stake") is not None:
        parts.append(f"stake {money(v('stake'), sign)}")
    if v("returns") is not None:
        parts.append(f"returns {money(v('returns'), sign)}")
    if v("to_win") is not None:
        parts.append(f"to win {money(v('to_win'), sign)}")
    return " · ".join(parts)


def _market(reading: Reading, verdict: Verdict) -> str:
    fact = reading.facts.get("market_odds")
    if fact is None or not fact.ok or verdict.margin is None:
        return ""
    line = f"{fact.printed} · house margin {verdict.margin:.1%}"
    if verdict.margin_if_whole:
        line += ", if these are all the outcomes"
    return line


def _fair(verdict: Verdict) -> str:
    """Two ways of taking the margin out, side by side, so the fair price reads as a range."""
    if verdict.fair_odds is None or verdict.margin is None:
        return ""
    parts = []
    if verdict.fair_odds_power is not None:
        parts.append(f"power {show_odds(verdict.fair_odds_power)} ({1 / verdict.fair_odds_power:.1%})")
    parts.append(f"proportional {show_odds(verdict.fair_odds)} ({1 / verdict.fair_odds:.1%})")
    return " · ".join(parts)


def render(reading: Reading, verdict: Verdict) -> str:
    lines = [_head(verdict)]
    legs = reading.selections()
    title = " · ".join(x for x in (reading.value("event"), reading.value("market")) if x)
    if title and len(legs) <= 1:
        lines.append(title)
    bet = _bet(reading)
    if bet:
        lines.append(f"bet      {bet}")
    if len(legs) > 1:
        lines.append("legs     " + " · ".join(
            f"{leg.pick} @ {leg.printed}" if leg.ok else f"{leg.pick} @ ?" for leg in legs))
    market = _market(reading, verdict)
    if market:
        lines.append(f"market   {market}")
        fair = _fair(verdict)
        if fair:
            lines.append(f"fair     {fair}")
    for wall in verdict.walls[1:]:
        lines.append(f"wall     {wall}")
    for item in verdict.checks[1 if verdict.kind == "CHECK" else 0:]:
        lines.append(f"check    {item}")
    for note in verdict.notes:
        lines.append(f"note     {note}")
    return "\n".join(lines)


def explain(reading: Reading) -> str:
    """Every fact, with the words it came from and whether they held up."""
    lines = []
    for fact in reading.facts.values():
        if fact.value in (None, False, "") and fact.ok:
            continue
        mark = "ok " if fact.ok else "NO "
        shown = fact.printed or fact.value
        if isinstance(shown, float):
            shown = f"{shown:g}"
        quote = f'  "{" ".join(fact.quote.split())}"' if fact.quote else ""
        problem = f"  <- {fact.problem}" if fact.problem else ""
        lines.append(f"{mark}{fact.name:<14}{str(shown):<20}{quote}{problem}")
    for problem in reading.problems:
        lines.append(f"!! {problem}")
    for name, hit in reading.scans.items():
        lines.append(f"scan {name:<13}\"{hit}\"")
    return "\n".join(lines)
