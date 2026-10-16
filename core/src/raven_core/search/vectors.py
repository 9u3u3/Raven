"""Nearest chunks by meaning: exact float search, or binary search with a rescore (section 13.12 item 3).

Binary mode keeps a 1-bit copy of each chunk vector (`chunk_bin`, sqlite-vec bit[N]) and a float16 copy in
a plain table (`chunk_f16`). A query takes the CANDIDATES nearest binary vectors by Hamming distance, then
rescores them with the float16 copies and keeps the best k.

Measured on 20,000 real embeddinggemma chunks (core/scripts/bench_binary.py has the method):
  200 candidates: recall@10 99%, 4.0x faster than exact float search.
  At 100,000 chunks: 300 candidates, about 3x faster (30 ms against 90 ms), recall@10 98 to 99%.
Below BINARY_MIN chunks the float search is fast enough (under about 45 ms), so "auto" keeps it.
The float16 table costs 1.5 KB per chunk. sqlite-vec's own table is too slow to read by rowid for the
rescore (about 60 ms for 300 rows).
"""

from __future__ import annotations

import logging
import threading

import numpy as np
import sqlite_vec

from .. import db

log = logging.getLogger("raven.vectors")

BINARY_MIN = 50_000
CANDIDATES = 300
_ready = {"bin": None}
_fill_lock = threading.Lock()


def mode() -> str:
    return db.get_setting("binary_vectors", "auto")


def wanted() -> bool:
    m = mode()
    if m == "off":
        return False
    if m == "on":
        return True
    return db.conn().execute("SELECT COUNT(*) FROM chunks WHERE embedded=1").fetchone()[0] >= BINARY_MIN


def tables_exist() -> bool:
    c = db.conn()
    return c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('chunk_bin','chunk_f16')").fetchone()[0] == 2


_cache = {"at": 0.0, "ready": False}


def ready() -> bool:
    """Binary search is on, and the copies cover the float table. Checked at most every 30 s."""
    import time

    now = time.monotonic()
    if now - _cache["at"] < 30:
        return _cache["ready"]
    ok = False
    if wanted() and tables_exist():
        c = db.conn()
        a = c.execute("SELECT COUNT(*) FROM chunk_f16").fetchone()[0]
        b = c.execute("SELECT COUNT(*) FROM chunks WHERE embedded=1").fetchone()[0]
        ok = a >= b * 0.99
    _cache.update(at=now, ready=ok)
    return ok


def forget_cache() -> None:
    _cache["at"] = 0.0


def loop(stop) -> None:
    """Keep the copies in step with the setting: fill them when binary mode is wanted, drop them when it is off."""
    while not stop.is_set():
        try:
            if wanted():
                if fill(stop):
                    forget_cache()
            elif mode() == "off" and tables_exist():
                drop()
                forget_cache()
        except Exception:  # noqa: BLE001
            log.exception("binary vectors")
        stop.wait(60)


def create(dim: int) -> None:
    with db.tx() as c:
        c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_bin USING vec0(embedding bit[{dim}])")
        c.execute("CREATE TABLE IF NOT EXISTS chunk_f16 (id INTEGER PRIMARY KEY, v BLOB)")


def drop() -> None:
    with db.tx() as c:
        c.execute("DROP TABLE IF EXISTS chunk_bin")
        c.execute("DROP TABLE IF EXISTS chunk_f16")


def add(c, rowid: int, vec) -> None:
    """Called inside the embed transaction for each new vector, when the tables exist."""
    a = np.asarray(vec, dtype=np.float32)
    c.execute("DELETE FROM chunk_bin WHERE rowid=?", (rowid,))
    c.execute("INSERT INTO chunk_bin(rowid, embedding) VALUES (?, vec_quantize_binary(?))", (rowid, a.tobytes()))
    c.execute("INSERT OR REPLACE INTO chunk_f16(id, v) VALUES (?, ?)", (rowid, a.astype(np.float16).tobytes()))


def remove(c, rowid: int) -> None:
    c.execute("DELETE FROM chunk_bin WHERE rowid=?", (rowid,))
    c.execute("DELETE FROM chunk_f16 WHERE id=?", (rowid,))


def fill(stop=None) -> int:
    """Build the copies for vectors that have none yet, in batches (background)."""
    if not _fill_lock.acquire(blocking=False):
        return 0
    try:
        dim = db.vec_dim()
        if not dim:
            return 0
        create(dim)
        n = 0
        while stop is None or not stop.is_set():
            rows = db.conn().execute(
                "SELECT rowid, embedding FROM chunk_vec WHERE rowid NOT IN (SELECT id FROM chunk_f16) LIMIT 2000"
            ).fetchall()
            if not rows:
                break
            with db.tx() as c:
                for r in rows:
                    add(c, r[0], np.frombuffer(r[1], dtype=np.float32))
            n += len(rows)
        if n:
            log.info("binary vectors: %d copies written", n)
        return n
    finally:
        _fill_lock.release()


def knn(vec, k: int) -> list[tuple[int, float]]:
    """[(chunk id, cosine distance)], nearest first."""
    q = np.asarray(vec, dtype=np.float32)
    c = db.conn()
    if ready():
        cand = [r[0] for r in c.execute(
            "SELECT rowid FROM chunk_bin WHERE embedding MATCH vec_quantize_binary(?) AND k = ? ORDER BY distance",
            (q.tobytes(), max(CANDIDATES, k * 2)),
        )]
        if cand:
            rows = c.execute(f"SELECT id, v FROM chunk_f16 WHERE id IN ({','.join('?' * len(cand))})", cand).fetchall()
            M = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1).astype(np.float32)
            sims = (M @ q) / (np.linalg.norm(M, axis=1) * (np.linalg.norm(q) or 1) + 1e-9)
            order = np.argsort(-sims)[:k]
            return [(rows[i][0], float(1 - sims[i])) for i in order]
    blob = sqlite_vec.serialize_float32(q)
    return [(r[0], r[1]) for r in c.execute(
        "SELECT rowid, distance FROM chunk_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance", (blob, k))]


def status() -> dict:
    c = db.conn()
    n = c.execute("SELECT COUNT(*) FROM chunks WHERE embedded=1").fetchone()[0]
    copies = c.execute("SELECT COUNT(*) FROM chunk_f16").fetchone()[0] if tables_exist() else 0
    return {"mode": mode(), "chunks": n, "copies": copies, "active": ready(), "threshold": BINARY_MIN}
