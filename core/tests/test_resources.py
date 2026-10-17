"""Resources: the local models stay in memory only while they work, and background LLM work waits for mains power."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.index.indexer import indexer  # noqa: E402
from raven_core.router import local  # noqa: E402
from raven_core.search import folders  # noqa: E402

db.init()
folders.init()


class FakeResponse:
    status_code = 200

    def json(self):
        return {"message": {"content": '{"summary": "Bills."}'}, "done_reason": "stop"}


def _record(monkeypatch):
    bodies = []

    def post(url, json=None, **k):
        bodies.append(json)
        return FakeResponse()

    monkeypatch.setattr(local._client, "post", post)
    return bodies


def test_a_user_call_keeps_the_model_a_short_time_and_a_background_call_shorter(monkeypatch):
    bodies = _record(monkeypatch)
    local.chat("s", "u", {"type": "object"})
    seen = []
    monkeypatch.setattr(local, "_chat", lambda *a: seen.append(indexer.interactive.is_set()) or {})
    local.chat("s", "u", {"type": "object"}, background=True)
    assert bodies[0]["keep_alive"] == local.KEEP == "5m"
    assert seen == [False]  # a background call does not pause the indexer


def test_background_summaries_wait_for_mains_power(monkeypatch):
    bodies = _record(monkeypatch)
    with db.tx() as c:
        c.execute("INSERT INTO folders(path, name, files, profile, names, summary_stale) VALUES (?, ?, ?, ?, ?, 1)",
                  ("/x/bills", "bills", 3, "bill.pdf", "[]"))
    monkeypatch.setattr(local, "available", lambda: True)
    monkeypatch.setitem(indexer.state, "embed_ready", True)
    monkeypatch.setattr(folders.embed, "embed_docs", lambda items: [[0.0] * 8 for _ in items])
    monkeypatch.setattr(folders, "_has_vec", lambda: False)
    monkeypatch.setattr(local, "background_allowed", lambda: False)
    assert folders.summarize_some(indexer) == 0 and not bodies
    monkeypatch.setattr(local, "background_allowed", lambda: True)
    assert folders.summarize_some(indexer) >= 1  # the full suite shares one database, so other folders can wait too
    assert bodies and {b["keep_alive"] for b in bodies} == {local.KEEP_BACKGROUND}


def test_exit_frees_only_the_ollama_models_that_raven_used(monkeypatch):
    from raven_core.index import embed
    from raven_core.memory import unload_ollama

    bodies = _record(monkeypatch)
    monkeypatch.setattr(embed._client, "post", lambda url, json=None, **k: bodies.append(json))
    monkeypatch.setattr(local, "_used", False)
    monkeypatch.setattr(embed, "_used_ollama", False)
    unload_ollama()
    assert not bodies
    monkeypatch.setattr(local, "_used", True)
    monkeypatch.setattr(embed, "_used_ollama", True)
    unload_ollama()
    assert [b["keep_alive"] for b in bodies] == [0, 0]
