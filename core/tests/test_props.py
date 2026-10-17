"""Granular control (PLAN2 P2-5): permissions, dates, tags, links, explanations, folder rules, filters."""

import os
import shutil
import stat
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core import platform as plat  # noqa: E402
from raven_core.fs import ops, props  # noqa: E402
from raven_core.search.filters import parse  # noqa: E402

db.init()


@pytest.mark.parametrize("mode,cur,want", [
    ("755", 0o100644, 0o755), ("0o600", 0o100644, 0o600), ("u+x", 0o100644, 0o744), ("go-r", 0o100644, 0o600),
    ("a+x", 0o100644, 0o755), ("a=r", 0o100755, 0o444), ("u=rw,go=", 0o100777, 0o600), ("a+X", 0o40644, 0o755),
    ("a+X", 0o100644, 0o644), (0o700, 0o100644, 0o700),
])
def test_parse_mode(mode, cur, want):
    assert props.parse_mode(mode, cur) == want


def test_parse_mode_rejects_junk():
    with pytest.raises(ops.OpError):
        props.parse_mode("rwx!", 0o100644)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes and system folders")
def test_chmod_recursive_undo_redo(tmp_path):
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    f = d / "sub" / "a.txt"
    f.write_text("x")
    os.chmod(f, 0o644)
    os.chmod(d / "sub", 0o755)
    r = props.set_mode([str(d)], "750", recursive=True, files_mode="640")
    assert r["changed"] >= 3
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o640
    assert stat.S_IMODE(os.stat(d / "sub").st_mode) == 0o750
    ops.undo()
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o644
    assert stat.S_IMODE(os.stat(d / "sub").st_mode) == 0o755
    ops.redo()
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o640


def test_touch_undo(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    before = os.stat(f).st_mtime_ns
    props.set_times([str(f)], 1_000_000_000)
    assert int(os.stat(f).st_mtime) == 1_000_000_000
    ops.undo()
    assert os.stat(f).st_mtime_ns == before


def test_tags_and_undo(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    props.set_tags([str(f)], add=["work", "urgent"])
    assert props.read_tags(str(f)) == ["urgent", "work"]
    props.set_tags([str(f)], remove=["urgent"])
    assert props.read_tags(str(f)) == ["work"]
    ops.undo()
    assert props.read_tags(str(f)) == ["urgent", "work"]
    f2 = tmp_path / "b.txt"
    f2.write_text("y")
    props.set_tags([str(f2)], add=["urgent"])
    assert parse("tag:urgent").post(str(f2)) and not parse("tag:missing").post(str(f2))


def test_links_and_broken(tmp_path):
    t = tmp_path / "target.txt"
    t.write_text("x")
    r = props.make_link(str(t))
    link = r["path"]
    assert os.path.islink(link) and plat.readlink(link) == str(t)
    h = props.make_link(str(t), kind="hard")["path"]
    assert os.stat(h).st_ino == os.stat(t).st_ino and props.link_count(str(t)) == 2
    ops.undo()  # the hard link goes
    assert not os.path.exists(h)
    t.unlink()
    broken = props.broken_links(str(tmp_path))
    assert [b["path"] for b in broken] == [link]
    with pytest.raises(ops.OpError):
        props.make_link(str(tmp_path), kind="hard")


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes and system folders")
def test_explain():
    assert props.explain("/etc")["level"] == "system"
    assert props.explain("/boot")["level"] == "danger"
    assert "Inside /usr" in props.explain("/usr/lib/python3")["text"]
    assert props.explain(os.path.expanduser("~/.cache"))["level"] == "safe"
    assert props.explain("/home/nobody/projects/x/node_modules")["level"] == "safe"
    assert props.explain(os.path.expanduser("~/Documents/notes")) is None
    assert props.is_system("/etc") and not props.is_system("/tmp")


def test_policies(tmp_path):
    a = tmp_path / "private"
    (a / "deep").mkdir(parents=True)
    props.set_policy(str(a), no_cloud=True, protect=True)
    assert props.policy_for(str(a / "deep" / "x.txt"), "no_cloud")
    assert props.policy_for(str(a), "protect")
    assert not props.policy_for(str(tmp_path / "other"), "no_cloud")
    from raven_core import organize

    with pytest.raises(ValueError, match="folder rule"):
        organize.suggest(str(a))
    from raven_core.storage import protect

    assert protect.blocked(str(a / "deep" / "x.txt"), str(tmp_path))
    props.set_policy(str(a), no_cloud=False, protect=False)
    assert not props.policy_for(str(a), "no_cloud")
    assert all(p["path"] != str(a) for p in props.policies())


def test_filters_parse():
    f = parse("report owner:harsh perm:755 is:hidden tag:work")
    assert f.text.strip() == "report" and f.owner == "harsh" and f.perm == "755" and f.is_ == {"hidden"} and f.tags == ["work"]
    assert f.active and f.needs_post
    assert "is_hidden = 1" in f.sql()[0]


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes and system folders")
def test_perm_filter(tmp_path):
    f = tmp_path / "run.sh"
    f.write_text("#!/bin/sh\n")
    os.chmod(f, 0o755)
    g = tmp_path / "doc.txt"
    g.write_text("x")
    os.chmod(g, 0o644)
    assert parse("perm:x").post(str(f)) and not parse("perm:x").post(str(g))
    assert parse("perm:644").post(str(g)) and not parse("perm:644").post(str(f))


@pytest.mark.skipif(not (shutil.which("lsof") or shutil.which("fuser")), reason="no lsof or fuser")
def test_who_uses(tmp_path):
    f = tmp_path / "busy.txt"
    f.write_text("x")
    with open(f):
        procs = props.who_uses(str(f))
    assert any(p["pid"] == os.getpid() for p in procs)


def test_secure_delete(tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text("top secret" * 100)
    r = props.secure_delete([str(f)])
    assert r["deleted"] == 1 and not f.exists()
