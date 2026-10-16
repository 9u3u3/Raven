"""Suggestion rows for Space -> Suggestions (sections 9.7 and 9.11). Nothing changes without a review."""

from __future__ import annotations

import os

from .dupes import job as dupe_job
from .scan import CATEGORIES, store


def _gb(n: int) -> str:
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


RULES = {
    "node_modules": ("node_modules folder", "node_modules folders"),
    "venv": ("Python virtual environment", "Python virtual environments"),
    "pycache": ("__pycache__ folder", "__pycache__ folders"),
    "build-cache": ("tool cache folder", "tool cache folders"),
    "build": ("build output folder", "build output folders"),
    "package-cache": ("package manager cache", "package manager caches"),
    "installer": ("old installer in Downloads", "old installers in Downloads"),
    "temp": ("temporary file", "temporary files"),
    "empty": ("empty folder", "empty folders"),
}


def suggestions(path: str) -> list[dict]:
    path = os.path.abspath(path)
    rows: list[dict] = []
    found = store.find(path)
    if found:
        res, _ = found
        by_rule: dict[str, list] = {}
        for j in res.junk:
            if _inside(j.path, path):
                by_rule.setdefault(j.rule, []).append(j)
        for rule, items in by_rule.items():
            items.sort(key=lambda j: j.size, reverse=True)
            one, many = RULES.get(rule, (rule, rule))
            total = sum(j.size for j in items)
            checked = [j for j in items if j.checked]
            noun = one if len(items) == 1 else many
            verb = "uses" if len(items) == 1 else "use"
            sentence = f"{len(items)} {noun} {verb} {_gb(total)}." if total else f"{len(items)} {noun}."
            if len(checked) < len(items):
                sentence += f" {len(items) - len(checked)} belong to projects changed in the last 30 days and stay unchecked."
            rows.append({
                "id": f"junk:{rule}",
                "sentence": sentence,
                "bytes": sum(j.size for j in checked),
                "total": total,
                "reversible": True,
                "action": "trash",
                "items": [
                    {"path": j.path, "size": j.size, "checked": j.checked, "note": j.reason, "is_dir": j.is_dir}
                    for j in items
                ],
            })
    groups = [g for g in dupe_job.groups if any(_inside(f["path"], path) for f in g["files"])]
    if groups:
        extra = [(f, g) for g in groups for f in g["files"] if not f["keep"] and _inside(f["path"], path)]
        wasted = sum(f["size"] for f, _ in extra)
        rows.append({
            "id": "dupes",
            "sentence": f"{len(extra)} duplicate copies of {len(groups)} files waste {_gb(wasted)}. Raven keeps one copy of each.",
            "bytes": wasted,
            "total": wasted,
            "reversible": True,
            "action": "trash",
            "items": [
                {"path": f["path"], "size": f["size"], "checked": True, "note": f"Same as {os.path.basename(next(x['path'] for x in g['files'] if x['keep']))}", "is_dir": False}
                for f, g in extra
            ],
        })
    from ..compress.candidates import candidates

    try:
        rows.extend(candidates(path))
    except Exception:  # noqa: BLE001 - a bad candidate must not hide the cleanup rows
        pass
    rows.sort(key=lambda r: r["total"], reverse=True)
    rows.extend(insights(path))
    return rows


def insights(path: str) -> list[dict]:
    """Rows that are not about space (section 9.11): messy folders and files with private data."""
    from .. import db, organize

    out: list[dict] = []
    # Messy folders: the folder itself and the folders one level below it.
    folders = [path]
    try:
        folders += sorted(e.path for e in os.scandir(path) if e.is_dir() and not e.name.startswith("."))[:40]
    except OSError:
        pass
    for f in folders:
        try:
            plan = organize.suggest(f, "both")
        except Exception:  # noqa: BLE001 - projects and unreadable folders have no plan
            continue
        organize.PLANS.pop(plan.id, None)
        moved = [m for m in plan.moves if m.move]
        if len(moved) < 8:
            continue
        groups = {os.path.relpath(os.path.dirname(m.dst), f) for m in moved}
        renamed = sum(1 for m in plan.moves if m.rename)
        out.append({
            "id": f"organize:{f}",
            "sentence": f"{len(moved)} loose files in {os.path.basename(f) or f} fit into {len(groups)} folders"
                        + (f", and {renamed} have generic names." if renamed else "."),
            "bytes": 0, "total": 0, "reversible": True, "action": "organize", "root": f,
            "items": [{"path": m.src, "size": 0, "checked": True, "note": os.path.relpath(m.dst, f), "is_dir": False} for m in moved[:50]],
        })
    from .. import naming

    n_generic, _ = naming.count_generic(path)
    if n_generic:
        out.append({
            "id": f"name:{path}", "sentence": f"{n_generic} files have generic names",
            "bytes": 0, "total": 0, "reversible": True, "action": "name", "root": path, "items": [],
        })
    like = path.rstrip("/").replace("%", "\\%").replace("_", "\\_") + "/%"
    rows = db.conn().execute(
        "SELECT path, size, pii_level FROM files WHERE is_dir=0 AND pii_level>=2 AND path LIKE ? ESCAPE '\\' ORDER BY path",
        (like,),
    ).fetchall()
    if rows:
        out.append({
            "id": "privacy",
            "sentence": f"{len(rows)} file{'s' if len(rows) != 1 else ''} hold bank account, card or ID numbers.",
            "bytes": 0, "total": 0, "reversible": True, "action": "privacy",
            "items": [{"path": r["path"], "size": r["size"] or 0, "checked": True, "note": "high-risk data", "is_dir": False} for r in rows[:50]],
        })
    return out


def category_names() -> list[str]:
    return CATEGORIES
