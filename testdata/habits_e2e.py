"""End-to-end test of personal habits (PLAN2 P2-8.4 exit test) through a real sidecar on testdata/home.

After 20 opens of one file, the file shows in Continue and ranks first for a short query that did not
rank it first before. "Delete what Raven learned" takes the effect away.

Usage: core/.venv/bin/python testdata/habits_e2e.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import run_device_test as rdt  # noqa: E402

ROOT = (HERE / "home").resolve()
QUERIES = ["notes", "report", "budget", "plan", "meeting", "invoice", "list"]


def main() -> int:
    data = tempfile.mkdtemp(prefix="raven-habits-")
    sc = rdt.Sidecar(data)
    ok = []

    def check(what, cond, detail=""):
        ok.append(bool(cond))
        print(f"{'PASS' if cond else 'FAIL'}  {what:48} {detail}")

    def ranked(q):
        return [r["path"] for r in sc.get("/search", q=q, limit=10)["results"] if not r.get("is_dir")]

    try:
        sc.post("/index/roots", {"roots": [str(ROOT)]})
        t = time.time()
        while time.time() - t < 300:
            s = sc.get("/index/status")
            if s["files"] > 10 and s["text_pending"] == 0 and s["embed_pending"] == 0:
                break
            time.sleep(1)
        # A short query whose second or third result is a file: that file is the one the user "likes".
        query, target, before = None, None, []
        for q in QUERIES:
            before = ranked(q)
            if len(before) >= 3:
                query, target = q, before[2]
                break
        check("a short query with 3 results", query is not None, f"{query!r}: {os.path.basename(target or '')} is #3")
        if not query:
            return 1
        for _ in range(20):
            sc.post("/ui/activity", {"kind": "open", "path": target})
        cont = [f["path"] for f in sc.get("/habits/continue")["files"]]
        check("the file shows in Continue", target in cont, f"{len(cont)} files")
        after = ranked(query)
        for r in sc.get("/search", q=query, limit=5)["results"]:
            print(f"      {r.get('score')!s:8} habit {r.get('habit')!s:6} {os.path.basename(r['path'])}")
        check("it ranks first for the query now", after[:1] == [target], os.path.basename(after[0]) if after else "")
        summary = sc.get("/habits/summary")
        check("the summary counts 20 opens", summary["counts"] == {"open": 20}, str(summary["counts"]))
        sc._req("/habits", method="DELETE").read()
        cont = [f["path"] for f in sc.get("/habits/continue")["files"]]
        check("delete: Continue is empty", cont == [])
        again = ranked(query)
        check("delete: the order is back", again[:3] == before[:3], " / ".join(os.path.basename(p) for p in again[:3]))
        sc.post("/settings", {"key": "habits", "value": False})
        counts = sc.get("/habits/summary")["counts"]
        sc.post("/ui/activity", {"kind": "open", "path": target})
        ranked(query)
        check("off: nothing is recorded", sc.get("/habits/summary")["counts"] == counts == {}, str(counts))
    finally:
        sc.stop()
        shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(ok)}/{len(ok)} checks pass")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
