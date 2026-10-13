"""Every example slip, replayed from the model's recorded answer, gets the verdict it was written for."""

import pytest
from conftest import EXAMPLES, recorded, slip_text

from betorskip.read import check
from betorskip.render import render
from betorskip.rules import Rules, decide

RULES = Rules.load(EXAMPLES / "rules.toml")

EXPECTED = {
    "single-home-win": ("BET", "BET · stake 2.0% of your bankroll · break-even 47.6%"),
    "decimal-comma": ("BET", "BET · stake 1.6% of your bankroll · break-even 43.5%"),
    "uk-fractional": ("BET", "BET · stake 4.0% of your bankroll · break-even 44.4%"),
    "us-parlay": ("BET", "BET · stake 4.0% of your bankroll · break-even 23.8%"),
    "live-in-play": ("BET", "BET · stake 2.0% of your bankroll · break-even 71.4%"),
    "big-stake": ("SKIP", "SKIP · stake £60.00 is 12.0% of your £500.00 bankroll (your max 5%)"),
    "six-fold": ("SKIP", "SKIP · 6 selections on one slip (your max 3)"),
    "thin-market": ("SKIP", "SKIP · the house keeps 14.4% on this market (your max 8%)"),
    "tennis-underdog": ("SKIP", "SKIP · odds 7.50 are above your max 6.00"),
    "correct-score": ("SKIP", 'SKIP · "correct score" is on your never list'),
    "bonus-rollover": ("SKIP", "SKIP · the bonus must be wagered 10 times (your max 5)"),
    "odds-changed": ("CHECK", 'CHECK · the slip says the price moved: "Odds changed 1.95 → 1.80"'),
}


def test_every_example_has_an_expectation():
    names = {path.stem for path in (EXAMPLES / "slips").glob("*.txt")}
    assert names == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_example_verdict(name):
    slip = slip_text(name)
    reading = check(recorded(name), slip)
    verdict = decide(reading, RULES, slip)
    kind, head = EXPECTED[name]
    assert verdict.kind == kind, render(reading, verdict)
    assert render(reading, verdict).splitlines()[0] == head


def test_the_market_line_shows_margin_and_fair_odds():
    slip = slip_text("single-home-win")
    reading = check(recorded("single-home-win"), slip)
    out = render(reading, decide(reading, RULES, slip))
    assert "market   2.10 / 3.40 / 3.50 · house margin 5.6% · fair odds 2.22 (45.1%)" in out


def test_fractional_odds_show_their_decimal_price():
    slip = slip_text("uk-fractional")
    reading = check(recorded("uk-fractional"), slip)
    assert "Ormsdale Athletic @ 5/4 (2.25)" in render(reading, decide(reading, RULES, slip))


def test_the_parlay_return_the_model_computed_is_refused():
    # The slip prints "To Win $64.00"; the model also offered "$84.00", which is not on it.
    slip = slip_text("us-parlay")
    reading = check(recorded("us-parlay"), slip)
    assert reading.facts["returns"].ok is False
    assert reading.value("to_win") == 64
    assert reading.problems == []
