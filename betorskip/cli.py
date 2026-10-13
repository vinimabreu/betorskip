"""betorskip: paste a bet slip (or point at a screenshot of one) and get BET, SKIP or CHECK."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

from . import model
from .read import UnreadableAnswer, read_image, read_slip
from .render import explain, render
from .rules import Rules, decide

IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_RULES = Path(os.environ.get("BETORSKIP_RULES", Path.home() / ".config" / "betorskip" / "rules.toml"))


def ocr(path: Path) -> str | None:
    """Text of a screenshot from Tesseract, if it is installed. The model reads this text, not the
    pixels, so every quote can be checked against something the model did not write.

    --psm 4 reads the slip as one column, line by line. The default layout analysis sees a slip's
    labels and amounts as two blocks and prints every label before every amount, which parts
    "Stake" from "£10.00"."""
    if not shutil.which("tesseract"):
        return None
    done = subprocess.run(["tesseract", str(path), "stdout", "--psm", "4"], capture_output=True, text=True,
                          timeout=120)
    return done.stdout if done.returncode == 0 and done.stdout.strip() else None


def _clipboard() -> str:
    if not shutil.which("pbpaste"):
        raise SystemExit("nothing to read: pass a file, pipe the slip in, or copy it first (pbpaste not found)")
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def probability(text: str) -> float:
    """'55%', '55' and '0.55' are all 55%."""
    value = float(text.strip().rstrip("%"))
    if value > 1 or text.strip().endswith("%"):
        value /= 100
    if not 0 < value < 1:
        raise argparse.ArgumentTypeError(f"{text} is not a probability between 0 and 100%")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="betorskip", description=__doc__)
    parser.add_argument("source", nargs="?", help="a text file or a screenshot; '-' reads stdin; "
                                                  "nothing reads the clipboard")
    parser.add_argument("--rules", type=Path, help=f"your rules (default {DEFAULT_RULES} if it exists)")
    parser.add_argument("--bankroll", type=float, help="today's bankroll, instead of the one in your rules")
    parser.add_argument("--prob", type=probability,
                        help="your own chance for this bet (55%%, 55 or 0.55): adds expected value and Kelly")
    parser.add_argument("--explain", action="store_true", help="show every fact with the words it came from")
    parser.add_argument("--json", action="store_true", help="print the reading and verdict as JSON")
    parser.add_argument("--model", default=model.DEFAULT_MODEL)
    parser.add_argument("--host", default=model.DEFAULT_HOST)
    parser.add_argument("--allow-remote-model", action="store_true",
                        help="let the slip leave this machine for a non-local Ollama host")
    args = parser.parse_args(argv)

    rules_path = args.rules or (DEFAULT_RULES if DEFAULT_RULES.exists() else None)
    try:
        rules = Rules.load(rules_path) if rules_path else Rules()
    except (OSError, ValueError) as error:  # tomllib.TOMLDecodeError is a ValueError
        print(f"betorskip: rules {rules_path}: {error}", file=sys.stderr)
        return 2
    if args.bankroll is not None:
        if args.bankroll < 0:
            print("betorskip: --bankroll cannot be negative", file=sys.stderr)
            return 2
        rules = replace(rules, bankroll=args.bankroll)
    ask = model.asker(model=args.model, host=args.host, allow_remote=args.allow_remote_model)

    slip: str | None
    try:
        if args.source and args.source != "-" and Path(args.source).suffix.lower() in IMAGES:
            path = Path(args.source)
            slip = ocr(path)
            reading = read_slip(ask, slip) if slip else read_image(ask, path.read_bytes())
        else:
            if args.source == "-" or (args.source is None and not sys.stdin.isatty()):
                slip = sys.stdin.read()
            elif args.source:
                slip = Path(args.source).read_text(encoding="utf-8")
            else:
                slip = _clipboard()
            if not slip.strip():
                raise SystemExit("the slip is empty")
            reading = read_slip(ask, slip)
    except (model.ModelUnavailable, model.RemoteHostRefused, UnreadableAnswer) as error:
        print(f"betorskip: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"betorskip: cannot read {args.source}: {error.strerror or error}", file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        print(f"betorskip: {args.source} is not text; screenshots must be .png, .jpg, .jpeg or .webp",
              file=sys.stderr)
        return 2

    verdict = decide(reading, rules, slip, prob=args.prob)
    if args.json:
        print(json.dumps({"verdict": asdict(verdict),
                          "facts": {name: asdict(fact) for name, fact in reading.facts.items()},
                          "problems": reading.problems, "scans": reading.scans,
                          "source_checked": reading.source_checked}, indent=2))
    else:
        print(render(reading, verdict))
        if args.explain:
            print()
            print(explain(reading))
    return 0
