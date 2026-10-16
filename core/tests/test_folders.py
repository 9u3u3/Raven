"""Folder search: profiles from the file index, name and path matches, and the most specific folder first.

These tests use no models: the profile, BM25 and fuzzy names work without embeddings or the LLM.
"""

import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.index.indexer import indexer  # noqa: E402
from raven_core.search import folders  # noqa: E402

db.init()
folders.init()

ROOT = Path(_tmp) / "home"
TREE = {
    "Projects/drone-sem3/code/flight_controller.py": "pid",
    "Projects/drone-sem3/code/motor_test.c": "pwm",
    "Projects/drone-sem3/report/drone_report.pdf": "",
    "Projects/web-app/index.js": "",
    "Projects/web-app/package.json": "{}",
    "Documents/Bank/statement_march.pdf": "",
    "Documents/Bank/statement_april.pdf": "",
    "Documents/Sem5/DBMS/dbms_unit1.md": "# Normal forms",
    "Pictures/2025-trip/DSC_4100.jpg": "",
    "Empty Folder/.keep": "",
}


def setup_module():
    for rel, text in TREE.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    db.set_setting("index_roots", [str(ROOT)])
    indexer.scan_root(str(ROOT))
    assert folders.refresh_profiles() > 0


def _paths(q: str) -> list[str]:
    return [os.path.relpath(r["path"], ROOT) for r in folders.search(q, home=str(ROOT))["results"]]


def test_profile_holds_kinds_and_names():
    row = db.conn().execute("SELECT files, profile FROM folders WHERE path=?", (str(ROOT / "Projects/drone-sem3"),)).fetchone()
    assert row["files"] == 3
    assert "Subfolders: code, report" in row["profile"]
    assert "drone_report.pdf" in row["profile"] and "Python x1" in row["profile"]


def test_description_finds_the_project_not_the_parent():
    got = _paths("drone project with code and report")
    assert got[0] == "Projects/drone-sem3"
    assert got.index("Projects/drone-sem3") < got.index("Projects")


def test_folder_by_contents():
    assert _paths("bank statements")[0] == "Documents/Bank"


def test_typo_in_name():
    assert _paths("dmbs")[0] == "Documents/Sem5/DBMS"


def test_short_name_needs_a_whole_word():
    # "OS"-type names must not match inside other words.
    assert "Documents/Sem5" not in _paths("photos")[:1]


def test_path_fragment():
    assert _paths("sem5/db")[0] == "Documents/Sem5/DBMS"


def test_typed_path_completes():
    res = folders.search(str(ROOT / "Proj"), home=str(ROOT))
    assert res["mode"] == "path"
    assert [r["path"] for r in res["results"]] == [str(ROOT / "Projects")]
    res = folders.search(str(ROOT / "Projects") + "/", home=str(ROOT))
    assert {os.path.basename(r["path"]) for r in res["results"]} == {"drone-sem3", "web-app"}


def test_home_path_and_server():
    res = folders.search("~/Doc", home=str(ROOT))
    assert res["results"][0]["path"] == str(ROOT / "Documents")
    res = folders.search("smb://pc/docs", home=str(ROOT))
    assert res["results"][0]["why"] == "connect"


def test_top_files_come_from_inside():
    r = folders.search("drone report", home=str(ROOT))["results"][0]
    assert r["top"] and all(t["path"].startswith(str(ROOT / "Projects/drone-sem3")) for t in r["top"])


def test_removed_folder_leaves_the_index():
    gone = ROOT / "Projects/web-app"
    for f in gone.iterdir():
        f.unlink()
    gone.rmdir()
    indexer.scan_root(str(ROOT))
    folders.refresh_profiles()
    assert "Projects/web-app" not in _paths("web app")
