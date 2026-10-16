"""Retrieval for Ask (section 9.2, RAG steps 1, 2 and 7).

Chunk level, not file level: BM25 and dense hits are fused with RRF per chunk.
Each file gives at most MAX_PER_FILE chunks, so one long file cannot fill the context.
Then each chunk grows to its parent context (the chunks next to it in the same
section) while the token budget allows.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import sqlite_vec

from .. import db
from ..index import embed
from ..search.filters import parse
from ..search.hybrid import MIN_SIM, RRF_K, _fts_query

TOP = 60
MAX_PER_FILE = 2
RERANK_TOP = 20
# A question is "found" when the best chunk reaches this cosine similarity, or when a
# keyword chunk holds most of the question's content words. Calibrated on testdata/questions.json.
FOUND_SIM = 0.38
FOUND_COVER = 0.5
# Chunks below this share of the best similarity add noise to the context.
REL_SIM = 0.8

STOP = set(
    "a an the of in on at to for from by with about into over under and or but not no is are was were be been being "
    "do does did done have has had can could should would will shall may might must what which who whom whose when "
    "where why how that this these those it its i me my we our you your he she they them their there here than then "
    "so if as any all some much many more most tell explain give show find please my mine file files document "
    "documents say says said".split()
)
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class Source:
    n: int
    chunk_id: int
    file_id: int
    path: str
    name: str
    page: int | None
    heading: str
    text: str  # the context text: the chunk with its parent context
    hit: str  # the matched chunk alone
    why: str
    sim: float | None
    chunk_ids: list[int] = field(default_factory=list)
    at: float | None = None

    def public(self) -> dict:
        snippet = " ".join(self.hit.split())
        return {"n": self.n, "path": self.path, "name": self.name, "page": self.page, "at": self.at, "heading": self.heading,
                "why": self.why, "snippet": snippet[:240] + ("…" if len(snippet) > 240 else "")}


def content_words(q: str) -> list[str]:
    return [w for w in _WORD.findall(q.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]


def _tokens(text: str) -> int:
    return int(len(text.split()) / 0.75) + 1


def retrieve(question: str, k: int = 6, budget: int = 1800) -> tuple[list[Source], dict]:
    """Return (sources, info). info has timings, the best similarity and whether the files hold an answer."""
    t0 = time.perf_counter()
    info: dict = {"timings": {}}
    f = parse(question)
    # A question's dates are about the document ("the bill from March"), not about the file's
    # modified time. Date filters stay off for Ask. Types and sizes stay on.
    if f.after or f.before:
        f.after = f.before = None
        f.text = question
    words = content_words(f.text)
    c = db.conn()
    where, args = f.sql("f") if f.active else ("1=1", [])

    # BM25 on the content words only. "what is the late fee" must not match every "the".
    kw: list[dict] = []
    q = _fts_query(" ".join(words))
    if q:
        kw = [dict(r) for r in c.execute(
            "SELECT c.id cid, c.file_id fid, bm25(chunks_fts, 4.0, 3.0, 2.0, 1.0) s FROM chunks_fts "
            "JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id "
            f"WHERE chunks_fts MATCH ? AND {where} ORDER BY s LIMIT ?",
            [q, *args, TOP],
        )]
    info["timings"]["bm25_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    dense: list[dict] = []
    if f.text and db.has_vec_table():
        t = time.perf_counter()
        try:
            vec = embed.embed_query(f.text)
        except Exception:  # noqa: BLE001
            vec = None
        info["timings"]["embed_ms"] = round((time.perf_counter() - t) * 1000, 1)
        if vec is not None:
            blob = sqlite_vec.serialize_float32(vec)
            if f.active:
                rows = c.execute(
                    "SELECT c.id cid, c.file_id fid, vec_distance_cosine(v.embedding, ?) d FROM chunk_vec v "
                    f"JOIN chunks c ON c.id = v.rowid JOIN files f ON f.id = c.file_id WHERE {where} ORDER BY d LIMIT ?",
                    [blob, *args, TOP],
                ).fetchall()
            else:
                from ..search import vectors

                near = vectors.knn(vec, TOP)
                dist = dict(near)
                ids = [i for i, _ in near]
                rows = sorted(
                    ({**dict(r), "d": dist[r["cid"]]} for r in c.execute(
                        f"SELECT c.id cid, c.file_id fid FROM chunks c WHERE c.id IN ({','.join('?' * len(ids))})", ids)),
                    key=lambda r: r["d"]) if ids else []
            dense = [dict(r) for r in rows if 1 - r["d"] >= MIN_SIM]
            if dense:
                floor = (1 - dense[0]["d"]) * REL_SIM
                dense = [h for h in dense if 1 - h["d"] >= floor]

    # Coverage: the share of content words in each keyword chunk (with its file name).
    texts: dict[int, str] = {}
    ids = list({h["cid"] for h in kw} | {h["cid"] for h in dense})
    if ids:
        for r in c.execute(
            f"SELECT c.id, c.text, f.name FROM chunks c JOIN files f ON f.id=c.file_id WHERE c.id IN ({','.join('?' * len(ids))})", ids
        ):
            texts[r[0]] = (r[2] + " " + r[1]).lower()

    def cover(cid: int) -> float:
        if not words:
            return 0.0
        t = texts.get(cid, "")
        return sum(1 for w in words if re.search(r"\b" + re.escape(w), t)) / len(words)

    fused: dict[int, dict] = {}
    for i, h in enumerate(kw):
        e = fused.setdefault(h["cid"], {"fid": h["fid"], "score": 0.0, "why": "keyword", "sim": None})
        e["score"] += max(cover(h["cid"]), 0.2) / (RRF_K + i + 1)
    for i, h in enumerate(dense):
        e = fused.setdefault(h["cid"], {"fid": h["fid"], "score": 0.0, "why": "meaning", "sim": None})
        e["score"] += 1 / (RRF_K + i + 1)
        e["sim"] = round(1 - h["d"], 3)
        if e["why"] == "keyword":
            e["why"] = "keyword + meaning"

    best_sim = max((1 - h["d"] for h in dense), default=0.0)
    best_cover = max((cover(h["cid"]) for h in kw), default=0.0)
    info["best_sim"] = round(best_sim, 3)
    info["best_cover"] = round(best_cover, 2)
    info["found"] = best_sim >= FOUND_SIM or (best_cover >= FOUND_COVER and len(words) >= 1)

    # A source must be close in meaning or hold half the content words. Weak keyword hits
    # ("the 1998 run" in a log file) only add noise to the prompt.
    ordered = sorted(
        ((cid, e) for cid, e in fused.items() if (e["sim"] or 0) >= MIN_SIM or cover(cid) >= FOUND_COVER),
        key=lambda kv: kv[1]["score"], reverse=True,
    )
    ordered = _rerank(question, words, ordered, texts, info)
    picked: list[tuple[int, dict]] = []
    per_file: dict[int, int] = {}
    for cid, e in ordered:
        if per_file.get(e["fid"], 0) >= MAX_PER_FILE:
            continue
        per_file[e["fid"]] = per_file.get(e["fid"], 0) + 1
        picked.append((cid, e))
        if len(picked) >= k:
            break

    sources = _expand(picked, budget)
    info["timings"]["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    info["candidates"] = len(fused)
    return sources, info


def _rerank(question: str, words: list[str], ordered: list, texts: dict[int, str], info: dict) -> list:
    """RAG step 1 with search step 6: the cross-encoder reorders the top RERANK_TOP chunks.

    The final order joins the reranker order and the fused order with RRF.
    """
    from ..search import rerank
    from ..search.hybrid import RERANK_WEIGHT, _window

    if not rerank.model.available() or len(ordered) < 2:
        return ordered
    head, tail = ordered[:RERANK_TOP], ordered[RERANK_TOP:]
    t = time.perf_counter()
    try:
        scores = rerank.model.scores(question, [_window(texts.get(cid, ""), words, 90) for cid, _ in head])
    except Exception:  # noqa: BLE001
        return ordered
    info["timings"]["rerank_ms"] = round((time.perf_counter() - t) * 1000, 1)
    rank = {i: r + 1 for r, i in enumerate(sorted(range(len(head)), key=lambda i: scores[i], reverse=True))}
    final = {head[i][0]: RERANK_WEIGHT / (RRF_K + rank[i]) + 1 / (RRF_K + i + 1) for i in range(len(head))}
    head.sort(key=lambda kv: final[kv[0]], reverse=True)
    return head + tail


def _expand(picked: list[tuple[int, dict]], budget: int) -> list[Source]:
    """Step 2: grow each chunk to its parent context while the token budget allows.

    The parent context is the chunk before and the chunk after in the same file and section.
    Two picked chunks of one file that touch become one source.
    """
    if not picked:
        return []
    c = db.conn()
    rows = {r["id"]: dict(r) for r in c.execute(
        "SELECT c.id, c.file_id, c.page, c.at, c.header, c.text, f.path, f.name FROM chunks c JOIN files f ON f.id=c.file_id "
        f"WHERE c.id IN ({','.join('?' * len(picked))})",
        [cid for cid, _ in picked],
    )}
    used = sum(_tokens(rows[cid]["text"]) for cid, _ in picked if cid in rows)
    out: list[Source] = []
    for cid, e in picked:
        r = rows.get(cid)
        if not r:
            continue
        heading = _heading(r["header"] or "")
        parts = [(cid, r["text"])]
        if used < budget:
            for nid in (cid - 1, cid + 1):
                nb = c.execute("SELECT id, file_id, page, header, text FROM chunks WHERE id=?", (nid,)).fetchone()
                if not nb or nb["file_id"] != r["file_id"] or _heading(nb["header"] or "") != heading:
                    continue
                if any(nid == s for s in [x for src in out for x in src.chunk_ids]):
                    continue
                cost = _tokens(nb["text"])
                if used + cost > budget:
                    continue
                used += cost
                parts.append((nid, nb["text"]))
        parts.sort()
        text = _join([p[1] for p in parts])
        out.append(Source(
            n=len(out) + 1, chunk_id=cid, file_id=r["file_id"], path=r["path"], name=r["name"], page=r["page"],
            heading=heading, text=text, hit=r["text"], why=e["why"], sim=e["sim"], chunk_ids=[p[0] for p in parts], at=r["at"],
        ))
    return out


def window(text: str, words: list[str], size: int) -> str:
    """The `size` words of `text` with the most content words in them."""
    toks = text.split()
    if len(toks) <= size:
        return text
    hits = [1 if any(t.lower().strip(".,:;()[]").startswith(w) for w in words) else 0 for t in toks]
    best, best_at, cur = -1, 0, sum(hits[:size])
    for i in range(len(toks) - size + 1):
        if i:
            cur += hits[i + size - 1] - hits[i - 1]
        if cur > best:
            best, best_at = cur, i
    return ("… " if best_at else "") + " ".join(toks[best_at:best_at + size]) + (" …" if best_at + size < len(toks) else "")


def _heading(header: str) -> str:
    m = re.search(r"Section: ([^|]+)", header)
    return m.group(1).strip() if m else ""


def _join(texts: list[str]) -> str:
    """Join neighbor chunks. Chunks overlap by about 38 words, so drop the repeated words."""
    out = texts[0]
    for t in texts[1:]:
        tail = out.split()[-60:]
        words = t.split()
        cut = 0
        for n in range(min(60, len(words)), 4, -1):
            if tail[-n:] == words[:n]:
                cut = n
                break
        out = out + " " + " ".join(words[cut:])
    return out
