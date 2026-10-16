"""Benchmark: float KNN against binary KNN + float rescore in sqlite-vec (section 13.12 item 3).

Reports the median query time and recall@10 of binary KNN + rescore against exact float search.
Random synthetic vectors make binary search look bad (their nearest neighbours are almost ties). Use real
embeddings: --vectors and --query-vectors take .npy files (rows = vectors).

Result on 20,000 real embeddinggemma chunks of Python source text, 20 real queries (2026-10-15):
  K=100: 7.4x faster, recall@10 98%   K=200: 4.0x, 99%   K=400: 1.9x, 99%
The rescore must read the float vectors from a plain table: a rowid lookup in the vec0 table costs
about 60 ms for 300 rows, which cancels the gain (search/vectors.py uses a float16 table).

Usage: python scripts/bench_binary.py [--n 200000] [--queries 50] [--candidates 400]
       python scripts/bench_binary.py --vectors real.npy --query-vectors q.npy
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
import time

import numpy as np
import sqlite_vec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--candidates", type=int, default=400)
    ap.add_argument("--vectors", help=".npy file of real embeddings")
    ap.add_argument("--query-vectors", help=".npy file of real query embeddings")
    a = ap.parse_args()
    rng = np.random.default_rng(7)
    if a.vectors:
        X = np.load(a.vectors).astype(np.float32)
        a.n, a.dim = X.shape
    else:
        centers = rng.normal(size=(500, a.dim)).astype(np.float32)
        X = centers[rng.integers(0, 500, a.n)] + 0.9 * rng.normal(size=(a.n, a.dim)).astype(np.float32)
    X /= np.linalg.norm(X, axis=1, keepdims=True)

    path = os.path.join(tempfile.mkdtemp(prefix="raven-bench-"), "b.db")
    c = sqlite3.connect(path)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.execute(f"CREATE VIRTUAL TABLE fv USING vec0(embedding float[{a.dim}] distance_metric=cosine)")
    c.execute(f"CREATE VIRTUAL TABLE bv USING vec0(embedding bit[{a.dim}])")
    t = time.perf_counter()
    for i in range(0, a.n, 5000):
        part = X[i:i + 5000]
        c.executemany("INSERT INTO fv(rowid, embedding) VALUES (?, ?)", [(i + j + 1, v.tobytes()) for j, v in enumerate(part)])
        c.executemany("INSERT INTO bv(rowid, embedding) VALUES (?, vec_quantize_binary(?))", [(i + j + 1, v.tobytes()) for j, v in enumerate(part)])
    c.commit()
    print(f"{a.n} vectors written in {time.perf_counter() - t:.0f} s, db {os.path.getsize(path) / 1e6:.0f} MB")

    if a.query_vectors:
        Q = np.load(a.query_vectors).astype(np.float32)
    else:
        Q = X[rng.integers(0, a.n, a.queries)] + 0.5 * rng.normal(size=(a.queries, a.dim)).astype(np.float32)
    Q /= np.linalg.norm(Q, axis=1, keepdims=True)
    tf, tb, recall = [], [], []
    for q in Q:
        blob = q.astype(np.float32).tobytes()
        t = time.perf_counter()
        exact = [r[0] for r in c.execute("SELECT rowid FROM fv WHERE embedding MATCH ? AND k = 10 ORDER BY distance", (blob,))]
        tf.append(time.perf_counter() - t)
        t = time.perf_counter()
        cand = [r[0] for r in c.execute(
            "SELECT rowid FROM bv WHERE embedding MATCH vec_quantize_binary(?) AND k = ? ORDER BY distance", (blob, a.candidates))]
        got = [r[0] for r in c.execute(
            f"SELECT rowid, vec_distance_cosine(embedding, ?) d FROM fv WHERE rowid IN ({','.join('?' * len(cand))}) ORDER BY d LIMIT 10",
            [blob, *cand])]
        tb.append(time.perf_counter() - t)
        recall.append(len(set(exact) & set(got)) / 10)
    print(f"float KNN:           median {np.median(tf) * 1000:.1f} ms")
    print(f"binary + rescore:    median {np.median(tb) * 1000:.1f} ms  ({np.median(tf) / np.median(tb):.1f}x faster)")
    print(f"recall@10 of binary: {np.mean(recall) * 100:.1f}%  ({a.candidates} candidates)")


if __name__ == "__main__":
    main()
