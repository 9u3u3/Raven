"""Platform helpers that do not need Windows to test."""

import os

import pytest

from raven_core import platform as plat


def test_windows_paths_go_out_with_slashes(monkeypatch):
    monkeypatch.setattr(plat, "IS_WIN", True)
    out = plat.to_client({"path": "C:\\Users\\a\\x.pdf", "unc": "\\\\nas\\share\\d", "text": "a\\b", "n": 3, "list": ["D:\\y"]})
    assert out == {"path": "C:/Users/a/x.pdf", "unc": "//nas/share/d", "text": "a\\b", "n": 3, "list": ["D:/y"]}


def test_posix_paths_unchanged():
    assert plat.to_client({"p": "/home/a"}) == {"p": "/home/a"}


def test_linux_mounts_and_owner():
    if not plat.IS_LINUX:
        return
    assert any(m[1] == "/" for m in plat.mounts())
    owner, _ = plat.owner_group(os.stat("/"))
    assert owner == "root"


def test_paths_from_the_window_get_their_native_form(monkeypatch):
    monkeypatch.setattr(plat, "IS_WIN", True)
    assert plat.from_client({"path": "C:/Users/a b/x.txt", "paths": ["//pc/share/d"], "q": "a/b"}) == {
        "path": "C:\\Users\\a b\\x.txt", "paths": ["\\\\pc\\share\\d"], "q": "a/b"}
    monkeypatch.setattr(plat, "IS_WIN", False)
    assert plat.from_client({"path": "C:/x"}) == {"path": "C:/x"}


def test_native_paths_middleware_rewrites_query_and_json(monkeypatch):
    import asyncio
    import json

    monkeypatch.setattr(plat, "IS_WIN", True)
    seen = {}

    async def app(scope, receive, send):
        seen["qs"] = scope["query_string"]
        seen["body"] = (await receive())["body"]

    body = json.dumps({"src": "C:/a/b.txt", "n": 1}).encode()
    scope = {"type": "http", "query_string": b"path=C%3A%2FUsers%2Fx&hidden=true",
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]}
    msgs = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return msgs.pop(0)

    asyncio.run(plat.NativePaths(app)(scope, receive, None))
    assert seen["qs"] == b"path=C%3A%5CUsers%5Cx&hidden=true"
    assert json.loads(seen["body"]) == {"src": "C:\\a\\b.txt", "n": 1}


@pytest.mark.skipif(os.name != "nt", reason="Windows drive names")
def test_drive_label_like_explorer():
    root = os.environ.get("SystemDrive", "C:") + "/"
    label = plat.drive_label(root)
    assert label.endswith(f"({root[:2]})") and len(label) > 5
    assert plat.drive_kind(root, "ntfs") == "drive"
    from raven_core.fs import listing

    assert any(d["path"] == root and d["label"] == label for d in listing.places()["drives"])
