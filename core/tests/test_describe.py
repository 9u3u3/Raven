"""The Inspector description (describe.py): folder counts and summaries, secrets hidden, clean text."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.describe import _clean, describe  # noqa: E402

db.init()


def test_folder_counts_and_empty(tmp_path):
    (tmp_path / "sub").mkdir()
    for n in ("a.pdf", "b.pdf", "c.jpg"):
        (tmp_path / n).write_bytes(b"x")
    assert "2 documents" in describe(str(tmp_path))["text"] and "1 folder" in describe(str(tmp_path))["text"]
    assert describe(str(tmp_path / "sub"))["text"] == "An empty folder."


def test_secret_text_is_not_shown(tmp_path):
    p = tmp_path / "passwords.txt"
    p.write_text("wifi: hunter2")
    with db.tx() as c:
        c.execute("INSERT INTO files(path, parent, name, ext, size, mtime, is_dir, root, category) VALUES (?,?,?,?,?,?,0,?,'Secret')",
                  (str(p), str(tmp_path), p.name, "txt", 13, 1, str(tmp_path)))
    try:
        d = describe(str(p))
        assert "hunter2" not in d["text"] and d["source"] == "privacy"
    finally:
        with db.tx() as c:  # other test files share this database
            c.execute("DELETE FROM files WHERE path=?", (str(p),))


def test_clean_removes_markdown_syntax():
    assert _clean("--- tags: a ---") == ""
    assert _clean("| Year | Value |\n| --- | --- |\n| 2011 | 5 |") == "Year, Value, 2011, 5"
    assert _clean("see [[Kyoto|the city]] and [[Japan]]") == "see Kyoto and Japan"
