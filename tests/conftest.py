import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "examples"


def slip_text(name: str) -> str:
    return (EXAMPLES / "slips" / f"{name}.txt").read_text(encoding="utf-8")


def recorded(name: str) -> dict:
    return json.loads((EXAMPLES / "readings" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test talks to a model or the internet."""
    import betorskip.model as model

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to call the model")

    monkeypatch.setattr(model, "chat", refuse)
