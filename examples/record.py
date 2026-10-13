"""Read every example slip with the local model and save the raw answers.

The tests replay these files, so they run anywhere without a model. Run this
again after changing the prompt: python examples/record.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from betorskip import model  # noqa: E402
from betorskip.read import read_slip  # noqa: E402

ask = model.asker()
for path in sorted((ROOT / "slips").glob("*.txt")):
    reading = read_slip(ask, path.read_text(encoding="utf-8"))
    out = ROOT / "readings" / f"{path.stem}.json"
    out.write_text(json.dumps(reading.raw, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out.name, flush=True)
