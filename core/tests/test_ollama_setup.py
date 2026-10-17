"""Raven installs Ollama into its data folder (PLAN4 5.3.1): the right file, a checked SHA-256, a safe unpack."""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
import zipfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import httpx  # noqa: E402
import zstandard  # noqa: E402

from raven_core import device, ollama_setup  # noqa: E402

ASSETS = {n: {"url": f"https://example.test/{n}", "size": 10, "sha256": ""} for n in (
    "ollama-linux-amd64.tar.zst", "ollama-linux-amd64-rocm.tar.zst", "ollama-linux-arm64.tar.zst",
    "ollama-darwin.tgz", "ollama-windows-amd64.zip", "ollama-windows-arm64.zip", "OllamaSetup.exe")}


@pytest.mark.parametrize("system, machine, amd, want", [
    ("linux", "x86_64", False, ["ollama-linux-amd64.tar.zst"]),
    ("linux", "x86_64", True, ["ollama-linux-amd64.tar.zst", "ollama-linux-amd64-rocm.tar.zst"]),
    ("linux", "aarch64", False, ["ollama-linux-arm64.tar.zst"]),
    ("darwin", "arm64", False, ["ollama-darwin.tgz"]),
    ("windows", "AMD64", False, ["ollama-windows-amd64.zip"]),
])
def test_the_right_files_for_each_system(monkeypatch, system, machine, amd, want):
    monkeypatch.setattr(device, "system", lambda: system)
    monkeypatch.setattr(ollama_setup._pf, "machine", lambda: machine)
    gpus = [{"name": "Radeon", "vendor": "amd", "vram": 8 << 30, "shared": False}] if amd else []
    monkeypatch.setattr(device, "info", lambda fresh=False: {"gpus": gpus})
    assert ollama_setup._wanted_assets(ASSETS) == want


def _tar_zst(files: dict[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            t.addfile(info, io.BytesIO(data))
    return zstandard.ZstdCompressor().compress(raw.getvalue())


@pytest.fixture
def serve(monkeypatch, tmp_path):
    """A fake GitHub: release() gives the files, httpx.Client serves their bytes."""
    monkeypatch.setattr(ollama_setup, "HOME", tmp_path / "ollama")
    monkeypatch.setattr(ollama_setup, "MARKER", tmp_path / "ollama" / "raven-install.json")
    monkeypatch.setattr(device, "system", lambda: "linux")
    blobs: dict[str, bytes] = {}

    def setup(name: str, data: bytes, sha: str | None = None):
        blobs[name] = data
        rel = {"version": "9.9.9", "files": [{"name": name, "url": f"https://example.test/{name}", "size": len(data),
                                              "sha256": sha if sha is not None else hashlib.sha256(data).hexdigest()}]}
        monkeypatch.setattr(ollama_setup, "release", lambda force=False: rel)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=blobs[req.url.path.lstrip("/")])

    real = httpx.Client
    monkeypatch.setattr(ollama_setup.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    return setup


def _wait():
    for _ in range(200):
        if ollama_setup._job["state"] != "running":
            return
        time.sleep(0.02)


def test_install_unpacks_into_the_data_folder(serve):
    serve("ollama-linux-amd64.tar.zst", _tar_zst({"bin/ollama": b"#!/bin/sh\necho ok\n", "lib/ollama/libx.so": b"x"}))
    ollama_setup.install()
    _wait()
    assert ollama_setup._job["state"] == "done", ollama_setup._job
    exe = ollama_setup.managed_binary()
    assert exe and exe.name == "ollama" and exe.parent.name == "bin"
    assert exe.is_relative_to(ollama_setup.HOME)
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
    assert json.loads(ollama_setup.MARKER.read_text())["version"] == "9.9.9"
    assert not (ollama_setup.HOME / ".download").exists()


def test_a_wrong_sha256_stops_the_install(serve):
    serve("ollama-linux-amd64.tar.zst", _tar_zst({"bin/ollama": b"x"}), sha="0" * 64)
    ollama_setup.install()
    _wait()
    assert ollama_setup._job["state"] == "error"
    assert "SHA-256" in ollama_setup._job["error"]
    assert ollama_setup.managed_binary() is None


def test_an_archive_member_outside_the_folder_is_refused(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../../evil.txt", "x")
    with pytest.raises(RuntimeError):
        ollama_setup._unpack(z, tmp_path / "out")
    assert not (tmp_path.parent / "evil.txt").exists()
    t = tmp_path / "bad.tar.zst"
    t.write_bytes(_tar_zst({"../evil.txt": b"x"}))
    with pytest.raises(tarfile.TarError):
        ollama_setup._unpack(t, tmp_path / "out2")


def test_a_system_ollama_wins(monkeypatch):
    monkeypatch.setattr(ollama_setup.shutil, "which", lambda n: "/usr/bin/ollama")
    assert ollama_setup.binary() == "/usr/bin/ollama"
    st = ollama_setup.status(quick=True)
    assert st["installed"] and not st["managed"] and st["steps"]
