"""The update check is off by default and asks once a day when it is on (PLAN3 phase 8)."""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db, update  # noqa: E402

db.init()
hits: list[str] = []


class Feed(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        hits.append(self.path)
        body = json.dumps({"tag_name": "v9.1.0", "html_url": "https://example.test/r"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve() -> str:
    s = HTTPServer(("127.0.0.1", 0), Feed)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{s.server_port}/latest"


def test_off_by_default_sends_nothing(monkeypatch):
    monkeypatch.setattr(update, "URL", _serve())
    hits.clear()
    assert update.check(force=True) == {"enabled": False, "current": update.VERSION}
    assert hits == []


def test_on_asks_once_a_day(monkeypatch):
    monkeypatch.setattr(update, "URL", _serve())
    hits.clear()
    db.set_setting("update_check", True)
    db.set_setting("update_last", None)
    r = update.check()
    assert r["latest"] == "v9.1.0" and r["newer"] is True and r["url"] == "https://example.test/r"
    assert update.check()["newer"] is True
    assert len(hits) == 1
    db.set_setting("update_check", False)


def test_version_order():
    assert update.newer("v0.10.0", "0.9.9")
    assert not update.newer("v0.2.0", "0.2.0")
    assert not update.newer("nightly", "0.2.0")
    # A beta comes before its final build, and a later beta is newer.
    assert update.newer("v0.3.0", "0.3.0-beta.1")
    assert update.newer("v0.3.0-beta.2", "0.3.0-beta.1")
    assert not update.newer("v0.3.0-beta.1", "0.3.0-beta.1")
    assert not update.newer("v0.3.0-beta.1", "0.3.0")
