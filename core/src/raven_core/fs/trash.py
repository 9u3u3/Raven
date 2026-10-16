"""FreeDesktop trash. Raven writes the trash itself so that undo knows where each item went."""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import quote, unquote


def _home_trash() -> Path:
    from ..platform import trash_home

    return trash_home()


def _mount_point(path: Path) -> Path:
    path = path.resolve() if not path.is_symlink() else path.parent.resolve() / path.name
    dev = os.lstat(path).st_dev
    cur = path
    while cur != cur.parent:
        parent = cur.parent
        if os.lstat(parent).st_dev != dev:
            return cur
        cur = parent
    return cur


def trash_dir_for(path: Path) -> Path:
    home_trash = _home_trash()
    home_trash.mkdir(parents=True, exist_ok=True)
    try:
        same_dev = os.lstat(path).st_dev == os.stat(home_trash).st_dev
    except OSError:
        same_dev = True
    if same_dev or os.name == "nt" or not hasattr(os, "getuid"):
        # Windows and macOS: one Raven trash. A move from another drive is a copy and a delete.
        return home_trash
    top = _mount_point(path)
    td = top / f".Trash-{os.getuid()}"
    try:
        (td / "files").mkdir(parents=True, exist_ok=True)
        (td / "info").mkdir(parents=True, exist_ok=True)
        return td
    except OSError:
        return home_trash


def move_to_trash(path: str | os.PathLike) -> tuple[str, str]:
    """Move one item to the trash. Return (trashed_path, info_path)."""
    p = Path(path)
    if not os.path.lexists(p):
        raise FileNotFoundError(str(p))
    td = trash_dir_for(p)
    files, info = td / "files", td / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    base = p.name
    name = base
    n = 1
    while True:
        info_path = info / f"{name}.trashinfo"
        try:
            try:
                fd = os.open(info_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except OSError as e:
                # gvfs FTP has no O_EXCL. Check, then create.
                if isinstance(e, FileExistsError) or os.path.lexists(info_path):
                    raise FileExistsError(str(info_path)) from e
                fd = os.open(info_path, os.O_CREAT | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            n += 1
            stem, dot, ext = base.partition(".") if not base.startswith(".") else (base, "", "")
            name = f"{stem}.{n}{dot}{ext}" if dot else f"{base}.{n}"
    abs_path = str(p.absolute())
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with os.fdopen(fd, "w") as f:
        f.write(f"[Trash Info]\nPath={quote(abs_path)}\nDeletionDate={stamp}\n")
    target = files / name
    try:
        from .ops import move_safe

        move_safe(str(p), str(target))
    except BaseException:
        os.unlink(info_path)
        raise
    return str(target), str(info_path)


def restore_from_trash(trashed: str, info_path: str, original: str) -> str:
    """Move a trashed item back. If the original name is in use, keep both."""
    from .ops import unique_path

    dst = original if not os.path.lexists(original) else unique_path(original)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    from .ops import move_safe

    move_safe(trashed, dst)
    try:
        os.unlink(info_path)
    except OSError:
        pass
    return dst


def original_path(info_path: str) -> str | None:
    try:
        for line in Path(info_path).read_text().splitlines():
            if line.startswith("Path="):
                return unquote(line[5:])
    except OSError:
        return None
    return None
