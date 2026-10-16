"""Ask end to end on a tiny index, in lite mode (no local model): the best passage with its citation."""

import json
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402
import sqlite_vec  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.index import embed  # noqa: E402

pytestmark = pytest.mark.skipif(not embed.static_available("potion-base-8m"), reason="the static model is not in models/")


def test_ask_lite_cites_the_passage(tmp_path):
    from raven_core.index.indexer import write_chunks
    from raven_core.rag import ask as core

    db.init()
    db.set_setting("embed_model", "potion-base-8m")
    db.set_setting("features", {"llm": False})
    docs = {
        "lease.txt": "Rent agreement for flat 4B. The rent is 18000 rupees a month. The late fee is 500 rupees after the 5th day.",
        "recipe.txt": "Mix the flour and the water. Bake the bread for forty minutes at a high heat.",
    }
    texts = []
    with db.tx() as c:
        for name, text in docs.items():
            p = tmp_path / name
            p.write_text(text)
            fid = c.execute("INSERT INTO files(path, parent, name, ext, size, mtime, is_dir, index_stage, root) VALUES (?,?,?,?,?,?,0,2,?)",
                            (str(p), str(tmp_path), name, "txt", len(text), 0, str(tmp_path))).lastrowid
            write_chunks(c, fid, name, None, [{"page": None, "start": 0, "end": len(text), "heading": "", "folder": "",
                                               "header": name, "text": text}])
            texts.append(text)
    vecs = embed.embed_docs([("", t) for t in texts])
    db.ensure_vec_table(len(vecs[0]), embed.model())
    with db.tx() as c:
        for cid, v in zip([r[0] for r in c.execute("SELECT id FROM chunks ORDER BY id")], vecs):
            c.execute("INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)", (cid, sqlite_vec.serialize_float32(v)))
            c.execute("UPDATE chunks SET embedded=1 WHERE id=?", (cid,))
    events = [json.loads(x) for x in core.ask("What is the late fee in my rent agreement?")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "sources" and "token" in kinds and kinds[-1] == "done", events
    text = "".join(e["t"] for e in events if e["type"] == "token")
    assert "500 rupees" in text and "[1]" in text
    assert events[0]["provider"] == "extract"
    db.set_setting("features", {})
