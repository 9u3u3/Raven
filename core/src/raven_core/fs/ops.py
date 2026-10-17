"""File operations with an undo journal (operations table).

Every change goes through this module. One group_id covers one user action,
so one undo reverses the whole action.

Journal op kinds:
  create  - dst is new (copy, mkdir, newfile). Undo trashes dst. Redo restores it.
  move    - src moved to dst (move, rename). Undo and redo swap the paths.
  trash   - src went to the trash at dst. Undo restores it. Redo trashes it again.
  delete  - permanent. It cannot be undone. The group never enters the undo stack.
  untrash - an item in the trash (src) went back to its first folder (dst). Undo trashes it again.
  attr    - an attribute of src changed (extra: what, old, new): permissions, owner, dates, tags or flags
            (PLAN2 P2-5). Undo sets old, redo sets new.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Iterable

from .. import db, events
from .trash import move_to_trash, original_path, restore_from_trash
from ..platform import like_below


class OpError(Exception):
    def __init__(self, message: str, status: int = 400, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


# ---------------------------------------------------------------- copies that a share accepts (PLAN2 P2-9.4)
# Some shares (gvfs FTP, SMB without unix extensions) refuse to set times and permissions. The data is what
# counts: these helpers copy it and skip the metadata the share refuses.

_NOT_SUPPORTED = {getattr(__import__("errno"), n) for n in ("ENOTSUP", "EOPNOTSUPP", "EPERM", "EACCES", "EINVAL", "ENOSYS", "EIO")
                  if hasattr(__import__("errno"), n)}


def copystat_safe(src, dst, follow_symlinks: bool = True) -> None:
    try:
        shutil.copystat(src, dst, follow_symlinks=follow_symlinks)
    except OSError as e:
        if e.errno not in _NOT_SUPPORTED:
            raise


def copy2_safe(src, dst, *, follow_symlinks: bool = True):
    if not follow_symlinks and os.path.islink(src):
        os.symlink(os.readlink(src), dst)
        return dst
    shutil.copyfile(src, dst)
    copystat_safe(src, dst)
    return dst


def copytree_safe(src, dst) -> None:
    """shutil.copytree(symlinks=True), with copy2_safe and copystat_safe."""
    os.makedirs(dst)
    for e in os.scandir(src):
        s, d = e.path, os.path.join(dst, e.name)
        if e.is_symlink():
            os.symlink(os.readlink(s), d)
        elif e.is_dir():
            copytree_safe(s, d)
        else:
            copy2_safe(s, d)
    copystat_safe(src, dst)


def move_safe(src, dst) -> str:
    """shutil.move with copy2_safe: a move to another disk or a share copies the data, then deletes."""
    if os.path.isdir(src) and not os.path.islink(src):
        try:
            os.rename(src, dst)
            return str(dst)
        except OSError:
            copytree_safe(src, dst)
            shutil.rmtree(src)
            return str(dst)
    return shutil.move(str(src), str(dst), copy_function=copy2_safe)


# Undo states in operations.undone.
LIVE, UNDONE, DEAD, FINAL = 0, 1, 2, 3


def unique_path(path: str) -> str:
    """Return a free path next to `path`: 'a (2).txt', 'a (3).txt' and so on."""
    p = Path(path)
    if not os.path.lexists(p):
        return str(p)
    name = p.name
    if p.is_dir() or "." not in name.lstrip("."):
        stem, ext = name, ""
    else:
        stem, ext = name.rsplit(".", 1)
        ext = "." + ext
        if stem.endswith(".tar"):
            stem, ext = stem[:-4], ".tar" + ext
    n = 2
    while True:
        cand = p.with_name(f"{stem} ({n}){ext}")
        if not os.path.lexists(cand):
            return str(cand)
        n += 1


class Group:
    """Collect the journal rows of one user action."""

    def __init__(self, source: str = "manual", label: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.source = source
        self.label = label
        self.rows: list[tuple] = []
        self.final = False

    def add(self, op: str, src: str | None, dst: str | None, extra: dict | None = None) -> None:
        self.rows.append((op, src, dst, json.dumps(extra or {})))
        if op == "delete":
            self.final = True

    def commit(self) -> None:
        if not self.rows:
            return
        now = int(time.time() * 1000)
        state = FINAL if self.final else LIVE
        with db.tx() as c:
            # A new action ends every redo chain.
            c.execute("UPDATE operations SET undone=? WHERE undone=?", (DEAD, UNDONE))
            for op, src, dst, extra in self.rows:
                extra_d = json.loads(extra)
                extra_d["label"] = self.label
                c.execute(
                    "INSERT INTO operations(group_id, source, op, src, dst, ts, undone, extra) VALUES (?,?,?,?,?,?,?,?)",
                    (self.id, self.source, op, src, dst, now, state, json.dumps(extra_d)),
                )
        events.publish("journal", group=self.id, label=self.label)


def _check_inside(src: Path, dst_dir: Path) -> None:
    try:
        dst_dir.resolve().relative_to(src.resolve())
    except ValueError:
        return
    raise OpError(f"Cannot put '{src.name}' inside itself", 400)


def _resolve_conflict(target: str, on_conflict: str, g: Group) -> str | None:
    if not os.path.lexists(target):
        return target
    if on_conflict == "skip":
        return None
    if on_conflict == "replace":
        trashed, info = move_to_trash(target)
        g.add("trash", target, trashed, {"info": info})
        return target
    return unique_path(target)


def copy(srcs: Iterable[str], dst_dir: str, on_conflict: str = "keep_both", source: str = "manual",
         group: Group | None = None) -> list[str]:
    """With `group`, the rows go to that group and the caller commits it (one undo for a whole command)."""
    srcs = list(srcs)
    d = Path(dst_dir)
    if not d.is_dir():
        raise OpError(f"Folder not found: {dst_dir}", 404)
    g = group or Group(source, f"Copy {len(srcs)} item{'s' if len(srcs) != 1 else ''}")
    out = []
    try:
        for s in srcs:
            sp = Path(s)
            if not os.path.lexists(sp):
                raise OpError(f"Not found: {s}", 404)
            if sp.is_dir() and not sp.is_symlink():
                _check_inside(sp, d)
            target = _resolve_conflict(str(d / sp.name), on_conflict, g)
            if target is None:
                continue
            if sp.is_dir() and not sp.is_symlink():
                copytree_safe(sp, target)
            else:
                copy2_safe(sp, target, follow_symlinks=False)
            g.add("create", str(sp), target)
            out.append(target)
    finally:
        if group is None:
            g.commit()
    return out


def move(srcs: Iterable[str], dst_dir: str, on_conflict: str = "keep_both", source: str = "manual",
         group: Group | None = None) -> list[str]:
    """With `group`, the rows go to that group and the caller commits it (one undo for a whole command)."""
    srcs = list(srcs)
    d = Path(dst_dir)
    if not d.is_dir():
        raise OpError(f"Folder not found: {dst_dir}", 404)
    g = group or Group(source, f"Move {len(srcs)} item{'s' if len(srcs) != 1 else ''}")
    out = []
    try:
        for s in srcs:
            sp = Path(s)
            if not os.path.lexists(sp):
                raise OpError(f"Not found: {s}", 404)
            if sp.parent.resolve() == d.resolve():
                continue
            if sp.is_dir() and not sp.is_symlink():
                _check_inside(sp, d)
            target = _resolve_conflict(str(d / sp.name), on_conflict, g)
            if target is None:
                continue
            move_safe(str(sp), target)
            g.add("move", str(sp), target)
            out.append(target)
    finally:
        if group is None:
            g.commit()
    return out


def _valid_name(name: str) -> str:
    name = name.strip()
    if not name or name in (".", "..") or "/" in name or "\0" in name or (os.name == "nt" and "\\" in name):
        raise OpError(f"'{name}' is not a valid name", 400)
    return name


def rename(path: str, new_name: str, source: str = "manual", group: Group | None = None) -> str:
    p = Path(path)
    new_name = _valid_name(new_name)
    if not os.path.lexists(p):
        raise OpError(f"Not found: {path}", 404)
    if new_name == p.name:
        return str(p)
    target = p.with_name(new_name)
    # A change of case only is a valid rename on case-insensitive file systems.
    if os.path.lexists(target) and target.name.lower() != p.name.lower():
        raise OpError(f"'{new_name}' already exists in this folder", 409)
    g = group or Group(source, f"Rename to {new_name}")
    os.rename(p, target)
    g.add("move", str(p), str(target))
    if group is None:
        g.commit()
    return str(target)


def rename_many(items: list[dict], source: str = "manual") -> list[str]:
    """Batch rename (section 6.1): [{path, name}] in one undo group.

    Raven checks every new name before it changes anything: a valid name, no two items with one name,
    and no clash with a file that is not in the batch. Chains and swaps (a -> b, b -> a) go through
    temporary names first.
    """
    plan = []
    for it in items:
        p = Path(it["path"])
        if not os.path.lexists(p):
            raise OpError(f"Not found: {p}", 404)
        new = _valid_name(it["name"])
        if new != p.name:
            plan.append((p, p.with_name(new)))
    if not plan:
        return []
    olds = {str(a) for a, _ in plan}
    seen: set[str] = set()
    for a, b in plan:
        key = str(b).lower() if os.name == "nt" else str(b)
        if key in seen:
            raise OpError(f"Two items would get the name '{b.name}'", 409)
        seen.add(key)
        if os.path.lexists(b) and str(b) not in olds and b.name.lower() != a.name.lower():
            raise OpError(f"'{b.name}' already exists in {b.parent.name}", 409)
    g = Group(source, f"Rename {len(plan)} item{'s' if len(plan) != 1 else ''}")
    temps = []
    try:
        for a, b in plan:
            tmp = a.with_name(f".raven-rename-{uuid.uuid4().hex[:8]}-{a.name}")
            os.rename(a, tmp)
            temps.append((a, tmp, b))
        for a, tmp, b in temps:
            os.rename(tmp, b)
            g.add("move", str(a), str(b), {"batch": True})
    except OSError:
        # Put back every item that is still under its temporary name.
        for a, tmp, _ in temps:
            if os.path.lexists(tmp):
                os.rename(tmp, a)
        raise
    finally:
        g.commit()
    return [str(b) for _, b in plan]


def mkdir(parent: str, name: str = "New folder", source: str = "manual", group: Group | None = None) -> str:
    target = unique_path(str(Path(parent) / _valid_name(name)))
    os.mkdir(target)
    g = group or Group(source, "New folder")
    g.add("create", None, target)
    if group is None:
        g.commit()
    return target


def newfile(parent: str, name: str = "New file.txt", source: str = "manual") -> str:
    target = unique_path(str(Path(parent) / _valid_name(name)))
    with open(target, "x"):
        pass
    g = Group(source, "New file")
    g.add("create", None, target)
    g.commit()
    return target


def in_trash(path: str) -> bool:
    """True for an item directly in a trash folder (<data>/Trash/files or <top>/.Trash-<uid>/files)."""
    files = Path(path).parent
    return files.name == "files" and (files.parent.name == "Trash" or files.parent.name.startswith(".Trash-")) and (
        files.parent / "info").is_dir()


def trash(paths: Iterable[str], source: str = "manual", label: str | None = None, group: Group | None = None) -> int:
    paths = list(paths)
    if any(in_trash(p) for p in paths):
        raise OpError("These items are in the trash already. Delete them permanently?", 409, code="in_trash")
    g = group or Group(source, label or f"Trash {len(paths)} item{'s' if len(paths) != 1 else ''}")
    n = 0
    try:
        for p in paths:
            try:
                trashed, info = move_to_trash(p)
            except OSError as e:
                from .network import is_network

                if is_network(p) and not isinstance(e, FileNotFoundError):
                    raise OpError("This share has no trash. Delete permanently?", 409, code="no_trash") from e
                raise
            g.add("trash", p, trashed, {"info": info})
            n += 1
    finally:
        if group is None:
            g.commit()
    return n


def restore(paths: Iterable[str], source: str = "manual") -> list[str]:
    """Put trashed items back where they were (the Path line of their .trashinfo file)."""
    paths = list(paths)
    g = Group(source, f"Restore {len(paths)} item{'s' if len(paths) != 1 else ''} from the trash")
    out = []
    try:
        for p in paths:
            pp = Path(p)
            info = pp.parent.parent / "info" / f"{pp.name}.trashinfo"
            if pp.parent.name != "files" or not info.exists():
                raise OpError(f"'{pp.name}' is not an item in the trash", 400)
            original = original_path(str(info))
            if not original:
                raise OpError(f"The trash has no first folder for '{pp.name}'", 400)
            back = restore_from_trash(str(pp), str(info), original)
            g.add("untrash", str(pp), back)
            out.append(back)
    finally:
        g.commit()
    return out


def delete(paths: Iterable[str], source: str = "manual") -> int:
    paths = list(paths)
    g = Group(source, f"Delete {len(paths)} item{'s' if len(paths) != 1 else ''} permanently")
    try:
        for p in paths:
            pp = Path(p)
            if pp.is_dir() and not pp.is_symlink():
                shutil.rmtree(pp)
            else:
                pp.unlink()
            # An item deleted from a trash folder: remove its .trashinfo too, or the trash keeps a stale entry.
            if in_trash(p):
                (pp.parent.parent / "info" / f"{pp.name}.trashinfo").unlink(missing_ok=True)
            g.add("delete", p, None)
    finally:
        g.commit()
        _end_groups_at([r[1] for r in g.rows])
    return len(g.rows)


def _end_groups_at(paths: list[str]) -> None:
    """Older actions whose result is now deleted cannot be undone. Mark their whole group DEAD, so Undo
    skips them and does not fail on a missing file (for example a trashed file deleted from the trash)."""
    if not paths:
        return
    with db.tx() as c:
        dead = set()
        for p in paths:
            for (gid,) in c.execute(
                "SELECT DISTINCT group_id FROM operations WHERE undone=? AND (dst=? OR dst LIKE ? ESCAPE '\\')",
                (LIVE, p, like_below(p)),
            ):
                dead.add(gid)
        for gid in dead:
            c.execute("UPDATE operations SET undone=? WHERE group_id=?", (DEAD, gid))
    if dead:
        events.publish("journal", group=None, label="")


# ---------------------------------------------------------------- undo / redo


def _group_rows(c, group_id: str):
    return c.execute("SELECT * FROM operations WHERE group_id=? ORDER BY id", (group_id,)).fetchall()


def _label(rows) -> str:
    try:
        return json.loads(rows[0]["extra"] or "{}").get("label", "")
    except Exception:
        return ""


def peek() -> dict:
    c = db.conn()
    u = c.execute("SELECT group_id FROM operations WHERE undone=? ORDER BY id DESC LIMIT 1", (LIVE,)).fetchone()
    r = c.execute("SELECT group_id FROM operations WHERE undone=? ORDER BY id ASC LIMIT 1", (UNDONE,)).fetchone()
    return {
        "undo": _label(_group_rows(c, u[0])) if u else None,
        "redo": _label(_group_rows(c, r[0])) if r else None,
    }


def _swap_moves(pairs: list[tuple[str, str]]) -> None:
    """Rename many (cur -> new) at once through temporary names, so swaps and chains work."""
    temps = []
    for cur, new in pairs:
        if not os.path.lexists(cur):
            raise OpError(f"Cannot undo: '{cur}' is gone", 409)
        tmp = os.path.join(os.path.dirname(cur), f".raven-rename-{uuid.uuid4().hex[:8]}-{os.path.basename(cur)}")
        os.rename(cur, tmp)
        temps.append((tmp, new))
    for tmp, new in temps:
        os.rename(tmp, new if not os.path.lexists(new) else unique_path(new))


def _is_batch(rows) -> bool:
    return all(r["op"] == "move" and json.loads(r["extra"] or "{}").get("batch") for r in rows)


def undo() -> dict:
    c = db.conn()
    row = c.execute("SELECT group_id FROM operations WHERE undone=? ORDER BY id DESC LIMIT 1", (LIVE,)).fetchone()
    if not row:
        raise OpError("Nothing to undo", 409)
    rows = _group_rows(c, row[0])
    if _is_batch(rows):
        _swap_moves([(r["dst"], r["src"]) for r in rows])
        with db.tx() as tc:
            for r in rows:
                tc.execute("UPDATE operations SET undone=? WHERE id=?", (UNDONE, r["id"]))
        label = _label(rows)
        events.publish("journal", group=row[0], label=label, undone=True)
        return {"undone": label, "paths": [r["src"] for r in rows]}
    updates = []
    for r in reversed(rows):
        extra = json.loads(r["extra"] or "{}")
        op, src, dst = r["op"], r["src"], r["dst"]
        if extra.get("elevated"):
            from . import elevate

            if op == "create" and os.path.lexists(dst):
                elevate.run({"op": "delete", "src": dst})
            elif op == "move":
                elevate.run({"op": "move", "src": dst, "dst": src})
        elif op == "create":
            if os.path.lexists(dst):
                trashed, info = move_to_trash(dst)
                extra.update(undo_trash=trashed, undo_info=info)
        elif op == "move":
            if not os.path.lexists(dst):
                raise OpError(f"Cannot undo: '{dst}' is gone", 409)
            back = src if not os.path.lexists(src) else unique_path(src)
            move_safe(dst, back)
            extra["undo_back"] = back
        elif op == "trash":
            back = restore_from_trash(dst, extra["info"], src)
            extra["undo_back"] = back
        elif op == "untrash":
            if os.path.lexists(dst):
                trashed, info = move_to_trash(dst)
                extra.update(undo_trash=trashed, undo_info=info)
        elif op == "attr":
            from .props import apply_attr

            apply_attr(src, extra["what"], extra["old"], bool(extra.get("elevated")))
        updates.append((json.dumps(extra), r["id"]))
    with db.tx() as tc:
        for extra, rid in updates:
            tc.execute("UPDATE operations SET undone=?, extra=? WHERE id=?", (UNDONE, extra, rid))
    label = _label(rows)
    events.publish("journal", group=row[0], label=label, undone=True)
    return {"undone": label, "paths": [r["src"] or r["dst"] for r in rows]}


def redo() -> dict:
    c = db.conn()
    row = c.execute("SELECT group_id FROM operations WHERE undone=? ORDER BY id ASC LIMIT 1", (UNDONE,)).fetchone()
    if not row:
        raise OpError("Nothing to redo", 409)
    rows = _group_rows(c, row[0])
    if _is_batch(rows):
        _swap_moves([(r["src"], r["dst"]) for r in rows])
        with db.tx() as tc:
            for r in rows:
                tc.execute("UPDATE operations SET undone=? WHERE id=?", (LIVE, r["id"]))
        label = _label(rows)
        events.publish("journal", group=row[0], label=label, redone=True)
        return {"redone": label}
    updates = []
    for r in rows:
        extra = json.loads(r["extra"] or "{}")
        op, src, dst = r["op"], r["src"], r["dst"]
        new_src, new_dst = src, dst
        if extra.get("elevated"):
            from . import elevate

            if op == "create":
                elevate.run({"op": "copy" if src else "mkdir", "src": src, "dst": dst})
            elif op == "move":
                elevate.run({"op": "move", "src": src, "dst": dst})
        elif op == "create":
            if "undo_trash" in extra:
                new_dst = restore_from_trash(extra.pop("undo_trash"), extra.pop("undo_info"), dst)
        elif op == "move":
            cur = extra.pop("undo_back", src)
            new_dst = dst if not os.path.lexists(dst) else unique_path(dst)
            move_safe(cur, new_dst)
        elif op == "trash":
            cur = extra.pop("undo_back", src)
            trashed, info = move_to_trash(cur)
            new_dst = trashed
            extra["info"] = info
        elif op == "untrash" and "undo_trash" in extra:
            new_dst = restore_from_trash(extra.pop("undo_trash"), extra.pop("undo_info"), dst)
        elif op == "attr":
            from .props import apply_attr

            apply_attr(src, extra["what"], extra["new"], bool(extra.get("elevated")))
        updates.append((json.dumps(extra), new_src, new_dst, r["id"]))
    with db.tx() as tc:
        for extra, s, d, rid in updates:
            tc.execute("UPDATE operations SET undone=?, extra=?, src=?, dst=? WHERE id=?", (LIVE, extra, s, d, rid))
    label = _label(rows)
    events.publish("journal", group=row[0], label=label, redone=True)
    return {"redone": label}
