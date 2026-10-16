"""Elevated file operations (section 9.13, P2). This file runs as root (pkexec) or as Administrator (UAC).

It imports nothing from Raven and does one operation per call, from one JSON argument:
  {"op": "rename"|"move"|"copy"|"mkdir"|"newfile"|"delete"|"chmod"|"chown"|"chattr", "src": ..., "dst": ...}
Every path must be absolute. The helper refuses the file system root and the system folders that a file
manager never needs to change. It prints one JSON line with the result.
"""

import json
import os
import shutil
import sys

# Changing these folders breaks the system. A user who needs it uses a terminal.
DENY = {"/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run", "/sbin", "/sys", "/usr", "/var",
        "C:\\", "C:\\Windows", "C:\\Windows\\System32"}


def _check(p):
    if not p or not os.path.isabs(p):
        raise ValueError(f"not an absolute path: {p!r}")
    real = os.path.realpath(p)
    if real in DENY or os.path.dirname(real) == real:
        raise ValueError(f"Raven does not change {real} as administrator")
    return p


def main():
    req = json.loads(sys.argv[1])
    op = req["op"]
    src = _check(req.get("src")) if req.get("src") else None
    dst = _check(req.get("dst")) if req.get("dst") else None
    if op == "rename" or op == "move":
        if os.path.lexists(dst):
            raise FileExistsError(dst)
        shutil.move(src, dst)
    elif op == "copy":
        if os.path.lexists(dst):
            raise FileExistsError(dst)
        if os.path.isdir(src) and not os.path.islink(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
    elif op == "mkdir":
        os.mkdir(dst)
    elif op == "newfile":
        with open(dst, "x"):
            pass
    elif op == "chmod":
        os.chmod(src, int(req["mode"]))
    elif op == "chown":
        import grp
        import pwd

        uid = pwd.getpwnam(req["owner"]).pw_uid if req.get("owner") else -1
        gid = grp.getgrnam(req["group"]).gr_gid if req.get("group") else -1
        os.chown(src, uid, gid, follow_symlinks=False)
    elif op == "chattr":
        import subprocess

        flags = req["flags"]
        if flags not in ("+i", "-i", "+a", "-a"):
            raise ValueError(f"unknown flags {flags}")
        subprocess.run(["chattr", flags, src], check=True, capture_output=True)
    elif op == "delete":
        if os.path.isdir(src) and not os.path.islink(src):
            shutil.rmtree(src)
        else:
            os.unlink(src)
    else:
        raise ValueError(f"unknown op {op}")
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - the parent reads the message
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)
