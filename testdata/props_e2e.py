"""End-to-end test of granular control (PLAN2 P2-5) through a real sidecar, on a temporary folder.

Usage: core/.venv/bin/python testdata/props_e2e.py
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import run_device_test as rdt  # noqa: E402


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="raven-props-"))
    data = tempfile.mkdtemp(prefix="raven-props-data-")
    d = root / "work"
    (d / "sub").mkdir(parents=True)
    files = [d / f"f{i}.txt" for i in range(5)] + [d / "sub" / f"g{i}.txt" for i in range(5)]
    for f in files:
        f.write_text(f"kestrel lantern note {f.name}\n")
        os.chmod(f, 0o644)
    secret = root / "private"
    secret.mkdir()
    (secret / "plans.txt").write_text("the zebra mandolin plan\n")
    sc = rdt.Sidecar(data)
    ok = []

    def check(what, cond, detail=""):
        ok.append(bool(cond))
        print(f"{'PASS' if cond else 'FAIL'}  {what:46} {detail}")

    def mode(p):
        return stat.S_IMODE(os.stat(p).st_mode)

    try:
        sc.post("/index/roots", {"roots": [str(root)]})
        t = time.time()
        while time.time() - t < 60:
            if sc.get("/index/status")["files"] >= 13:
                break
            time.sleep(0.5)
        r = sc.post("/fs/chmod", {"paths": [str(d)], "mode": "750", "recursive": True, "files_mode": "600"})
        check("chmod 10 files recursively", all(mode(f) == 0o600 for f in files) and mode(d / "sub") == 0o750, f"{r['changed']} changed")
        sc.post("/fs/undo", {})
        check("undo puts every mode back", all(mode(f) == 0o644 for f in files))
        target = d / "f0.txt"
        link = sc.post("/fs/link", {"target": str(target)})["path"]
        check("symbolic link made", os.path.islink(link), os.path.basename(link))
        target.unlink()
        res = sc.get("/search", q="is:broken", limit=20)
        check("is:broken finds it", any(x["path"] == link for x in res["results"]), f"{len(res['results'])} results")
        info = sc.get("/fs/info", path="/etc")
        check("explain /etc", info.get("explain", {}).get("level") == "system", info.get("explain", {}).get("text", "")[:50])
        sc.post("/fs/tags", {"paths": [str(files[1])], "add": ["urgent"]})
        res = sc.get("/search", q="kestrel tag:urgent", limit=20)
        check("tag:urgent filter", [os.path.basename(x["path"]) for x in res["results"]] == ["f1.txt"],
              str([x["name"] for x in res["results"]]))
        res = sc.get("/search", q="zebra mandolin", limit=5)
        check("private file is indexed first", any(x["path"].endswith("plans.txt") for x in res["results"]))
        sc.post("/fs/policies", {"path": str(secret), "no_index": True, "no_cloud": True})
        t = time.time()
        gone = False
        while time.time() - t < 30:
            res = sc.get("/search", q="zebra mandolin", limit=5)
            if not any(x["path"].endswith("plans.txt") for x in res["results"]):
                gone = True
                break
            time.sleep(1)
        check("rule 'not indexed' drops the folder", gone)
        pol = sc.get("/fs/policies")["policies"]
        check("rule listed", any(p["path"] == str(secret) and p["no_cloud"] for p in pol))
        uses = sc.get("/fs/uses", path=str(files[2]))
        check("who uses a free file: nobody", uses["processes"] == [])
    finally:
        sc.stop()
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(ok)}/{len(ok)} checks pass")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
