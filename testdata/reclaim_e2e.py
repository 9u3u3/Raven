"""End-to-end test of Reclaim (PLAN2 P2-4) on a fake home folder. Your real home is never touched.

1. Build a fake home: app caches, developer caches, old trash items, an archive next to its extracted folder,
   a large old file, PNG files that compress better, and duplicate photos.
2. Start a sidecar with HOME set to the fake home, index it, run a Space scan and the duplicate search.
3. Reclaim: check the rows, plan "free N MB", run the plan, check the files, undo, check again.

Usage: core/.venv/bin/python testdata/reclaim_e2e.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import run_device_test as rdt  # noqa: E402

MB = 1024 * 1024


def blob(p: Path, n: int) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(n))
    return p


def build(home: Path, trash: Path) -> dict:
    from PIL import Image

    blob(home / ".cache" / "someapp" / "data.bin", 30 * MB)
    blob(home / ".cache" / "otherapp" / "c" / "x.bin", 12 * MB)
    blob(home / ".cache" / "pip" / "http" / "w.bin", 20 * MB)
    blob(home / ".gradle" / "caches" / "g.bin", 15 * MB)
    td = trash  # the sidecar's XDG_DATA_HOME/Trash (run_device_test.Sidecar sets XDG_DATA_HOME)
    blob(td / "files" / "old_thing.bin", 8 * MB)
    (td / "info").mkdir(parents=True, exist_ok=True)
    (td / "info" / "old_thing.bin.trashinfo").write_text("[Trash Info]\nPath=/x/old_thing.bin\nDeletionDate=2020-01-01T10:00:00\n")
    docs = home / "Documents"
    trip = docs / "trip"
    for i in range(3):
        blob(trip / f"p{i}.bin", 2 * MB)
    with zipfile.ZipFile(docs / "trip.zip", "w", zipfile.ZIP_STORED) as z:
        for i in range(3):
            z.write(trip / f"p{i}.bin", f"trip/p{i}.bin")
    big = blob(docs / "old_backup.iso", 40 * MB)
    os.utime(big, (time.time() - 400 * 86400, time.time() - 400 * 86400))
    pics = home / "Pictures"
    pics.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (1200, 900), "white")
    for x in range(0, 1200, 4):
        for y in range(0, 900, 37):
            im.putpixel((x, y), (20, 20, 20))
    im.save(pics / "diagram.png", "PNG", compress_level=0)
    photo = blob(pics / "IMG_0001.jpg", 3 * MB)
    shutil.copy(photo, pics / "IMG_0001 copy.jpg")
    return {"archive": docs / "trip.zip", "big": big, "png": pics / "diagram.png", "dup": pics / "IMG_0001 copy.jpg",
            "cache": home / ".cache" / "someapp" / "data.bin"}


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="raven-reclaim-home-"))
    data = tempfile.mkdtemp(prefix="raven-reclaim-data-")
    f = build(home, Path(data) / "xdg" / "Trash")
    png_before = f["png"].stat().st_size
    os.environ.update(HOME=str(home), RAVEN_RECLAIM_LARGE=str(20 * MB), RAVEN_RECLAIM_MIN_CACHE=str(MB),
                      RAVEN_RECLAIM_OLD_DAYS="180")
    sc = rdt.Sidecar(data)
    ok = []

    def check(what, cond, detail=""):
        ok.append(bool(cond))
        print(f"{'PASS' if cond else 'FAIL'}  {what:44} {detail}")

    try:
        sc.post("/index/roots", {"roots": [str(home)]})
        t = time.time()
        while time.time() - t < 120:
            s = sc.get("/index/status")
            if s["files"] >= 8 and s["text_pending"] == 0:
                break
            time.sleep(1)
        sc.post("/space/scan", {"path": str(home)})
        sc.post("/space/duplicates", {"path": str(home)})
        t = time.time()
        while time.time() - t < 60:
            if not sc.get("/space/status")["scanning"] and not sc.get("/space/duplicates")["running"]:
                break
            time.sleep(0.5)
        r = sc.get("/space/reclaim", path=str(home))
        rows = {x["id"]: x for x in r["rows"]}
        print("rows:", ", ".join(f"{k} {x['total'] // MB} MB" for k, x in rows.items()), f"· {r['ms']} ms")
        check("app caches row", "caches" in rows and any("someapp" in i["path"] for i in rows["caches"]["items"]))
        check("developer caches row (pip, Gradle)", {i["note"] for i in rows.get("dev-caches", {}).get("items", [])} >= {"pip", "Gradle"})
        check("old trash item is checked", any(i["checked"] for i in rows.get("trash", {}).get("items", [])))
        check("archive next to its folder", [i["path"] for i in rows.get("archive-pairs", {}).get("items", [])] == [str(f["archive"])])
        check("large and old file", [i["path"] for i in rows.get("large-old", {}).get("items", [])] == [str(f["big"])])
        check("lossless optimizer finds the PNG", any(i["path"] == str(f["png"]) for i in rows.get("optimize", {}).get("items", [])))
        check("duplicates as hard links", any(i["path"] == str(f["dup"]) for i in rows.get("hardlinks", {}).get("items", [])))
        check("total is the sum of the rows", r["total"] == sum(x["total"] for x in r["rows"] if not x.get("alternative")))

        p = sc.post("/space/reclaim/plan", {"target": 60 * MB, "id": r["id"]})
        check("plan: 60 MB is enough", p["enough"], f"{p['bytes'] // MB} MB from {list(p['rows'])}")
        check("plan: safe rows only for 60 MB", all(rows[k]["safety"] == "safe" for k in p["rows"]))

        sel = {"caches": [i["path"] for i in rows["caches"]["items"]], "archive-pairs": [str(f["archive"])],
               "optimize": [str(f["png"])], "large-old": [str(f["big"])]}
        if "hardlinks" in rows:
            sel["hardlinks"] = [str(f["dup"])]
        res = sc.post("/space/reclaim/run", {"id": r["id"], "rows": sel})
        check("run: no errors", not res["errors"], "; ".join(res["errors"])[:100])
        check("cache content deleted, folder kept", not f["cache"].exists() and f["cache"].parent.is_dir())
        check("archive in the trash", not f["archive"].exists())
        check("PNG smaller", f["png"].stat().st_size < png_before, f"{png_before // 1024} KB -> {f['png'].stat().st_size // 1024} KB")
        if "hardlinks" in sel:
            check("duplicate is a hard link", os.stat(f["dup"]).st_ino == os.stat(f["dup"].with_name("IMG_0001.jpg")).st_ino)
        sc.post("/fs/undo", {})
        check("undo: archive back", f["archive"].exists())
        check("undo: large file back", f["big"].exists())
        check("undo: PNG back to its old size", f["png"].stat().st_size == png_before)
        if "hardlinks" in sel:
            check("undo: duplicate is its own file again", os.stat(f["dup"]).st_ino != os.stat(f["dup"].with_name("IMG_0001.jpg")).st_ino)
        r2 = sc.get("/space/reclaim", path=str(home))
        check("rescan: the cache row is smaller", sum(x["total"] for x in r2["rows"] if x["id"] == "caches") < rows["caches"]["total"])
        fc = sc.get("/space/forecast", path=str(home))
        check("forecast answers", "used_pct" in fc, str(fc))
    finally:
        sc.stop()
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)
    print(f"\n{sum(ok)}/{len(ok)} checks pass")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
