"""Lite mode check (PLAN2 P2-6.3): quality and budgets on a small computer.

The sidecar runs in a systemd scope with 4 GB of RAM and 2 CPU threads, with no GPU and no Ollama
(RAVEN_OLLAMA_URL points to a closed port). It indexes testdata/home in lite mode, then measures:
  - search quality on testdata/queries.json (MRR and top 3), for each static model
  - sidecar RAM when idle and the peak while it indexes
  - the time of a name search and of a meaning search
  - the time from start to the first /health answer

Usage: core/.venv/bin/python testdata/lite_eval.py [--models potion-retrieval-32m,potion-base-8m] [--no-limits]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CORE = HERE.parent / "core"
ROOT = (HERE / "home").resolve()


def rss(pid: int) -> int:
    """Resident memory of a process and its children, in bytes."""
    total = 0
    pids = [pid]
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
        pids += [int(x) for x in out]
    except OSError:
        pass
    for p in pids:
        try:
            for line in Path(f"/proc/{p}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) * 1024
        except OSError:
            pass
    return total


class Lite:
    def __init__(self, model: str, limits: bool, home: str | None = None) -> None:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.token = secrets.token_hex(8)
        self.data = tempfile.mkdtemp(prefix="raven-lite-")
        env = {**os.environ, "RAVEN_DATA_DIR": self.data, "XDG_DATA_HOME": str(Path(self.data) / "xdg"),
               "RAVEN_OLLAMA_URL": "http://127.0.0.1:9", "CUDA_VISIBLE_DEVICES": "", "MALLOC_ARENA_MAX": "2",
               "RAVEN_MODELS_DIR": str((HERE.parent / "models").resolve()), "RAVEN_STATIC_IDLE": "30",
               **({"HOME": home} if home else {})}
        cmd = [str(CORE / ".venv/bin/python"), "-m", "raven_core", "--port", str(self.port), "--token", self.token]
        if limits and shutil.which("systemd-run"):
            cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=4G", "-p", "AllowedCPUs=0,1"] + cmd
        self.log = open(Path(self.data) / "sidecar.log", "w")
        t0 = time.time()
        self.proc = subprocess.Popen(cmd, cwd=CORE, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        while True:
            try:
                self.get("/health")
                break
            except Exception:  # noqa: BLE001
                if time.time() - t0 > 60:
                    raise SystemExit("the sidecar did not start")
                time.sleep(0.05)
        self.start_s = time.time() - t0
        self.model = model

    def pid(self) -> int:
        # With systemd-run --scope the Popen pid is the Python process itself (scope exec).
        return self.proc.pid

    def get(self, route, **q):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{route}" + ("?" + urllib.parse.urlencode(q) if q else ""),
                                   headers={"Authorization": f"Bearer {self.token}"})
        return json.load(urllib.request.urlopen(r, timeout=120))

    def post(self, route, body):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{route}", json.dumps(body).encode(),
                                   {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(r, timeout=120))

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.data, ignore_errors=True)


def run(model: str, limits: bool, home: str | None = None) -> dict:
    sc = Lite(model, limits, home)
    peak = [0]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], rss(sc.pid()))
            time.sleep(0.5)

    th = threading.Thread(target=sample, daemon=True)
    try:
        sc.post("/settings", {"key": "first_run_done", "value": True})
        sc.post("/settings", {"key": "welcome_done", "value": True})
        sc.post("/lite/mode", {"mode": "lite"})
        sc.post("/settings", {"key": "embed_model", "value": model})
        th.start()
        t = time.time()
        sc.post("/index/roots", {"roots": [str(ROOT)]})
        while time.time() - t < 900:
            s = sc.get("/index/status")
            if s["files"] > 10 and s["text_pending"] == 0 and s["embed_pending"] == 0 and s.get("embed_ready"):
                break
            time.sleep(1)
        index_s = time.time() - t
        stop.set()
        time.sleep(5)
        idle = rss(sc.pid())
        queries = json.loads((HERE / "queries.json").read_text())
        rr, top3, lat = 0.0, 0, []
        for q in queries:
            t1 = time.perf_counter()
            res = sc.get("/search", q=q["query"], limit=10)
            lat.append((time.perf_counter() - t1) * 1000)
            paths = [os.path.relpath(r["path"], ROOT) for r in res["results"]]
            if q["expected"] in paths:
                k = paths.index(q["expected"]) + 1
                rr += 1 / k
                top3 += k <= 3
        t1 = time.perf_counter()
        sc.get("/search", q="lease", limit=10, mode="names")
        name_ms = (time.perf_counter() - t1) * 1000
        lat.sort()
        # The other features, with no Ollama and no GPU (P2-6 exit test).
        works = {}
        works["file list"] = bool(sc.get("/fs/list", path=str(ROOT))["entries"])
        sc.post("/space/scan", {"path": str(ROOT)})
        t2 = time.time()
        while time.time() - t2 < 60 and sc.get("/space/status")["scanning"]:
            time.sleep(0.5)
        works["space scan"] = bool(sc.get("/space/tree", path=str(ROOT), depth=1))
        works["reclaim"] = "rows" in sc.get("/space/reclaim", path=str(ROOT))
        works["organize (rules)"] = "moves" in sc.post("/organize/suggest", {"path": str(ROOT / "Downloads")})
        r = urllib.request.Request(f"http://127.0.0.1:{sc.port}/ask", json.dumps({"question": "What is the late fee in my rent agreement?"}).encode(),
                                   {"Authorization": f"Bearer {sc.token}", "Content-Type": "application/json"})
        answer = "".join(json.loads(x)["t"] for x in urllib.request.urlopen(r, timeout=60) if json.loads(x)["type"] == "token")
        works["ask (passages)"] = "[" in answer and "500" in answer
        print("    ask answer:", answer[:300].replace("\n", " | "))
        works["no ollama"] = sc.get("/lite")["features"]["llm"]["on"] is False
        # The static model leaves memory after RAVEN_STATIC_IDLE (30 s here, 5 minutes in use).
        time.sleep(75)
        idle_unloaded = rss(sc.pid())
        return {"model": model, "works": works, "rss_idle_unloaded_mb": idle_unloaded // 2**20, "mrr": round(rr / len(queries), 3), "top3": f"{top3}/{len(queries)}", "index_s": round(index_s),
                "rss_idle_mb": idle // 2**20, "rss_peak_mb": peak[0] // 2**20, "search_median_ms": round(lat[len(lat) // 2]),
                "search_max_ms": round(lat[-1]), "name_ms": round(name_ms), "start_s": round(sc.start_s, 2),
                "embed_model": sc.get("/lite")["embed_model"]}
    finally:
        stop.set()
        sc.stop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="potion-retrieval-32m,potion-base-8m")
    ap.add_argument("--no-limits", action="store_true")
    ap.add_argument("--home", default=None, help="a HOME for the sidecar (the size of a home changes the folder index)")
    a = ap.parse_args()
    if not ROOT.is_dir():
        raise SystemExit("testdata/home is missing: run testdata/make_testdata.py first")
    rows = [run(m, not a.no_limits, a.home) for m in a.models.split(",")]
    for r in rows:
        print(json.dumps(r))
    budgets = {"rss_idle_mb": 300, "rss_peak_mb": 800, "search_median_ms": 300, "name_ms": 100, "start_s": 3}
    print("\nbudget check (P2-6.3):")
    for r in rows:
        bad = [f"{k} {r[k]} > {v}" for k, v in budgets.items() if r[k] > v]
        print(f"  {r['model']:22} MRR {r['mrr']}  top3 {r['top3']}  " + ("all budgets pass" if not bad else "; ".join(bad)))
        print("    features: " + ", ".join(f"{k} {'ok' if v else 'FAIL'}" for k, v in r["works"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
