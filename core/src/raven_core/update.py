"""Update check (PLAN3 phase 8). Off by default: Raven sends no request until the user turns it on.

The check is one GET of the latest release, at most once a day. It sends no file data and no user id.
RAVEN_UPDATE_URL points it at another release feed (tests, a mirror).
"""

from __future__ import annotations

import os
import re
import time

from . import db
from .config import VERSION

URL = os.environ.get("RAVEN_UPDATE_URL", "https://api.github.com/repos/9u3u3/Raven/releases/latest")
EVERY = 24 * 3600


def _parse(v: str) -> tuple[int, ...]:
    m = re.match(r"v?(\d+(?:\.\d+)*)", v.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def newer(latest: str, current: str = VERSION) -> bool:
    a, b = _parse(latest), _parse(current)
    return bool(a) and a > b


def check(force: bool = False) -> dict:
    if not db.get_setting("update_check", False):
        return {"enabled": False, "current": VERSION}
    last = db.get_setting("update_last") or {}
    if not force and time.time() - last.get("checked", 0) < EVERY:
        return {"enabled": True, "current": VERSION, **last, "newer": newer(last.get("latest", ""))}
    import httpx

    out: dict = {"checked": int(time.time())}
    try:
        r = httpx.get(URL, timeout=5, headers={"Accept": "application/vnd.github+json"}, follow_redirects=True)
        if r.status_code == 404:
            out["error"] = "No release is published yet."
        else:
            r.raise_for_status()
            d = r.json()
            out.update(latest=d.get("tag_name", ""), url=d.get("html_url", ""))
    except Exception as e:  # noqa: BLE001  (offline or a bad feed: say so, try again tomorrow)
        out["error"] = f"The update check failed: {type(e).__name__}"
    db.set_setting("update_last", out)
    return {"enabled": True, "current": VERSION, **out, "newer": newer(out.get("latest", ""))}
