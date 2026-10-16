"""File graph (PLAN2 P2-7): references, mentions, same content, local graphs and the folder level."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db, graph  # noqa: E402
from raven_core.index.indexer import write_chunks  # noqa: E402

db.init()


def test_references():
    md = "See [the plan](plan.md) and [[Budget 2026|money]] and ![img](img/a.png) and [web](https://x.org)."
    assert graph.references(md, "md") == [("path", "plan.md"), ("path", "img/a.png"), ("wiki", "Budget 2026")]
    assert graph.references('<a href="b.html#top">b</a><img src="c.png">', "html") == [("path", "b.html"), ("path", "c.png")]
    assert graph.references("\\input{intro}\\includegraphics[width=3cm]{fig/one.png}", "tex") == [("path", "intro"), ("path", "fig/one.png")]
    py = "import os\nfrom .util import x\nfrom pkg.mod import y\n"
    assert graph.references(py, "py") == [("py", "os"), ("py", ".util"), ("py", "pkg.mod")]
    js = "import a from './a'\nconst b = require('../lib/b')\nimport c from 'react'\n"
    assert graph.references(js, "ts") == [("path", "./a"), ("path", "../lib/b")]


def _add(root, rel, text=None, light=0):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text or "")
    with db.tx() as c:
        fid = c.execute("INSERT INTO files(path, parent, name, ext, size, mtime, is_dir, index_stage, root, light) VALUES (?,?,?,?,?,?,0,2,?,?)",
                        (str(p), str(p.parent), p.name, p.name.rsplit(".", 1)[-1], len(text or ""), 1, str(root), light)).lastrowid
        if text and not light:
            write_chunks(c, fid, p.name, None, [{"page": None, "start": 0, "end": len(text), "heading": "", "folder": "",
                                                 "header": p.name, "text": text}])
    return fid


def test_build_and_query(tmp_path):
    a = _add(tmp_path, "vault/Index.md", "Start here. [[Ideas]] and [plan](sub/plan.md). The numbers are in budget_v2.xlsx.")
    b = _add(tmp_path, "vault/Ideas.md", "Some ideas. Back to [[Index]].")
    c = _add(tmp_path, "vault/sub/plan.md", "The plan.")
    d = _add(tmp_path, "vault/budget_v2.xlsx", "x")
    e = _add(tmp_path, "code/app/main.py", "from .util import helper\nimport json\n", light=1)
    f = _add(tmp_path, "code/app/util.py", "def helper(): pass\n", light=1)
    g = _add(tmp_path, "docs/report.docx", "Report text")
    h = _add(tmp_path, "docs/report.pdf", "Report text")
    orphan = _add(tmp_path, "docs/alone.txt", "nothing links here")
    graph.build()
    edges = {(r[0], r[1], r[2]) for r in db.conn().execute("SELECT src, dst, type FROM graph_edges")}
    assert (a, b, "ref") in edges and (b, a, "ref") in edges and (a, c, "ref") in edges
    assert (a, d, "mention") in edges
    assert (e, f, "ref") in edges  # an import in a names-only project
    assert (min(g, h), max(g, h), "same") in edges
    full = graph.graph(str(tmp_path), types=["ref", "mention", "same"])
    ids = {n["id"] for n in full["nodes"]}
    assert full["level"] == "file" and orphan not in ids and {a, b, c, d, e, f, g, h} <= ids
    assert next(n for n in full["nodes"] if n["id"] == a)["degree"] >= 3
    with_orphans = graph.graph(str(tmp_path), types=["ref"], orphans=True)
    assert orphan in {n["id"] for n in with_orphans["nodes"]}
    folders = graph.graph(str(tmp_path), types=["ref", "mention", "same"], limit=3)
    assert folders["level"] == "folder" and {os.path.basename(n["path"]) for n in folders["nodes"]} == {"vault", "code", "docs"}
    loc = graph.local(str(tmp_path / "vault/Ideas.md"), depth=1)
    assert loc["center"] == b and {n["id"] for n in loc["nodes"]} == {a, b}
    loc2 = graph.local(str(tmp_path / "vault/Ideas.md"), depth=2)
    assert {c, d} <= {n["id"] for n in loc2["nodes"]}


def test_for_path_reads_only_that_folder(tmp_path):
    """A path graph walks only the named folder: the tree, the links in text files and the same content."""
    vault = tmp_path / "vault"
    (vault / "sub").mkdir(parents=True)
    (vault / "a.md").write_text("See [[b]] and the sheet budget_2026.xlsx.")
    (vault / "b.md").write_text("plain note")
    (vault / "sub" / "budget_2026.xlsx").write_bytes(b"x" * 10)
    (vault / "sub" / "copy.bin").write_bytes(b"x" * 10)  # the same bytes as the sheet
    (vault / ".hidden").mkdir()
    (vault / ".hidden" / "secret.md").write_text("[[a]]")
    (tmp_path / "outside.md").write_text("[[a]]")
    g = graph.for_path(str(vault))
    paths = {n["path"] for n in g["nodes"]}
    assert str(tmp_path / "outside.md") not in paths
    assert not any(".hidden" in p for p in paths)
    kinds = {(os.path.basename(e["src"]), os.path.basename(e["dst"]), e["type"]) for e in g["edges"]}
    assert ("a.md", "b.md", "ref") in kinds
    assert ("a.md", "budget_2026.xlsx", "mention") in kinds
    assert ("vault", "sub", "folder") in kinds
    assert any(t == "same" for *_, t in kinds)
    only = graph.for_path(str(vault), ["ref"])
    assert {e["type"] for e in only["edges"]} == {"ref"}


def test_for_path_missing_folder():
    import pytest

    with pytest.raises(FileNotFoundError):
        graph.for_path("/no/such/folder")
