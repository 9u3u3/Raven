"""Space: scanner totals, junk rules, duplicates and forget()."""

import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="raven-space-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))

from raven_core import db  # noqa: E402
from raven_core.storage import dupes, scan  # noqa: E402

db.init()


def _old(p, days):
    t = time.time() - days * 86400
    os.utime(p, (t, t))


def make_tree(root):
    proj = root / "proj"
    (proj / "node_modules" / "a").mkdir(parents=True)
    (proj / "package.json").write_text("{}")
    (proj / "node_modules" / "a" / "index.js").write_bytes(b"x" * 20000)
    _old(proj / "node_modules", 100)
    _old(proj, 100)
    (root / "photos").mkdir()
    (root / "photos" / "a.jpg").write_bytes(b"J" * 9000)
    (root / "photos" / "a (2).jpg").write_bytes(b"J" * 9000)
    (root / "Downloads").mkdir()
    (root / "Downloads" / "a.jpg").write_bytes(b"J" * 9000)
    (root / "Downloads" / "big.mp4.part").write_bytes(b"p" * 5000)
    _old(root / "Downloads" / "big.mp4.part", 3)
    (root / "empty").mkdir()
    _old(root / "empty", 40)
    # An app folder: its node_modules is not junk.
    (root / ".local" / "app" / "node_modules").mkdir(parents=True)
    (root / ".local" / "app" / "package.json").write_text("{}")


def test_scan_totals_and_junk(tmp_path):
    make_tree(tmp_path)
    res = scan.Scanner(str(tmp_path)).run()
    assert res.files == 7
    assert res.root.cats[scan.CODEDEPS] > 0 and res.root.cats[scan.PHOTOS] > 0
    rules = {(j.rule, os.path.relpath(j.path, tmp_path).replace(os.sep, "/")) for j in res.junk}
    assert ("node_modules", "proj/node_modules") in rules
    assert ("temp", "Downloads/big.mp4.part") in rules
    assert ("empty", "empty") in rules
    assert not any(".local" in p for _, p in rules)
    nm = next(j for j in res.junk if j.rule == "node_modules")
    assert nm.checked  # the project is idle for 100 days


def test_find_and_forget(tmp_path):
    make_tree(tmp_path)
    store = scan.SpaceStore()
    store.results[str(tmp_path)] = scan.Scanner(str(tmp_path)).run()
    _, node = store.find(str(tmp_path / "proj"))
    nm_size = next(c for c in node.children if c.name == "node_modules").size
    total = store.results[str(tmp_path)].root.size
    store.forget([(str(tmp_path / "proj" / "node_modules"), nm_size)])
    assert store.results[str(tmp_path)].root.size == total - nm_size
    assert not any(j.rule == "node_modules" for j in store.results[str(tmp_path)].junk)


def test_duplicates_keep_choice(tmp_path):
    make_tree(tmp_path)
    job = dupes.DupeJob()
    job._run(str(tmp_path), 1024)
    assert len(job.groups) == 1
    g = job.groups[0]
    assert g["count"] == 3 and g["wasted"] == 18000
    keep = next(f for f in g["files"] if f["keep"])
    # The copy outside Downloads with the original name wins.
    assert keep["path"].replace(os.sep, "/").endswith("photos/a.jpg")


def test_protected_folders(tmp_path):
    """Duplicates and empty folders inside projects and SDKs are left alone."""
    from raven_core.storage import protect

    protect.forget()
    sdk = tmp_path / "Android" / "Sdk" / "ndk" / "lib"
    sdk.mkdir(parents=True)
    (sdk / "libc.so").write_bytes(b"S" * 9000)
    (tmp_path / "Android" / "Sdk" / "copy.so").write_bytes(b"S" * 9000)
    app = tmp_path / "Codes" / "app"
    (app / "src" / "empty").mkdir(parents=True)
    (app / "build.gradle").write_text("")
    (app / "src" / "icon.png").write_bytes(b"I" * 9000)
    (app / "icon2.png").write_bytes(b"I" * 9000)
    _old(app / "src" / "empty", 90)
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "a.pdf").write_bytes(b"P" * 9000)
    (docs / "a copy.pdf").write_bytes(b"P" * 9000)

    job = dupes.DupeJob()
    job._run(str(tmp_path), 1024)
    paths = {f["path"] for g in job.groups for f in g["files"]}
    assert len(job.groups) == 1 and all(f"{os.sep}Documents{os.sep}" in p for p in paths)

    res = scan.Scanner(str(tmp_path)).run()
    assert not any(j.rule == "empty" for j in res.junk)
    assert protect.blocked(str(sdk / "libc.so"), str(tmp_path))
    assert protect.blocked(str(app / "src"), str(tmp_path))
    assert protect.blocked(str(app), str(tmp_path), allow_project_root=True) is False
    assert not protect.blocked(str(docs / "a.pdf"), str(tmp_path))


