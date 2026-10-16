"""Run a file operation as administrator (section 9.13, P2): pkexec on Linux, UAC on Windows.

Raven uses this only after a normal attempt failed with "Permission denied" and the user chose
"Retry as administrator" (Power user mode). The OS asks for the password, never Raven. Each operation
goes to the journal with "elevated" set, so its undo also asks for the password.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import ops

HELPER = Path(__file__).with_name("elevate_helper.py")
OPS = {"rename", "move", "copy", "mkdir", "newfile", "delete", "chmod", "chown", "chattr"}


class ElevateError(ops.OpError):
    pass


def available() -> dict:
    if os.name == "nt":
        return {"ok": True, "how": "UAC"}
    if not shutil.which("pkexec"):
        return {"ok": False, "how": "pkexec", "error": "pkexec is not installed (package polkit)."}
    return {"ok": True, "how": "pkexec"}


def _python() -> str:
    # A packaged Raven is a frozen binary: it runs the helper through its own --elevated mode.
    return sys.executable


def _argv(req: dict) -> list[str]:
    payload = json.dumps(req)
    if getattr(sys, "frozen", False):
        return [_python(), "--elevated", payload]
    return [_python(), str(HELPER), payload]


def run(req: dict) -> None:
    if req.get("op") not in OPS:
        raise ElevateError("Unknown operation")
    argv = _argv(req)
    if os.name == "nt":
        # UAC: PowerShell starts the helper elevated and waits for it.
        ps_args = ",".join("'" + a.replace("'", "''") + "'" for a in argv[1:])
        cmd = ["powershell", "-NoProfile", "-Command",
               f"$p = Start-Process -FilePath '{argv[0]}' -ArgumentList {ps_args} -Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ElevateError("The administrator step failed or was cancelled.", 403)
        return
    r = subprocess.run(["pkexec", *argv], capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        return
    out = (r.stdout or "").strip().splitlines()
    err = (r.stderr or "").strip()
    # No GUI agent: pkexec says "No authentication agent found" or, with no terminal either,
    # "Error creating textual authentication agent".
    if "authentication agent" in err.lower():
        raise ElevateError("No password prompt is running (a polkit agent). Start one, for example hyprpolkitagent on Hyprland, "
                           "polkit-gnome or polkit-kde-agent, then try again.", 424)
    if r.returncode in (126, 127) and not out:
        raise ElevateError("The administrator step was cancelled.", 403)
    try:
        msg = json.loads(out[-1]).get("error") if out else err
    except ValueError:
        msg = err or out[-1]
    raise ElevateError(f"As administrator: {msg}"[:300], 400)


def do(op: str, src: str | None = None, dst: str | None = None, label: str | None = None) -> dict:
    """One elevated operation, written to the journal (one undo, which asks again)."""
    run({"op": op, "src": src, "dst": dst})
    g = ops.Group("admin", label or f"{op.title()} as administrator")
    if op in ("rename", "move"):
        g.add("move", src, dst, {"elevated": True})
    elif op in ("copy", "mkdir", "newfile"):
        g.add("create", src, dst, {"elevated": True})
    else:
        g.add("delete", src, None, {"elevated": True})
    g.commit()
    return {"journal": ops.peek()}
