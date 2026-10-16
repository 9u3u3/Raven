"""Latency of each Raven step against a running sidecar (PLAN.md 5.1 budgets), as medians.

Usage: python scripts/latency.py --port 8799 --token t [--asks 4]
The sidecar must have the test data indexed (testdata/make_testdata.py) for the numbers to mean anything.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request

FIND = ["rent agreement", "invoice for the website design work", "notes on deadlocks and the banker's algorithm",
        "electricity bill for a flat in Pune", "recipe for pav bhaji", "railway journey ticket to Delhi"]
VISUAL = ["a bar chart", "a sunset", "upi payment screenshot", "a checkerboard"]
ASK = ["What is the late fee in my rent agreement?", "When is my phone bill due?", "What is the PNR of my train ticket to Delhi?",
       "How many units of electricity did I use?"]

BUDGET = {"keyword (name + BM25)": 100, "full hybrid + rerank": 500, "folder search": 300, "search by appearance": 300,
          "redaction of a payload": 50, "Ask first token (local)": 1000}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8799")
    ap.add_argument("--token", default="t")
    ap.add_argument("--asks", type=int, default=4)
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    hdr = {"Authorization": f"Bearer {a.token}"}

    def get(path, **q):
        t = time.perf_counter()
        r = json.load(urllib.request.urlopen(urllib.request.Request(base + path + "?" + urllib.parse.urlencode(q), headers=hdr)))
        return r, (time.perf_counter() - t) * 1000

    def post(path, body):
        t = time.perf_counter()
        req = urllib.request.Request(base + path, json.dumps(body).encode(), {**hdr, "Content-Type": "application/json"})
        return urllib.request.urlopen(req), t

    rows: dict[str, list[float]] = {}
    for q in FIND:  # warm
        get("/search", q=q, mode="keyword")
        get("/search", q=q)
    for q in FIND:
        rows.setdefault("keyword (name + BM25)", []).append(get("/search", q=q, mode="keyword")[1])
        r, ms = get("/search", q=q)
        rows.setdefault("full hybrid + rerank", []).append(ms)
        rows.setdefault("  of which rerank", []).append(r["timings"].get("rerank_ms", 0))
        rows.setdefault("folder search", []).append(get("/search/folders", q=q)[1])
    for q in VISUAL:
        rows.setdefault("search by appearance", []).append(get("/visual/search", q=q)[1])
    payload = "Invoice for Rahul Sharma, A/C 50100234567890, rahul.s@gmail.com, PAN ABCDE1234F, phone 9876543210. " * 3
    for _ in range(5):
        r, t = post("/privacy/redact", {"text": payload})
        r.read()
        rows.setdefault("redaction of a payload", []).append((time.perf_counter() - t) * 1000)
    for q in ASK[: a.asks]:
        r, t = post("/ask", {"question": q})
        first = None
        for line in r:
            e = json.loads(line)
            if e["type"] == "token" and first is None:
                first = (time.perf_counter() - t) * 1000
        rows.setdefault("Ask first token (local)", []).append(first or 0)
        rows.setdefault("Ask full answer (local)", []).append((time.perf_counter() - t) * 1000)

    print(f"{'step':28} {'median':>9} {'budget':>9}")
    for k, v in rows.items():
        b = BUDGET.get(k)
        med = statistics.median(v)
        mark = "" if b is None else (" ok" if med <= b else " over")
        print(f"{k:28} {med:8.0f}ms {(str(b) + 'ms') if b else '':>9}{mark}")


if __name__ == "__main__":
    main()
