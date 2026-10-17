"""Smoke test of a built package (PLAN4 R5). CI runs it after electron-builder on Linux and Windows.

1. Find the app: on Windows, install the NSIS setup without a window (/S). On Linux, unpack the AppImage.
2. Start the frozen sidecar by itself and call the routes that touch the OS: /health, /about, /device,
   /features, /fs/list, /data, /diagnostics.
3. Start the app. Wait for "sidecar ready" in main.log. Save a screenshot.
4. Stop the app. The sidecar must stop too (the parent watch), and the data folder lock must be free.

Usage: python scripts/smoke_package.py <release dir> <output dir>
Exit code 0 when every check passes. The output dir gets the screenshot, the logs and smoke.txt.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

WIN = os.name == "nt"
TOKEN = "smoke-token"
results: list[str] = []
failed = False


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    results.append(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
    print(results[-1], flush=True)
    failed |= not ok


def call(port: int, route: str, timeout: float = 20) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_health(port: int, proc: subprocess.Popen | None, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            call(port, "/health", 2)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def find_app(release: Path, work: Path) -> tuple[Path, Path]:
    """(the app program, the sidecar program)."""
    if WIN:
        setup = sorted(release.glob("*.exe"))[0]
        subprocess.run([str(setup), "/S"], check=True, timeout=600)
        base = Path(os.environ["LOCALAPPDATA"]) / "Programs"
        apps = [p for p in base.glob("*/Raven.exe")]
        check("NSIS install", bool(apps), str(apps[0].parent) if apps else f"no Raven.exe under {base}")
        app = apps[0]
        return app, app.parent / "resources" / "raven-core" / "raven-core.exe"
    image = sorted(release.glob("*.AppImage"))[0]
    image.chmod(0o755)
    subprocess.run([str(image), "--appimage-extract"], cwd=work, check=True, stdout=subprocess.DEVNULL, timeout=600)
    root = work / "squashfs-root"
    check("AppImage unpack", (root / "AppRun").exists())
    return root / "AppRun", root / "resources" / "raven-core" / "raven-core"


def sidecar_alone(core: Path, data: Path, home: Path) -> None:
    env = {**os.environ, "RAVEN_DATA_DIR": str(data / "alone"), "RAVEN_TOKEN": TOKEN}
    port = 8799
    t0 = time.time()
    proc = subprocess.Popen([str(core), "--port", str(port)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        up = wait_health(port, proc, 120)
        check("frozen sidecar starts", up, f"{time.time() - t0:.1f} s")
        if not up:
            print(proc.stdout.read().decode(errors="replace")[-4000:] if proc.poll() is not None else "(still running)")
            return
        h = call(port, "/health")
        check("release build", h.get("dev") is False, f"dev={h.get('dev')}")
        a = call(port, "/about")
        check("notices and changelog bundled", bool(a["notices"]["python"]) and "## " in a.get("changelog", ""), a.get("version", ""))
        d = call(port, "/device", 60)
        check("device check", (d.get("ram") or {}).get("total", 0) > 0 and bool((d.get("cpu") or {}).get("threads")), d.get("summary", "")[:300])
        f = call(port, "/features", 60)
        check("feature list", len(f.get("features", [])) >= 8, f"{len(f.get('features', []))} features")
        (home / "Docs").mkdir(parents=True, exist_ok=True)
        (home / "Docs" / "note.txt").write_text("hello", encoding="utf-8")
        lst = call(port, "/fs/list?path=" + urllib.request.quote(str(home / "Docs")))
        names = [e["name"] for e in lst.get("entries", [])]
        check("folder list", "note.txt" in names, f"{names} path={lst.get('path')}")
        if WIN:
            check("paths use /", "\\" not in json.dumps(lst.get("path", "")), str(lst.get("path")))
        dd = call(port, "/data")
        check("data folder info", dd.get("total", 0) > 0, f"{dd.get('total')} bytes")
        dg = call(port, "/diagnostics", 60)
        check("diagnostics", "Raven " in dg.get("text", ""))
    finally:
        proc.terminate()
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main_logs() -> list[Path]:
    if WIN:
        pats = [os.path.join(os.environ["APPDATA"], "*", "logs", "main.log")]
    else:
        pats = [os.path.expanduser("~/.config/*/logs/main.log")]
    return [Path(p) for pat in pats for p in glob.glob(pat)]


def sidecar_pids() -> list[int]:
    import psutil

    return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("raven-core")]


def screenshot(out: Path) -> None:
    try:
        from PIL import ImageGrab

        ImageGrab.grab().save(out)
        check("screenshot", out.exists(), str(out))
    except Exception as e:  # noqa: BLE001
        check("screenshot", False, repr(e))


def app_run(app: Path, data: Path, out: Path) -> None:
    # main.log is shared with a normal Raven on this computer: read only what this run adds.
    start = {p: p.stat().st_size for p in main_logs()}

    def new_text(p: Path) -> str:
        with open(p, "rb") as f:
            f.seek(start.get(p, 0))
            return f.read().decode("utf-8", errors="replace")

    env = {**os.environ, "RAVEN_DATA_DIR": str(data / "app")}
    args = [str(app)] + ([] if WIN else ["--no-sandbox"])
    proc = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ready = False
    end = time.time() + 150
    while time.time() < end and not ready:
        time.sleep(2)
        ready = any("sidecar ready" in new_text(p) for p in main_logs())
    check("app starts its sidecar", ready, ", ".join(str(p) for p in main_logs()) or "no main.log")
    time.sleep(8)  # the first paint
    screenshot(out / "app.png")
    pids = sidecar_pids()
    check("sidecar process runs", bool(pids), str(pids))
    # Stop the app the hard way: the sidecar must see that its parent is gone (__main__._watch_parent).
    import psutil

    try:
        parent = psutil.Process(proc.pid)
        kids = parent.children(recursive=True)
        electron = [k for k in kids if not (k.name() or "").lower().startswith("raven-core")]
        for k in [parent, *electron]:
            try:
                k.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    proc.wait(20)  # reap it: a zombie still answers os.kill(pid, 0)
    end = time.time() + 20
    while time.time() < end and sidecar_pids():
        time.sleep(1)
    left = sidecar_pids()
    check("sidecar stops after the app dies", not left, str(left))
    for p in main_logs():
        (out / f"main-{p.parent.parent.name}.log").write_text(new_text(p), encoding="utf-8")
    for p in (data / "app").glob("logs/*.log"):
        shutil.copy(p, out / p.name)
    if proc.stdout:
        try:
            (out / "app-stdout.txt").write_bytes(proc.stdout.read() if proc.poll() is not None else b"")
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    release, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="raven-smoke-"))
    data, home = work / "data", work / "home"
    app, core = find_app(release, work)
    check("sidecar in the package", core.exists(), str(core))
    if core.exists():
        sidecar_alone(core, data, home)
    app_run(app, data, out)
    (out / "smoke.txt").write_text("\n".join(results) + "\n", encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
