"""Third-party notices for Settings, Help (PLAN4 B6). scripts/notices.py writes third_party.json."""

from __future__ import annotations

import json
from pathlib import Path

_FILE = Path(__file__).with_name("third_party.json")


def load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"models": [], "programs": [], "python": [], "node": []}
