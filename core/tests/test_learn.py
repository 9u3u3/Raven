"""Watch rules, corrections and organize preferences."""

import os
import struct
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db, learn, rules  # noqa: E402
from raven_core.fs import ops  # noqa: E402

db.init()
learn.init()
rules.init()


def _old(p, secs=120):
    t = time.time() - secs
    os.utime(p, (t, t))


def test_move_rule_new_files_only_and_undo(tmp_path):
    inbox, out = tmp_path / "Downloads", tmp_path / "Installers"
    inbox.mkdir()
    (inbox / "before.deb").write_text("x")
    _old(inbox / "before.deb")
    rules.add_rule({"folder": str(inbox), "match": "ext", "value": "deb, AppImage", "action": "move", "target": str(out)})
    # Files that were there before the rule stay, unless the user runs it on them.
    (inbox / "new.AppImage").write_text("x")
    _old(inbox / "new.AppImage")
    (inbox / "fresh.deb").write_text("x")  # too new: it may still be downloading
    res = rules.run_rule(rules.list_rules()[-1])
    assert res["moved"] == 1 and (out / "new.AppImage").exists() and (inbox / "before.deb").exists()
    ops.undo()
    assert (inbox / "new.AppImage").exists()
    # After undo the rule does not take the file again.
    assert rules.run_rule(rules.list_rules()[-1])["moved"] == 0
    assert rules.run_rule(rules.list_rules()[-1], existing=True)["moved"] == 1  # before.deb, by Run now


def test_rename_rule(tmp_path):
    d = tmp_path / "Scans"
    d.mkdir()
    r = rules.add_rule({"folder": str(d), "match": "name", "value": "scan", "action": "rename", "target": "{date} {name}{ext}"})
    p = d / "scan_01.pdf"
    p.write_text("x")
    _old(p)
    res = rules.run_rule(r)
    assert res["moved"] == 1 and any(x.name.endswith(" scan_01.pdf") and x.name[:4].isdigit() for x in d.iterdir())


def test_by_example_and_organize_prefs(tmp_path):
    vec = [1.0, 0.0, 0.0]
    with db.tx() as c:
        c.execute("INSERT INTO corrections(path, name, category, vec, ts) VALUES (?,?,?,?,?)",
                  ("/x/a.pdf", "a.pdf", "Lease", struct.pack("3f", *vec), 0))
    got = learn.by_example([0.99, 0.1, 0.0])
    assert got and got[0] == "Lease" and "a.pdf" in got[2]
    assert learn.by_example([0.0, 1.0, 0.0]) is None

    class M:
        def __init__(self, src, dst, cat):
            self.src, self.dst, self.category, self.move = src, dst, cat, True

    root = str(tmp_path)
    moves = [M(f"{root}/i.pdf", f"{root}/Money/i.pdf", "Invoice")]
    try:
        changed = learn.note_plan_edits(root, moves, [{"src": f"{root}/i.pdf", "dst": f"{root}/Money/Invoices/i.pdf"}])
        assert changed == {"Invoice": "Money/Invoices"} and learn.folder_prefs()["Invoice"] == "Money/Invoices"
    finally:
        # The tests share one database: other tests expect the default folders.
        db.set_setting("organize_folders", {})
        with db.tx() as c:
            c.execute("DELETE FROM corrections")


def test_galaxy_cluster_plan_and_apply(tmp_path):
    from raven_core import galaxy

    (tmp_path / "a").mkdir()
    for n in ("bill1.pdf", "bill2.pdf"):
        (tmp_path / "a" / n).write_text("x")
    db.set_setting("galaxy", {"sig": "x", "points": [
        {"id": 1, "path": str(tmp_path / "a" / "bill1.pdf"), "name": "bill1.pdf", "c": 0, "x": 0, "y": 0},
        {"id": 2, "path": str(tmp_path / "a" / "bill2.pdf"), "name": "bill2.pdf", "c": 0, "x": 0, "y": 0}],
        "clusters": [{"id": 0, "name": "Phone bills", "count": 2, "x": 0, "y": 0}]})
    plan = galaxy.cluster_plan(0)
    assert plan["folder"] == str(tmp_path / "a" / "Phone bills") and len(plan["moves"]) == 2
    res = galaxy.cluster_apply(plan["folder"], plan["moves"])
    assert res["moved"] == 2 and (tmp_path / "a" / "Phone bills" / "bill1.pdf").exists()
    ops.undo()
    assert (tmp_path / "a" / "bill1.pdf").exists() and not (tmp_path / "a" / "Phone bills").exists()
    db.set_setting("galaxy", None)
