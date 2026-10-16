"""Hybrid search (section 9.2): fuzzy name + BM25 + dense vectors, fused with RRF."""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import sqlite_vec
from rapidfuzz import fuzz, process, utils

from .. import db
from ..fs.kinds import kind_of
from ..index import embed
from ..index.indexer import indexer
from .filters import Filters, parse

RRF_K = 60
TOP = 100
# Dense hits weaker than this cosine similarity are noise for display. Calibrated on testdata.
MIN_SIM = 0.30
# A meaning hit must reach this share of the best hit's similarity.
REL_SIM = 0.75
RERANK_TOP = 20
# The reranker reads this many words around the query words of each candidate (CPU time grows with length).
RERANK_WORDS = 70
# The reranker order counts this much more than the fused order. Calibrated on testdata/queries.json.
RERANK_WEIGHT = 1.0

_TOKEN = re.compile(r"[\w]+", re.UNICODE)


# ---------------------------------------------------------------- names


@dataclass
class _Names:
    """The name list for fuzzy name search (PLAN3 P0-10). Only the ids, the processed names and one small
    fingerprint per row stay in RAM. The other columns come from SQLite for the hits only. The old list kept
    8 columns of every row in a dict: +666 MB on a real index of 665,325 rows."""

    version: int = -1
    ids: "np.ndarray" = None  # int64, sorted
    names: list[str] = None  # in the order of ids
    sig: "np.ndarray" = None  # int64: hash of (path, is_dir), in the order of ids
    # The changes between versions, for /index/names?since= (PLAN3 P0-9): (from, to, changed ids, removed ids).
    diffs: deque = field(default_factory=lambda: deque(maxlen=DIFF_KEEP))


