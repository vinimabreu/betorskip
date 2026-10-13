"""The model client stays on this machine; the command line wires it all together."""

import json

import pytest
from conftest import EXAMPLES, recorded

import betorskip.cli as cli
import betorskip.model as model


@pytest.mark.parametrize("host", ["http://10.0.0.5:11434", "https://ollama.example.com", "192.168.1.20"])
def test_remote_host_is_refused(host, monkeypatch):
    monkeypatch.undo()
    with pytest.raises(model.RemoteHostRefused):
        model.chat("s", "u", host=host)


@pytest.mark.parametrize("host", ["http://127.0.0.1:11434", "localhost:11434", "http://[::1]:11434", "0.0.0.0"])
def test_loopback_hosts(host):
    assert model.is_loopback(host)


def fake_asker(name):
    def factory(**kwargs):
        def ask(system, user, schema=None, images=None):
            return json.dumps(recorded(name))
        return ask
    return factory


def slip(name):
    return str(EXAMPLES / "slips" / f"{name}.txt")


RULES = str(EXAMPLES / "rules.toml")


def test_cli_prints_the_verdict(monkeypatch, capsys):
    monkeypatch.setattr(model, "asker", fake_asker("thin-market"))
    code = cli.main([slip("thin-market"), "--rules", RULES])
    assert code == 0 and capsys.readouterr().out.startswith("SKIP · the house keeps 14.4%")


def test_cli_bankroll_overrides_the_rules(monkeypatch, capsys):
    monkeypatch.setattr(model, "asker", fake_asker("big-stake"))
    cli.main([slip("big-stake"), "--rules", RULES, "--bankroll", "2000"])
    assert capsys.readouterr().out.startswith("BET · stake 3.0% of your bankroll")


@pytest.mark.parametrize("given", ["52%", "52", "0.52"])
def test_cli_prob_in_any_form(monkeypatch, capsys, given):
    monkeypatch.setattr(model, "asker", fake_asker("single-home-win"))
    cli.main([slip("single-home-win"), "--rules", RULES, "--prob", given])
    assert "at your 52% the bet is worth +9.2%" in capsys.readouterr().out


def test_cli_refuses_a_probability_out_of_range(monkeypatch):
    monkeypatch.setattr(model, "asker", fake_asker("single-home-win"))
    with pytest.raises(SystemExit):
        cli.main([slip("single-home-win"), "--prob", "150%"])


def test_cli_json(monkeypatch, capsys):
    monkeypatch.setattr(model, "asker", fake_asker("uk-fractional"))
    cli.main([slip("uk-fractional"), "--json", "--rules", RULES])
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"]["kind"] == "BET" and data["facts"]["selection 1"]["value"] == 2.25


def test_cli_explain_shows_quotes(monkeypatch, capsys):
    monkeypatch.setattr(model, "asker", fake_asker("us-parlay"))
    cli.main([slip("us-parlay"), "--explain", "--rules", RULES])
    out = capsys.readouterr().out
    assert '"Parlay odds +320"' in out and 'NO returns' in out


def test_cli_reports_a_missing_model(monkeypatch):
    def factory(**kwargs):
        def ask(*a, **k):
            raise model.ModelUnavailable("Ollama is not reachable")
        return ask
    monkeypatch.setattr(model, "asker", factory)
    assert cli.main([slip("six-fold")]) == 2


def test_cli_missing_file_and_binary_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(model, "asker", fake_asker("six-fold"))
    assert cli.main([str(tmp_path / "nope.txt")]) == 2
    binary = tmp_path / "photo.heic"
    binary.write_bytes(b"\x00\xff\xfe\x81" * 50)
    assert cli.main([str(binary)]) == 2
    assert "not text" in capsys.readouterr().err


def test_bad_rules_file_is_a_clean_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(model, "asker", fake_asker("six-fold"))
    assert cli.main([slip("six-fold"), "--rules", str(tmp_path / "missing.toml")]) == 2
    broken = tmp_path / "broken.toml"
    broken.write_text("[odds\nmax_legs = \n")
    assert cli.main([slip("six-fold"), "--rules", str(broken)]) == 2
    assert "betorskip: rules" in capsys.readouterr().err


def test_negative_bankroll_flag(monkeypatch, capsys):
    monkeypatch.setattr(model, "asker", fake_asker("six-fold"))
    assert cli.main([slip("six-fold"), "--bankroll", "-1"]) == 2


def test_ocr_reads_the_slip_line_by_line(monkeypatch, tmp_path):
    # Tesseract's default layout analysis prints every label before every amount on a two-column
    # slip ("Stake" far from "£10.00"); --psm 4 keeps each line together.
    seen = {}

    class Done:
        returncode, stdout = 0, "Stake £10.00\n"

    def run(command, **kwargs):
        seen["command"] = command
        return Done()

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli.ocr(tmp_path / "slip.png") == "Stake £10.00\n"
    assert seen["command"][-2:] == ["--psm", "4"]


def test_cli_reports_an_unreadable_answer(monkeypatch, capsys):
    def factory(**kwargs):
        return lambda *a, **k: "not json"
    monkeypatch.setattr(model, "asker", factory)
    assert cli.main([slip("six-fold")]) == 2
    assert "could not be read" in capsys.readouterr().err
