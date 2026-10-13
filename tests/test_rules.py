"""Each rule, and what happens when the reading cannot confirm what a rule needs."""

from dataclasses import replace

import pytest
from conftest import EXAMPLES, recorded, slip_text

from betorskip.read import check
from betorskip.rules import Rules, decide

RULES = Rules.load(EXAMPLES / "rules.toml")


def verdict_for(name, rules=RULES, raw=None, slip=None, prob=None):
    slip = slip if slip is not None else slip_text(name)
    reading = check(raw if raw is not None else recorded(name), slip)
    return decide(reading, rules, slip, prob=prob)


def test_defaults_leave_most_rules_off():
    v = verdict_for("six-fold", rules=Rules())
    assert v.kind == "BET" and v.stake_pct is None


def test_bankroll_zero_turns_stake_rules_off():
    assert verdict_for("big-stake", rules=replace(RULES, bankroll=0)).kind == "BET"


def test_a_bigger_bankroll_lets_the_stake_through():
    assert verdict_for("big-stake", rules=replace(RULES, bankroll=2000)).kind == "BET"


def test_min_odds():
    v = verdict_for("live-in-play", rules=replace(RULES, min_odds=1.5))
    assert v.walls == ["odds 1.40 are below your min 1.50"]


def test_max_legs_zero_is_off():
    v = verdict_for("six-fold", rules=replace(RULES, max_legs=0, max_odds=0))
    assert v.kind == "BET"


def test_never_matches_whole_words_only():
    rules = replace(RULES, never=["result"])
    assert verdict_for("single-home-win", rules=rules).kind == "SKIP"
    assert verdict_for("single-home-win", rules=replace(RULES, never=["resul"])).kind == "BET"


def test_live_can_be_a_wall():
    v = verdict_for("live-in-play", rules=replace(RULES, live_is_wall=True))
    assert v.kind == "SKIP" and v.walls[0].startswith("a live bet")


def test_your_probability_adds_value_and_kelly():
    v = verdict_for("single-home-win", prob=0.52)
    assert v.kind == "BET"
    assert any(note.startswith("at your 52% the bet is worth +9.2% of the stake; Kelly says 8.4% of bankroll, "
                               "0.25 Kelly is £10.45") for note in v.notes)


def test_a_bet_below_your_edge_is_a_wall():
    v = verdict_for("single-home-win", prob=0.45)
    assert v.kind == "SKIP" and v.walls == ["at your 45% the bet is worth -5.5% of the stake (your min +2%)"]


def test_the_slip_disagreeing_with_itself_is_a_check():
    slip = slip_text("single-home-win").replace("£21.00", "£27.00")
    raw = recorded("single-home-win")
    raw["returns"] = {"value": 27.0, "quote": "Potential returns £27.00"}
    v = verdict_for("single-home-win", raw=raw, slip=slip)
    assert v.kind == "CHECK" and "disagree" in v.checks[0]


def test_unconfirmed_odds_are_a_check():
    raw = recorded("single-home-win")
    raw["selections"][0]["quote"] = "Northgate Rovers to win 2.10"
    raw["total_odds"] = {"text": "", "quote": ""}
    v = verdict_for("single-home-win", raw=raw)
    assert v.kind == "CHECK" and v.checks[0] == "could not confirm selection 1: quote is not on the slip"


def test_no_odds_at_all_is_a_check():
    raw = recorded("single-home-win")
    raw["selections"] = []
    raw["total_odds"] = {"text": "", "quote": ""}
    assert "no odds could be read from the slip" in verdict_for("single-home-win", raw=raw).checks


def test_a_stake_on_the_slip_that_was_not_read_is_a_check():
    raw = recorded("single-home-win")
    raw["stake"] = {"value": None, "quote": ""}
    assert "the slip shows a stake and none could be read" in verdict_for("single-home-win", raw=raw).checks


def test_a_slip_with_no_stake_gets_your_max_as_a_note():
    slip = slip_text("single-home-win").replace("Stake £10.00\n", "").replace("Place bet £10.00", "Place bet")
    raw = recorded("single-home-win")
    raw["stake"] = {"value": None, "quote": ""}
    raw["returns"] = {"value": None, "quote": ""}
    v = verdict_for("single-home-win", raw=raw, slip=slip)
    assert v.kind == "BET" and "no stake on the slip; your max is 25.00" in v.notes


def test_a_live_scan_the_model_missed_is_a_check():
    raw = recorded("live-in-play")
    raw["live"] = {"present": False, "quote": ""}
    v = verdict_for("live-in-play", raw=raw)
    assert v.kind == "CHECK" and "the reading found no live" in v.checks[0]


def test_bonus_without_rollover_is_a_note():
    raw = recorded("bonus-rollover")
    raw["rollover"] = {"value": None, "quote": ""}
    slip = slip_text("bonus-rollover").replace(
        "Your bonus must be wagered 10 times before it can be withdrawn. Minimum odds 1.50.\n", "")
    v = verdict_for("bonus-rollover", raw=raw, slip=slip)
    assert v.kind == "BET" and any(note.startswith("bonus funds: read the terms") for note in v.notes)


def test_a_rollover_the_slip_does_not_show_is_ignored():
    raw = recorded("single-home-win")
    raw["rollover"] = {"value": 10, "quote": "10 times"}
    v = verdict_for("single-home-win", raw=raw)
    assert v.kind == "BET" and any("does not show a rollover" in note for note in v.notes)


