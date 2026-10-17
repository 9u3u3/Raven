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


def changelog() -> str:
    """CHANGELOG.md: next to this file in a package (the spec bundles it), else at the repo root."""
    for p in (Path(__file__).with_name("CHANGELOG.md"), Path(__file__).resolve().parents[3] / "CHANGELOG.md"):
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""
