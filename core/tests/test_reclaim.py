"""Reclaim (PLAN2 P2-4): sources, the planner, lossless optimizers, hard links, undo."""

import os
import tempfile
import zipfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.fs import ops  # noqa: E402
from raven_core.storage import reclaim  # noqa: E402

db.init()


def _blob(p, n):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(n))


def _index(path, size=None, ext=None, atime=None, mtime=None):
    with db.tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO files(path, parent, name, ext, size, mtime, atime, is_dir, index_stage) VALUES (?,?,?,?,?,?,?,0,2)",
            (str(path), str(path.parent), path.name, ext or path.name.rsplit(".", 1)[-1], size or path.stat().st_size,
             mtime or int(path.stat().st_mtime), atime or int(path.stat().st_atime)),
        )


def test_caches_and_dev_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(reclaim, "MIN_CACHE", 30_000)  # du counts 4 KB blocks: "tiny" uses 8 KB
    _blob(tmp_path / ".cache" / "someapp" / "blob", 50_000)
    _blob(tmp_path / ".cache" / "pip" / "wheels" / "w", 50_000)
    _blob(tmp_path / ".cache" / "tiny" / "x", 10)
    _blob(tmp_path / ".gradle" / "caches" / "g", 50_000)
    rows = {r["id"]: r for r in reclaim.app_caches(str(tmp_path)) + reclaim.dev_caches(str(tmp_path))}
    assert [os.path.basename(i["path"]) for i in rows["caches"]["items"]] == ["someapp"]  # pip is a dev cache, tiny is small
    devs = {i["note"] for i in rows["dev-caches"]["items"]}
    assert devs == {"pip", "Gradle"}
    assert reclaim._delete_content(str(tmp_path / ".cache" / "someapp")) > 0
    assert (tmp_path / ".cache" / "someapp").is_dir() and not any((tmp_path / ".cache" / "someapp").iterdir())


def test_archive_pair(tmp_path):
    d = tmp_path / "trip"
    d.mkdir()
    (d / "a.txt").write_text("alpha" * 100)
    (d / "b.txt").write_text("beta" * 100)
    z = tmp_path / "trip.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.write(d / "a.txt", "trip/a.txt")
        f.write(d / "b.txt", "trip/b.txt")
    lone = tmp_path / "other.zip"
    with zipfile.ZipFile(lone, "w") as f:
        f.writestr("x.txt", "x")
    _index(z)
    _index(lone)
    rows = reclaim.archive_pairs(str(tmp_path))
    assert [i["path"] for i in rows[0]["items"]] == [str(z)]


def test_large_old(tmp_path, monkeypatch):
    monkeypatch.setattr(reclaim, "LARGE", 1000)
    old = tmp_path / "old.iso"
    new = tmp_path / "new.iso"
    _blob(old, 5000)
    _blob(new, 5000)
    t_old = 1_500_000_000
    _index(old, atime=t_old, mtime=t_old)
    _index(new)
    rows = reclaim.large_old(str(tmp_path))
    assert [i["path"] for i in rows[0]["items"]] == [str(old)]
    assert rows[0]["items"][0]["checked"] is False  # the user decides


def test_optimize_png_and_undo(tmp_path):
    p = tmp_path / "shot.png"
    im = Image.new("RGB", (600, 400), "white")
    for x in range(0, 600, 3):
        im.putpixel((x, x % 400), (200, 30, 30))
    im.save(p, "PNG", compress_level=0)
    before = p.stat().st_size
    g = ops.Group("reclaim", "Free space")
    saved = reclaim.optimize_one(str(p), g)
    g.commit()
    assert saved and saved > 0 and p.stat().st_size < before
    with Image.open(p) as a:
        assert a.convert("RGB").tobytes() == im.tobytes()
    ops.undo()
    assert p.stat().st_size == before


def test_optimize_skips_when_not_smaller(tmp_path):
    p = tmp_path / "noise.png"
    Image.effect_noise((200, 200), 100).save(p, "PNG", optimize=True)
    g = ops.Group("reclaim", "x")
    assert reclaim.optimize_one(str(p), g) is None
    g.commit()
    assert p.exists() and not list(tmp_path.glob(".raven-opt-*"))


