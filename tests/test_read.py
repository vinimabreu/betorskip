"""Each check on the model's reading, with the failure that motivated it."""

import copy
import json
from pathlib import Path

import pytest
from conftest import recorded, slip_text

from betorskip.read import amounts, check, parse_odds, read_image


@pytest.mark.parametrize("printed, decimal", [
    ("2.10", 2.10), ("2,10", 2.10), ("11/10", 2.10), ("5/4", 2.25), ("evens", 2.0), ("EVS", 2.0),
    ("+150", 2.50), ("-110", 1 + 100 / 110), ("+100", 2.0), ("−120", 1 + 100 / 120),
])
def test_odds_in_every_format(printed, decimal):
    assert parse_odds(printed) == pytest.approx(decimal)


@pytest.mark.parametrize("printed", ["1", "0.95", "+50", "abc", "1/0", ""])
def test_not_odds(printed):
    assert parse_odds(printed) is None


@pytest.mark.parametrize("quote, value", [
    ("Stake £10.00", 10.0), ("€21,00", 21.0), ("£1,050.00", 1050.0), ("R$ 1.234,56", 1234.56), ("2K", 2000.0),
])
def test_amounts_in_either_convention(quote, value):
    assert value in amounts(quote)


def test_a_quote_that_is_not_on_the_slip():
    raw = recorded("single-home-win")
    raw["selections"][0]["quote"] = "Northgate Rovers to win 2.10"
    assert check(raw, slip_text("single-home-win")).facts["selection 1"].problem == "quote is not on the slip"


def test_odds_that_are_not_in_their_quote():
    raw = recorded("single-home-win")
    raw["selections"][0]["odds"] = "2.20"
    assert check(raw, slip_text("single-home-win")).facts["selection 1"].ok is False


def test_an_amount_is_not_odds():
    # "10.00" is on the slip only as "£10.00": money, not a price.
    raw = recorded("single-home-win")
    raw["total_odds"] = {"text": "10.00", "quote": "10.00"}
    fact = check(raw, slip_text("single-home-win")).facts["total_odds"]
    assert fact.ok is False and "amount of money" in fact.problem


def test_a_number_needs_its_label():
    raw = recorded("single-home-win")
    raw["returns"] = {"value": 10.0, "quote": "Place bet £10.00"}
    assert "does not label" in check(raw, slip_text("single-home-win")).facts["returns"].problem


def test_a_number_must_be_in_its_quote():
    raw = recorded("single-home-win")
    raw["stake"] = {"value": 100.0, "quote": "Stake £10.00"}
    assert check(raw, slip_text("single-home-win")).facts["stake"].problem == "100 is not the number in the quote"


def test_the_market_must_add_up_to_one_whole_market():
    # The model offered the picked price alone as "the market": 57%, not a book.
    reading = check(recorded("bonus-rollover"), slip_text("bonus-rollover"))
    assert reading.facts["market_odds"].ok is False
    assert "not one whole market" in reading.facts["market_odds"].problem


def test_two_parlay_legs_are_not_a_market():
    reading = check(recorded("us-parlay"), slip_text("us-parlay"))
    assert reading.facts["market_odds"].ok is False


def test_a_flag_needs_words_of_its_kind_around_it():
    raw = recorded("single-home-win")
    raw["live"] = {"present": True, "quote": "Match Result"}
    assert check(raw, slip_text("single-home-win")).facts["live"].problem == "the slip does not say that there"


def test_the_slips_arithmetic_is_checked():
    slip = slip_text("single-home-win").replace("Potential returns £21.00", "Potential returns £27.00")
    raw = recorded("single-home-win")
    raw["returns"] = {"value": 27.0, "quote": "Potential returns £27.00"}
    reading = check(raw, slip)
    assert reading.problems == ["the slip's numbers disagree: £10.00 at 2.10 returns £21.00, the slip shows £27.00"]


def test_a_lost_decimal_point_in_the_odds_is_put_back():
    # OCR drops the small point: 2.10 comes out as 210. Stake 10 and returns 21.00 say where it was.
    slip = slip_text("single-home-win").replace("2.10", "210")
    raw = recorded("single-home-win")
    raw["selections"][0].update(odds="210", quote="Northgate Rovers 210")
    raw["total_odds"] = {"text": "", "quote": ""}
    raw["market_odds"] = {"prices": [], "quote": ""}
    reading = check(raw, slip)
    assert reading.problems == []
    assert reading.odds() == pytest.approx(2.10)
    assert "no decimal point" in reading.facts["selection 1"].note