def test_backups_and_caches_are_left_alone(tmp_path):
    from raven_core.storage import protect

    protect.forget()
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs-backup").mkdir()
    (tmp_path / "Docs" / "a.pdf").write_bytes(b"P" * 9000)
    (tmp_path / "Docs-backup" / "a.pdf").write_bytes(b"P" * 9000)
    job = dupes.DupeJob()
    job._run(str(tmp_path), 1024)
    assert job.groups == []
    assert protect.is_software_dir(str(tmp_path / "Videos" / "CacheClip"))


def test_installs_and_models_are_software(tmp_path):
    from raven_core.storage import protect

    protect.forget()
    (tmp_path / "arduino-1.8.19").mkdir()
    (tmp_path / "laya-model").mkdir()
    (tmp_path / "laya-model" / "tokenizer.json").write_text("{}")
    (tmp_path / "Sem5-notes").mkdir()
    assert protect.is_software_dir(str(tmp_path / "arduino-1.8.19"))
    assert protect.is_software_dir(str(tmp_path / "laya-model"))
    assert not protect.is_software_dir(str(tmp_path / "Sem5-notes"))
    assert not protect.is_software_dir(str(tmp_path / "DBMS 2024"))


def test_near_duplicate_photos(tmp_path):
    import time as _t

    from PIL import Image, ImageDraw

    from raven_core.storage import dupes

    def photo(name, size, shift=0):
        im = Image.new("RGB", size, (30, 60, 120))
        d = ImageDraw.Draw(im)
        d.ellipse([size[0] // 4 + shift, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4], fill=(240, 200, 40))
        d.rectangle([0, size[1] * 2 // 3, size[0], size[1]], fill=(20, 120, 40))
        im.save(tmp_path / name, quality=92)

    photo("beach.jpg", (1600, 1200))
    photo("beach_small.jpg", (800, 600))       # the same scene, smaller
    photo("beach_edit.jpg", (1600, 1200), 6)   # a small change
    Image.new("RGB", (1600, 1200), (200, 10, 10)).save(tmp_path / "red.jpg")
    for f in tmp_path.iterdir():
        f.write_bytes(f.read_bytes() + b"\0" * 25000) if f.stat().st_size < 21000 else None
    job = dupes.DupeJob()
    job.start(str(tmp_path))
    end = _t.time() + 30
    while job.running and _t.time() < end:
        _t.sleep(0.05)
    near = [g for g in job.groups if g.get("kind") == "near"]
    assert len(near) == 1
    names = {os.path.basename(f["path"]): f for f in near[0]["files"]}
    assert set(names) == {"beach.jpg", "beach_small.jpg", "beach_edit.jpg"}
    assert not names["beach_small.jpg"]["keep"]


def test_one_installer_does_not_make_a_software_folder(tmp_path):
    """Downloads with an installer is a user folder: duplicates, names, Organize and Reclaim must read it."""
    from raven_core.storage import protect

    dl = tmp_path / "Downloads"
    dl.mkdir()
    for n in ("putty.exe", "fw9.pdf", "notes.txt", "photo.jpg"):
        (dl / n).write_bytes(b"x")
    assert not protect.is_software_dir(str(dl))
    other = tmp_path / "stuff"
    other.mkdir()
    for n in ("a.pdf", "b.txt", "c.jpg", "setup.exe"):
        (other / n).write_bytes(b"x")
    assert not protect.is_software_dir(str(other))  # one program among four files
    app = tmp_path / "game"
    app.mkdir()
    for n in ("game.exe", "launcher.exe", "readme.txt"):
        (app / n).write_bytes(b"x")
    assert protect.is_software_dir(str(app))  # mostly programs
    lib = tmp_path / "tool"
    lib.mkdir()
    for n in ("libfoo.so", "a.txt", "b.txt", "c.txt", "d.txt"):
        (lib / n).write_bytes(b"x")
    assert protect.is_software_dir(str(lib))  # a library: an installed app


def _summary(res):
    """What Space shows: totals of every kept folder, the junk and the largest files."""
    nodes = {}
    stack = [res.root]
    while stack:
        n = stack.pop()
        nodes[n.path] = (n.size, n.files, n.dirs, tuple(n.cats), sorted(t[1] for t in n.top), n.entries)
        stack.extend(n.children)
    junk = sorted((j.rule, j.path, j.size, j.checked) for j in res.junk)
    largest = sorted((x[0], x[1]) for x in res.largest)
    return res.files, nodes, junk, largest


def _same_as_full(root, base):
    ref = scan.Scanner(str(root)).run()
    sc = scan.Scanner(str(root), base=base)
    new = sc.run()
    assert new.refreshed and _summary(new) == _summary(ref)
    return new, sc


def test_refresh_without_changes_reads_no_folder(tmp_path, monkeypatch):
    make_tree(tmp_path)
    base = scan.Scanner(str(tmp_path)).run()
    walked = []
    real = scan.Scanner._walk
    monkeypatch.setattr(scan.Scanner, "_walk", lambda self, path, *a, **k: walked.append(path) or real(self, path, *a, **k))
    new = scan.Scanner(str(tmp_path), base=base).run()
    assert walked == [] and _summary(new) == _summary(base)


def test_refresh_matches_a_full_scan_after_changes(tmp_path):
    make_tree(tmp_path)
    (tmp_path / "docs" / "deep" / "deeper").mkdir(parents=True)
    (tmp_path / "docs" / "deep" / "deeper" / "a.pdf").write_bytes(b"d" * 3000)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "x.txt").write_bytes(b"x" * 100)
    base = scan.Scanner(str(tmp_path)).run()
    time.sleep(0.01)
    # A new file deep down, a removed folder, a new project with node_modules, a renamed folder, a new nested folder.
    (tmp_path / "docs" / "deep" / "deeper" / "b.mp4").write_bytes(b"v" * 70000)
    import shutil

    shutil.rmtree(tmp_path / "photos")
    p2 = tmp_path / "proj2"
    (p2 / "node_modules" / "b").mkdir(parents=True)
    (p2 / "package.json").write_text("{}")
    (p2 / "node_modules" / "b" / "i.js").write_bytes(b"y" * 5000)
    os.rename(tmp_path / "other", tmp_path / "renamed")
    (tmp_path / "docs" / "new" / "inner").mkdir(parents=True)
    (tmp_path / "docs" / "new" / "inner" / "n.md").write_bytes(b"n" * 10)
    new, sc = _same_as_full(tmp_path, base)
    # Unchanged folders kept their own files from the base: Downloads, proj, docs/deep (not deeper).
    assert {str(tmp_path / "Downloads"), str(tmp_path / "proj"), str(tmp_path / "docs" / "deep")} <= sc._kept
    assert str(tmp_path / "docs" / "deep" / "deeper") not in sc._kept
    # A second refresh on the refreshed scan is right too.
    (tmp_path / "renamed" / "y.txt").write_bytes(b"y" * 200)
    _, sc = _same_as_full(tmp_path, new)
    assert str(tmp_path / "renamed") not in sc._kept and str(tmp_path / "Downloads") in sc._kept


def test_saved_scan_loads_after_a_restart(tmp_path):
    make_tree(tmp_path)
    store = scan.SpaceStore()
    res = scan.Scanner(str(tmp_path)).run()
    store._save(res)
    again = scan.SpaceStore()
    again.loaded.clear()
    again.load_saved()
    assert again.loaded.is_set()
    loaded = again.results[str(tmp_path)]
    assert _summary(loaded) == _summary(res)
    # A broken file is dropped, not loaded.
    bad = scan.SpaceStore._save_dir() / "0000000000000000.scan"
    bad.write_bytes(b"not a scan")
    scan.SpaceStore().load_saved()
    assert not bad.exists()
