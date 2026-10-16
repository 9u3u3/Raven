"""Copy queue: progress, conflicts, pause, cancel, one undo per job. Restore from the trash."""

import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.fs import jobs, ops  # noqa: E402

db.init()


def wait(job, states=("done", "cancelled", "error"), timeout=20):
    end = time.time() + timeout
    while job.status not in states and time.time() < end:
        time.sleep(0.02)
    return job.status


def tree(tmp_path):
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "dst").mkdir()
    (tmp_path / "src" / "a.txt").write_text("alpha")
    (tmp_path / "src" / "sub" / "b.txt").write_text("beta")
    (tmp_path / "dst" / "a.txt").write_text("old")
    return tmp_path


def test_copy_with_conflict_choices_and_one_undo(tmp_path):
    t = tree(tmp_path)
    srcs = [str(t / "src" / "a.txt"), str(t / "src" / "sub")]
    found = jobs.conflicts(srcs, str(t / "dst"))
    assert [c["name"] for c in found] == ["a.txt"]
    job = jobs.queue.add("copy", srcs, str(t / "dst"), {srcs[0]: "replace"})
    assert wait(job) == "done"
    assert (t / "dst" / "a.txt").read_text() == "alpha"
    assert (t / "dst" / "sub" / "b.txt").read_text() == "beta"
    assert job.bytes_done == job.bytes_total == 9 and job.files_done == 2
    ops.undo()
    assert (t / "dst" / "a.txt").read_text() == "old" and not (t / "dst" / "sub").exists()


def test_skip_and_keep_both(tmp_path):
    t = tree(tmp_path)
    a = str(t / "src" / "a.txt")
    job = jobs.queue.add("copy", [a], str(t / "dst"), {a: "skip"})
    assert wait(job) == "done" and job.skipped == 1 and (t / "dst" / "a.txt").read_text() == "old"
    job = jobs.queue.add("copy", [a], str(t / "dst"))
    assert wait(job) == "done" and (t / "dst" / "a (2).txt").read_text() == "alpha"


def test_cancel_removes_the_partial_item(tmp_path, monkeypatch):
    t = tree(tmp_path)
    big = t / "src" / "big.bin"
    big.write_bytes(os.urandom(32 * 1024 * 1024))
    tick = jobs.Queue._tick

    def slow(self, job, n):
        time.sleep(0.02)  # a slow disk, so the test can pause in the middle
        tick(self, job, n)

    monkeypatch.setattr(jobs.Queue, "_tick", slow)
    job = jobs.queue.add("copy", [str(big)], str(t / "dst"))
    wait(job, ("running",))
    time.sleep(0.1)
    jobs.queue.control(job.id, "pause")
    time.sleep(0.2)
    done = job.bytes_done
    time.sleep(0.3)
    assert job.bytes_done == done and job.status == "paused"
    jobs.queue.control(job.id, "cancel")
    assert wait(job) == "cancelled"
    assert not (t / "dst" / "big.bin").exists()


def test_move_same_device(tmp_path):
    t = tree(tmp_path)
    s = str(t / "src" / "sub")
    job = jobs.queue.add("move", [s], str(t / "dst"))
    assert wait(job) == "done" and (t / "dst" / "sub" / "b.txt").exists() and not os.path.exists(s)
    ops.undo()
    assert os.path.exists(s)


def test_restore_from_trash_and_undo(tmp_path):
    p = tmp_path / "keep.txt"
    p.write_text("x")
    ops.trash([str(p)])
    trashed = os.path.join(os.environ["XDG_DATA_HOME"], "Trash", "files", "keep.txt")
    assert os.path.exists(trashed) and not p.exists()
    back = ops.restore([trashed])
    assert back == [str(p)] and p.read_text() == "x"
    ops.undo()
    assert not p.exists()
    ops.redo()
    assert p.exists()


def test_rename_many_with_swap_and_undo(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")
    (tmp_path / "c.txt").write_text("C")
    out = ops.rename_many([{"path": str(tmp_path / "a.txt"), "name": "b.txt"},
                           {"path": str(tmp_path / "b.txt"), "name": "a.txt"},
                           {"path": str(tmp_path / "c.txt"), "name": "03 - c.txt"}])
    assert len(out) == 3
    assert (tmp_path / "b.txt").read_text() == "A" and (tmp_path / "a.txt").read_text() == "B"
    assert not any(p.name.startswith(".raven-rename") for p in tmp_path.iterdir())
    ops.undo()
    assert (tmp_path / "c.txt").exists() and (tmp_path / "a.txt").read_text() == "A" and (tmp_path / "b.txt").read_text() == "B"
    ops.redo()
    assert (tmp_path / "b.txt").read_text() == "A" and (tmp_path / "03 - c.txt").exists()


def test_rename_many_refuses_clashes(tmp_path):
    import pytest

    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")
    (tmp_path / "keep.txt").write_text("K")
    with pytest.raises(ops.OpError):
        ops.rename_many([{"path": str(tmp_path / "a.txt"), "name": "x.txt"}, {"path": str(tmp_path / "b.txt"), "name": "x.txt"}])
    with pytest.raises(ops.OpError):
        ops.rename_many([{"path": str(tmp_path / "a.txt"), "name": "keep.txt"}])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "b.txt", "keep.txt"]
