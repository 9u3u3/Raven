"""File operations and the undo journal."""

import os
import sys
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ["RAVEN_DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["XDG_DATA_HOME"] = os.path.join(_tmp, "xdg")

from raven_core import db  # noqa: E402
from raven_core.fs import ops  # noqa: E402

db.init()


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "one.txt").write_text("one")
    (tmp_path / "a" / "sub").mkdir()
    (tmp_path / "a" / "sub" / "two.txt").write_text("two")
    return tmp_path


def drain():
    while ops.peek()["undo"]:
        ops.undo()


def test_copy_undo_redo(tree):
    drain()
    out = ops.copy([str(tree / "a" / "one.txt"), str(tree / "a" / "sub")], str(tree / "b"))
    assert (tree / "b" / "one.txt").read_text() == "one"
    assert (tree / "b" / "sub" / "two.txt").exists()
    ops.undo()
    assert not (tree / "b" / "one.txt").exists() and not (tree / "b" / "sub").exists()
    ops.redo()
    assert (tree / "b" / "one.txt").exists() and (tree / "b" / "sub" / "two.txt").exists()
    assert len(out) == 2


def test_copy_conflict_keeps_both(tree):
    out = ops.copy([str(tree / "a" / "one.txt")], str(tree / "a"))
    assert out[0].endswith("one (2).txt")


def test_move_rename_undo(tree):
    ops.move([str(tree / "a" / "one.txt")], str(tree / "b"))
    assert (tree / "b" / "one.txt").exists()
    ops.rename(str(tree / "b" / "one.txt"), "uno.txt")
    assert (tree / "b" / "uno.txt").exists()
    ops.undo()
    assert (tree / "b" / "one.txt").exists()
    ops.undo()
    assert (tree / "a" / "one.txt").exists() and not (tree / "b" / "one.txt").exists()


def test_rename_conflict(tree):
    (tree / "a" / "x.txt").write_text("x")
    with pytest.raises(ops.OpError) as e:
        ops.rename(str(tree / "a" / "x.txt"), "one.txt")
    assert e.value.status == 409


def test_trash_undo_redo(tree):
    p = tree / "a" / "sub"
    ops.trash([str(p)])
    assert not p.exists()
    from raven_core.platform import trash_home

    trash_files = os.path.join(trash_home(), "files")
    assert "sub" in os.listdir(trash_files)
    ops.undo()
    assert (p / "two.txt").read_text() == "two"
    ops.redo()
    assert not p.exists()
    ops.undo()
    assert p.exists()



def test_delete_in_the_trash_removes_the_info_file(tree):
    p = tree / "a" / "one.txt"
    ops.trash([str(p)])
    from raven_core.platform import trash_home

    trash = str(trash_home())
    name = next(n for n in os.listdir(os.path.join(trash, "files")) if n.startswith("one"))
    assert os.path.exists(os.path.join(trash, "info", f"{name}.trashinfo"))
    with pytest.raises(ops.OpError, match="in the trash already"):
        ops.trash([os.path.join(trash, "files", name)])
    ops.delete([os.path.join(trash, "files", name)])
    assert not os.path.exists(os.path.join(trash, "files", name))
    assert not os.path.exists(os.path.join(trash, "info", f"{name}.trashinfo"))
    # The move to the trash cannot be undone now. Undo skips it and does not fail on the missing file.
    drain()


def test_mkdir_newfile_undo(tree):
    d = ops.mkdir(str(tree))
    f = ops.newfile(str(tree))
    assert os.path.isdir(d) and os.path.isfile(f)
    ops.undo()
    assert not os.path.exists(f)
    ops.undo()
    assert not os.path.exists(d)


def test_new_action_clears_redo(tree):
    ops.mkdir(str(tree), "x1")
    ops.undo()
    assert ops.peek()["redo"]
    ops.mkdir(str(tree), "x2")
    assert ops.peek()["redo"] is None


def test_delete_is_not_undoable(tree):
    drain()
    ops.delete([str(tree / "a" / "one.txt")])
    assert not (tree / "a" / "one.txt").exists()
    assert ops.peek()["undo"] is None


def test_copy_into_itself(tree):
    with pytest.raises(ops.OpError):
        ops.copy([str(tree / "a")], str(tree / "a" / "sub"))


def test_delete_many_folders_reports_progress(tmp_path):
    dirs = []
    for n in range(9):
        d = tmp_path / f"node_modules.{n}"
        (d / "pkg").mkdir(parents=True)
        for k in range(20):
            (d / "pkg" / f"f{k}.js").write_text("x")
        dirs.append(str(d))
    seen = []
    n = ops.delete(dirs, progress=lambda done, total, path: seen.append((done, total, path)))
    assert n == 9 and not any(os.path.exists(d) for d in dirs)
    assert seen == [(k + 1, 9, d) for k, d in enumerate(dirs)]


def test_delete_stops_at_the_first_error(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("x")
    with pytest.raises(OSError):
        ops.delete([str(tmp_path / "missing.txt")] + [str(ok)])
    assert ok.exists()  # the items after the error stay
