"""Folder listing, item details and the places in the left column."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from . import kinds
from .kinds import ext_of, kind_info, mime_of
from ..platform import readlink

REAL_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "f2fs", "zfs", "jfs", "reiserfs"}


def entry(path: str, name: str | None = None, sniff: bool = False) -> dict | None:
    """One row of a listing. `sniff`: read the first bytes of a file whose name gives no kind (local disks only)."""
    name = name if name is not None else os.path.basename(path.rstrip("/")) or path
    try:
        st = os.lstat(path)
    except OSError:
        return None
    is_link = stat.S_ISLNK(st.st_mode)
    if is_link:
        try:
            tst = os.stat(path)
            is_dir = stat.S_ISDIR(tst.st_mode)
            size = tst.st_size
        except OSError:
            is_dir, size = False, 0
    else:
        is_dir = stat.S_ISDIR(st.st_mode)
        size = st.st_size
    label, group = kind_info(name, is_dir)
    if sniff and not is_dir and group == "other" and size and stat.S_ISREG(st.st_mode):
        res = kinds.sniff(path)
        if res and res[0] != "Text":
            label, group = res[0], res[1]
        elif res:
            label, group = "Text", "doc"
    return {
        "name": name,
        "path": path,
        "is_dir": is_dir,
        "is_link": is_link,
        "is_hidden": name.startswith("."),
        "size": None if is_dir else size,
        "mtime": int(st.st_mtime * 1000),
        "ctime": int(st.st_ctime * 1000),
        "atime": int(st.st_atime * 1000),
        "kind": label,
        "kind_group": group,
        "ext": "" if is_dir else ext_of(name),
        **({"system": True} if is_dir and _system_dir(path) else {}),
    }


def _system_dir(path: str) -> bool:
    from .props import is_system

    return is_system(path)


def list_dir(path: str, hidden: bool = True) -> dict:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise FileNotFoundError(str(p))
    out = []
    from .network import is_network

    # Reading file heads costs a round trip per file on a share. Sniff on local disks only.
    local = not is_network(str(p))
    with os.scandir(p) as it:
        for de in it:
            if not hidden and de.name.startswith("."):
                continue
            e = entry(de.path, de.name, sniff=local)
            if e:
                out.append(e)
    # Item counts for folders cost one scandir each. Skip them for very large folders.
    if len(out) <= 2000:
        for e in out:
            if e["is_dir"] and not e["is_link"]:
                try:
                    with os.scandir(e["path"]) as sub:
                        e["child_count"] = sum(1 for _ in sub)
                except OSError:
                    e["child_count"] = None
    _add_categories(str(p), out)
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "entries": out, "writable": os.access(p, os.W_OK)}


def _add_categories(folder: str, entries: list[dict]) -> None:
    """The Kind column shows the AI category after the type: "PDF · Lease" (section 12.4)."""
    from .. import classify, db

    try:
        rows = db.conn().execute(
            "SELECT name, category, category_source, category_reason, sniffed_kind, kind_mismatch FROM files "
            "WHERE parent=? AND (category IS NOT NULL OR kind_mismatch > 0)",
            (folder,),
        ).fetchall()
    except Exception:  # noqa: BLE001 - the index is optional for a listing
        return
    cats = {r["name"]: r for r in rows}
    for e in entries:
        r = cats.get(e["name"])
        if not r:
            continue
        if r["category"] in classify.SHOWN:
            e["category"] = r["category"]
            e["category_why"] = f"{r['category_source']}: {r['category_reason']}"
        if r["kind_mismatch"]:
            # 1: the name and the content disagree. 2: a program with a document name.
            e["kind_mismatch"] = r["kind_mismatch"]
            e["sniffed_kind"] = r["sniffed_kind"]


def _mode_string(mode: int) -> str:
    return stat.filemode(mode)


def info(path: str) -> dict:
    e = entry(path)
    if e is None:
        raise FileNotFoundError(path)
    st = os.lstat(path)
    from .. import platform as plat

    owner, group = plat.owner_group(st)
    e.update(
        mode=_mode_string(st.st_mode),
        mode_octal=oct(st.st_mode & 0o7777),
        owner=owner,
        group=group,
        mime="inode/directory" if e["is_dir"] else mime_of(e["name"]),
        inode=st.st_ino,
        target=readlink(path) if e["is_link"] else None,
        acl=plat.acl_summary(path),
    )
    try:
        regular = not e["is_dir"] and stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        regular = False  # a broken link
    if regular:
        k = kinds.check(path, e["name"])
        e.update(kind=k["kind"], kind_group=k["group"], sniffed_kind=k["sniffed"], kind_mismatch=k["mismatch"],
                 kind_warning=k["warning"])
    if e["is_dir"]:
        try:
            e["child_count"] = sum(1 for _ in os.scandir(path))
        except OSError:
            e["child_count"] = None
    from . import props

    e.update(explain=props.explain(path), tags=props.read_tags(path), flags=props.get_flags(path),
             nlink=st.st_nlink, uid=st.st_uid)
    return e


def _xdg_dirs() -> dict[str, str]:
    home = Path.home()
    out = {}
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            if line.startswith("XDG_") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').replace("$HOME", str(home))
                out[k[4:-4].title()] = v
    return out


def places() -> dict:
    home = Path.home()
    xdg = _xdg_dirs()
    folders = []
    for label in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"):
        p = xdg.get(label) or str(home / label)
        if Path(p).is_dir() and Path(p) != home:
            folders.append({"label": label, "path": p})

    drives, removable, network = [], [], []
    seen = set()
    from .. import platform as plat

    user = os.environ.get("USER") or os.environ.get("USERNAME", "")
    for dev, mnt, fstype in plat.mounts():
        kind = plat.drive_kind(mnt, fstype)
        if (kind is None and fstype not in REAL_FS) or mnt in seen:
            continue
        if mnt.startswith(("/boot", "/efi", "/var/lib", "/snap")):
            continue
        if dev in seen:
            continue
        seen.add(mnt)
        seen.add(dev)
        try:
            du = shutil.disk_usage(mnt)
        except OSError:
            continue
        item = {"label": Path(dev).name if mnt == "/" else (Path(mnt).name or mnt), "path": mnt, "device": dev,
                "total": du.total, "free": du.free, "fstype": fstype}
        if kind == "removable" or mnt.startswith(("/run/media/", "/media/")):
            removable.append(item)
        else:
            drives.append(item)

    from . import network as net

    gvfs = net.gvfs_dir()
    if gvfs.is_dir():
        try:
            for d in sorted(gvfs.iterdir()):
                network.append({**net.label(d.name), "path": str(d)})
        except OSError:
            pass
    network += [m for m in net.kernel_mounts() if m["path"] not in seen]

    trash = plat.trash_home() / "files"
    return {
        "home": str(home),
        "user": user,
        "folders": folders,
        "drives": drives,
        "removable": removable,
        "network": network,
        "trash": str(trash),
    }


def disk_free(path: str) -> dict:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    du = shutil.disk_usage(p)
    return {"path": str(p), "total": du.total, "free": du.free, "used": du.used}