def test_a_lost_decimal_point_in_the_returns_is_put_back():
    slip = slip_text("single-home-win").replace("£21.00", "£2100")
    raw = recorded("single-home-win")
    raw["returns"] = {"value": 2100, "quote": "Potential returns £2100"}
    reading = check(raw, slip)
    assert reading.problems == [] and reading.value("returns") == pytest.approx(21.0)


def test_odds_and_returns_both_without_a_point():
    # 10 x 210 = 2100: the arithmetic agrees, so the bare 210 itself has to be caught.
    slip = slip_text("single-home-win").replace("2.10", "210").replace("£21.00", "£2100")
    raw = recorded("single-home-win")
    raw["selections"][0].update(odds="210", quote="Northgate Rovers 210")
    raw["total_odds"] = {"text": "", "quote": ""}
    raw["market_odds"] = {"prices": [], "quote": ""}
    raw["returns"] = {"value": 2100, "quote": "Potential returns £2100"}
    assert check(raw, slip).problems == ["odds printed as 210 with no decimal point"]


def test_winnings_that_include_the_stake_are_the_return():
    slip = slip_text("decimal-comma")
    raw = recorded("decimal-comma")
    raw["returns"] = {"value": None, "quote": ""}
    raw["to_win"] = {"value": 18.4, "quote": "Possible winnings €18,40"}
    reading = check(raw, slip)
    assert reading.value("returns") == pytest.approx(18.4) and "to_win" not in reading.facts
    assert reading.problems == []


def test_a_missed_leg_is_caught_by_the_fold_count():
    raw = recorded("six-fold")
    raw["selections"] = raw["selections"][:5]
    reading = check(raw, slip_text("six-fold"))
    assert any("6-Fold" in problem for problem in reading.problems)


def test_the_total_must_match_the_legs():
    raw = recorded("six-fold")
    raw["selections"][2].update(odds="1.30", quote="Crestwood · Match Result · 1.30")
    reading = check(raw, slip_text("six-fold"))
    assert any("legs multiplied" in problem for problem in reading.problems)


def test_scans_read_the_slip_itself():
    reading = check(recorded("live-in-play"), slip_text("live-in-play"))
    assert reading.scans["live"] == "67'"
    assert "bonus" in check(recorded("bonus-rollover"), slip_text("bonus-rollover")).scans


def test_an_image_read_without_text_is_unchecked():
    import json

    raw = recorded("single-home-win")
    reading = read_image(lambda *a, **k: json.dumps(raw), b"\x89PNG")
    assert reading.source_checked is False


def test_check_does_not_change_the_raw_answer():
    raw = recorded("decimal-comma")
    before = copy.deepcopy(raw)
    check(raw, slip_text("decimal-comma"))
    assert raw == before


# Tesseract text and Gemma's raw answer for four of the made-up slips, rendered as low-resolution
# JPEG screenshots. Each one taught a check. Two teams were renamed after recording, because their first
# made-up names turned out to belong to real clubs; everything else is as Tesseract and Gemma gave it.
OCR_CASES = json.loads((Path(__file__).parent / "ocr_cases.json").read_text(encoding="utf-8"))


def ocr_reading(name):
    case = OCR_CASES[name]
    return check(case["raw"], case["ocr"])


def test_ocr_lost_the_point_in_the_total_and_the_returns_put_it_back():
    # "Total odds 1747": stake £5.00 and returns £87.33 say it is 17.47.
    reading = ocr_reading("six-fold")
    assert reading.facts["total_odds"].value == pytest.approx(17.47)
    assert "1747 with no decimal point" in reading.facts["total_odds"].note


def test_ocr_misread_legs_are_caught_by_the_total():
    # 1.62 came out as 1.82, 1.80 as 4.80, 1.30 as 4.30: the legs multiply to 173, not 17.47.
    reading = ocr_reading("six-fold")
    assert any("legs multiplied (173.08)" in problem for problem in reading.problems)
    assert "odds" in reading.suspect


def test_gemma_repairing_a_garbled_line_is_refused():
    # The text has "Redcliff Rangers 472" and no price beside Stonebridge; the model's tidy
    # "Redcliff Rangers 1.72 Draw 3.20 Stonebridge 4.72" is not in it.
    reading = ocr_reading("thin-market")
    assert reading.facts["market_odds"].problem == "quote is not on the slip"


