"""The agent (section 9.10): an abstract plan becomes concrete steps, a review, then a run.

Safety limits:
 - Raven touches only paths inside the indexed roots, the folder in view, and folders the
   command names. System folders are never touched.
 - At most MAX_STEPS steps and MAX_FILES files per plan.
 - Trash and overwrite steps need a second confirm in the review.
 - One journal group covers the whole command, so one undo reverses it.
 - There is no shell tool. The agent can only call the tools below.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..privacy.scope import is_private
from .. import db
from ..compress import jobs as compress_jobs
from ..convert import registry as conv
from ..fs import ops
from ..fs.kinds import ext_of
from ..index.indexer import roots as index_roots

MAX_STEPS = 20
MAX_FILES = 2000
FIND_DEPTH = 4
SYSTEM_DIRS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run", "/sbin", "/sys", "/usr", "/var", "/opt")
ARCHIVE_EXTS = ("zip", "7z", "tar", "gz", "tgz", "zst", "xz", "bz2")


class PlanError(Exception):
    pass


@dataclass
class Item:
    src: str | None  # the input path (None for mkdir)
    dst: str | None  # the predicted result path
    size: int = 0
    is_dir: bool = False


@dataclass
class Step:
    op: str
    label: str
    items: list[Item]
    to: str = ""
    detail: str = ""
    confirm: bool = False
    input: int | None = None  # the number of the step whose results this step uses (1-based)

    def outputs(self) -> list[str]:
        if self.op in ("find", "selection"):
            return [i.src for i in self.items if i.src]
        if self.op == "trash":
            return []
        return [i.dst for i in self.items if i.dst]


@dataclass
class Plan:
    id: str
    command: str
    steps: list[Step]
    tier: str
    planner: str
    ms: int
    cloud_bytes: int = 0
    warnings: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def public(self, home: str) -> dict:
        shown = [s for s in self.steps if s.op not in ("find", "selection")]
        return {
            "id": self.id, "command": self.command, "tier": self.tier, "planner": self.planner, "ms": self.ms,
            "cloud_bytes": self.cloud_bytes, "warnings": self.warnings,
            "steps": [
                {
                    "n": self.steps.index(s) + 1, "op": s.op, "label": s.label, "detail": s.detail, "confirm": s.confirm,
                    "files": [{"path": i.src or i.dst, "dst": i.dst, "size": i.size, "is_dir": i.is_dir,
                               "note": _note(s, i, home)} for i in s.items],
                }
                for s in shown
            ],
        }


def _note(s: Step, i: Item, home: str) -> str:
    def t(p: str | None) -> str:
        if not p:
            return ""
        return "~" + p[len(home):] if home and p.startswith(home + "/") else p
    if s.op in ("convert", "move", "copy", "compress", "extract"):
        return "→ " + t(i.dst)
    if s.op == "rename":
        return "→ " + os.path.basename(i.dst or "")
    return ""


PLANS: dict[str, Plan] = {}


# ---------------------------------------------------------------- context and limits


@dataclass
class Context:
    cwd: str
    selection: list[str]
    command: str
    home: str = field(default_factory=lambda: str(Path.home()))
    named: list[str] = field(default_factory=list)  # folders the command names, resolved

    def allowed(self, path: str) -> bool:
        p = os.path.abspath(path)
        from ..fs.network import gvfs_dir

        # gvfs mounts the user's shares below /run/user/<uid>/gvfs: a share, not a system folder.
        user_share = _within(p, str(gvfs_dir())) and p != str(gvfs_dir())
        if any(p == d or p.startswith(d + "/") for d in SYSTEM_DIRS) and not p.startswith(self.home + "/") and not user_share:
            return False
        from ..storage.protect import user_protected
        from ..privacy.scope import is_private

        if user_protected(p) or is_private(p):
            return False  # a folder rule (PLAN2 P2-5), or a private folder (PLAN4 7.6)
        bases = [*index_roots(), self.cwd, *self.named, *[os.path.dirname(s) for s in self.selection]]
        return any(p == b or p.startswith(b.rstrip("/") + "/") for b in bases if b)


def _within(p: str, base: str) -> bool:
    return p == base or p.startswith(base.rstrip("/") + "/")


# ---------------------------------------------------------------- folder names


def resolve_folder(name: str, ctx: Context) -> str | None:
    """'Sem5', 'Documents/Sem5', '~/x' or an absolute path -> a folder path.

    Order: an absolute or ~ path, then a path below the folder in view, then a folder of
    that name in the index (the one nearest to the folder in view), then below home.
    """
    name = re.sub(r"^(?:the|my)\s+", "", name.strip().strip("'\"").rstrip("/"), flags=re.I)
    if not name:
        return ctx.cwd
    if name.lower() in ("home", "home folder", "my home"):
        return ctx.home
    found = _resolve_folder(name, ctx)
    if found is None and re.search(r"\s+(folder|directory)$", name, re.I):
        # "the Projects folder": the word "folder" is not part of the name. "New Folder" is.
        found = _resolve_folder(re.sub(r"\s+(folder|directory)$", "", name, flags=re.I), ctx)
    return found


def _resolve_folder(name: str, ctx: Context) -> str | None:
    if name.lower() in ("here", "this folder", "current folder", "the current folder"):
        return ctx.cwd
    if name.startswith("~"):
        p = os.path.expanduser(name)
        return p if os.path.isdir(p) else None
    if name.startswith("/"):
        return name if os.path.isdir(name) else None
    # The folder in view, or one of its parents, by name.
    anc = ctx.cwd
    while anc and anc != "/":
        if os.path.basename(anc).lower() == name.lower():
            return anc
        anc = os.path.dirname(anc)
    for base in (ctx.cwd, ctx.home):
        p = _ci_join(base, name)
        if p and os.path.isdir(p):
            return p
    near = _below(ctx.cwd, name)
    if near:
        return near
    # A folder of that name in the index. The last part must match, the rest must appear in the path.
    parts = name.split("/")
    rows = db.conn().execute(
        "SELECT path FROM files WHERE is_dir=1 AND name = ? COLLATE NOCASE", (parts[-1],)
    ).fetchall()
    cands = [r[0] for r in rows if "/".join(parts).lower() in r[0].lower() and os.path.isdir(r[0])]
    if cands:
        def dist(p: str) -> tuple:
            common = os.path.commonpath([p, ctx.cwd]) if ctx.cwd else "/"
            return (-len(common), p.count("/"), p)
        return sorted(cands, key=dist)[0]
    return None


def _below(base: str, name: str, depth: int = 3, budget: int = 2000) -> str | None:
    """A folder named `name` (or ending in the path `name`) up to `depth` levels below `base`, nearest first."""
    if not base or not os.path.isdir(base):
        return None
    level = [base]
    for _ in range(depth):
        nxt = []
        for d in level:
            try:
                subs = sorted(e.path for e in os.scandir(d) if e.is_dir(follow_symlinks=False)
                              and not e.name.startswith(".") and e.name not in ("node_modules", "__pycache__")
                              and not is_private(e.path))
            except OSError:
                continue
            for p in subs:
                if p.lower().endswith("/" + name.lower()):
                    return p
            nxt += subs
            budget -= len(subs)
            if budget <= 0:
                return None
        level = nxt
    return None


def _ci_join(base: str, rel: str) -> str | None:
    """Join `rel` to `base` and match each part without regard to case."""
    cur = base
    for part in rel.split("/"):
        if not part or part == ".":
            continue
        exact = os.path.join(cur, part)
        if os.path.isdir(exact):
            cur = exact
            continue
        try:
            match = next((n for n in os.listdir(cur) if n.lower() == part.lower() and os.path.isdir(os.path.join(cur, n))), None)
        except OSError:
            return None
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur


# ---------------------------------------------------------------- find


def _find(st: dict, ctx: Context) -> list[Item]:
    folder_name = st.get("folder") or ""
    folder = resolve_folder(folder_name, ctx) if folder_name else ctx.cwd
    if folder is None:
        raise PlanError(f"Raven did not find a folder named '{folder_name}'")
    if folder_name:
        ctx.named.append(folder)
    if st.get("self"):
        return [Item(folder, folder, _size(folder), True)]
    name = (st.get("name") or "").strip()
    if name:
        p = os.path.expanduser(name) if name.startswith(("~", "/")) else os.path.join(folder, name)
        if os.path.lexists(p):
            return [Item(p, p, _size(p), os.path.isdir(p))]
        # Not in the folder in view: a file of that name in the index.
        row = db.conn().execute("SELECT path FROM files WHERE name = ? COLLATE NOCASE ORDER BY mtime DESC LIMIT 1",
                                (os.path.basename(name),)).fetchone()
        if row and os.path.lexists(row[0]):
            ctx.named.append(os.path.dirname(row[0]))
            return [Item(row[0], row[0], _size(row[0]))]
        if "." in name and " " not in name:
            raise PlanError(f"Raven did not find '{name}'")
        # "lecture notes" is a topic, not a file name.
        st = {**st, "about": name, "name": ""}
        return _find(st, ctx)
    types = {t.lower().lstrip(".") for t in st.get("types") or []}
    about = (st.get("about") or "").strip()
    out: list[Item] = []
    if about:
        from ..search.hybrid import search
        q = about + (" " + " ".join(f"type:{t}" for t in sorted(types)) if types else "")
        for r in search(q, limit=100)["results"]:
            if not r["is_dir"] and _within(r["path"], folder) and (not types or ext_of(r["path"]) in types):
                out.append(Item(r["path"], r["path"], r["size"] or 0))
        return out
    if not types:
        # "everything in X": the items directly in the folder.
        for de in sorted(os.scandir(folder), key=lambda d: d.name.lower()):
            if de.name.startswith(".") or is_private(de.path):
                continue
            out.append(Item(de.path, de.path, _size(de.path), de.is_dir(follow_symlinks=False)))
        return out
    base_depth = folder.rstrip("/").count("/")
    for dp, dns, fns in os.walk(folder):
        dns[:] = sorted(d for d in dns if not d.startswith(".") and d not in ("node_modules", "__pycache__")
                        and not is_private(os.path.join(dp, d)))
        if dp.count("/") - base_depth >= FIND_DEPTH:
            dns[:] = []
        for fn in sorted(fns):
            if fn.startswith("."):
                continue
            if ext_of(fn) in types:
                p = os.path.join(dp, fn)
                out.append(Item(p, p, _size(p)))
                if len(out) > MAX_FILES:
                    raise PlanError(f"More than {MAX_FILES} files match. Name a smaller folder.")
    return out


def _size(p: str) -> int:
    try:
        st = os.lstat(p)
    except OSError:
        return 0
    if not os.path.isdir(p):
        return st.st_size
    total = 0
    for dp, _, fns in os.walk(p):
        for fn in fns:
            try:
                total += os.lstat(os.path.join(dp, fn)).st_size
            except OSError:
                pass
    return total


def _unique(path: str, taken: set[str]) -> str:
    """Like ops.unique_path, but also avoids names that earlier steps of this plan will create."""
    if not os.path.lexists(path) and path not in taken:
        taken.add(path)
        return path
    stem, ext = os.path.splitext(path)
    if path.endswith(".tar.zst"):
        stem, ext = path[: -len(".tar.zst")], ".tar.zst"
    n = 2
    while True:
        cand = f"{stem} ({n}){ext}"
        if not os.path.lexists(cand) and cand not in taken:
            taken.add(cand)
            return cand
        n += 1


# ---------------------------------------------------------------- resolve


def resolve(abstract: list[dict], ctx: Context, tier: str, planner: str, ms: int, cloud_bytes: int = 0) -> Plan:
    if not abstract:
        raise PlanError("The plan is empty")
    if len(abstract) > MAX_STEPS:
        raise PlanError(f"The plan has {len(abstract)} steps. The limit is {MAX_STEPS}.")
    steps: list[Step] = []
    taken: set[str] = set()
    pending_dirs: dict[str, int] = {}  # folders that a mkdir step of this plan creates

    def inputs(st: dict) -> tuple[int, list[str]]:
        n = st.get("input")
        if not isinstance(n, int) or n < 1 or n > len(steps):
            n = len(steps)
        if n < 1:
            raise PlanError(f"Step '{st.get('op')}' has no files to work on")
        return n, steps[n - 1].outputs()

    def target_dir(name: str, near: list[str]) -> str:
        # The LLM planner can also write "a folder named Text": the name is "Text".
        name = re.sub(r"^(?:an?\s+)?(?:new\s+)?(?:folder|directory|dir)\s+(?:named|called)\s+", "", name.strip(), flags=re.I)
        p = resolve_folder(name, ctx)
        if p:
            ctx.named.append(p)
            return p
        if name.startswith(("/", "~")):
            p = os.path.expanduser(name)
        else:
            # A new folder: next to the source folder, or in the folder in view.
            src_dirs = {os.path.dirname(x) for x in near}
            base = os.path.dirname(os.path.commonpath(list(src_dirs))) if src_dirs else ctx.cwd
            # Files directly in the folder in view: the new folder goes inside it, not next to it.
            if src_dirs == {ctx.cwd}:
                base = ctx.cwd
            if base == ctx.home or not base or not ctx.allowed(base):
                base = ctx.cwd
            if "/" in name:
                head, tail = name.rsplit("/", 1)
                parent = resolve_folder(head, ctx)
                if parent:
                    base, name = parent, tail
            p = os.path.join(base, name)
        if p not in pending_dirs:
            if not ctx.allowed(os.path.dirname(p)):
                raise PlanError(f"Raven may not create {p}")
            ctx.named.append(p)
            steps.insert(len(steps), Step("mkdir", f"Create folder {os.path.basename(p)}", [Item(None, p)], to=p))
            pending_dirs[p] = len(steps)
        return p

    for st in abstract:
        op = st.get("op")
        if op == "selection":
            if not ctx.selection:
                raise PlanError("Select files first, or name them in the command")
            steps.append(Step("selection", "Selected files", [Item(p, p, _size(p), os.path.isdir(p)) for p in ctx.selection]))
        elif op == "find":
            items = _find(st, ctx)
            if not items:
                what = ", ".join(st.get("types") or []) or st.get("name") or st.get("about") or "files"
                raise PlanError(f"No {what} found in {st.get('folder') or 'this folder'}")
            steps.append(Step("find", "Find", items))
        elif op == "convert":
            n, srcs = inputs(st)
            fmt = str(st.get("to", "")).lower().lstrip(".")
            items, skipped = [], 0
            for s in srcs:
                if conv.route_for(ext_of(s), fmt) is None:
                    skipped += 1
                    continue
                p = Path(s)
                items.append(Item(s, _unique(str(p.with_name(p.stem + "." + fmt)), taken), _size(s)))
            if not items:
                raise PlanError(f"Raven cannot convert these files to {fmt.upper()}")
            detail = f"{skipped} file{'s' if skipped != 1 else ''} cannot become {fmt.upper()} and stay as they are" if skipped else ""
            exts = sorted({ext_of(i.src).upper() for i in items})
            steps.append(Step("convert", f"Convert {'/'.join(exts)} to {fmt.upper()}", items, to=fmt, detail=detail, input=n))
        elif op in ("move", "copy"):
            n, srcs = inputs(st)
            if not srcs:
                raise PlanError(f"Nothing to {op}")
            d = target_dir(str(st.get("to", "")), srcs)
            items = []
            overwrite = False
            for s in srcs:
                if os.path.dirname(s) == d and op == "move":
                    continue
                items.append(Item(s, _unique(os.path.join(d, os.path.basename(s)), taken), _size(s), os.path.isdir(s)))
            steps.append(Step(op, f"{op.title()} to {os.path.basename(d)}", items, to=d, input=n, confirm=overwrite,
                              detail="Name clashes get a number, so nothing is overwritten"))
        elif op == "rename":
            n, srcs = inputs(st)
            pattern = str(st.get("to", "")).strip()
            if not pattern:
                raise PlanError("The rename has no new name")
            items = []
            for k, s in enumerate(srcs, 1):
                new = _pattern(pattern, s, k, len(srcs))
                if "/" in new or new in (".", ".."):
                    raise PlanError(f"'{new}' is not a valid name")
                dst = os.path.join(os.path.dirname(s), new)
                if dst != s:
                    dst = _unique(dst, taken)
                items.append(Item(s, dst, _size(s), os.path.isdir(s)))
            steps.append(Step("rename", f"Rename {len(items)} item{'s' if len(items) != 1 else ''}", items, to=pattern, input=n))
        elif op == "mkdir":
            name = str(st.get("to", "")).strip()
            parent = resolve_folder(st.get("in") or "", ctx) if st.get("in") else ctx.cwd
            if parent is None:
                raise PlanError(f"Raven did not find a folder named '{st.get('in')}'")
            if "/" in name:
                head, name = name.rsplit("/", 1)
                parent = _ci_join(parent, head) or os.path.join(parent, head)
            p = _unique(os.path.join(parent, name), taken)
            ctx.named.append(p)
            steps.append(Step("mkdir", f"Create folder {os.path.basename(p)}", [Item(None, p)], to=p))
            pending_dirs[p] = len(steps)
        elif op == "trash":
            n, srcs = inputs(st)
            steps.append(Step("trash", "Move to trash", [Item(s, None, _size(s), os.path.isdir(s)) for s in srcs], input=n,
                              confirm=True, detail="Undo brings the files back from the trash"))
        elif op == "compress":
            n, srcs = inputs(st)
            method = str(st.get("to") or "7z")
            if method not in ("7z", "zip", "tar.zst"):
                method = "7z"
            root = os.path.commonpath([os.path.dirname(s) for s in srcs]) if srcs else ctx.cwd
            label = os.path.basename(srcs[0]) if len(srcs) == 1 else os.path.basename(root)
            archive = _unique(os.path.join(root, f"{Path(label).stem if len(srcs) == 1 and not os.path.isdir(srcs[0]) else label}-{time.strftime('%Y%m%d')}.{method}"), taken)
            items = [Item(s, archive, _size(s), os.path.isdir(s)) for s in srcs]
            steps.append(Step("compress", f"Compress into {os.path.basename(archive)}", items, to=method, input=n, confirm=True,
                              detail="Raven checks the archive, then moves the originals to the trash"))
        elif op == "extract":
            n, srcs = inputs(st)
            items = []
            for s in srcs:
                if not s.lower().endswith(ARCHIVE_EXTS):
                    continue
                to = str(st.get("to") or "")
                base = target_dir(to, [s]) if to else os.path.dirname(s)
                name = re.sub(r"(\.tar)?\.(zip|7z|tar|gz|tgz|zst|xz|bz2)$", "", os.path.basename(s), flags=re.I)
                items.append(Item(s, _unique(os.path.join(base, name), taken), _size(s)))
            if not items:
                raise PlanError("No archive to extract")
            steps.append(Step("extract", f"Extract {len(items)} archive{'s' if len(items) != 1 else ''}", items, input=n))
        else:
            raise PlanError(f"Unknown step '{op}'")

    # A model plan can hold a step with nothing to do, such as a move into the same folder.
    steps = [s for s in steps if s.items or s.op in ("find", "selection")]
    for s in steps:
        for i in s.items:
            for p in (i.src, i.dst):
                if p and is_private(p):
                    raise PlanError(f"{os.path.basename(p)} is in a private folder. The agent does not touch it.")
                if p and not ctx.allowed(p):
                    raise PlanError(f"{p} is outside the indexed folders and the folders that the command names")
    if sum(len(s.items) for s in steps if s.op not in ("find", "selection")) > MAX_FILES:
        raise PlanError(f"The plan changes more than {MAX_FILES} files")
    plan = Plan(uuid.uuid4().hex[:10], ctx.command, steps, tier, planner, ms, cloud_bytes)
    # Keep the last few plans only.
    for k in sorted(PLANS, key=lambda k: PLANS[k].created)[:-20]:
        PLANS.pop(k, None)
    PLANS[plan.id] = plan
    return plan


def _pattern(pattern: str, src: str, k: int, total: int) -> str:
    """'notes.md', 'lecture-{n}', '{name}-old'. Many files with a plain name get a number."""
    stem, ext = os.path.splitext(os.path.basename(src))
    if os.path.isdir(src):
        stem, ext = os.path.basename(src), ""
    out = pattern.replace("{name}", stem).replace("{n}", str(k)).replace("{date}", time.strftime("%Y-%m-%d"))
    if total > 1 and pattern == out:
        out = f"{out} {k}"
    if not os.path.splitext(out)[1] and ext:
        out += ext
    return out


# ---------------------------------------------------------------- run


def run(plan_id: str, keep: dict[int, list[str]] | None = None):
    """Run a plan. A generator: it yields {"print": jobs} when a step needs the renderer to
    print HTML to PDF, and gets the printed paths back with send(). It returns the result.

    `keep` maps a step number to the input paths that stay checked in the review."""
    plan = PLANS.pop(plan_id, None)
    if plan is None:
        raise PlanError("This plan is gone. Run the command again.")
    g = ops.Group("agent", _label(plan))
    actual: dict[str, str] = {}  # predicted path -> real path
    done: list[str] = []
    errors: list[str] = []
    try:
        for idx, s in enumerate(plan.steps, 1):
            if s.op in ("find", "selection"):
                continue
            kept = set(keep.get(idx, [])) if keep and idx in keep else None
            items = [i for i in s.items if kept is None or (i.src or i.dst) in kept]
            if s.input is not None:
                # Inputs that an earlier step did not make (unchecked or failed) drop out.
                items = [i for i in items if i.src is None or actual.get(i.src, i.src) and os.path.lexists(actual.get(i.src, i.src))]
            if not items:
                continue
            real = lambda p: actual.get(p, p)  # noqa: E731
            if s.op == "mkdir":
                for i in items:
                    if not os.path.isdir(i.dst):
                        made = ops.mkdir(os.path.dirname(i.dst), os.path.basename(i.dst), source="agent", group=g)
                        actual[i.dst] = made
            elif s.op == "convert":
                jobs, job_dst = [], {}
                for i in items:
                    res = conv.convert([real(i.src)], s.to, source="agent", group=g)
                    errors += [f"{os.path.basename(e['path'])}: {e['error']}" for e in res["errors"]]
                    if res["created"]:
                        actual[i.dst] = res["created"][0]
                        done.append(res["created"][0])
                    for j in res["print_jobs"]:
                        jobs.append(j)
                        job_dst[j["out"]] = i.dst
                if jobs:
                    # MD and HTML print to PDF in the renderer. The runner waits for the paths.
                    printed = yield {"print": jobs}
                    for p in printed or []:
                        g.add("create", None, p)
                        if p in job_dst:
                            actual[job_dst[p]] = p
                        done.append(p)
            elif s.op in ("move", "copy"):
                d = real(s.to)
                fn = ops.move if s.op == "move" else ops.copy
                for i in items:
                    out = fn([real(i.src)], d, source="agent", group=g)
                    if out:
                        actual[i.dst] = out[0]
                        if s.op == "move":
                            actual[i.src] = out[0]
                        done.append(out[0])
            elif s.op == "rename":
                for i in items:
                    out = ops.rename(real(i.src), os.path.basename(i.dst), source="agent", group=g)
                    actual[i.dst] = out
                    actual[i.src] = out
            elif s.op == "trash":
                ops.trash([real(i.src) for i in items], source="agent", group=g)
            elif s.op == "compress":
                paths = [real(i.src) for i in items]
                root = os.path.commonpath([os.path.dirname(p) for p in paths])
                try:
                    job = compress_jobs.compress(root, paths, s.to, group=g)
                except Exception as e:  # noqa: BLE001 - CompressError and tool errors
                    errors.append(str(e))
                    continue
                if job.get("archive_path"):
                    actual[items[0].dst] = job["archive_path"]
                    done.append(job["archive_path"])
            elif s.op == "extract":
                for i in items:
                    out = _extract(real(i.src), i.dst)
                    g.add("create", None, out)
                    actual[i.dst] = out
                    done.append(out)
    finally:
        g.commit()
    return {"label": g.label, "created": done, "errors": errors, "group": g.id}


def _label(plan: Plan) -> str:
    """'Convert DOCX to PDF (6), Move to Submissions (6)': the undo line names the whole command."""
    parts = [s.label + (f" ({len(s.items)})" if s.op != "mkdir" else "") for s in plan.steps if s.op not in ("find", "selection")]
    return ", ".join(parts)


def _extract(archive: str, out: str) -> str:
    os.makedirs(out)
    low = archive.lower()
    try:
        if low.endswith(".7z"):
            subprocess.run(["7z", "x", "-y", f"-o{out}", archive], check=True, capture_output=True, timeout=3600)
        elif low.endswith((".tar.zst", ".zst")) and shutil.which("tar"):
            subprocess.run(["tar", "--zstd", "-xf", archive, "-C", out], check=True, capture_output=True, timeout=3600)
        else:
            shutil.unpack_archive(archive, out)
    except Exception as e:
        shutil.rmtree(out, ignore_errors=True)
        raise PlanError(f"Raven could not extract {os.path.basename(archive)}: {e}") from e
    return out
