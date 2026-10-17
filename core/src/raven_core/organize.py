"""Organize plans (section 9.5): a messy folder becomes a clean structure.

1. Raven groups the loose files of a folder by category (section 9.4). Rules give a plan at once.
2. "Think harder" asks a model for the tree: the local model for a small folder, the cloud
   (redacted metadata only) for a large one.
3. Generic names (naming.is_generic: Untitled(1).pdf, scan_0042.pdf) get a name from the title.
4. The user checks, edits or drags items. Apply runs the plan as one journal group.

Safety: never delete, unique names on a clash, no changes inside projects or app folders.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field

from . import db
from .fs import ops
from .privacy import gate, review
from .storage import protect

GROUPS = {
    "Lecture notes": "Study", "Assignment": "Study", "Exam": "Study", "Syllabus": "Study",
    "Resume": "Career", "Cover letter": "Career",
    "Invoice": "Money", "Receipt": "Money", "Bank statement": "Money", "Bill": "Money",
    "Ticket": "Travel", "Travel plan": "Travel",
    "Lease": "Home", "Notice": "Home", "Certificate": "Certificates",
    "Report": "Documents", "Meeting notes": "Documents", "Presentation": "Documents",
    "Personal notes": "Notes", "Recipe": "Notes",
    "Photo": "Photos", "Screenshot": "Screenshots", "Image": "Images", "Video": "Videos", "Audio": "Audio",
    "Installer": "Installers", "Archive": "Archives", "Code": "Code", "Data": "Data", "Spreadsheet": "Data",
    "Log": "Logs", "Ebook": "Books", "Font": "Fonts", "Disk image": "Disk images",
}
SMALL = 20  # fewer loose files plan on the local model (Think harder). Measured: about 5 s per file on this laptop


@dataclass
class Move:
    src: str
    dst: str
    reason: str
    category: str | None
    rename: bool
    move: bool


@dataclass
class OrgPlan:
    id: str
    root: str
    moves: list[Move]
    skipped: list[dict]
    tier: str
    planner: str
    ms: int
    warnings: list[str] = field(default_factory=list)


PLANS: dict[str, OrgPlan] = {}


def _clean_title(title: str) -> str:
    t = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", title).strip(" .-_")
    words = t.split()[:7]
    t = " ".join(words)
    if t.isupper():
        small = {"of", "the", "and", "for", "to", "in", "a", "an", "on"}
        t = " ".join(w.lower() if i and w.lower() in small else w.capitalize() for i, w in enumerate(t.split()))
    return t[:70].strip(" .-_")


def new_name(name: str, title: str | None, category: str | None) -> str | None:
    """A readable name for a file with a generic name, or None to keep the name."""
    from .naming import is_generic

    stem, ext = os.path.splitext(name)
    if not is_generic(name):
        return None
    t = _clean_title(title or "")
    if not t:
        # No title to read (photos, empty text): a name such as "Photo.png" says less than the old one.
        return None
    if category and t and category.lower().split()[0] not in t.lower():
        base = f"{category} - {t}"
    else:
        base = t or category
    return base + ext


def _loose_files(root: str) -> list[os.DirEntry]:
    out = []
    with os.scandir(root) as it:
        for de in it:
            if de.name.startswith(".") or not de.is_file(follow_symlinks=False):
                continue
            out.append(de)
    return sorted(out, key=lambda d: d.name.lower())


def _rows(root: str) -> dict[str, dict]:
    return {
        r["name"]: dict(r)
        for r in db.conn().execute(
            "SELECT name, title, category, category_source, category_reason, size, mtime FROM files WHERE parent=?", (root,)
        )
    }


def suggest(root: str, mode: str = "both", think_harder: bool = False) -> OrgPlan:
    t0 = time.perf_counter()
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise ValueError(f"Not a folder: {root}")
    if protect.user_protected(root):
        raise ValueError("A folder rule protects this folder. Raven does not reorganize it.")
    from .privacy.scope import is_private, require_ai

    require_ai(root)  # a private folder (PLAN4 7.6)
    if protect.is_project_root(root) or protect.is_software_dir(root):
        raise ValueError("This folder is a project or holds software. Raven does not reorganize it, because tools depend on its layout.")
    files = _loose_files(root)
    rows = _rows(root)
    existing = {d.name.lower(): d.name for d in os.scandir(root) if d.is_dir() and not is_private(d.path)}
    skipped: list[dict] = []
    picks: dict[str, tuple[str | None, str, str | None]] = {}  # name -> (folder, reason, category)
    tier, planner = "rules", "categories"
    if think_harder:
        picks, tier, planner = _model_plan(root, files, rows)
    moves: list[Move] = []
    taken: set[str] = set()
    for de in files:
        r = rows.get(de.name) or {}
        cat = r.get("category")
        if de.name.endswith((".part", ".crdownload", ".tmp")):
            skipped.append({"path": de.path, "reason": "the download is not finished"})
            continue
        if cat == "Secret":
            skipped.append({"path": de.path, "reason": "it holds passwords or keys: keep it where you know it is"})
            continue
        if de.name in picks:
            folder, reason, cat2 = picks[de.name]
            cat = cat2 or cat
        else:
            if not cat or r.get("category_source") in (None, "pending", "llm-pending"):
                # New files: the name and extension rules need no model, so they run now.
                from . import classify

                quick = classify.rule(de.path, de.name)
                if quick is None:
                    skipped.append({"path": de.path, "reason": "not classified yet"})
                    continue
                cat, r = quick[0], {**r, "category": quick[0], "category_source": "rule", "category_reason": quick[2]}
            from . import learn

            # A folder the user chose for this category in an earlier plan wins over the default.
            group = learn.folder_prefs().get(cat) or GROUPS.get(cat)
            folder = group
            reason = f"{cat.lower()} ({r.get('category_source')}: {r.get('category_reason') or ''})".strip()
        if folder:
            folder = existing.get(folder.lower(), folder)
        dst_dir = os.path.join(root, folder) if (folder and mode in ("both", "move")) else root
        name = de.name
        renamed = False
        if mode in ("both", "rename"):
            nn = new_name(de.name, r.get("title"), cat)
            if nn and nn != de.name:
                name, renamed = nn, True
        if dst_dir == root and not renamed:
            continue
        dst = os.path.join(dst_dir, name)
        dst = _unique(dst, taken)
        moves.append(Move(de.path, dst, reason if dst_dir != root else f"generic name, title: {r.get('title')}", cat,
                          renamed, dst_dir != root))
    plan = OrgPlan(uuid.uuid4().hex[:10], root, moves, skipped, tier, planner, round((time.perf_counter() - t0) * 1000))
    for k in sorted(PLANS)[:-10]:
        PLANS.pop(k, None)
    PLANS[plan.id] = plan
    return plan


def _unique(path: str, taken: set[str]) -> str:
    if not os.path.lexists(path) and path not in taken:
        taken.add(path)
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.lexists(f"{stem} ({n}){ext}") or f"{stem} ({n}){ext}" in taken:
        n += 1
    taken.add(f"{stem} ({n}){ext}")
    return f"{stem} ({n}){ext}"


# ---------------------------------------------------------------- think harder


TREE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "folder": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["i", "folder", "reason"],
            },
        }
    },
    "required": ["items"],
}
TREE_SYSTEM = (
    "You organize the loose files of one folder on a student's laptop. For each numbered file, pick a folder path "
    "relative to this folder (at most two levels, such as Study/DBMS or Money/Bills), or an empty string to leave "
    "it in place. Use short, plain folder names. Group by subject, project and purpose. Reuse existing folder "
    "names when they fit. Give a reason of at most eight words. Placeholders such as [PERSON_1] stand for private "
    "values. Copy them exactly."
)


def _model_plan(root: str, files: list[os.DirEntry], rows: dict[str, dict]) -> tuple[dict, str, str]:
    from .router import cloud, local
    from .router import providers as registry
    from .router import router as core

    from .fs.props import policy_for

    big = len(files) >= SMALL
    order = core.provider_order("folder_summary") if core.ai_mode() != "local" and not policy_for(root, "no_cloud") else []
    red = gate.Redactor() if (big and order) else None
    from .privacy.scope import is_private

    subs = sorted(d.name for d in os.scandir(root) if d.is_dir() and not d.name.startswith(".") and not is_private(d.path))

    titles = [((rows.get(de.name) or {}).get("title") or "")[:60] for de in files[:200]]
    names = [*subs[:40], *(de.name for de in files[:200]), *(t for t in titles if t)]
    safe = dict(zip(names, red.redact_names(names))) if red else {x: x for x in names}  # one layer-2 call (PLAN4 B7)

    lines = [f"Existing folders: {', '.join(safe[s] for s in subs[:40]) or 'none'}", "Files:"]
    for n, (de, title) in enumerate(zip(files[:200], titles), 1):
        row = rows.get(de.name) or {}
        month = time.strftime("%Y-%m", time.localtime(row.get("mtime") or de.stat().st_mtime))
        lines.append(f"{n}. {safe[de.name]} | {row.get('category') or 'unknown'} | {month}" + (f" | {safe[title]}" if title else ""))
    payload = "\n".join(lines)
    data = None
    tier, planner = "T2", local.LLM
    if red is not None:
        try:
            sealed = gate.seal(payload, red)
            sealed = review.confirm(sealed, red, "Organize plan", order, TREE_SYSTEM)  # PLAN4 7.7
        except gate.Blocked:
            sealed = None
        if sealed is not None:
            for prov in order:
                lid = gate.ledger_write("organize", "T3", prov, TREE_SYSTEM + "\n\n" + sealed, len(red.mapping))
                try:
                    data, _ = registry.chat(prov, TREE_SYSTEM, sealed, TREE_SCHEMA)
                    tier, planner = "T3", prov
                    break
                except cloud.CloudError:
                    gate.ledger_mark_failed(lid, "organize")
    if data is None:
        if not local.available():
            raise ValueError(local.unavailable_reason())
        if red is not None:
            # The local model reads the real names. Nothing leaves the machine.
            return _model_plan_local(root, files, rows, subs)
        # About 30 tokens per item (index, folder, reason). A cut answer keeps its complete items (local.salvage).
        data = local.chat(TREE_SYSTEM, payload, TREE_SCHEMA, max_tokens=36 * min(len(files), 200) + 50)
    out = {}
    for it in data.get("items", []):
        try:
            de = files[int(it["i"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        folder = gate.restore(str(it.get("folder") or ""), red.mapping if red else {}).strip().strip("/")
        if ".." in folder.split("/") or folder.startswith("/"):
            continue
        out[de.name] = (folder or None, gate.restore(str(it.get("reason") or ""), red.mapping if red else {}), None)
    return out, tier, planner


def _model_plan_local(root, files, rows, subs):
    from .router import local

    lines = [f"Existing folders: {', '.join(subs[:40]) or 'none'}", "Files:"]
    for n, de in enumerate(files[:200], 1):
        row = rows.get(de.name) or {}
        lines.append(f"{n}. {de.name} | {row.get('category') or 'unknown'}")
    data = local.chat(TREE_SYSTEM, "\n".join(lines), TREE_SCHEMA, max_tokens=24 * min(len(files), 200) + 50)
    out = {}
    for it in data.get("items", []):
        try:
            de = files[int(it["i"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        folder = str(it.get("folder") or "").strip().strip("/")
        if ".." in folder.split("/") or folder.startswith("/"):
            continue
        out[de.name] = (folder or None, str(it.get("reason") or ""), None)
    return out, "T2", local.LLM


# ---------------------------------------------------------------- apply


def apply(plan_id: str, items: list[dict]) -> dict:
    """Run the moves that the user kept (with their edits) as one journal group."""
    plan = PLANS.pop(plan_id, None)
    if plan is None:
        raise ValueError("This plan is gone. Suggest again.")
    root = plan.root
    allowed = {m.src for m in plan.moves}
    g = ops.Group("organize", f"Organize {os.path.basename(root)}")
    done, errors = 0, []
    try:
        for it in items:
            src, dst = os.path.abspath(it["src"]), os.path.abspath(it["dst"])
            if src not in allowed:
                errors.append(f"{src} is not in the plan")
                continue
            if not dst.startswith(root.rstrip("/") + "/"):
                errors.append(f"{dst} is outside {root}")
                continue
            if not os.path.lexists(src):
                errors.append(f"{os.path.basename(src)} is gone")
                continue
            d = os.path.dirname(dst)
            # Create the missing folders, one level at a time, in the same group.
            missing = []
            cur = d
            while not os.path.isdir(cur):
                missing.append(cur)
                cur = os.path.dirname(cur)
            for m in reversed(missing):
                os.mkdir(m)
                g.add("create", None, m)
            if os.path.lexists(dst):
                dst = ops.unique_path(dst)
            os.rename(src, dst)
            g.add("move", src, dst)
            done += 1
    finally:
        g.commit()
    from . import learn

    learned = learn.note_plan_edits(root, plan.moves, items)
    return {"moved": done, "errors": errors, "label": g.label, "journal": ops.peek(), "learned": learned}


def public(plan: OrgPlan) -> dict:
    return {
        "id": plan.id, "root": plan.root, "tier": plan.tier, "planner": plan.planner, "ms": plan.ms,
        "warnings": plan.warnings, "skipped": plan.skipped,
        "moves": [{"src": m.src, "dst": m.dst, "reason": m.reason, "category": m.category, "rename": m.rename, "move": m.move}
                  for m in plan.moves],
        "before": _tree(plan.root),
    }


def _tree(root: str) -> list[dict]:
    out = []
    try:
        for de in sorted(os.scandir(root), key=lambda d: (not d.is_dir(), d.name.lower())):
            if de.name.startswith("."):
                continue
            out.append({"name": de.name, "is_dir": de.is_dir(), "path": de.path})
    except OSError:
        pass
    return out