def test_hardlink_and_undo(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    data = os.urandom(20000)
    a.write_bytes(data)
    b.write_bytes(data)
    g = ops.Group("reclaim", "x")
    reclaim.hardlink_one(str(a), str(b), g)
    g.commit()
    assert os.stat(a).st_ino == os.stat(b).st_ino
    ops.undo()
    assert os.stat(a).st_ino != os.stat(b).st_ino and b.read_bytes() == data


def test_hardlink_refuses_changed_files(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"1" * 100)
    b.write_bytes(b"2" * 100)
    with pytest.raises(ValueError):
        reclaim.hardlink_one(str(a), str(b), ops.Group("reclaim", "x"))


def test_plan_prefers_safe_and_skips_blocked():
    reclaim._last.update(id="s1", path="/", rows={
        "big-check": {"id": "big-check", "safety": "check", "reversible": True, "action": "trash", "total": 900,
                      "items": [{"path": "/c1", "size": 900}]},
        "safe": {"id": "safe", "safety": "safe", "reversible": True, "action": "trash", "total": 300,
                 "items": [{"path": "/s1", "size": 200}, {"path": "/s2", "size": 100}]},
        "browser": {"id": "browser", "safety": "safe", "reversible": False, "action": "delete", "total": 5000,
                    "items": [{"path": "/b", "size": 5000, "blocked": True}]},
        "admin": {"id": "admin", "safety": "safe", "reversible": False, "action": "command", "admin": True, "total": 9000,
                  "items": [{"path": "system:x", "size": 9000}]},
    })
    p = reclaim.plan(250, "s1")
    assert p["rows"] == {"safe": ["/s1", "/s2"]} and p["enough"]
    p = reclaim.plan(1000, "s1")
    assert p["rows"] == {"safe": ["/s1", "/s2"], "big-check": ["/c1"]}
    with pytest.raises(ValueError):
        reclaim.plan(10, "old-scan")


def test_run_only_acts_on_scanned_paths(tmp_path):
    keep = tmp_path / "keep.txt"
    keep.write_text("x")
    reclaim._last.update(id="s2", path=str(tmp_path), rows={
        "r": {"id": "r", "safety": "safe", "reversible": True, "action": "trash", "total": 1,
              "items": [{"path": str(tmp_path / "gone.txt"), "size": 1}]}})
    res = reclaim.run("s2", {"r": [str(keep)]})
    assert keep.exists() and res["errors"]


def test_forecast(monkeypatch):
    key = f"disk:{reclaim._mount_of(reclaim.HOME)}"
    import time

    now = int(time.time())
    with db.tx() as c:
        c.execute("DELETE FROM size_snapshots WHERE path=?", (key,))
        for d in range(5):
            c.execute("INSERT INTO size_snapshots VALUES (?,?,?)", (now - (4 - d) * 86400, key, 10**9 + d * 2 * 10**9))
    f = reclaim.forecast(reclaim.HOME)
    assert f["per_day"] and abs(f["per_day"] - 2 * 10**9) < 10**7
    assert f["days"] is not None


def test_sharpness_prefers_sharp(tmp_path):
    from PIL import ImageFilter

    from raven_core.storage.dupes import sharpness

    im = Image.new("RGB", (400, 300), "white")
    for x in range(0, 400, 8):
        for y in range(300):
            im.putpixel((x, y), (0, 0, 0))
    im.save(tmp_path / "sharp.jpg")
    im.filter(ImageFilter.GaussianBlur(4)).save(tmp_path / "blur.jpg")
    assert sharpness(str(tmp_path / "sharp.jpg")) > sharpness(str(tmp_path / "blur.jpg"))


def test_trim_thumbs_keeps_frames(tmp_path, monkeypatch):
    from raven_core import config

    monkeypatch.setattr(config, "THUMB_DIR", tmp_path)
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "f.webp").write_bytes(b"x" * 500)
    for i in range(5):
        p = tmp_path / f"t{i}.webp"
        p.write_bytes(b"x" * 100)
        os.utime(p, (1000 + i, 1000 + i))
    assert reclaim.trim_thumbs(limit=250) == 3
    assert sorted(p.name for p in tmp_path.glob("*.webp")) == ["t3.webp", "t4.webp"]
    assert (tmp_path / "frames" / "f.webp").exists()
