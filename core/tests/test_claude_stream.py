"""claude -p answers stream piece by piece (PLAN3 P1-8). A fake claude script stands in for the CLI."""

import os
import stat
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.router import cloud  # noqa: E402

db.init()

STREAM = r"""#!/bin/sh
cat > /dev/null
echo '{"type":"system","subtype":"init"}'
echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}}'
echo 'not json'
echo '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}}'
echo '{"type":"result","is_error":false,"result":"Hello"}'
"""
WHOLE = """#!/bin/sh
cat > /dev/null
echo '{"type":"result","is_error":false,"result":"All at once"}'
"""
ERROR = """#!/bin/sh
cat > /dev/null
echo '{"type":"result","is_error":true,"result":"Not signed in"}'
"""


def _fake(tmp_path, monkeypatch, body: str) -> None:
    p = tmp_path / "claude"
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(cloud, "claude_ok", lambda: True)
    monkeypatch.setattr(cloud, "claude_env", lambda config_dir=None: dict(os.environ))


def test_pieces_come_one_by_one(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, STREAM)
    out = list(cloud.claude_stream("sys", "question"))
    assert out[0].startswith("claude -p")
    assert out[1:] == ["Hel", "lo"]


def test_an_answer_without_pieces_comes_at_the_end(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, WHOLE)
    assert list(cloud.claude_stream("sys", "q"))[1:] == ["All at once"]


def test_an_error_result_raises(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, ERROR)
    with pytest.raises(cloud.CloudError, match="Not signed in"):
        list(cloud.claude_stream("sys", "q"))
