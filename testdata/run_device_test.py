"""Index testdata/device with a fresh sidecar, time each stage, and run every check of device_manifest.json.

Usage: core/.venv/bin/python testdata/run_device_test.py [--keep] [--timeout 1800]
First: core/.venv/bin/python testdata/make_device.py
The sidecar uses a temporary data folder, so the real index is never touched. Ollama must run (meaning, Ask).
Exit code 0 when every check passes.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CORE = HERE.parent / "core"


class Sidecar:
    def __init__(self, data: str) -> None:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.token = secrets.token_hex(8)
        self.log = open(Path(data) / "sidecar.log", "w")
        env = {**os.environ, "RAVEN_DATA_DIR": data, "XDG_DATA_HOME": str(Path(data) / "xdg")}
        self.proc = subprocess.Popen([str(CORE / ".venv/bin/python"), "-m", "raven_core", "--port", str(self.port), "--token", self.token],
                                     cwd=CORE, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(300):
            try:
                self.get("/health")
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        raise SystemExit("the sidecar did not start; see sidecar.log")

    def _req(self, path, body=None, method=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", json.dumps(body).encode() if body is not None else None,
                                   {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}, method=method)
        return urllib.request.urlopen(r, timeout=600)

    def get(self, route, **q):
        return json.load(self._req(route + ("?" + urllib.parse.urlencode(q) if q else "")))

    def post(self, path, body):
        return json.load(self._req(path, body))

    def ask(self, question):
        text, done = "", {}
        for line in self._req("/ask", {"question": question}):
            e = json.loads(line)
            if e["type"] == "token":
                text += e["t"]
            elif e["type"] == "done":
                done = e
        return text, done

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def wait_index(sc: Sidecar, timeout: int) -> dict[str, float]:
    """Seconds until each stage has no work left."""
    t0 = time.time()
    done: dict[str, float] = {}
    keys = {"names": lambda s: s["stage"] != "metadata" and s["files"] > 0, "text": lambda s: s["text_pending"] == 0,
            "meaning": lambda s: s["embed_pending"] == 0 and s["embed_ready"], "images": lambda s: (s.get("visual_pending") or 0) == 0,
            "speech": lambda s: (s.get("speech_pending") or 0) == 0}
    last = ""
    while time.time() - t0 < timeout:
        s = sc.get("/index/status")
        cls = sc.get("/classify/stats")
        for k, ok in keys.items():
            if k not in done and ok(s) and all(p in done for p in list(keys)[: list(keys).index(k)]):
                done[k] = time.time() - t0
        if "classify" not in done and len(done) == len(keys) and cls["done"] >= cls["total"]:
            done["classify"] = time.time() - t0
        line = (f"\r  {time.time() - t0:5.0f}s  files {s['files']}  text {s['text_pending']}  meaning {s['embed_pending']}  "
                f"images {s.get('visual_pending')}  speech {s.get('speech_pending')}  classified {cls['done']}/{cls['total']}   ")
        if line != last:
            print(line, end="", flush=True)
            last = line
        if "classify" in done:
            break
        time.sleep(2)
    print()
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temporary data folder")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--only", default="", help="run only these check kinds, such as named,not_named,kind")
    ap.add_argument("--sub", default="", help="index only this folder below the device root, such as Downloads")
    a = ap.parse_args()
    man = json.loads((HERE / "device_manifest.json").read_text())
    root = man["root"]
    if a.only:
        man["checks"] = [c for c in man["checks"] if c["kind"] in a.only.split(",")]
    index_root = os.path.join(root, a.sub) if a.sub else root
    data = tempfile.mkdtemp(prefix="raven-device-")
    print(f"data folder: {data}\nindexing {root}")
    sc = Sidecar(data)
    results: list[tuple[str, bool, str]] = []
    try:
        t = time.time()
        sc.post("/index/roots", {"roots": [index_root]})
        stages = wait_index(sc, a.timeout)
        db = sqlite3.connect(f"file:{data}/raven.db?mode=ro", uri=True)
        db.row_factory = sqlite3.Row

        def row(name):
            return db.execute("SELECT * FROM files WHERE path LIKE ? ORDER BY length(path) LIMIT 1", (f"%/{name}",)).fetchone()

        def chunks(fid, at=None):
            q = "SELECT text FROM chunks WHERE file_id=?" + (" AND at IS NOT NULL" if at else "")
            return [r[0] for r in db.execute(q, (fid,))]

        dupes = None
        names_plan, names = None, {}
        for c in man["checks"]:
            k, what = c["kind"], c["what"]
            try:
                if k in ("find", "name"):
                    r = sc.get("/search", q=c["query"], limit=10)
                    names = [x["name"] for x in r["results"]]
                    rank = names.index(c["expect"]) + 1 if c["expect"] in names else None
                    results.append((what, bool(rank and rank <= 5), f"rank {rank}" if rank else f"top: {names[:3]}"))
                elif k == "visual":
                    r = sc.get("/visual/search", q=c["query"], limit=10)
                    hits = [x for x in r["results"] if x["name"] == c["expect"]]
                    rank = [x["name"] for x in r["results"]].index(c["expect"]) + 1 if hits else None
                    ok = bool(rank and rank <= 5) and (hits[0]["at"] or 0) >= c.get("at_min", 0)
                    results.append((what, ok, f"rank {rank}" + (f" at {hits[0]['at']}s" if hits and hits[0]["at"] is not None else "")
                                    if rank else f"top: {[x['name'] for x in r['results'][:3]]}"))
                elif k == "no_ocr":
                    f = row(c["expect"])
                    results.append((what, f is not None and not chunks(f["id"]), "no text chunks" if f else "not indexed"))
                elif k in ("near", "dupe"):
                    if dupes is None:
                        sc.post("/space/duplicates", {"path": root, "min_size": 1})
                        for _ in range(300):
                            dupes = sc.get("/space/duplicates", path=root)
                            if not dupes["running"]:
                                break
                            time.sleep(1)
                    want = set(c["expect"])
                    hit = [g for g in dupes["groups"] if (g.get("kind") == "near") == (k == "near")
                           and want <= {f["path"].rsplit("/", 1)[1] for f in g["files"]}]
                    results.append((what, bool(hit), f"{len(dupes['groups'])} groups"))
                elif k == "archive":
                    bad = []
                    for arc in c["expect"]:
                        f = row(arc)
                        if f is None:
                            bad.append(f"{arc} missing")
                            continue
                        inner = sc.get("/fs/list", path=f["path"] + "/trip_docs/inner")
                        if "tickets.md" not in [e["name"] for e in inner["entries"]]:
                            bad.append(arc)
                    results.append((what, not bad, "all 4 open" if not bad else f"failed: {bad}"))
                elif k == "transcripts":
                    bad = [n for n in c["expect"] if not (row(n) and any(c["word"] in t.lower() for t in chunks(row(n)["id"], at=True)))]
                    results.append((what, not bad, "all have speech" if not bad else f"missing: {bad}"))
                elif k == "no_speech":
                    f = row(c["expect"])
                    results.append((what, f is not None and f["transcript_sig"] is not None and not chunks(f["id"], at=True),
                                    "checked, empty" if f and f["transcript_sig"] else "not transcribed yet"))
                elif k == "names_only":
                    f = row(c["expect"])
                    r = sc.get("/search", q=c["expect"], limit=10)
                    ok = f is not None and f["light"] == 1 and not chunks(f["id"]) and c["expect"] in [x["name"] for x in r["results"]]
                    results.append((what, ok, f"light={f['light'] if f else '-'}, found by name"))
                elif k == "absent":
                    f = db.execute("SELECT path FROM files WHERE path LIKE ?", (f"%/{c['expect']}",)).fetchone()
                    results.append((what, f is None, "not in the index" if f is None else f"listed: {f[0]}"))
                elif k == "category":
                    f = row(c["expect"])
                    results.append((what, f is not None and f["category"] == c["category"], f"category {f['category'] if f else '-'}"))
                elif k == "pii":
                    f = row(c["expect"])
                    results.append((what, f is not None and (f["pii_level"] or 0) >= 1, f"pii_level {f['pii_level'] if f else '-'}"))
                elif k == "ask":
                    text, done = sc.ask(c["question"])
                    ok = c["word"].lower() in text.lower() and "[" in text
                    results.append((what, ok, f"first word {done.get('first_ms')} ms: {text[:70]!r}"))
                elif k in ("named", "not_named"):
                    if names_plan is None:
                        names_plan = sc.post("/name/suggest", {"path": root, "use_llm": True})
                        names = {os.path.basename(i["src"]): i for i in names_plan["items"]}
                    it = names.get(c["expect"])
                    if k == "not_named":
                        results.append((what, it is None, f"suggested {it['name']!r}" if it else "kept"))
                    else:
                        got = it["name"] if it else ""
                        ok = bool(it) and all(w.lower() in got.lower() for w in c["words"])
                        results.append((what, ok, f"{got!r} ({it['source']})" if it else "no name"))
                elif k == "kind":
                    info = sc.get("/fs/info", path=os.path.join(root, c["expect"]))
                    ok = info["kind"] == c["label"] and bool(info.get("kind_mismatch")) == c.get("mismatch", False) \
                        and bool(info.get("kind_warning")) == c.get("warning", False)
                    results.append((what, ok, f"{info['kind']} · content {info.get('sniffed_kind')} · mismatch {info.get('kind_mismatch')}"))
                elif k == "ask_none":
                    text, done = sc.ask(c["question"])
                    results.append((what, bool(done.get("not_found")), text[:60]))
            except Exception as e:  # noqa: BLE001
                results.append((what, False, f"error: {e}"[:120]))
        total = time.time() - t
    finally:
        sc.stop()

    print("\nStage times (seconds from the start of the index):")
    for k in ("names", "text", "meaning", "images", "speech", "classify"):
        print(f"  {k:9} {stages.get(k, float('nan')):7.0f}")
    print(f"\n{'check':42} result  detail")
    for what, ok, detail in results:
        print(f"{what:42} {'PASS' if ok else 'FAIL'}    {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} checks pass · {total:.0f} s in all"
          + (f" · tools missing: {', '.join(man['skipped_tools'])}" if man["skipped_tools"] else ""))
    if not a.keep:
        import shutil

        shutil.rmtree(data, ignore_errors=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
