"""Private folders and the media rule (PLAN4 7.6): no AI and no model reads a private folder, and no photo, video
or audio goes to a cloud AI."""

import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))
os.environ.setdefault("RAVEN_TOKEN", "test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from raven_core import app as app_mod  # noqa: E402
from raven_core import config, db, habits  # noqa: E402
from raven_core.fs import props  # noqa: E402
from raven_core.index import indexer as ix  # noqa: E402
from raven_core.privacy import gate, scope  # noqa: E402

db.init()
client = TestClient(app_mod.app, headers={"Authorization": f"Bearer {app_mod.TOKEN}"})


@pytest.fixture()
def home(tmp_path):
    (tmp_path / "Diary").mkdir()
    (tmp_path / "Diary" / "secret.md").write_text("# My diary\nI met Asha at the lake.")
    (tmp_path / "Diary" / "deep").mkdir()
    (tmp_path / "Diary" / "deep" / "note.txt").write_text("more private words")
    (tmp_path / "Work").mkdir()
    (tmp_path / "Work" / "plan.md").write_text("# Quarter plan\nShip the release.")
    db.set_setting("index_roots", [str(tmp_path)])
    yield tmp_path
    for r in props.policies():
        props.set_policy(r["path"], **{k: False for k in props.POLICIES})


def _indexed(root: Path) -> set[str]:
    rows = db.conn().execute("SELECT path FROM files WHERE path LIKE ?", (f"{root}{os.sep}%",)).fetchall()
    return {os.path.relpath(r[0], root).replace(os.sep, "/") for r in rows}


def test_private_implies_the_other_rules(home):
    props.set_policy(str(home / "Diary"), private=True)
    f = str(home / "Diary" / "deep" / "note.txt")
    assert scope.is_private(f)
    for flag in ("no_index", "no_cloud", "no_compress"):
        assert props.policy_for(f, flag)
    assert not props.policy_for(f, "protect")  # the user still changes the files
    assert not scope.is_private(str(home / "Work" / "plan.md"))
    assert props.private_paths() == [str(home / "Diary")]


def test_the_walk_skips_a_private_folder_and_drops_old_rows(home):
    ix.indexer.scan_root(str(home))
    assert "Diary/secret.md" in _indexed(home)
    props.set_policy(str(home / "Diary"), private=True)
    ix.indexer.scan_root(str(home))
    got = _indexed(home)
    assert not any(p.startswith("Diary") for p in got)
    assert "Work/plan.md" in got


def test_a_watcher_event_in_a_private_folder_adds_nothing(home):
    props.set_policy(str(home / "Diary"), private=True)
    ix.indexer.scan_root(str(home))
    (home / "Diary" / "new.md").write_text("new private text")
    ix.indexer.update_paths([str(home / "Diary" / "new.md")])
    assert "Diary/new.md" not in _indexed(home)


def test_the_route_forgets_what_raven_knows(home):
    ix.indexer.scan_root(str(home))
    secret = str(home / "Diary" / "secret.md")
    habits.record("open", secret)
    db.visit(str(home / "Diary"))
    db.set_setting("galaxy", {"points": [1], "clusters": [], "sig": "x"})
    r = client.post("/fs/policies", json={"path": str(home / "Diary"), "private": True})
    assert r.status_code == 200, r.text
    assert r.json()["private"] == 1
    assert r.json()["forgot"]["files"] >= 3
    c = db.conn()
    assert c.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (f"{home}/Diary%",)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM activity WHERE path LIKE ?", (f"{home}/Diary%",)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM folder_visits WHERE path LIKE ?", (f"{home}/Diary%",)).fetchone()[0] == 0
    assert db.get_setting("galaxy") is None
    # The files stay as they are.
    assert Path(secret).read_text().startswith("# My diary")


def test_no_habits_from_a_private_folder(home):
    props.set_policy(str(home / "Diary"), private=True)
    secret = str(home / "Diary" / "secret.md")
    habits.record("open", secret)
    db.visit(str(home / "Diary"))
    c = db.conn()
    assert c.execute("SELECT COUNT(*) FROM activity WHERE path=?", (secret,)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM folder_visits WHERE path=?", (str(home / "Diary"),)).fetchone()[0] == 0


def test_ai_entry_points_refuse_a_private_path(home):
    props.set_policy(str(home / "Diary"), private=True)
    with pytest.raises(scope.PrivateError):
        scope.require_ai(str(home / "Diary" / "secret.md"))
    r = client.post("/router/run", json={"task": "folder_summary", "path": str(home / "Diary")})
    assert r.status_code == 403
    r = client.post("/agent/plan", json={"command": "sort these files", "cwd": str(home / "Diary")})
    assert r.status_code in (400, 403, 422)
    assert "private" in r.text.lower()


def test_plain_file_work_still_runs_in_a_private_folder(home):
    props.set_policy(str(home / "Diary"), private=True)
    r = client.get("/fs/list", params={"path": str(home / "Diary")})
    assert r.status_code == 200
    assert {e["name"] for e in r.json()["entries"]} >= {"secret.md", "deep"}
    r = client.post("/fs/rename", json={"path": str(home / "Diary" / "secret.md"), "name": "renamed.md"})
    assert r.status_code == 200, r.text
    assert (home / "Diary" / "renamed.md").exists()


def test_a_private_thumbnail_stays_in_memory(home):
    from PIL import Image

    pic = home / "Diary" / "face.png"
    Image.new("RGB", (400, 300), "red").save(pic)
    props.set_policy(str(home / "Diary"), private=True)
    out = app_mod._thumb(str(pic), 256)
    assert isinstance(out, bytes) and out[:4] == b"RIFF"
    assert not config.thumb_file(str(pic), os.stat(pic), 256).exists()
    open_pic = home / "Work" / "chart.png"
    Image.new("RGB", (400, 300), "blue").save(open_pic)
    assert isinstance(app_mod._thumb(str(open_pic), 256), Path)


def test_media_never_goes_to_the_cloud(home):
    assert scope.is_media("/x/IMG_2001.jpg") and scope.is_media("/x/clip.mp4") and scope.is_media("/x/a.mp3")
    assert not scope.is_media("/x/notes.md")
    assert not scope.cloud_allowed(str(home / "Work" / "photo.jpg"))
    assert scope.cloud_allowed(str(home / "Work" / "plan.md"))


@pytest.mark.parametrize("payload", [
    "look: data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ",
    "frame " + "QUJD" * 200,
])
def test_the_gate_blocks_binary_data(payload):
    blocked, why = gate.final_check(payload)
    assert blocked and "binary" in why


def test_the_gate_lets_plain_text_pass():
    assert gate.final_check("The quarter plan ships the release in March.") == (False, "")
