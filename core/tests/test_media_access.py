"""Media URLs carry a media-only token, and /fs/raw cannot open a file as a page (PLAN3 P0-4)."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))
os.environ.setdefault("RAVEN_TOKEN", "test-token")
os.environ["RAVEN_MEDIA_TOKEN"] = "media-token"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from raven_core import app as app_mod  # noqa: E402
from raven_core import db  # noqa: E402

db.init()
# Another test file can import the app first. Then the env value above comes too late, so set it here.
app_mod.MEDIA_TOKEN = "media-token"
client = TestClient(app_mod.app)


@pytest.fixture()
def files(tmp_path):
    (tmp_path / "page.html").write_text("<script>fetch('/fs/delete')</script>")
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (tmp_path / "pic.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    return tmp_path


def test_html_is_sent_as_plain_text_in_a_sandbox(files):
    r = client.get("/fs/raw", params={"path": str(files / "page.html"), "token": "media-token"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"].startswith("sandbox")


def test_svg_keeps_its_type_with_a_sandbox(files):
    r = client.get("/fs/raw", params={"path": str(files / "pic.svg"), "token": "media-token"})
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["content-security-policy"].startswith("sandbox")


def test_pdf_has_no_sandbox_so_the_viewer_works(files):
    r = client.get("/fs/raw", params={"path": str(files / "doc.pdf"), "token": "media-token"})
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in r.headers


def test_media_token_opens_only_media_routes(files):
    assert client.get("/settings", params={"token": "media-token"}).status_code == 401
    assert client.get("/fs/list", params={"path": str(files), "token": "media-token"}).status_code == 401
    assert client.post("/settings", params={"token": "media-token"}, json={"key": "theme", "value": "dark"}).status_code == 401
    # The main token still opens the media routes (scripts and a plain browser use it).
    r = client.get("/fs/raw", params={"path": str(files / "doc.pdf"), "token": app_mod.TOKEN})
    assert r.status_code == 200


def test_no_media_token_means_no_media_access(files, monkeypatch):
    monkeypatch.setattr(app_mod, "MEDIA_TOKEN", "")
    r = client.get("/fs/raw", params={"path": str(files / "doc.pdf"), "token": ""})
    assert r.status_code == 401
