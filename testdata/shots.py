"""Screenshots of every screen at two window sizes (PLAN2 P2-10.5), with testdata/device as the indexed home.

1. A sidecar indexes testdata/device and scans it for Space, in a temporary data folder.
2. The built app (electron-vite build) opens each screen in a hidden window and saves a PNG (RAVEN_SHOT).

Usage: core/.venv/bin/python testdata/shots.py [--out docs/shots] [--sizes 1280x800,1920x1080] [--screens files,space]
       [--no-build] [--tag before]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
APP = HERE.parent / "app"
sys.path.insert(0, str(HERE))
import run_device_test as rdt  # noqa: E402

SCREENS = ["files", "space", "organize", "privacy", "settings", "ask"]


def prepare(data: str, root: str) -> None:
    sc = rdt.Sidecar(data)
    try:
        sc.post("/settings", {"key": "first_run_done", "value": True})
        sc.post("/index/roots", {"roots": [root]})
        t = time.time()
        while time.time() - t < 300:
            s = sc.get("/index/status")
            if s["files"] > 50 and s["text_pending"] == 0:
                break
            time.sleep(2)
        sc.post("/space/scan", {"path": root})
        t = time.time()
        while time.time() - t < 120 and sc.get("/space/status")["scanning"]:
            time.sleep(1)
    finally:
        sc.stop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parent / "docs" / "shots"))
    ap.add_argument("--sizes", default="1280x800,1920x1080")
    ap.add_argument("--screens", default=",".join(SCREENS))
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--fonts", default="", help="commit,geist,jetbrains,plex,system: one screenshot set per font")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    root = str((HERE / "device").resolve())
    if not a.no_build:
        subprocess.run(["pnpm", "exec", "electron-vite", "build"], cwd=APP, check=True, capture_output=True)
    data = tempfile.mkdtemp(prefix="raven-shots-")
    print(f"preparing {data} …")
    prepare(data, root)
    electron = APP / "node_modules" / ".bin" / "electron"
    env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RENDERER_URL"}
    env.update(RAVEN_DATA_DIR=data, XDG_DATA_HOME=str(Path(data) / "xdg"), RAVEN_SHOT_DELAY="9000")
    made = []
    runs = [(f, z, c) for f in (a.fonts.split(",") if a.fonts else [""]) for z in a.sizes.split(",") for c in a.screens.split(",")]
    for font, size, screen in runs:
        name = f"{screen}-{size}{'-' + font if font else ''}{'-' + a.tag if a.tag else ''}.png"
        env.update(RAVEN_SHOT=str(out / name), RAVEN_SHOT_SIZE=size, RAVEN_SHOT_SCREEN=screen, RAVEN_SHOT_FONT=font,
                   RAVEN_SHOT_PATH=str(Path(root) / "Downloads") if screen == "files" else "")
        r = subprocess.run([str(electron), "."], cwd=APP, env=env, capture_output=True, text=True, timeout=120)
        ok = (out / name).exists()
        made.append(name)
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  {r.stderr[-300:]}"))
    shutil.rmtree(data, ignore_errors=True)
    print(f"{len(made)} screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