DIFF_KEEP = 32
# More names than this: the names are scored in chunks on several threads (rapidfuzz releases the GIL).
PARALLEL_FROM = 20_000
_WORKERS = max(1, min(8, (os.cpu_count() or 2) // 2))
_pool: ThreadPoolExecutor | None = None

_names = _Names()
_names_lock = threading.Lock()


def _load_names() -> _Names:
    with _names_lock:
        if _names.version == indexer.names_version and _names.ids is not None:
            return _names
        version = indexer.names_version
        ids, names, sig = [], [], []
        for fid, path, name, is_dir in db.conn().execute("SELECT id, path, name, is_dir FROM files ORDER BY id"):
            ids.append(fid)
            names.append(utils.default_process(name))
            sig.append(hash((path, is_dir)))
        new_ids, new_sig = np.array(ids, dtype=np.int64), np.array(sig, dtype=np.int64)
        del ids, sig
        if _names.ids is not None:
            old_ids, old_sig = _names.ids, _names.sig
            at = np.searchsorted(old_ids, new_ids)
            ok = at < len(old_ids)
            ok[ok] = old_ids[at[ok]] == new_ids[ok]
            same = ok.copy()
            same[ok] = old_sig[at[ok]] == new_sig[ok]
            changed = new_ids[~same].tolist()
            removed = np.setdiff1d(old_ids, new_ids, assume_unique=True).tolist()
            _names.diffs.append((_names.version, version, changed, removed))
        _names.ids, _names.names, _names.sig, _names.version = new_ids, names, new_sig, version
        return _names


def _rows_by_id(ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    c = db.conn()
    for i in range(0, len(ids), 900):  # SQLite allows 999 parameters by default
        part = ids[i:i + 900]
        for r in c.execute(
            f"SELECT id, path, parent, name, ext, size, mtime, is_dir FROM files WHERE id IN ({','.join('?' * len(part))})", part
        ):
            out[r["id"]] = dict(r)
    return out


def name_items(since: int | None = None) -> dict:
    """The name list for the renderer. With `since`, only the changes after that version, when the sidecar
    still has them. A full list of a large index is many MB, and the indexer changes it often (PLAN3 P0-9)."""
    n = _load_names()
    if since is not None and since != n.version:
        chain, v = [], since
        for d in n.diffs:
            if d[0] == v:
                chain.append(d)
                v = d[1]
        if v == n.version and chain:
            changed = sorted({i for d in chain for i in d[2]} | {i for d in chain for i in d[3]})
            if len(changed) <= max(1000, len(n.ids) // 2):
                rows = _rows_by_id(changed)
                return {
                    "version": n.version, "full": False,
                    "upsert": [[r["id"], r["path"], r["is_dir"]] for r in rows.values()],
                    "remove": [i for i in changed if i not in rows],
                }
    if since is not None and since == n.version:
        return {"version": n.version, "full": False, "upsert": [], "remove": []}
    rows = db.conn().execute("SELECT id, path, is_dir FROM files").fetchall()
    return {"version": n.version, "full": True, "items": [[r[0], r[1], r[2]] for r in rows]}


def _fuzzy(q: str, names: list[str], limit: int) -> list[tuple[str, float, int]]:
    global _pool
    if len(names) < PARALLEL_FROM or _WORKERS == 1:
        return process.extract(q, names, scorer=fuzz.WRatio, limit=limit, score_cutoff=72, processor=None)
    # PLAN3 P1-5: one query over 665,325 names took 368 ms in one thread, and cdist(workers=-1) uses one thread
    # for one query. Chunks on 8 threads: 70 ms, with the same scores.
    if _pool is None:
        _pool = ThreadPoolExecutor(_WORKERS, thread_name_prefix="names")
    step = (len(names) + _WORKERS - 1) // _WORKERS

    def score(i: int) -> np.ndarray:
        return process.cdist([q], names[i:i + step], scorer=fuzz.WRatio, score_cutoff=72, processor=None, dtype=np.uint8)[0]

    scores = np.concatenate(list(_pool.map(score, range(0, len(names), step))))
    idx = np.flatnonzero(scores)
    if len(idx) > limit:
        idx = idx[np.argpartition(scores[idx], -limit)[-limit:]]
    idx = idx[np.argsort(-scores[idx], kind="stable")]
    return [(names[i], float(scores[i]), int(i)) for i in idx]


def _name_hits(f: Filters, limit: int = TOP) -> list[int]:
    q = utils.default_process(f.text)
    if not q:
        return []
    n = _load_names()
    hits = _fuzzy(q, n.names, limit * 3)
    rows = _rows_by_id([int(n.ids[idx]) for _, _, idx in hits]) if f.active else {}
    # WRatio matches parts of letters too: "a bar chart" gives a.txt 85 points. A name counts only when one real
    # word of the query (2 letters or more) is close to one word of the name. A typo ("dmbs") stays close.
    words = [w for w in q.split() if len(w) >= 2]
    out = []
    for name, score, idx in hits:
        if words and len(q.split()) > 1:
            toks = [t for t in name.split() if len(t) >= 2]
            if not any(t.startswith(w) or w.startswith(t) or fuzz.ratio(w, t) >= 75 for w in words for t in toks):
                continue
        fid = int(n.ids[idx])
        if f.active and (fid not in rows or not f.match(rows[fid])):
            continue
        out.append(fid)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- BM25


def _fts_query(text: str) -> str:
    toks = [t for t in _TOKEN.findall(text.lower()) if len(t) > 1 or t.isdigit()]
    if not toks:
        return ""
    parts = [f'"{t}"' for t in toks[:-1]] + [f'"{toks[-1]}"*']
    return " OR ".join(parts)


def _bm25(f: Filters) -> list[dict]:
    q = _fts_query(f.text)
    if not q:
        return []
    where, args = f.sql("f")
    sql = (
        "SELECT c.id cid, c.file_id fid, c.page, c.at, bm25(chunks_fts, 4.0, 3.0, 2.0, 1.0) s "
        "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id "
        f"WHERE chunks_fts MATCH ? AND {where} ORDER BY s LIMIT ?"
    )
    return [dict(r) for r in db.conn().execute(sql, [q, *args, TOP * 3])]


# ---------------------------------------------------------------- dense


def _dense(f: Filters, timings: dict) -> list[dict]:
    if not f.text or not db.has_vec_table():
        return []
    t0 = time.perf_counter()
    try:
        vec = embed.embed_query(f.text)
    except Exception:
        return []
    timings["embed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    blob = sqlite_vec.serialize_float32(vec)
    c = db.conn()
    t1 = time.perf_counter()
    if f.active:
        where, args = f.sql("f")
        rows = c.execute(
            "SELECT c.id cid, c.file_id fid, c.page, c.at, vec_distance_cosine(v.embedding, ?) d "
            "FROM chunk_vec v JOIN chunks c ON c.id = v.rowid JOIN files f ON f.id = c.file_id "
            f"WHERE {where} ORDER BY d LIMIT ?",
            [blob, *args, TOP * 2],
        ).fetchall()
    else:
        from . import vectors

        near = vectors.knn(vec, TOP if vectors.ready() else TOP * 2)
        dist = dict(near)
        ids = [i for i, _ in near]
        rows = sorted(
            ({**dict(r), "d": dist[r["cid"]]} for r in c.execute(
                f"SELECT c.id cid, c.file_id fid, c.page, c.at FROM chunks c WHERE c.id IN ({','.join('?' * len(ids))})", ids)),
            key=lambda r: r["d"]) if ids else []
    timings["dense_ms"] = round((time.perf_counter() - t1) * 1000, 1)
    hits = [dict(r) for r in rows if 1 - r["d"] >= MIN_SIM]
    if hits:
        # Drop the long tail of weak meaning hits. They add noise to the fusion.
        floor = (1 - hits[0]["d"]) * REL_SIM
        hits = [h for h in hits if 1 - h["d"] >= floor]
    return hits


# ---------------------------------------------------------------- snippets


def _snippet(text: str, terms: list[str], width: int = 180) -> list[dict]:
    """Return segments [{t, hit}] around the first matched term."""
    text = " ".join(text.split())
    low = text.lower()
    pos = -1
    for t in terms:
        m = re.search(r"\b" + re.escape(t), low)
        if m:
            pos = m.start()
            break
    if pos < 0:
        s = text[:width]
        return [{"t": s + ("…" if len(text) > width else ""), "hit": False}]
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    window = text[start:end]
    if start > 0:
        window = "…" + window
    if end < len(text):
        window += "…"
    if not terms:
        return [{"t": window, "hit": False}]
    pat = re.compile(r"\b(" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")\w*", re.I)
    out, last = [], 0
    for m in pat.finditer(window):
        if m.start() > last:
            out.append({"t": window[last:m.start()], "hit": False})
        out.append({"t": m.group(0), "hit": True})
        last = m.end()
    if last < len(window):
        out.append({"t": window[last:], "hit": False})
    return out


# ---------------------------------------------------------------- fuse


def _coverage(text: str, kw: list[tuple[int, dict]]) -> dict[int, float]:
    """Share of the query words that the best keyword chunk contains (with the file name).

    The FTS query joins words with OR, so a chunk with one word of three still matches.
    This weight keeps such partial matches below full ones in the fusion.
    """
    toks = [t for t in _TOKEN.findall(text.lower()) if len(t) > 1 or t.isdigit()]
    if len(toks) < 2 or not kw:
        return {}
    cids = [row["cid"] for _, row in kw]
    rows = db.conn().execute(
        f"SELECT c.id, c.text, f.name FROM chunks c JOIN files f ON f.id=c.file_id WHERE c.id IN ({','.join('?' * len(cids))})",
        cids,
    ).fetchall()
    texts = {r[0]: (r[2] + " " + r[1]).lower() for r in rows}
    out = {}
    for fid, row in kw:
        t = texts.get(row["cid"], "")
        found = sum(1 for i, w in enumerate(toks) if re.search(r"\b" + re.escape(w) + (r"\b" if i < len(toks) - 1 else ""), t))
        out[fid] = max(found, 1) / len(toks)
    return out


def _rank_files(rows: list[dict]) -> list[tuple[int, dict]]:
    """Keep the best chunk per file. Rows arrive best first."""
    seen, out = set(), []
    for r in rows:
        if r["fid"] in seen:
            continue
        seen.add(r["fid"])
        out.append((r["fid"], r))
    return out


def _filter_only(f: Filters) -> list[dict]:
    where, args = f.sql("f")
    rows = db.conn().execute(
        f"SELECT id FROM files f WHERE {where} AND f.is_dir=0 ORDER BY f.mtime DESC LIMIT ?", [*args, TOP]
    ).fetchall()
    return [{"fid": r[0], "why": "filter"} for r in rows]


def search(q: str, mode: str = "hybrid", limit: int = 50) -> dict:
    t0 = time.perf_counter()
    timings: dict = {}
    f = parse(q)

    if f.is_ & {"link", "broken"}:
        return _links(q, f, limit, t0)
    if not f.text and f.active:
        hits = _filter_only(f)
        results = _decorate([(h["fid"], {"why": "filter", "chunk": None, "page": None, "at": None, "score": 0, "ranks": {}}) for h in hits], [])
        if f.needs_post:
            results = [r for r in results if f.post(r["path"])]
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return {"query": q, "text": f.text, "chips": f.chips, "mode": mode, "results": results[:limit], "timings": timings}

    t = time.perf_counter()
    names = _name_hits(f)
    timings["name_ms"] = round((time.perf_counter() - t) * 1000, 1)
    t = time.perf_counter()
    kw = _rank_files(_bm25(f))
    timings["bm25_ms"] = round((time.perf_counter() - t) * 1000, 1)
    dense = _rank_files(_dense(f, timings)) if mode == "hybrid" else []

    fused: dict[int, dict] = {}

    def add(fid: int, rank: int, source: str, row: dict | None, weight: float = 1.0):
        e = fused.setdefault(fid, {"score": 0.0, "ranks": {}, "rows": {}})
        e["score"] += weight / (RRF_K + rank)
        e["ranks"][source] = rank
        if row is not None:
            e["rows"][source] = row

    for i, fid in enumerate(names):
        add(fid, i + 1, "name", None)
    cover = _coverage(f.text, kw)
    for i, (fid, row) in enumerate(kw):
        add(fid, i + 1, "keyword", row, cover.get(fid, 1.0))
    for i, (fid, row) in enumerate(dense):
        add(fid, i + 1, "meaning", row)

    t = time.perf_counter()
    ordered = sorted(fused.items(), key=lambda kv: kv[1]["score"], reverse=True)
    if mode == "hybrid" and len(ordered) > 1:
        ordered = _rerank(f.text, ordered, timings)
    ordered = _habit_boost(f.text, ordered)
    ordered = ordered[:limit]
    items = []
    for fid, e in ordered:
        why = min(e["ranks"], key=lambda s: e["ranks"][s])
        row = e["rows"].get(why) or e["rows"].get("keyword") or e["rows"].get("meaning")
        items.append((fid, {
            "why": why,
            "chunk": row["cid"] if row else None,
            "page": row["page"] if row else None,
            "at": row["at"] if row else None,
            "score": round(e["score"], 5),
            "ranks": e["ranks"],
            "sim": round(1 - e["rows"]["meaning"]["d"], 3) if "meaning" in e["rows"] else None,
            "rerank": e.get("rerank"),
            "habit": e.get("habit"),
        }))
    terms = [t for t in _TOKEN.findall(f.text.lower()) if len(t) > 1]
    results = _decorate(items, terms)
    if f.needs_post:
        results = [r for r in results if f.post(r["path"])]
    timings["fuse_ms"] = round((time.perf_counter() - t) * 1000, 1)
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return {"query": q, "text": f.text, "chips": f.chips, "mode": mode, "results": results, "timings": timings}


# A short query ("notes") is ambiguous: your habits decide more. A long one says what it wants.
HABIT_W_SHORT = 0.008
HABIT_W_LONG = 0.003


def _habit_boost(text: str, ordered: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    """PLAN2 P2-8.2: files you open often and lately, and files you clicked for this query, rank higher."""
    import math

    from .. import habits

    if not ordered or not habits.enabled():
        return ordered
    top = ordered[:100]
    ids = [fid for fid, _ in top]
    paths = {r[0]: r[1] for r in db.conn().execute(f"SELECT id, path FROM files WHERE id IN ({','.join('?' * len(ids))})", ids)}
    b = habits.boost(text, list(paths.values()))
    if not b:
        return ordered
    w = HABIT_W_SHORT if len(_TOKEN.findall(text)) <= 2 else HABIT_W_LONG
    for fid, e in top:
        bonus = b.get(paths.get(fid, ""), 0.0)
        if bonus:
            e["score"] += w * math.log1p(bonus)
            e["habit"] = round(bonus, 2)  # not a rank: "why" picks the best rank of name, keyword or meaning
    return sorted(top, key=lambda kv: kv[1]["score"], reverse=True) + ordered[100:]


def _links(q: str, f, limit: int, t0: float) -> dict:
    """is:link and is:broken (PLAN2 P2-5). The index skips links, so Raven walks the indexed folders
    (or the in: folder) on disk."""
    import os

    from ..fs.kinds import kind_of
    from ..fs.props import broken_links
    from ..index.indexer import roots

    bases = [r for r in roots() if not f.folder or f.folder.lower() in r.lower()] or roots()
    out: list[dict] = []
    for base in bases:
        if "broken" in f.is_:
            found = broken_links(base, limit=limit)
        else:
            found = []
            for dp, dns, fns in os.walk(base):
                dns[:] = [d for d in dns if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
                for n in dns + fns:
                    p = os.path.join(dp, n)
                    if os.path.islink(p):
                        found.append({"path": p, "target": os.readlink(p)})
                if len(found) >= limit:
                    break
        for x in found:
            if f.folder and f.folder.lower() not in os.path.dirname(x["path"]).lower():
                continue
            name = os.path.basename(x["path"])
            if f.text and f.text.lower() not in name.lower():
                continue
            out.append({"id": None, "path": x["path"], "name": name, "parent": os.path.dirname(x["path"]), "is_dir": False,
                        "size": None, "mtime": 0, "kind": kind_of(name, False), "title": None,
                        "snippet": [{"t": f"→ {x['target']}", "hit": False}], "why": "filter"})
    return {"query": q, "text": f.text, "chips": f.chips, "mode": "filter", "results": out[:limit],
            "timings": {"total_ms": round((time.perf_counter() - t0) * 1000, 1)}}


def _window(text: str, words: list[str], size: int) -> str:
    toks = text.split()
    if len(toks) <= size:
        return text
    hits = [any(t.lower().startswith(w) for w in words) for t in toks]
    best, at, cur = -1, 0, sum(hits[:size])
    for i in range(len(toks) - size + 1):
        if i:
            cur += hits[i + size - 1] - hits[i - 1]
        if cur > best:
            best, at = cur, i
    return " ".join(toks[at:at + size])


def _rerank(text: str, ordered: list[tuple[int, dict]], timings: dict) -> list[tuple[int, dict]]:
    """Step 6: the cross-encoder reads the query with each of the top RERANK_TOP files.

    The new order joins the fused order with RRF, so one weak reranker score cannot drop a strong name match.
    """
    from .. import lite
    from . import rerank

    if not lite.enabled("reranker"):
        return ordered

    if not rerank.model.available():
        return ordered
    head, tail = ordered[:RERANK_TOP], ordered[RERANK_TOP:]
    words = [w for w in _TOKEN.findall(text.lower()) if len(w) > 2]
    cids = [e["rows"][s]["cid"] for _, e in head for s in ("keyword", "meaning") if s in e["rows"]]
    c = db.conn()
    texts = {r[0]: r[1] for r in c.execute(
        f"SELECT id, text FROM chunks WHERE id IN ({','.join('?' * len(cids))})", cids)} if cids else {}
    names = {r[0]: (r[1], r[2]) for r in c.execute(
        f"SELECT id, name, title FROM files WHERE id IN ({','.join('?' * len(head))})", [fid for fid, _ in head])}
    passages = []
    for fid, e in head:
        name, title = names.get(fid, ("", None))
        row = e["rows"].get("keyword") or e["rows"].get("meaning")
        body = texts.get(row["cid"], "") if row else ""
        passages.append(f"{name}. {title or ''}. {_window(body, words, RERANK_WORDS)}")
    t = time.perf_counter()
    try:
        scores = rerank.model.scores(text, passages)
    except Exception:  # noqa: BLE001
        return ordered
    timings["rerank_ms"] = round((time.perf_counter() - t) * 1000, 1)
    by_model = sorted(range(len(head)), key=lambda i: scores[i], reverse=True)
    model_rank = {i: r + 1 for r, i in enumerate(by_model)}
    for i, (_, e) in enumerate(head):
        e["rerank"] = round(scores[i], 2)
        e["final"] = RERANK_WEIGHT / (RRF_K + model_rank[i]) + 1 / (RRF_K + i + 1)
    head.sort(key=lambda kv: kv[1]["final"], reverse=True)
    return head + tail


def _decorate(items: list[tuple[int, dict]], terms: list[str]) -> list[dict]:
    if not items:
        return []
    c = db.conn()
    ids = [fid for fid, _ in items]
    frows = {r["id"]: r for r in c.execute(
        f"SELECT id, path, parent, name, ext, size, mtime, is_dir, title FROM files WHERE id IN ({','.join('?' * len(ids))})", ids
    )}
    cids = [m["chunk"] for _, m in items if m.get("chunk")]
    crows = {}
    if cids:
        crows = {r["id"]: r for r in c.execute(
            f"SELECT id, text FROM chunks WHERE id IN ({','.join('?' * len(cids))})", cids
        )}
    out = []
    for fid, m in items:
        fr = frows.get(fid)
        if fr is None:
            continue
        snippet = []
        if m.get("chunk") in crows:
            snippet = _snippet(crows[m["chunk"]]["text"], terms if m["why"] != "meaning" else terms)
        out.append({
            "id": fid,
            "path": fr["path"],
            "name": fr["name"],
            "parent": fr["parent"],
            "is_dir": bool(fr["is_dir"]),
            "size": fr["size"],
            "mtime": (fr["mtime"] or 0) * 1000,
            "kind": kind_of(fr["name"], bool(fr["is_dir"])),
            "title": fr["title"],
            "snippet": snippet,
            **m,
        })
    return out
