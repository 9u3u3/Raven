"""Phase 4 exit test: compress, then restore, gives identical BLAKE3 hashes, paths and modified times."""

import os
import shutil
import tempfile
import time

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-compress-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ["XDG_DATA_HOME"] = os.path.join(_tmp, "xdg")

from PIL import Image  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.compress import jobs  # noqa: E402
from raven_core.fs import ops  # noqa: E402
from raven_core.storage.dupes import full_hash  # noqa: E402

db.init()


def snapshot(root):
    out = {}
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (full_hash(p), st.st_mtime_ns, st.st_mode & 0o777)
        for d in dns:
            p = os.path.join(dp, d)
            out[os.path.relpath(p, root) + "/"] = (None, os.stat(p).st_mtime_ns, os.stat(p).st_mode & 0o777)
    return out


def make_project(root):
    lab = root / "lab"
    (lab / "sub").mkdir(parents=True)
    (lab / "empty").mkdir()
    for i in range(3):
        (lab / f"run{i}.log").write_text("2025-01-01 INFO reading=0.123 status=ok\n" * 4000)
    (lab / "sub" / "data.csv").write_text("a,b,c\n" + "1,2,3\n" * 5000)
    os.chmod(lab / "sub" / "data.csv", 0o640)
    t = time.time() - 200 * 86400
    for p in [*lab.rglob("*"), lab]:
        os.utime(p, (t, t + 1.123456))
    return lab


@pytest.mark.parametrize("method", ["7z", "tar.zst", "zip"])
def test_archive_roundtrip(tmp_path, method):
    lab = make_project(tmp_path)
    before = snapshot(tmp_path)
    job = jobs.compress(str(tmp_path), [str(lab)], method)
    assert job["status"] == "done" and not lab.exists()
    assert os.path.exists(job["archive"]) and job["size_after"] < job["size_before"]
    ghost = jobs.summary()["outputs"][job["archive"]]
    res = jobs.restore(job["id"])
    assert res["status"] == "restored" and not os.path.exists(job["archive"])
    after = snapshot(tmp_path)
    assert after == before  # hashes, paths, modified times and permissions
    restored_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(lab) for f in fs)
    assert ghost == restored_size == job["size_before"]


def test_compress_undo(tmp_path):
    lab = make_project(tmp_path)
    before = snapshot(tmp_path)
    job = jobs.compress(str(tmp_path), [str(lab)], "7z")
    ops.undo()  # one Ctrl+Z: originals back, archive in the trash
    assert snapshot(tmp_path) == before
    assert jobs.get_job(job["id"])["status"] == "undone"


def test_jxl_and_zst(tmp_path):
    photos = tmp_path / "trip"
    photos.mkdir()
    for i in range(2):
        img = Image.new("RGB", (640, 480), (i * 60, 120, 200))
        img.save(photos / f"p{i}.jpg", "JPEG", quality=90)
    big = tmp_path / "dump.json"
    big.write_text('{"k": "value", "n": 12345}\n' * 30000)
    before = snapshot(tmp_path)
    j1 = jobs.compress(str(photos), [str(p) for p in sorted(photos.iterdir())], "jxl")
    j2 = jobs.compress(str(tmp_path), [str(big)], "zst")
    assert all(o.endswith(".jxl") for o in j1["outputs"]) and j2["outputs"][0].endswith(".json.zst")
    jobs.restore(j1["id"])
    jobs.restore(j2["id"])
    assert snapshot(tmp_path) == before


def test_failure_keeps_originals(tmp_path, monkeypatch):
    lab = make_project(tmp_path)
    before = snapshot(tmp_path)

    def broken(*a, **k):
        raise jobs.CompressError("Verify failed: test")

    monkeypatch.setattr(jobs.m, "verify_tree", broken)
    with pytest.raises(jobs.CompressError):
        jobs.compress(str(tmp_path), [str(lab)], "7z")
    assert snapshot(tmp_path) == before
    assert not list(tmp_path.glob("*.7z"))


def _photo(path, size=(1200, 900)):
    import numpy as np

    rng = np.random.default_rng(7)
    base = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    base[..., 0] = np.linspace(0, 255, size[0], dtype=np.uint8)[None, :]
    base[..., 1] = np.linspace(0, 255, size[1], dtype=np.uint8)[:, None]
    base[..., 2] = rng.integers(0, 40, size=(size[1], size[0]), dtype=np.uint8)
    Image.fromarray(base).save(path, quality=95)


@pytest.mark.parametrize("method", ["avif", "jxl-q"])
def test_lossy_needs_consent_and_has_no_restore(tmp_path, method):
    p = tmp_path / "beach.jpg"
    _photo(p)
    t = time.time() - 300 * 86400
    os.utime(p, (t, t))
    with pytest.raises(jobs.CompressError):
        jobs.compress(str(tmp_path), [str(p)], method)
    job = jobs.compress(str(tmp_path), [str(p)], method, accept_loss=True)
    assert job["status"] == "done" and not job["reversible"] and not p.exists()
    out = job["outputs"][0]
    assert out.endswith(".avif" if method == "avif" else ".jxl") and abs(os.stat(out).st_mtime - t) < 2
    # No ghost and no restore. Undo brings the original back from the trash.
    assert out not in jobs.summary()["outputs"]
    with pytest.raises(jobs.CompressError):
        jobs.restore(job["id"])
    ops.undo()
    assert p.exists() and not os.path.exists(out)


def test_lossy_never_in_auto_mode(tmp_path):
    p = tmp_path / "a.jpg"
    _photo(p, (400, 300))
    with pytest.raises(jobs.CompressError):
        jobs.compress(str(tmp_path), [str(p)], "avif", mode="auto", accept_loss=True)
