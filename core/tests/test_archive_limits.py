"""Archive extraction refuses zip bombs and archives larger than the free disk space (PLAN3 phase 8)."""

import io
import os
import subprocess
import tarfile
import tempfile
import zipfile
from collections import namedtuple

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.compress import methods  # noqa: E402
from raven_core.fs import archive  # noqa: E402

db.init()

MB = 1024**2


def _bomb(path, members: int = 1, size: int = 64 * MB) -> str:
    """A small zip that expands to members * size bytes of zeros."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        zeros = b"\0" * MB
        for n in range(members):
            with z.open(f"z{n}.bin", "w", force_zip64=True) as f:
                for _ in range(size // MB):
                    f.write(zeros)
    return str(path)


def _left(folder) -> list[str]:
    return sorted(os.listdir(folder))


def test_a_zip_bomb_fails_and_writes_nothing(tmp_path, monkeypatch):
    p = _bomb(tmp_path / "bomb.zip", members=4)
    assert os.path.getsize(p) < MB
    monkeypatch.setattr(archive, "EXTRACT_MAX_BYTES", 128 * MB)
    with pytest.raises(archive.ArchiveError, match="Raven extracts at most") as e:
        archive.extract(p)
    assert e.value.status == 413
    assert _left(tmp_path) == ["bomb.zip"]


def test_too_many_files(tmp_path, monkeypatch):
    with zipfile.ZipFile(tmp_path / "many.zip", "w") as z:
        for n in range(20):
            z.writestr(f"f{n}.txt", "x")
    monkeypatch.setattr(archive, "EXTRACT_MAX_FILES", 10)
    with pytest.raises(archive.ArchiveError, match="20 files"):
        archive.extract(str(tmp_path / "many.zip"))
    assert _left(tmp_path) == ["many.zip"]


def test_not_enough_disk(tmp_path, monkeypatch):
    p = _bomb(tmp_path / "big.zip", size=8 * MB)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _p: usage(0, 0, archive.DISK_MARGIN + MB))
    with pytest.raises(archive.ArchiveError, match="GB free") as e:
        archive.extract(p)
    assert e.value.status == 507


def test_a_streamed_tar_zst_stops_at_the_limit(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for n in range(6):
            data = b"\0" * (4 * MB)
            info = tarfile.TarInfo(f"part{n}.bin")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    p = tmp_path / "parts.tar.zst"
    p.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=buf.getvalue(), capture_output=True, check=True).stdout)
    monkeypatch.setattr(archive, "EXTRACT_MAX_BYTES", 10 * MB)
    with pytest.raises(archive.ArchiveError, match="expands to"):
        archive.extract(str(p))
    assert _left(tmp_path) == ["parts.tar.zst"]


def test_7z_uses_its_headers(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.bin").write_bytes(b"\0" * (3 * MB))
    p = tmp_path / "a.7z"
    subprocess.run(["7z", "a", "-bd", str(p), str(tmp_path / "src")], capture_output=True, check=True)
    assert archive.totals_7z(str(p)) == (3 * MB, 2)  # the folder and the file
    monkeypatch.setattr(archive, "EXTRACT_MAX_BYTES", 2 * MB)
    with pytest.raises(archive.ArchiveError):
        archive.extract(str(p))


def test_one_member_larger_than_the_cache(tmp_path, monkeypatch):
    p = _bomb(tmp_path / "one.zip", size=4 * MB)
    monkeypatch.setattr(archive, "CACHE_MAX", 2 * MB)
    with pytest.raises(archive.ArchiveError, match="Extract the archive"):
        archive.member_file(p + "/z0.bin")


def test_restore_of_a_compressed_zip_checks_too(tmp_path, monkeypatch):
    p = _bomb(tmp_path / "c.zip", size=8 * MB)
    monkeypatch.setattr(archive, "EXTRACT_MAX_BYTES", 4 * MB)
    with pytest.raises(archive.ArchiveError):
        methods.extract_archive("zip", p, str(tmp_path / "out"))
    assert _left(tmp_path / "out") == []


def test_a_normal_archive_still_extracts(tmp_path):
    p = _bomb(tmp_path / "ok.zip", size=2 * MB)
    out = archive.extract(p)
    assert os.path.getsize(os.path.join(out, "z0.bin")) == 2 * MB
