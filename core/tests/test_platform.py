"""Platform helpers that do not need Windows to test."""

import os

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
