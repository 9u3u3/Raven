"""Personal habits (PLAN2 P2-8)."""

import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db, habits  # noqa: E402
from raven_core.fs import ops  # noqa: E402

db.init()
habits.init()


@pytest.fixture(autouse=True)
def fresh():
    habits.clear()
    db.set_setting("habits_since", 0)
    with db.tx() as c:
        c.execute("DELETE FROM operations")
    db.set_setting("habits", True)
    yield


def _at(kind, path, ts, query=None):
    with db.tx() as c:
        c.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?)", (int(ts), kind, path, os.path.dirname(path) if path else None,
                                                              None, query, None))


def test_record_and_switch(tmp_path):
    p = str(tmp_path / "a.txt")
    habits.record("open", p)
    habits.record("bogus", p)
    assert habits.summary()["counts"] == {"open": 1}
    db.set_setting("habits", False)
    habits.record("open", p)
    db.set_setting("habits", True)
    assert habits.summary()["counts"] == {"open": 1}


def test_frecency_decays():
    now = time.time()
    _at("open", "/x/new.txt", now)
    _at("open", "/x/old.txt", now - 60 * 86400)
    s = habits.file_scores()
    assert s["/x/new.txt"] > 10 * s["/x/old.txt"]


def test_continue_prefers_this_time_of_day(tmp_path):
    a, b = tmp_path / "lecture.pdf", tmp_path / "taxes.xlsx"
    a.write_text("x")
    b.write_text("y")
    now = time.time()
    for d in range(1, 5):
        _at("open", str(a), now - d * 86400 + 600)  # the same hour on 4 days
        _at("open", str(b), now - d * 86400 + 9 * 3600)  # 9 hours later
    files = habits.continue_files(now=now)
    assert [f["name"] for f in files[:2]] == ["lecture.pdf", "taxes.xlsx"]


def test_predict_folder(tmp_path):
    dl, docs = tmp_path / "Downloads", tmp_path / "Docs" / "Bills"
    dl.mkdir()
    docs.mkdir(parents=True)
    for i in range(3):
        f = dl / f"bill{i}.pdf"
        f.write_text("x")
        ops.move([str(f)], str(docs))
    new = dl / "bill9.pdf"
    new.write_text("x")
    p = habits.predict_folder(str(new))
    assert p and p["folder"] == str(docs) and p["count"] == 3
    assert habits.predict_folder(str(dl / "song.mp3")) is None


def test_naming_style(tmp_path):
    for i, n in enumerate(["2024-01-0{} trip photo.jpg", "2024-02-0{} rent receipt.pdf", "2024-03-0{} club notes.md",
                           "2024-04-0{} lab report.pdf", "2024-05-0{} bus pass.pdf"]):
        f = tmp_path / f"x{i}.dat"
        f.write_text("x")
        ops.rename(str(f), n.format(i + 1))
    st = habits.naming_style()
    assert st["date"] == "first" and st["sep"] == " " and st["case"] == "lower"
    from raven_core import naming

    assert naming.compose("Deed of Lease", "2024-06-01", "pdf", False, st) == "2024-06-01 deed of lease.pdf"


def test_quiet_and_muted():
    habits.suggestion_feedback("caches", False)
    q = habits.quiet()
    assert "caches" in q["hidden"] and not q["muted"]
    for _ in range(3):
        habits.suggestion_feedback("junk:node_modules", False)
    assert "junk" in habits.quiet()["muted"]
    habits.suggestion_feedback("junk:venv", True)
    assert "junk" not in habits.quiet()["muted"]


def test_rename_rule_suggestion(tmp_path):
    for i in range(3):
        f = tmp_path / f"IMG_{1000 + i}.jpg"
        f.write_text("x")
        ops.rename(str(f), f"Beach day {i}.jpg")
    sug = habits.rename_rule_suggestions()
    assert sug and sug[0]["action"] == "name" and sug[0]["folder"] == str(tmp_path)


def test_search_boost_and_clear(tmp_path):
    a, b = str(tmp_path / "a.txt"), str(tmp_path / "b.txt")
    habits.record("result_click", b, query="Rent  Receipt")
    out = habits.boost("rent receipt", [a, b])
    assert out.get(b, 0) > 2 and a not in out
    habits.clear()
    assert habits.boost("rent receipt", [a, b]) == {}
    assert habits.summary()["counts"] == {}
