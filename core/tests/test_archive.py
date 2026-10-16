"""Archives as folders: create, list, read a member, extract, and copy a member out."""

import os
import tempfile
import time

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.fs import archive, jobs, ops  # noqa: E402

db.init()


@pytest.mark.parametrize("fmt", ["zip", "tar.gz", "tar.zst", "7z"])
def test_round_trip(tmp_path, fmt):
    (tmp_path / "proj" / "src").mkdir(parents=True)
    (tmp_path / "proj" / "src" / "a.py").write_text("print(1)")
    (tmp_path / "proj" / "readme.md").write_text("# hi")
    p = archive.create([str(tmp_path / "proj")], fmt)
    assert archive.is_archive(p) and archive.split(p + "/proj/src/a.py") == (p, "proj/src/a.py")
    assert [e["name"] for e in archive.list_dir(p)["entries"]] == ["proj"]
    inner = archive.list_dir(p + "/proj")
    assert sorted(e["name"] for e in inner["entries"]) == ["readme.md", "src"] and not inner["writable"]
    assert open(archive.member_file(p + "/proj/src/a.py")).read() == "print(1)"
    x = archive.extract(p)
    assert os.path.basename(x) == "proj (2)" and open(os.path.join(x, "src", "a.py")).read() == "print(1)"
    ops.undo()
    assert not os.path.exists(x)


def test_copy_member_out(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "note.txt").write_text("inside")
    p = archive.create([str(tmp_path / "d" / "note.txt")], "zip")
    out = tmp_path / "out"
    out.mkdir()
    job = jobs.queue.add("copy", [p + "/note.txt"], str(out))
    end = time.time() + 10
    while job.status not in ("done", "error") and time.time() < end:
        time.sleep(0.02)
    assert job.status == "done" and (out / "note.txt").read_text() == "inside"


def test_zip_slip_is_refused(tmp_path):
    import zipfile

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.txt", "x")
    with pytest.raises(archive.ArchiveError):
        archive.extract(str(bad))
    assert not (tmp_path.parent / "evil.txt").exists()
