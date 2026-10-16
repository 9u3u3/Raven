"""The name list: small in RAM, sent as changes, fast on a large index (PLAN3 P0-9, P0-10, P1-5)."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.index.indexer import indexer  # noqa: E402
from raven_core.search import hybrid  # noqa: E402
from raven_core.search.filters import parse  # noqa: E402

db.init()


def _add(path: str, size: int = 10, ext: str = "pdf") -> int:
    with db.tx() as c:
        c.execute("INSERT INTO files(path, parent, name, ext, size, mtime, is_dir) VALUES (?, ?, ?, ?, ?, 0, 0)",
                  (path, os.path.dirname(path), os.path.basename(path), ext, size))
        return c.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]


def _bump() -> None:
    indexer.names_version += 1


def test_changes_only_after_the_first_list():
    a = _add("/names-test/alpha report.pdf")
    _bump()
    full = hybrid.name_items()
    assert full["full"] and [a, "/names-test/alpha report.pdf", 0] in full["items"]
    v = full["version"]
    b = _add("/names-test/beta notes.pdf")
    with db.tx() as c:
        c.execute("DELETE FROM files WHERE id=?", (a,))
    _bump()
    hybrid._load_names()  # a search can load the new version first: the chain still works
    d = hybrid.name_items(since=v)
    assert d["full"] is False
    assert d["upsert"] == [[b, "/names-test/beta notes.pdf", 0]]
    assert d["remove"] == [a]
    assert hybrid.name_items(since=d["version"]) == {"version": d["version"], "full": False, "upsert": [], "remove": []}


def test_unknown_version_gets_the_full_list():
    _bump()
    assert hybrid.name_items(since=12345)["full"] is True


def test_memory_holds_no_row_dicts():
    _bump()
    n = hybrid._load_names()
    assert not hasattr(n, "rows")
    assert len(n.ids) == len(n.names) == len(n.sig)


def test_filters_still_apply_to_name_hits():
    small = _add("/names-test/zephyr budget.xlsx", size=100, ext="xlsx")
    big = _add("/names-test/zephyr budget.pdf", size=50 * 1024**2, ext="pdf")
    _bump()
    assert set(hybrid._name_hits(parse("zephyr budget"))) >= {small, big}
    assert hybrid._name_hits(parse("zephyr budget type:pdf")) == [big]


def test_parallel_search_matches_the_single_thread_result(monkeypatch):
    names = [f"file number {i}" for i in range(3000)] + ["quarterly invoice march", "invoice draft", "invoices"]
    single = hybrid._fuzzy("invoice", names, 10)
    monkeypatch.setattr(hybrid, "PARALLEL_FROM", 0)
    parallel = hybrid._fuzzy("invoice", names, 10)
    assert {(n, round(s)) for n, s, _ in single} == {(n, round(s)) for n, s, _ in parallel}
    assert parallel[0][1] >= parallel[-1][1]
