"""Run testdata/queries.json against a running sidecar. Report top-3 hits and latency.

Usage: python scripts/eval_queries.py --port 8799 --token t [--queries ../testdata/queries.json] [--root ../testdata/home]
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--queries", default=os.path.join(os.path.dirname(__file__), "../../testdata/queries.json"))
    ap.add_argument("--root", default=os.path.join(os.path.dirname(__file__), "../../testdata/home"))
    ap.add_argument("--mode", default="hybrid")
    a = ap.parse_args()
    root = os.path.realpath(a.root)
    queries = json.load(open(a.queries))
    ok = 0
    for item in queries:
        url = f"http://127.0.0.1:{a.port}/search?" + urllib.parse.urlencode({"q": item["query"], "mode": a.mode, "limit": 10})
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {a.token}"})
        t0 = time.perf_counter()
        res = json.load(urllib.request.urlopen(req))
        ms = (time.perf_counter() - t0) * 1000
        top = [(os.path.relpath(r["path"], root), r["why"]) for r in res["results"][:3]]
        hit = next((i + 1 for i, (p, _) in enumerate(top) if p == item["expected"]), None)
        ok += hit is not None
        mark = f"#{hit}" if hit else "MISS"
        print(f"{mark:5} {ms:6.0f} ms  {item['query']!r} -> {item['expected']}")
        if not hit:
            for p, why in top:
                print(f"         got {p} ({why})")
    print(f"\n{ok}/{len(queries)} queries put the expected file in the top 3")


if __name__ == "__main__":
    main()
