"""Names only: files in code projects and software folders below an index root skip text, meaning, images
and speech, and keep their names in the index."""

import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.index import indexer as ix  # noqa: E402
from raven_core.storage import protect  # noqa: E402

db.init()


def _tree(base: Path) -> Path:
    home = base / "home"
    for rel, text in {
        "Documents/notes.md": "# Normal forms\nThird normal form",
        "Codes/app/package.json": "{}",
        "Codes/app/src/index.js": "console.log('hi')",
        "Android/Sdk/platforms/android.jar": "x",
        "Android/Sdk/source.properties": "Pkg.Revision=1",
    }.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return home


def _rows(root: Path) -> dict[str, tuple[int, int]]:
    return {os.path.relpath(r[0], root).replace(os.sep, "/"): (r[1], r[2]) for r in db.conn().execute(
        "SELECT path, index_stage, light FROM files WHERE root=? AND is_dir=0", (str(root),))}


def test_projects_and_sdks_are_names_only(tmp_path):
    home = _tree(tmp_path)
    db.set_setting("index_code", False)
    protect.forget()
    ix.indexer.scan_root(str(home))
    r = _rows(home)
    assert r["Documents/notes.md"] == (1, 0)          # read in the text stage
    # A code project is one names-only folder row (PLAN3 P0-7). Raven does not list its files.
    assert "Codes/app/src/index.js" not in r and "Codes/app/package.json" not in r
    app = db.conn().execute("SELECT is_dir, light FROM files WHERE path=?", (str(home / "Codes" / "app"),)).fetchone()
    assert tuple(app) == (1, 1)
    # An SDK stays as one names-only folder too.
    assert "Android/Sdk/platforms/android.jar" not in r
    sdk = db.conn().execute("SELECT is_dir, light FROM files WHERE path=?", (str(home / "Android"),)).fetchone()
    assert tuple(sdk) == (1, 1)


def test_full_index_rule_lists_project_files_by_name(tmp_path):
    from raven_core.fs import props

    home = _tree(tmp_path)
    db.set_setting("index_code", False)
    protect.forget()
    props.set_policy(str(home / "Codes"), full_index=True)
    try:
        ix.indexer.scan_root(str(home))
        r = _rows(home)
        assert r["Codes/app/src/index.js"] == (2, 1)  # listed, name only
        # The rule opens code projects only: the SDK stays one row.
        assert "Android/Sdk/platforms/android.jar" not in r
    finally:
        props.set_policy(str(home / "Codes"), full_index=False)
    # Without the rule, the next scan drops the project files again.
    ix.indexer.scan_root(str(home))
    assert "Codes/app/src/index.js" not in _rows(home)


def test_a_big_delete_marks_the_index_for_compaction(tmp_path, monkeypatch):
    home = _tree(tmp_path)
    db.set_setting("index_compact_due", False)
    db.set_setting("index_code", True)
    try:
        ix.indexer.scan_root(str(home))  # lists the project files
    finally:
        db.set_setting("index_code", False)
    monkeypatch.setattr(ix, "BIG_DELETE", 2)
    ix.indexer.scan_root(str(home))  # drops them again: more than BIG_DELETE rows
    assert db.get_setting("index_compact_due") is True
    db.set_setting("index_compact_due", False)


def test_a_project_root_is_read(tmp_path):
    home = _tree(tmp_path)
    proj = home / "Codes" / "app"
    protect.forget()
    ix.indexer.scan_root(str(proj))
    assert _rows(proj)["src/index.js"] == (1, 0)


def test_setting_reads_code_too(tmp_path):
    home = _tree(tmp_path)
    db.set_setting("index_code", True)
    try:
        ix.indexer.scan_root(str(home))
        assert _rows(home)["Codes/app/src/index.js"] == (1, 0)
    finally:
        db.set_setting("index_code", False)


def test_dataset_folders_and_camera_photos(tmp_path):
    from PIL import Image

    from raven_core.index.extract import camera_photo, extract

    ix.db_cache.index_code = False
    assert ix._names_only_dir(str(tmp_path / "scikit_learn_data"))
    p = tmp_path / "IMG_1.jpg"
    im = Image.new("RGB", (64, 64), "white")
    exif = im.getexif()
    exif[271] = "Canon"
    im.save(p, exif=exif)
    assert camera_photo(str(p)) and extract(str(p), "jpg").sections == []
    q = tmp_path / "scan.jpg"
    Image.new("RGB", (64, 64), "white").save(q)
    assert not camera_photo(str(q))


def test_downloads_with_an_installer_is_read(tmp_path):
    home = tmp_path / "home"
    dl = home / "Downloads"
    dl.mkdir(parents=True)
    (dl / "setup.exe").write_bytes(b"MZ")
    (dl / "letter.txt").write_text("Dear sir, the hostel fee is due.")
    ix.db_cache.index_code = False
    protect.forget()
    ix.indexer.scan_root(str(home))
    r = _rows(home)
    assert r["Downloads/letter.txt"] == (1, 0) and "Downloads/setup.exe" in r
