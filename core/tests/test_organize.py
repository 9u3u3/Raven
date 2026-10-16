"""Classification rules, Organize plans, and one undo for a whole plan."""

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import classify, db, organize  # noqa: E402
from raven_core.fs import ops  # noqa: E402

db.init()


@pytest.mark.parametrize("name,cat", [
    ("Screenshot from 2024-03-11.png", "Screenshot"),
    ("IMG_2032.jpg", "Photo"),
    ("Rahul_Sharma_resume.pdf", "Resume"),
    ("hall_ticket.pdf", "Exam"),
    ("dbms_unit4.docx", "Lecture notes"),
    ("wifi_passwords.txt", "Secret"),
    ("setup.AppImage", "Installer"),
    ("lecture_recording.mp4.part", "Other"),
    ("photo.png.zip", "Archive"),
])
def test_rules(name, cat):
    assert classify.rule(f"/x/{name}", name)[0] == cat


def test_unknown_names_wait_for_meaning():
    assert classify.rule("/x/Untitled(1).pdf", "Untitled(1).pdf") is None


@pytest.mark.parametrize("name,title,cat,out", [
    ("Untitled(1).pdf", "PRIYA NAIR", "Resume", "Resume - Priya Nair.pdf"),
    ("scan_0042.pdf", "DEED OF LEASE", "Lease", "Deed of Lease.pdf"),
    ("IMG_2044.pdf", "Indian Railways, e-ticket", "Ticket", "Indian Railways, e-ticket.pdf"),
    ("download (7).pdf", "Order confirmation", "Receipt", "Receipt - Order confirmation.pdf"),
    ("IMG_2031.png", "", "Photo", None),
    ("cover_letter.docx", "Cover letter", "Cover letter", None),
])
def test_new_names(name, title, cat, out):
    assert organize.new_name(name, title, cat) == out


def _put(c, root, name, cat, title=None):
    p = os.path.join(root, name)
    with open(p, "w") as f:
        f.write("x")
    c.execute(
        "INSERT INTO files(path, parent, name, ext, size, mtime, is_dir, category, category_source, category_reason, title) "
        "VALUES (?,?,?,?,?,?,0,?,?,?,?)",
        (p, root, name, name.rsplit(".", 1)[-1], 1, 0, cat, "rule", "test", title),
    )


def test_suggest_apply_undo(tmp_path):
    while ops.peek()["undo"]:
        ops.undo()
    root = str(tmp_path)
    os.mkdir(os.path.join(root, "Photos"))
    with db.tx() as c:
        _put(c, root, "IMG_1.jpg", "Photo")
        _put(c, root, "hw1.pdf", "Assignment")
        _put(c, root, "Untitled.docx", "Invoice", "Tax Invoice")
        _put(c, root, "wifi_password.txt", "Secret")
        _put(c, root, "movie.mp4.part", "Other")
    plan = organize.suggest(root)
    dst = {os.path.basename(m.src): os.path.relpath(m.dst, root) for m in plan.moves}
    assert dst == {"IMG_1.jpg": "Photos/IMG_1.jpg", "hw1.pdf": "Study/hw1.pdf", "Untitled.docx": "Money/Tax Invoice.docx"}
    assert {os.path.basename(s["path"]) for s in plan.skipped} == {"wifi_password.txt", "movie.mp4.part"}
    before = sorted(os.listdir(root))
    res = organize.apply(plan.id, [{"src": m.src, "dst": m.dst} for m in plan.moves])
    assert res["moved"] == 3 and not res["errors"]
    assert os.path.exists(os.path.join(root, "Money", "Tax Invoice.docx"))
    ops.undo()
    assert sorted(os.listdir(root)) == before


def test_apply_rejects_paths_outside_the_plan(tmp_path):
    root = str(tmp_path)
    with db.tx() as c:
        _put(c, root, "hw2.pdf", "Assignment")
    plan = organize.suggest(root)
    res = organize.apply(plan.id, [{"src": "/etc/passwd", "dst": os.path.join(root, "x")},
                                   {"src": plan.moves[0].src, "dst": "/tmp/elsewhere.pdf"}])
    assert res["moved"] == 0 and len(res["errors"]) == 2


def test_new_files_get_rule_categories_at_once(tmp_path):
    from raven_core import organize

    for n in ("invoice_march.pdf", "IMG_20250101_1200.jpg"):
        (tmp_path / n).write_bytes(b"x" * 10)
    plan = organize.suggest(str(tmp_path))
    moved = {os.path.basename(m.src) for m in plan.moves}
    assert "IMG_20250101_1200.jpg" in moved, (moved, plan.skipped)


def test_cut_json_answer_keeps_complete_items():
    from raven_core.router.local import salvage

    cut = '{"items": [{"i": 1, "folder": "Bills", "reason": "a {bill}"}, {"i": 2, "folder": "Tic'
    assert salvage(cut) == {"items": [{"i": 1, "folder": "Bills", "reason": "a {bill}"}]}
    assert salvage('{"a') is None
