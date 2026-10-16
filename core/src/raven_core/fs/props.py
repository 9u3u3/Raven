"""Granular control (PLAN2 P2-5): permissions, owners, dates, tags, attributes, links, busy files, folder
explanations, folder rules, drives and secure delete.

Every change goes through the journal: an "attr" row keeps the old and the new value, so one undo puts
the old values back. Changes that need an administrator go through fs/elevate.py (pkexec or UAC).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

from .. import db
from . import ops

TAG_XATTR = "user.xdg.tags"
MAX_ITEMS = 20_000  # a recursive change touches at most this many items


# ---------------------------------------------------------------- walking


def _targets(paths: list[str], recursive: bool, dirs: bool = True, files: bool = True) -> list[str]:
    out: list[str] = []
    for p in paths:
        if not os.path.lexists(p):
            raise ops.OpError(f"Not found: {p}", 404)
        out.append(p)
        if recursive and os.path.isdir(p) and not os.path.islink(p):
            for root, ds, fs in os.walk(p):
                if dirs:
                    out += [os.path.join(root, d) for d in ds]
                if files:
                    out += [os.path.join(root, f) for f in fs]
                if len(out) > MAX_ITEMS:
                    raise ops.OpError(f"More than {MAX_ITEMS} items. Use a terminal for a change this large.", 413)
    return out


# ---------------------------------------------------------------- the attr journal op


def apply_attr(path: str, what: str, value, elevated: bool = False) -> None:
    """Set one attribute. ops.undo and ops.redo call this with the old or the new value."""
    if elevated:
        from . import elevate

        if what == "mode":
            elevate.run({"op": "chmod", "src": path, "mode": value})
        elif what == "owner":
            elevate.run({"op": "chown", "src": path, "owner": value[0], "group": value[1]})
        elif what == "flags":
            elevate.run({"op": "chattr", "src": path, "flags": value})
        else:
            raise ops.OpError(f"{what} cannot change as administrator")
        return
    if what == "mode":
        if os.chmod in os.supports_follow_symlinks:
            os.chmod(path, value, follow_symlinks=False)
        else:
            os.chmod(path, value)  # Linux has no lchmod: set_mode skips links
    elif what == "times":
        os.utime(path, ns=(value[0], value[1]), follow_symlinks=False)
    elif what == "owner":
        import grp
        import pwd

        uid = pwd.getpwnam(value[0]).pw_uid if value[0] else -1
        gid = grp.getgrnam(value[1]).gr_gid if value[1] else -1
        os.chown(path, uid, gid, follow_symlinks=False)
    elif what == "tags":
        _write_tags(path, value)
    elif what == "flags":
        r = subprocess.run(["chattr", value, path], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ops.OpError((r.stderr or "chattr failed").strip()[:200], 403)
    else:
        raise ops.OpError(f"Unknown attribute {what}")


def _record(g: ops.Group, path: str, what: str, old, new, elevated: bool = False) -> None:
    g.add("attr", path, path, {"what": what, "old": old, "new": new, **({"elevated": True} if elevated else {})})


# ---------------------------------------------------------------- permissions


def parse_mode(mode: str | int, current: int) -> int:
    """'755', '0o644', 'u+x', 'go-w', 'a=r' or an int. Returns the new permission bits."""
    if isinstance(mode, int):
        return mode & 0o7777
    m = mode.strip()
    if re.fullmatch(r"(0o)?[0-7]{3,4}", m):
        return int(m.replace("0o", ""), 8)
    bits = current & 0o7777
    for part in m.split(","):
        mm = re.fullmatch(r"([ugoa]*)([+=-])([rwxXst]*)", part.strip())
        if not mm:
            raise ops.OpError(f"Not a permission: {mode!r}. Use 755 or u+x.")
        who, op, what = mm.groups()
        who = who or "a"
        mask = 0
        for w in who:
            for ch in what:
                mask |= {
                    ("u", "r"): 0o400, ("u", "w"): 0o200, ("u", "x"): 0o100, ("g", "r"): 0o40, ("g", "w"): 0o20,
                    ("g", "x"): 0o10, ("o", "r"): 0o4, ("o", "w"): 0o2, ("o", "x"): 0o1,
                    ("u", "s"): 0o4000, ("g", "s"): 0o2000, ("o", "t"): 0o1000, ("a", "t"): 0o1000,
                }.get((w, ch), 0) if w != "a" else {"r": 0o444, "w": 0o222, "x": 0o111, "t": 0o1000}.get(ch, 0)
                if ch == "X" and (stat.S_ISDIR(current) or current & 0o111):
                    mask |= {"u": 0o100, "g": 0o10, "o": 0o1, "a": 0o111}[w]
        if op == "+":
            bits |= mask
        elif op == "-":
            bits &= ~mask
        else:
            clear = {"u": 0o4700, "g": 0o2070, "o": 0o1007, "a": 0o7777}
            for w in who:
                bits &= ~clear[w]
            bits |= mask
    return bits


def set_mode(paths: list[str], mode: str | int, recursive: bool = False, files_mode: str | int | None = None,
             elevated: bool = False) -> dict:
    """chmod with undo. `files_mode`: another mode for files in a recursive change (folders keep `mode`)."""
    targets = _targets(paths, recursive)
    g = ops.Group("manual", f"Permissions of {len(paths)} item{'s' if len(paths) != 1 else ''}")
    n = 0
    try:
        for p in targets:
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                continue  # Linux has no permissions on a link itself
            want = files_mode if (files_mode is not None and not stat.S_ISDIR(st.st_mode)) else mode
            new = parse_mode(want, st.st_mode)
            old = st.st_mode & 0o7777
            if new == old:
                continue
            try:
                apply_attr(p, "mode", new, elevated)
            except PermissionError as e:
                raise ops.OpError(f"Permission denied: {p}", 403, code="needs_admin") from e
            _record(g, p, "mode", old, new, elevated)
            n += 1
    finally:
        g.commit()
    return {"changed": n, "journal": ops.peek(), "label": g.label}


def set_owner(paths: list[str], owner: str, group: str, recursive: bool = False) -> dict:
    """chown. Only root can give a file away, so this always runs as administrator."""
    import grp
    import pwd

    if owner:
        pwd.getpwnam(owner)
    if group:
        grp.getgrnam(group)
    targets = _targets(paths, recursive)
    g = ops.Group("manual", f"Owner of {len(paths)} item{'s' if len(paths) != 1 else ''}")
    n = 0
    try:
        for p in targets:
            st = os.lstat(p)
            from ..platform import owner_group

            old = list(owner_group(st))
            new = [owner or old[0], group or old[1]]
            if new == old:
                continue
            elevated = os.geteuid() != 0
            apply_attr(p, "owner", new, elevated=elevated)
            _record(g, p, "owner", old, new, elevated)
            n += 1
    finally:
        g.commit()
    return {"changed": n, "journal": ops.peek(), "label": g.label}


def set_times(paths: list[str], mtime: float, recursive: bool = False) -> dict:
    targets = _targets(paths, recursive)
    g = ops.Group("manual", f"Date of {len(paths)} item{'s' if len(paths) != 1 else ''}")
    new_ns = int(mtime * 1e9)
    try:
        for p in targets:
            st = os.lstat(p)
            old = [st.st_atime_ns, st.st_mtime_ns]
            new = [st.st_atime_ns, new_ns]
            apply_attr(p, "times", new)
            _record(g, p, "times", old, new)
    finally:
        g.commit()
    return {"changed": len(targets), "journal": ops.peek(), "label": g.label}


# ---------------------------------------------------------------- attributes (chattr, Windows attrib)


def get_flags(path: str) -> str:
    if os.name == "nt":
        r = subprocess.run(["attrib", path], capture_output=True, text=True, timeout=10)
        return r.stdout[:12].replace(" ", "")
    if not shutil.which("lsattr") or os.path.islink(path):
        return ""
    r = subprocess.run(["lsattr", "-d", path], capture_output=True, text=True, timeout=10)
    return r.stdout.split(" ", 1)[0].replace("-", "") if r.returncode == 0 else ""


def set_flag(path: str, flag: str, on: bool) -> dict:
    """i (immutable) or a (append only). Linux needs root for both."""
    if flag not in ("i", "a"):
        raise ops.OpError("The attribute must be i (immutable) or a (append only)")
    if os.name == "nt":
        raise ops.OpError("Use the hidden and read-only attributes on Windows")
    g = ops.Group("manual", f"{'Lock' if flag == 'i' and on else 'Unlock' if flag == 'i' else 'Append only'} {os.path.basename(path)}")
    new, old = f"{'+' if on else '-'}{flag}", f"{'-' if on else '+'}{flag}"
    try:
        apply_attr(path, "flags", new, elevated=os.geteuid() != 0)
        _record(g, path, "flags", old, new, elevated=os.geteuid() != 0)
    finally:
        g.commit()
    return {"flags": get_flags(path), "journal": ops.peek(), "label": g.label}


# ---------------------------------------------------------------- tags (xdg extended attribute, or the database)


def _tags_table() -> None:
    db.conn().execute("CREATE TABLE IF NOT EXISTS tags (path TEXT PRIMARY KEY, tags TEXT)")


def read_tags(path: str) -> list[str]:
    try:
        raw = os.getxattr(path, TAG_XATTR, follow_symlinks=False).decode()
        return [t for t in raw.split(",") if t]
    except (OSError, AttributeError):
        pass
    _tags_table()
    row = db.conn().execute("SELECT tags FROM tags WHERE path=?", (path,)).fetchone()
    return json.loads(row[0]) if row else []


def _write_tags(path: str, tags: list[str]) -> None:
    tags = sorted({t.strip() for t in tags if t.strip()})
    try:
        if tags:
            os.setxattr(path, TAG_XATTR, ",".join(tags).encode(), follow_symlinks=False)
        else:
            try:
                os.removexattr(path, TAG_XATTR, follow_symlinks=False)
            except OSError:
                pass
        stored = True
    except (OSError, AttributeError):
        stored = False  # no extended attributes here (FAT, some shares, Windows): keep them in the database
    _tags_table()
    with db.tx() as c:
        if tags and not stored:
            c.execute("INSERT OR REPLACE INTO tags VALUES (?, ?)", (path, json.dumps(tags)))
        else:
            c.execute("DELETE FROM tags WHERE path=?", (path,))


def set_tags(paths: list[str], add: list[str] | None = None, remove: list[str] | None = None) -> dict:
    g = ops.Group("manual", "Tags")
    try:
        for p in paths:
            old = read_tags(p)
            new = sorted((set(old) | set(add or [])) - set(remove or []))
            if new == sorted(old):
                continue
            _write_tags(p, new)
            _record(g, p, "tags", old, new)
    finally:
        g.commit()
    return {"tags": {p: read_tags(p) for p in paths}, "journal": ops.peek()}


def all_tags() -> list[str]:
    _tags_table()
    out = set()
    for (t,) in db.conn().execute("SELECT tags FROM tags"):
        out |= set(json.loads(t))
    return sorted(out)


# ---------------------------------------------------------------- links


def make_link(target: str, link: str | None = None, kind: str = "symbolic") -> dict:
    target = os.path.abspath(target)
    if not os.path.lexists(target):
        raise ops.OpError(f"Not found: {target}", 404)
    if link is None:
        stem, ext = os.path.splitext(os.path.basename(target))
        link = os.path.join(os.path.dirname(target), f"{stem} link{ext}" if not os.path.isdir(target) else f"{stem} link")
    link = ops.unique_path(os.path.abspath(link))
    if kind == "hard":
        if os.path.isdir(target):
            raise ops.OpError("A hard link must point to a file, not a folder")
        os.link(target, link)
    elif kind == "symbolic":
        os.symlink(target, link, target_is_directory=os.path.isdir(target))
    else:
        raise ops.OpError("The link kind must be symbolic or hard")
    g = ops.Group("manual", f"{'Hard' if kind == 'hard' else 'Symbolic'} link to {os.path.basename(target)}")
    g.add("create", None, link)
    g.commit()
    return {"path": link, "journal": ops.peek(), "label": g.label}


def broken_links(folder: str, recursive: bool = True, limit: int = 2000) -> list[dict]:
    out = []
    stack = [folder]
    while stack and len(out) < limit:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                if e.is_symlink():
                    t = os.readlink(e.path)
                    full = t if os.path.isabs(t) else os.path.join(d, t)
                    if not os.path.exists(full):
                        out.append({"path": e.path, "target": t})
                elif recursive and e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
    return out


def link_count(path: str) -> int:
    try:
        return os.lstat(path).st_nlink
    except OSError:
        return 0


# ---------------------------------------------------------------- what uses a file


def who_uses(path: str) -> list[dict]:
    """Programs that hold `path` (or a file below it) open: [{pid, name, user}]."""
    out = []
    if os.name == "nt":
        return out  # the Restart Manager answer needs a native call; the error message names the file
    if shutil.which("lsof"):
        args = ["lsof", "-F", "pcL", "-w"] + (["+D", path] if os.path.isdir(path) else [path])
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return out
        cur: dict = {}
        for line in r.stdout.splitlines():
            if not line:
                continue
            k, v = line[0], line[1:]
            if k == "p":
                if cur:
                    out.append(cur)
                cur = {"pid": int(v)}
            elif k == "c":
                cur["name"] = v
            elif k == "L":
                cur["user"] = v
        if cur:
            out.append(cur)
    elif shutil.which("fuser"):
        r = subprocess.run(["fuser", path], capture_output=True, text=True, timeout=20)
        for pid in re.findall(r"\d+", r.stdout):
            try:
                name = Path(f"/proc/{pid}/comm").read_text().strip()
            except OSError:
                name = "?"
            out.append({"pid": int(pid), "name": name})
    return out


# ---------------------------------------------------------------- explain a folder

EXPLAIN = {
    "/": ("The top of the file system. Every disk and folder hangs below it.", "system"),
    "/bin": ("Programs for everyone. Managed by the package manager.", "system"),
    "/sbin": ("Programs for the administrator. Managed by the package manager.", "system"),
    "/boot": ("The kernel and the boot loader. A wrong change stops the computer from starting.", "danger"),
    "/dev": ("Devices as files (disks, terminals). Made by the kernel, not real files.", "system"),
    "/etc": ("System settings. Changes need an administrator. Keep a copy before you edit.", "system"),
    "/home": ("The home folders of the users.", "user"),
    "/lib": ("Shared code for programs. Managed by the package manager.", "system"),
    "/lib64": ("Shared 64-bit code for programs. Managed by the package manager.", "system"),
    "/media": ("Removable drives (USB, SD cards) mount here.", "user"),
    "/mnt": ("A place for drives that you mount by hand.", "user"),
    "/opt": ("Programs that came outside the package manager.", "system"),
    "/proc": ("Live data about running programs. Made by the kernel, not real files.", "system"),
    "/root": ("The administrator's home folder.", "system"),
    "/run": ("Data for the running system. Gone after a restart. Network shares (gvfs) mount below /run/user.", "system"),
    "/srv": ("Data that this computer serves (web, FTP).", "system"),
    "/sys": ("Live data about hardware. Made by the kernel, not real files.", "system"),
    "/tmp": ("Temporary files. Often gone after a restart.", "safe"),
    "/usr": ("Installed programs and their data. Managed by the package manager.", "system"),
    "/usr/share": ("Data that installed programs share: icons, fonts, docs.", "system"),
    "/usr/local": ("Programs that you built or installed by hand.", "system"),
    "/var": ("Data that changes: logs, caches, databases, mail.", "system"),
    "/var/log": ("System logs. Raven Space can shrink the journal safely.", "system"),
    "/var/cache": ("System caches. The package cache can be cleaned in Space.", "safe"),
    "/var/lib": ("State of system services and package managers. Do not change.", "danger"),
    "/var/tmp": ("Temporary files that survive a restart.", "safe"),
    "~/.cache": ("App caches. Apps make them again. Space can clean them.", "safe"),
    "~/.config": ("Settings of your apps. Deleting a folder resets that app.", "user"),
    "~/.local/share": ("Data of your apps: saved games, mail, browser profiles. Keep it.", "user"),
    "~/.local/share/Trash": ("Your trash.", "safe"),
    "~/.local/bin": ("Programs that you installed for your user only.", "user"),
    "~/.ssh": ("Your SSH keys. Never share. Raven never indexes this folder.", "danger"),
    "~/.gnupg": ("Your GPG keys. Never share. Raven never indexes this folder.", "danger"),
    "~/.mozilla": ("Firefox profiles: bookmarks, passwords, history.", "user"),
    "~/.npm": ("The npm cache. Safe to clean.", "safe"),
    "~/.cargo": ("Rust tools and the crate cache.", "user"),
    "~/.rustup": ("Rust compilers.", "user"),
    "~/node_modules": ("JavaScript packages. npm installs them again.", "safe"),
    "C:\\Windows": ("Windows itself. Do not change.", "danger"),
    "C:\\Program Files": ("Installed programs. Use the uninstaller.", "system"),
    "C:\\Program Files (x86)": ("Installed 32-bit programs. Use the uninstaller.", "system"),
    "~\\AppData": ("Settings and data of your apps. Local holds caches, Roaming holds settings.", "user"),
    "~/Library": ("macOS app data and settings.", "user"),
}
NAME_EXPLAIN = {
    "node_modules": ("JavaScript packages for this project. npm installs them again.", "safe"),
    ".git": ("The history of this project (git). Deleting it deletes the history.", "danger"),
    ".venv": ("A Python environment. uv or pip makes it again.", "safe"),
    "venv": ("A Python environment. uv or pip makes it again.", "safe"),
    "__pycache__": ("Compiled Python files. Python makes them again.", "safe"),
    "target": ("Build output. The build tool makes it again.", "safe"),
    "build": ("Build output. The build tool makes it again.", "safe"),
    "dist": ("Build output. The build tool makes it again.", "safe"),
    ".Trash-1000": ("The trash of this drive.", "safe"),
    "lost+found": ("Pieces that a disk check rescued. Usually empty.", "system"),
    "Steam": ("Steam games and their data.", "user"),
}


def explain(path: str) -> dict | None:
    """One line about what a folder is, and a level: safe, user, system or danger. None for an ordinary folder."""
    p = os.path.abspath(path).rstrip("/") or "/"
    home = str(Path.home())
    key = "~" + p[len(home):] if p.startswith(home + os.sep) or p == home else p
    if key in EXPLAIN:
        text, level = EXPLAIN[key]
        return {"text": text, "level": level, "source": "table"}
    if p in EXPLAIN:
        text, level = EXPLAIN[p]
        return {"text": text, "level": level, "source": "table"}
    name = os.path.basename(p)
    if name in NAME_EXPLAIN:
        text, level = NAME_EXPLAIN[name]
        return {"text": text, "level": level, "source": "table"}
    # Below a system folder: say whose it is.
    for top in ("/usr", "/etc", "/var", "/opt", "/boot", "/lib", "/bin", "/sbin"):
        if p.startswith(top + "/"):
            text, level = EXPLAIN.get(top, ("A system folder.", "system"))
            return {"text": f"Inside {top}: {text}", "level": level, "source": "table"}
    if key.startswith("~/.cache/"):
        return {"text": "An app cache. The app makes it again.", "level": "safe", "source": "table"}
    if key.startswith("~/.config/"):
        return {"text": f"Settings of {name}. Deleting it resets that app.", "level": "user", "source": "table"}
    return None


def is_system(path: str) -> bool:
    e = explain(path)
    return bool(e and e["level"] in ("system", "danger"))


# ---------------------------------------------------------------- folder rules (policies)

# full_index (PLAN3 P0-7): the indexer reads every file of the code projects in this folder. Without it, a code
# project is one row in the index.
POLICIES = ("no_index", "no_cloud", "protect", "no_compress", "full_index")


def _policy_table() -> None:
    db.conn().execute(
        "CREATE TABLE IF NOT EXISTS folder_policy (path TEXT PRIMARY KEY, no_index INTEGER DEFAULT 0, "
        "no_cloud INTEGER DEFAULT 0, protect INTEGER DEFAULT 0, no_compress INTEGER DEFAULT 0)")
    cols = {r[1] for r in db.conn().execute("PRAGMA table_info(folder_policy)")}
    for name in POLICIES:
        if name not in cols:
            db.conn().execute(f"ALTER TABLE folder_policy ADD COLUMN {name} INTEGER DEFAULT 0")


_policy_cache: dict = {"ts": 0.0, "rows": []}


def policies() -> list[dict]:
    _policy_table()
    return [dict(r) for r in db.conn().execute("SELECT * FROM folder_policy ORDER BY path")]


def set_policy(path: str, **flags) -> dict:
    _policy_table()
    path = os.path.abspath(os.path.expanduser(path))
    bad = set(flags) - set(POLICIES)
    if bad:
        raise ops.OpError(f"Unknown rule: {', '.join(sorted(bad))}")
    cur = {k: 0 for k in POLICIES}
    row = db.conn().execute("SELECT * FROM folder_policy WHERE path=?", (path,)).fetchone()
    if row:
        cur.update({k: row[k] for k in POLICIES})
    cur.update({k: int(bool(v)) for k, v in flags.items()})
    with db.tx() as c:
        if any(cur.values()):
            c.execute(f"INSERT OR REPLACE INTO folder_policy (path, {', '.join(POLICIES)}) VALUES (?{', ?' * len(POLICIES)})",
                      (path, *[cur[k] for k in POLICIES]))
        else:
            c.execute("DELETE FROM folder_policy WHERE path=?", (path,))
    _policy_cache["ts"] = 0.0
    return {"path": path, **cur}


def policy_for(path: str, flag: str) -> bool:
    """True when `path` or a folder above it has the rule `flag`. Cached for 5 s: the indexer asks often."""
    now = time.monotonic()
    if now - _policy_cache["ts"] > 5:
        try:
            _policy_cache["rows"] = policies()
        except Exception:  # noqa: BLE001 - a locked or missing database means no rules
            _policy_cache["rows"] = []
        _policy_cache["ts"] = now
    p = os.path.abspath(path)
    for r in _policy_cache["rows"]:
        if r[flag] and (p == r["path"] or p.startswith(r["path"].rstrip("/") + "/")):
            return True
    return False


# ---------------------------------------------------------------- drives


def drives() -> list[dict]:
    """Block devices with their partitions (lsblk), with the mount state, inodes and mount options."""
    if not shutil.which("lsblk"):
        return []
    r = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,LABEL,SIZE,FSTYPE,MOUNTPOINT,RM,HOTPLUG,TYPE,MODEL"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return []
    opts = {}
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            f = line.split()
            if len(f) >= 4:
                opts[f[1].replace("\\040", " ")] = f[3]
    except OSError:
        pass
    out = []

    def walk(devs, parent=None):
        for d in devs:
            if d.get("type") in ("part", "disk", "crypt", "lvm") and d.get("fstype"):
                item = {"name": d["name"], "path": d["path"], "label": d.get("label") or "", "size": int(d.get("size") or 0),
                        "fstype": d.get("fstype"), "mount": d.get("mountpoint"), "removable": bool(d.get("rm") or d.get("hotplug")),
                        "model": (parent or {}).get("model") or d.get("model") or "", "disk": (parent or d).get("path")}
                mnt = d.get("mountpoint")
                if mnt and mnt != "[SWAP]":
                    try:
                        sv = os.statvfs(mnt)
                        item.update(inodes=sv.f_files, inodes_free=sv.f_ffree, options=opts.get(mnt, ""))
                    except OSError:
                        pass
                out.append(item)
            walk(d.get("children") or [], d if d.get("type") == "disk" else parent)

    walk(json.loads(r.stdout).get("blockdevices", []))
    return out


def drive_action(device: str, action: str) -> dict:
    """mount, unmount or eject (power off) a drive with udisksctl. udisks asks polkit when it needs to."""
    if not re.fullmatch(r"/dev/[\w/.-]+", device):
        raise ops.OpError("Not a device path")
    if not shutil.which("udisksctl"):
        raise ops.OpError("udisksctl is not installed (package udisks2)", 424)
    if action == "mount":
        args = ["udisksctl", "mount", "-b", device, "--no-user-interaction"]
    elif action == "unmount":
        args = ["udisksctl", "unmount", "-b", device, "--no-user-interaction"]
    elif action == "eject":
        disk = next((d["disk"] for d in drives() if d["path"] == device), device)
        for d in drives():
            if d["disk"] == disk and d["mount"]:
                subprocess.run(["udisksctl", "unmount", "-b", d["path"], "--no-user-interaction"], capture_output=True, timeout=60)
        args = ["udisksctl", "power-off", "-b", disk, "--no-user-interaction"]
    else:
        raise ops.OpError("The action must be mount, unmount or eject")
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["udisksctl failed"]
        if "busy" in msg[0].lower():
            raise ops.OpError("A program still uses this drive. Close it, then try again.", 409, code="busy")
        raise ops.OpError(msg[0][:200], 400)
    m = re.search(r" at (.+?)\.?$", r.stdout.strip())
    from .. import events

    events.publish("places_changed")
    return {"ok": True, "mount": m.group(1) if m else None}


# ---------------------------------------------------------------- secure delete


def secure_delete(paths: list[str]) -> dict:
    """Overwrite each file 3 times, then delete it. Permanent: no undo. On an SSD the drive may keep old copies,
    so the reply says so."""
    n = 0
    targets = _targets(paths, recursive=True, dirs=False)
    for p in targets:
        if os.path.islink(p) or not os.path.isfile(p):
            continue
        if shutil.which("shred"):
            r = subprocess.run(["shred", "-n", "3", "-z", "-u", p], capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                raise ops.OpError(r.stderr.strip()[:200] or "shred failed")
        else:
            size = os.path.getsize(p)
            with open(p, "r+b") as f:
                for _ in range(3):
                    f.seek(0)
                    left = size
                    while left > 0:
                        k = min(left, 1 << 20)
                        f.write(os.urandom(k))
                        left -= k
                    f.flush()
                    os.fsync(f.fileno())
            os.unlink(p)
        n += 1
    for p in paths:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
    rotational = _rotational(paths[0]) if paths else None
    note = None if rotational else "On an SSD or a flash drive, the drive may keep old copies of the data."
    return {"deleted": n, "note": note}


def _rotational(path: str) -> bool | None:
    try:
        dev = os.stat(os.path.dirname(os.path.abspath(path))).st_dev
        name = Path(f"/sys/dev/block/{os.major(dev)}:{os.minor(dev)}").resolve().name
        base = re.sub(r"p?\d+$", "", name) if not name.startswith("dm-") else name
        return Path(f"/sys/block/{base}/queue/rotational").read_text().strip() == "1"
    except OSError:
        return None