def test_an_image_without_text_cannot_be_a_bet():
    reading = check(recorded("single-home-win"), None)
    assert decide(reading, RULES, None).kind == "CHECK"


@pytest.mark.parametrize("body, message", [
    ("[odds]\nmax_legz = 3\n", "unknown setting"),
    ("[odds]\nmax_legs = 2.5\n", "should be int"),
    ("[markets]\nnever = \"correct score\"\n", "list of words"),
    ("[bankroll]\nbankroll = -5\n", "cannot be negative"),
    ("[markets]\nlive_is_wall = 1\n", "should be bool"),
])
def test_rules_file_is_strict(tmp_path, body, message):
    path = tmp_path / "rules.toml"
    path.write_text(body)
    with pytest.raises(ValueError, match=message):
        Rules.load(path)


def test_example_rules_load():
    assert RULES.bankroll == 500 and RULES.never == ["correct score", "first goalscorer", "bet builder"]


def test_a_misread_market_cannot_skip_when_the_slip_disputes_its_odds():
    # Low-resolution OCR read 2,30 / 3,25 / 3,10 as 2,20 / 2,25 / 2,20: a "35% margin". The pick's
    # 2,20 times the stake does not give the slip's return, so the odds are in doubt, and so is the market.
    slip = slip_text("decimal-comma").replace("2,30", "2,20").replace("3,25", "2,25").replace("3,10", "2,20")
    raw = recorded("decimal-comma")
    raw["selections"][0].update(odds="2,20", quote="2,20")
    raw["total_odds"] = {"text": "2,20", "quote": "2,20"}
    raw["market_odds"] = {"prices": ["2,20", "2,25", "2,20"], "quote": "1 2,20 X 2,25 2 2,20"}
    v = verdict_for("decimal-comma", raw=raw, slip=slip)
    assert v.kind == "CHECK" and not v.walls


def test_a_market_read_in_part_says_the_margin_is_unknown():
    # On one screenshot Tesseract dropped the "Draw 3.20" line: 1.72 and 4.00 alone are not a market,
    # so the margin rule cannot apply, and the verdict says so instead of staying silent.
    slip = slip_text("thin-market").replace("Draw 3.20\n", "")
    raw = recorded("thin-market")
    raw["market_odds"] = {"prices": ["1.72", "4.00"], "quote": "Redcliff Rangers 1.72 Stonebridge 4.00"}
    v = verdict_for("thin-market", raw=raw, slip=slip.replace("Redcliff Rangers 1.72\nStonebridge 4.00",
                                                              "Redcliff Rangers 1.72 Stonebridge 4.00"))
    assert v.kind == "BET" and any(note.startswith("house margin unknown") for note in v.notes)


def test_a_stake_the_slip_disputes_cannot_skip():
    # £60.00 is 12% of the bankroll, but the slip's return disagrees with stake times odds.
    slip = slip_text("big-stake").replace("Potential returns £99.00", "Potential returns £120.00")
    raw = recorded("big-stake")
    raw["returns"] = {"value": 120.0, "quote": "Potential returns £120.00"}
    v = verdict_for("big-stake", raw=raw, slip=slip)
    assert v.kind == "CHECK" and not v.walls


def test_a_leg_count_the_slip_disputes_cannot_skip():
    # Five legs read on a 6-Fold: the count is in doubt, so "too many legs" does not get to decide.
    raw = recorded("six-fold")
    raw["selections"] = raw["selections"][:5]
    v = verdict_for("six-fold", raw=raw)
    assert v.kind == "CHECK" and not v.walls


@pytest.mark.parametrize("name", ["big-stake", "bonus-rollover", "live-in-play", "six-fold", "thin-market"])
def test_an_image_read_without_text_cannot_skip(name):
    reading = check(recorded(name), None)
    v = decide(reading, replace(RULES, live_is_wall=True), None)
    assert v.kind == "CHECK" and not v.walls


def test_kelly_never_goes_over_your_max_stake():
    v = verdict_for("single-home-win", prob=0.60)
    assert any(note.endswith("0.25 Kelly is £25.00 (your max stake)") for note in v.notes)


def test_no_kelly_on_a_skip():
    v = verdict_for("correct-score", prob=0.5)
    assert v.kind == "SKIP" and not any("Kelly" in note for note in v.notes)


def test_your_probability_is_shown_as_you_gave_it():
    v = verdict_for("single-home-win", prob=0.005)
    assert v.walls[0].startswith("at your 0.5% the bet")


@pytest.mark.parametrize("body, message", [
    ("[bankroll]\nmax_stake_pct = nan\n", "must be a number"),
    ("[bankroll]\nmax_stake_pct = inf\n", "must be a number"),
    ("[bankroll]\nmax_stake_pct = 5\n[odds]\nmax_stake_pct = 50\n", "set twice"),
    ("[markets]\nnever = [\"\"]\n", "empty entry"),
    ("[value]\nkelly_fraction = 25\n", "at most 1"),
    ("[bankroll]\nmax_stake_pct = 150\n", "at most 100"),
])
def test_rules_that_would_switch_a_rule_off_silently(tmp_path, body, message):
    path = tmp_path / "rules.toml"
    path.write_text(body)
    with pytest.raises(ValueError, match=message):
        Rules.load(path)