def test_the_model_putting_a_point_back_does_not_count_until_the_arithmetic_agrees():
    # The text says 172; the model wrote 1.72. The total keeps what the text says, then the
    # returns (£10.00 -> £17.20) put the point back.
    reading = ocr_reading("thin-market")
    assert reading.facts["total_odds"].value == pytest.approx(1.72)
    assert "172 with no decimal point" in reading.facts["total_odds"].note


def test_a_hash_is_the_plus_of_american_odds():
    assert parse_odds("#320") == pytest.approx(4.2)


def test_a_lost_minus_sign_cannot_skip_a_bet():
    # "-110" came out as "“110"; read as decimal odds 110, the parlay would be "above your max".
    from betorskip.rules import Rules, decide
    case = OCR_CASES["us-parlay"]
    reading = check(case["raw"], case["ocr"])
    verdict = decide(reading, Rules.load(Path(__file__).parent.parent / "examples" / "rules.toml"), case["ocr"])
    assert "odds printed as 110 with no decimal point" in reading.problems
    assert verdict.kind == "CHECK" and not verdict.walls


def test_a_single_quoted_twice_is_one_selection():
    reading = ocr_reading("uk-fractional")
    assert len(reading.selections()) == 1 and reading.problems == []


def test_the_nearest_label_wins():
    # "£45.00" sits after "Stake £20.00 Returns": it is the return, not the stake.
    raw = recorded("uk-fractional")
    raw["stake"] = {"value": 45.0, "quote": "£45.00"}
    raw["returns"] = {"value": None, "quote": ""}
    assert "does not label" in check(raw, slip_text("uk-fractional")).facts["stake"].problem


def test_ocr_retums_still_labels_the_return():
    slip = slip_text("single-home-win").replace("Potential returns £21.00", "Retums £21.00")
    raw = recorded("single-home-win")
    raw["returns"] = {"value": 21.0, "quote": "£21.00"}
    assert check(raw, slip).facts["returns"].ok


def test_a_book_far_over_100_percent_is_not_one_market():
    raw = recorded("single-home-win")
    raw["market_odds"] = {"prices": ["2.10", "3.40", "3.50", "1.25"],
                          "quote": "Northgate Rovers 2.10 Draw 3.40 Kingsport City 3.50"}
    slip = slip_text("single-home-win").replace("Kingsport City 3.50", "Kingsport City 3.50 1.25")
    raw["market_odds"]["quote"] = "Northgate Rovers 2.10 Draw 3.40 Kingsport City 3.50 1.25"
    fact = check(raw, slip).facts["market_odds"]
    assert fact.ok is False and "add up to 136%" not in fact.problem and "not one whole market" in fact.problem


def test_money_written_after_the_number_is_not_odds():
    from betorskip.read import _odds_in
    assert not _odds_in("8,00", "Stake 8,00 €")
    assert not _odds_in("10.00", "10.00 EUR")
    assert _odds_in("2,30", "Stonebridge 2,30")


@pytest.mark.parametrize("answer", ["", "[]", '{"selections": 5}'])
def test_an_answer_that_is_not_the_schema_is_unreadable(answer):
    from betorskip.read import UnreadableAnswer, read_slip
    with pytest.raises(UnreadableAnswer):
        read_slip(lambda *a, **k: answer, slip_text("single-home-win"))


def test_a_label_inside_the_quote_belongs_to_the_number_after_it():
    # "Stake £20.00 Returns £45.00" quoted for a stake of 45: the label before 45 is "Returns".
    raw = recorded("uk-fractional")
    raw["stake"] = {"value": 45.0, "quote": "Stake £20.00 Returns £45.00"}
    raw["returns"] = {"value": None, "quote": ""}
    slip = slip_text("uk-fractional").replace("Stake £20.00\nReturns £45.00", "Stake £20.00 Returns £45.00")
    assert "does not label" in check(raw, slip).facts["stake"].problem


def test_a_number_given_as_text_is_not_read():
    raw = recorded("single-home-win")
    raw["stake"] = {"value": "10", "quote": "Stake £10.00"}
    assert check(raw, slip_text("single-home-win")).facts["stake"].problem == "'10' is not a number"
