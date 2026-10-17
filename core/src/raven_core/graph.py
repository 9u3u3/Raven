"""File graph (PLAN2 P2-7): files are dots, links are lines, like the Obsidian graph.

Link types:
  ref      the text points to the file: Markdown links, [[wikilinks]], HTML href/src, LaTeX \\include,
           Python and JavaScript imports
  mention  the text names another indexed file ("see budget_v2.xlsx")
  similar  the closest files by meaning (the file vectors that search and the file map use)
  same     the same content: exact duplicates, or one document in two formats (report.docx, report.pdf)

The edges live in graph_edges and are built again when the index changed (a background build, as the file map).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import defaultdict
from urllib.parse import unquote

from . import db, events

log = logging.getLogger("raven.graph")

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_edges (src INTEGER, dst INTEGER, type TEXT, weight REAL);
CREATE INDEX IF NOT EXISTS graph_src ON graph_edges(src);
CREATE INDEX IF NOT EXISTS graph_dst ON graph_edges(dst);
"""
TYPES = ("ref", "mention", "similar", "same")
SIM_TOP = 3
SIM_MIN = float(os.environ.get("RAVEN_GRAPH_SIM_MIN", "0.0"))  # 0: a threshold from the data (see _sim_threshold)
TEXT_MAX = 200_000
NODE_LIMIT = 3000

_MD_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_WIKI = re.compile(r"\[\[([^\]|#\n]+)(?:[#|][^\]\n]*)?\]\]")
_HTML = re.compile(r"""(?:href|src)\s*=\s*["']([^"'#?]+)""", re.I)
_TEX = re.compile(r"\\(?:input|include|includegraphics|bibliography)(?:\[[^\]]*\])?\{([^}]+)\}")
_PY = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import|import\s+([\w.]+))", re.M)
_JS = re.compile(r"""(?:import\s[^'"\n]*?from\s*|import\s*\(\s*|require\s*\(\s*|^\s*import\s+)['"](\.{1,2}/[^'"]+)['"]""", re.M)
_MENTION = re.compile(r"[\w][\w\-. ()]{1,80}?\.(?:pdf|docx?|xlsx?|pptx?|odt|ods|odp|md|txt|csv|json|png|jpe?g|gif|svg|zip|"
                      r"py|js|ts|tsx|ipynb|tex|html?|mp4|mp3|wav|epub)\b", re.I)
_JS_EXT = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte", "/index.ts", "/index.tsx", "/index.js")

_state = {"building": False, "error": None, "built": 0.0, "signature": None, "edge_count": 0}
_lock = threading.Lock()


def init() -> None:
    db.conn().executescript(SCHEMA)


def _signature() -> str:
    c = db.conn()
    a = c.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(SUM(mtime), 0) FROM files WHERE is_dir=0").fetchone()
    b = c.execute("SELECT COUNT(*) FROM chunks WHERE embedded=1").fetchone()
    return f"{a[0]}:{a[1]}:{a[2]}:{b[0]}"


# ---------------------------------------------------------------- reference extraction


def references(text: str, ext: str) -> list[tuple[str, str]]:
    """(kind, target) pairs: kind "path" (relative or absolute), "wiki" (a note name), "py" (a module)."""
    out: list[tuple[str, str]] = []
    for m in _MD_LINK.finditer(text):
        t = m.group(1)
        if not re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):  # http:, mailto:, data:
            out.append(("path", unquote(t.split("#", 1)[0])))
    for m in _WIKI.finditer(text):
        out.append(("wiki", m.group(1).strip()))
    if ext in ("html", "htm", "md", "markdown", "svg", "xml"):
        for m in _HTML.finditer(text):
            t = m.group(1)
            if not re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):
                out.append(("path", unquote(t)))
    if ext in ("tex", "bib"):
        for m in _TEX.finditer(text):
            for t in m.group(1).split(","):
                out.append(("path", t.strip()))
    if ext in ("py", "ipynb"):
        for m in _PY.finditer(text):
            mod = m.group(1) or m.group(2)
            if mod:
                out.append(("py", mod))
    if ext in ("js", "jsx", "ts", "tsx", "mjs", "vue", "svelte"):
        for m in _JS.finditer(text):
            out.append(("path", m.group(1)))
    return [(k, t) for k, t in out if t and len(t) < 300]


class _Index:
    def __init__(self, rows) -> None:
        self.by_path: dict[str, int] = {}
        self.by_name: dict[str, list[int]] = defaultdict(list)
        self.by_stem: dict[str, list[int]] = defaultdict(list)
        self.path_of: dict[int, str] = {}
        for r in rows:
            fid, path, name = r["id"], r["path"], r["name"]
            self.by_path[path] = fid
            self.path_of[fid] = path
            self.by_name[name.lower()].append(fid)
            self.by_stem[name.rsplit(".", 1)[0].lower()].append(fid)

    def nearest(self, ids: list[int], near: str) -> int | None:
        """Of several files with one name, the one closest to `near` (the longest common path)."""
        if not ids:
            return None
        if len(ids) == 1:
            return ids[0]
        return max(ids, key=lambda i: len(os.path.commonpath([self.path_of[i], near])))

    def resolve(self, kind: str, target: str, src_path: str) -> int | None:
        d = os.path.dirname(src_path)
        if kind == "path":
            base = os.path.normpath(target if os.path.isabs(target) else os.path.join(d, target))
            for suf in _JS_EXT:
                fid = self.by_path.get(base + suf)
                if fid is not None:
                    return fid
            # A Markdown link to "Note" or "Note.md" in a vault: the name alone.
            return self.nearest(self.by_name.get(os.path.basename(base).lower(), []), d)
        if kind == "wiki":
            t = target.lower()
            return self.nearest(self.by_stem.get(t, []) or self.by_name.get(t, []), d)
        if kind == "py":
            dots = len(target) - len(target.lstrip("."))
            mod = target.lstrip(".").replace(".", "/")
            bases = [d] if dots <= 1 else [os.path.normpath(os.path.join(d, *([".."] * (dots - 1))))]
            if not dots:
                # An absolute import: look upward for a folder that holds the module.
                cur = d
                for _ in range(6):
                    bases.append(cur)
                    cur = os.path.dirname(cur)
            for b in bases:
                for cand in (f"{b}/{mod}.py", f"{b}/{mod}/__init__.py"):
                    fid = self.by_path.get(os.path.normpath(cand))
                    if fid is not None:
                        return fid
        return None


# ---------------------------------------------------------------- build


def _text_of(c, fid: int) -> str:
    out, size = [], 0
    for (t,) in c.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY id", (fid,)):
        out.append(t or "")
        size += len(t or "")
        if size > TEXT_MAX:
            break
    return "\n".join(out)


def _raw_text(path: str, ext: str) -> str:
    """Code projects are indexed by name only: read small text files directly for their imports."""
    if ext not in ("py", "js", "jsx", "ts", "tsx", "mjs", "vue", "svelte", "md", "markdown", "html", "htm", "tex"):
        return ""
    try:
        if os.path.getsize(path) > TEXT_MAX:
            return ""
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _sim_threshold(top1: list[float]) -> float:
    """The cosine above which two files count as similar. Models differ (embeddinggemma scores higher than a
    static model), so the threshold comes from the data: the median of each file's best match."""
    if SIM_MIN:
        return SIM_MIN
    if not top1:
        return 1.0
    s = sorted(top1)
    return max(0.3, s[len(s) // 2])


def build() -> dict:
    with _lock:
        if _state["building"]:
            return status()
        _state.update(building=True, error=None)
    t0 = time.perf_counter()
    try:
        init()
        c = db.conn()
        rows = c.execute("SELECT id, path, name, parent, ext, light FROM files WHERE is_dir=0").fetchall()
        ix = _Index(rows)
        edges: dict[tuple[int, int, str], float] = {}

        def add(a: int, b: int, kind: str, w: float = 1.0) -> None:
            if a == b:
                return
            key = (min(a, b), max(a, b), kind) if kind in ("similar", "same") else (a, b, kind)
            edges[key] = max(edges.get(key, 0.0), w)

        # ref and mention
        names = {n for n in ix.by_name if len(n) >= 5}
        for r in rows:
            ext = (r["ext"] or "").lower()
            text = _raw_text(r["path"], ext) if r["light"] else _text_of(c, r["id"])
            if not text and ext in ("py", "js", "ts", "tsx", "jsx", "md", "html", "tex"):
                text = _raw_text(r["path"], ext)
            if not text:
                continue
            for kind, target in references(text, ext):
                fid = ix.resolve(kind, target, r["path"])
                if fid is not None:
                    add(r["id"], fid, "ref")
            if r["light"]:
                continue
            for m in _MENTION.finditer(text[:50_000]):
                # The longest file name at the end of the match: "see budget v2.xlsx" tries "see budget v2.xlsx",
                # then "budget v2.xlsx", then "v2.xlsx".
                words = m.group(0).strip().lower().replace("(", " ").split()
                for k in range(len(words)):
                    cand = " ".join(words[k:])
                    if cand in names:
                        fid = ix.nearest(ix.by_name[cand], os.path.dirname(r["path"]))
                        if fid is not None:
                            add(r["id"], fid, "mention")
                        break

        # similar
        try:
            from .galaxy import _vectors

            import numpy as np

            files, X = _vectors()
            if len(files) >= 2:
                top1, cands = [], []
                for i0 in range(0, len(files), 1024):
                    S = X[i0:i0 + 1024] @ X.T
                    for k in range(S.shape[0]):
                        S[k, i0 + k] = -1.0
                    idx = np.argpartition(-S, min(SIM_TOP, S.shape[1] - 1), axis=1)[:, :SIM_TOP]
                    for k in range(S.shape[0]):
                        best = sorted(((float(S[k, j]), int(j)) for j in idx[k]), reverse=True)
                        top1.append(best[0][0])
                        cands.append((i0 + k, best))
                th = _sim_threshold(top1)
                for i, best in cands:
                    for sim, j in best:
                        if sim >= th:
                            add(files[i]["id"], files[j]["id"], "similar", round(sim, 3))
        except Exception:  # noqa: BLE001 - no vectors yet: the other links still count
            log.exception("graph: similar links failed")

        # same: exact duplicates, then one document in two formats
        try:
            from .storage.dupes import job

            for g in job.groups:
                if g.get("kind") == "near":
                    continue
                ids = [ix.by_path.get(f["path"]) for f in g["files"]]
                ids = [i for i in ids if i is not None]
                for a in ids[1:]:
                    add(ids[0], a, "same")
        except Exception:  # noqa: BLE001
            pass
        by_stem: dict[tuple[str, str], list[int]] = defaultdict(list)
        for r in rows:
            if (r["ext"] or "").lower() in ("pdf", "docx", "doc", "odt", "pptx", "ppt", "odp", "xlsx", "ods", "md", "html", "tex"):
                by_stem[(r["parent"], r["name"].rsplit(".", 1)[0].lower())].append(r["id"])
        for ids in by_stem.values():
            for a in ids[1:]:
                add(ids[0], a, "same", 0.8)

        with db.tx() as tc:
            tc.execute("DELETE FROM graph_edges")
            tc.executemany("INSERT INTO graph_edges VALUES (?,?,?,?)", [(a, b, k, w) for (a, b, k), w in edges.items()])
        _state.update(built=time.time(), signature=_signature(), edge_count=len(edges))
        log.info("graph: %d edges for %d files in %.0f ms", len(edges), len(rows), (time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        log.exception("graph build failed")
        _state["error"] = str(e)[:200]
    finally:
        _state["building"] = False
        events.publish("graph_ready", **status())
    return status()


def build_async() -> dict:
    if not _state["building"]:
        threading.Thread(target=build, name="graph", daemon=True).start()
    return status()


def status() -> dict:
    return {k: _state[k] for k in ("building", "error", "built", "edge_count")} | {"stale": _state["signature"] != _signature()}


# ---------------------------------------------------------------- queries


def _inside(path: str, root: str) -> bool:
    from .platform import inside

    return inside(path, root)


def graph(root: str | None = None, types: list[str] | None = None, limit: int = NODE_LIMIT, orphans: bool = False) -> dict:
    """Nodes and edges below `root`. Too many files: one node per folder (level "folder"); a click on a
    folder asks again with that folder as the root."""
    init()
    if _state["signature"] != _signature() and not _state["building"]:
        if not _state["built"]:
            build()  # the first call waits for the graph
        else:
            build_async()
    types = [t for t in (types or list(TYPES)) if t in TYPES]
    c = db.conn()
    files = {r["id"]: dict(r) for r in c.execute("SELECT id, path, name, parent, ext, size, category FROM files WHERE is_dir=0")}
    if root:
        root = os.path.abspath(os.path.expanduser(root))
        files = {k: v for k, v in files.items() if _inside(v["path"], root)}
    q = f"SELECT src, dst, type, weight FROM graph_edges WHERE type IN ({','.join('?' * len(types))})"
    edges = [dict(src=a, dst=b, type=t, w=w) for a, b, t, w in c.execute(q, types) if a in files and b in files]
    degree: dict[int, int] = defaultdict(int)
    for e in edges:
        degree[e["src"]] += 1
        degree[e["dst"]] += 1
    ids = set(files) if orphans else set(degree)
    from .fs.kinds import group_of

    if len(ids) <= limit:
        nodes = [{"id": i, "path": files[i]["path"], "name": files[i]["name"], "group": group_of(files[i]["name"], False),
                  "size": files[i]["size"], "degree": degree.get(i, 0), "folder": files[i]["parent"], "category": files[i]["category"]}
                 for i in ids]
        return {"level": "file", "root": root, "nodes": nodes, "edges": [e for e in edges if e["src"] in ids and e["dst"] in ids],
                "types": types, **status()}
    # Folder level: the first folder below the root for each file.
    base = root if root else (os.path.commonpath([files[i]["path"] for i in ids]) if ids else "/")

    def folder_of(p: str) -> str:
        rel = os.path.relpath(os.path.dirname(p), base)
        top = rel.split(os.sep, 1)[0]
        return base if top in (".", "") else os.path.join(base, top)

    fmap = {i: folder_of(files[i]["path"]) for i in ids}
    count: dict[str, int] = defaultdict(int)
    for f in fmap.values():
        count[f] += 1
    agg: dict[tuple[str, str], float] = defaultdict(float)
    for e in edges:
        a, b = fmap.get(e["src"]), fmap.get(e["dst"])
        if a and b and a != b:
            agg[tuple(sorted((a, b)))] += 1
    nodes = [{"id": f, "path": f, "name": os.path.basename(f) or f, "group": "folder", "size": None, "degree": n, "folder": f,
              "files": n} for f, n in count.items()]
    return {"level": "folder", "root": base, "nodes": nodes,
            "edges": [{"src": a, "dst": b, "type": "folder", "w": w} for (a, b), w in agg.items()], "types": types, **status()}


def local(path: str, depth: int = 1, types: list[str] | None = None) -> dict:
    """One file and its links, `depth` steps out (1 or 2)."""
    init()
    if _state["signature"] != _signature() and not _state["building"] and not _state["built"]:
        build()
    types = [t for t in (types or list(TYPES)) if t in TYPES]
    c = db.conn()
    row = c.execute("SELECT id FROM files WHERE path=?", (os.path.abspath(path),)).fetchone()
    if not row:
        return {"level": "file", "nodes": [], "edges": [], "center": None}
    seen = {row["id"]}
    frontier = {row["id"]}
    edges = []
    tq = ",".join("?" * len(types))
    for _ in range(max(1, min(depth, 2))):
        if not frontier:
            break
        fq = ",".join("?" * len(frontier))
        found = c.execute(f"SELECT src, dst, type, weight FROM graph_edges WHERE type IN ({tq}) AND (src IN ({fq}) OR dst IN ({fq}))",
                          [*types, *frontier, *frontier]).fetchall()
        nxt = set()
        for a, b, t, w in found:
            edges.append({"src": a, "dst": b, "type": t, "w": w})
            for x in (a, b):
                if x not in seen:
                    nxt.add(x)
        seen |= nxt
        frontier = nxt
    from .fs.kinds import group_of

    rows = c.execute(f"SELECT id, path, name, parent, size FROM files WHERE id IN ({','.join('?' * len(seen))})", list(seen)).fetchall()
    degree: dict[int, int] = defaultdict(int)
    uniq = {(e["src"], e["dst"], e["type"]): e for e in edges}
    for e in uniq.values():
        degree[e["src"]] += 1
        degree[e["dst"]] += 1
    nodes = [{"id": r["id"], "path": r["path"], "name": r["name"], "group": group_of(r["name"], False), "size": r["size"],
              "degree": degree.get(r["id"], 0), "folder": r["parent"]} for r in rows]
    return {"level": "file", "nodes": nodes, "edges": list(uniq.values()), "center": row["id"]}


# ---------------------------------------------------------------- one folder, on request

PATH_LIMIT = 1500  # files in one path graph
PATH_TYPES = ("folder", *TYPES)
_TEXTISH = ("txt", "md", "markdown", "py", "js", "jsx", "ts", "tsx", "mjs", "vue", "svelte", "html", "htm", "tex", "csv", "json", "rst")


def for_path(path: str, types: list[str] | None = None, limit: int = PATH_LIMIT) -> dict:
    """The graph of one folder that the user names. Raven reads only that folder: it walks the disk below it,
    makes a node for each folder and file, and links them by content. The global graph is not needed.

    folder   a folder holds a file or a subfolder (the shape of the tree)
    ref      a link, wikilink or import in the text
    mention  the text names another file of the folder
    similar  close meaning (only for indexed files, from their chunk vectors)
    same     the same content (size and BLAKE3), or one document in two formats
    """
    from .config import EXCLUDE_NAMES
    from .fs.kinds import group_of

    root = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Not a folder: {path}")
    types = [t for t in (types or list(PATH_TYPES)) if t in PATH_TYPES]
    files: list[dict] = []
    dirs: list[str] = [root]
    cut = False
    for d, sub, names in os.walk(root):
        sub[:] = sorted(s for s in sub if not s.startswith(".") and s not in EXCLUDE_NAMES and not os.path.islink(os.path.join(d, s)))
        dirs.extend(os.path.join(d, s) for s in sub)
        for n in sorted(names):
            p = os.path.join(d, n)
            if n.startswith(".") or os.path.islink(p):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            files.append({"id": p, "path": p, "name": n, "parent": d, "ext": n.rsplit(".", 1)[-1].lower() if "." in n else "", "size": size})
            if len(files) >= limit:
                cut = True
                break
        if cut:
            break

    edges: dict[tuple[str, str, str], float] = {}

    def add(a: str, b: str, kind: str, w: float = 1.0) -> None:
        if a != b and kind in types:
            key = (min(a, b), max(a, b), kind) if kind in ("similar", "same") else (a, b, kind)
            edges[key] = max(edges.get(key, 0.0), w)

    seen_dirs = {f["parent"] for f in files} | {root}
    for d in dirs:
        if d != root and any(_inside(x, d) for x in seen_dirs):
            add(os.path.dirname(d), d, "folder")
    for f in files:
        add(f["parent"], f["id"], "folder")

    # Text: the index has it for indexed files. Small text files are read from the disk.
    c = db.conn()
    from .platform import subtree_range

    lo, hi = subtree_range(root)
    indexed = {r["path"]: r["id"] for r in c.execute("SELECT id, path FROM files WHERE is_dir=0 AND path >= ? AND path < ?", (lo, hi))}
    ix = _Index(files)
    if "ref" in types or "mention" in types:
        names = {n for n in ix.by_name if len(n) >= 5}
        for f in files:
            fid = indexed.get(f["path"])
            text = _text_of(c, fid) if fid is not None else ""
            if not text and f["ext"] in _TEXTISH and f["size"] <= TEXT_MAX:
                try:
                    with open(f["path"], encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    text = ""
            if not text:
                continue
            for kind, target in references(text, f["ext"]):
                t = ix.resolve(kind, target, f["path"])
                if t is not None:
                    add(f["id"], t, "ref")
            for m in _MENTION.finditer(text[:50_000]):
                words = m.group(0).strip().lower().replace("(", " ").split()
                for k in range(len(words)):
                    cand = " ".join(words[k:])
                    if cand in names:
                        t = ix.nearest(ix.by_name[cand], f["parent"])
                        if t is not None:
                            add(f["id"], t, "mention")
                        break

    if "similar" in types and len(indexed) >= 2:
        try:
            import numpy as np

            ids, vecs = [], []
            for p, fid in indexed.items():
                if p not in ix.by_path:
                    continue
                blobs = c.execute("SELECT v.embedding FROM chunks ch JOIN chunk_vec v ON v.rowid = ch.id WHERE ch.file_id=? ORDER BY ch.id LIMIT 4",
                                  (fid,)).fetchall()
                if blobs:
                    m = np.mean([np.frombuffer(b[0], dtype=np.float32) for b in blobs], axis=0)
                    vecs.append(m / (np.linalg.norm(m) or 1.0))
                    ids.append(p)
            if len(ids) >= 2:
                X = np.array(vecs, dtype=np.float32)
                S = X @ X.T
                np.fill_diagonal(S, -1.0)
                k = min(SIM_TOP, len(ids) - 1)
                best = np.argsort(-S, axis=1)[:, :k]
                th = _sim_threshold([float(S[i, best[i, 0]]) for i in range(len(ids))])
                for i in range(len(ids)):
                    for j in best[i]:
                        if S[i, j] >= th:
                            add(ids[i], ids[int(j)], "similar", round(float(S[i, j]), 3))
        except Exception:  # noqa: BLE001 - no vectors: the other links still count
            log.exception("path graph: similar links failed")

    if "same" in types:
        by_size: dict[int, list[dict]] = defaultdict(list)
        for f in files:
            if 0 < f["size"] <= 200 * 1024 * 1024:
                by_size[f["size"]].append(f)
        import blake3

        for group in by_size.values():
            if len(group) < 2:
                continue
            by_hash: dict[str, list[str]] = defaultdict(list)
            for f in group:
                try:
                    h = blake3.blake3()
                    with open(f["path"], "rb") as fh:
                        for block in iter(lambda: fh.read(1 << 20), b""):
                            h.update(block)
                    by_hash[h.hexdigest()].append(f["id"])
                except OSError:
                    continue
            for same in by_hash.values():
                for a in same[1:]:
                    add(same[0], a, "same")
        by_stem: dict[tuple[str, str], list[str]] = defaultdict(list)
        for f in files:
            if f["ext"] in ("pdf", "docx", "doc", "odt", "pptx", "ppt", "odp", "xlsx", "ods", "md", "html", "tex"):
                by_stem[(f["parent"], f["name"].rsplit(".", 1)[0].lower())].append(f["id"])
        for same in by_stem.values():
            for a in same[1:]:
                add(same[0], a, "same", 0.8)

    out_edges = [{"src": a, "dst": b, "type": k, "w": w} for (a, b, k), w in edges.items()]
    degree: dict[str, int] = defaultdict(int)
    for e in out_edges:
        if e["type"] != "folder":
            degree[e["src"]] += 1
            degree[e["dst"]] += 1
    used = {e["src"] for e in out_edges} | {e["dst"] for e in out_edges}
    nodes = [{"id": d, "path": d, "name": os.path.basename(d) or d, "group": "folder", "size": None, "degree": degree.get(d, 0),
              "folder": os.path.dirname(d), "files": 0} for d in dirs if d in used or d == root]
    count: dict[str, int] = defaultdict(int)
    for f in files:
        count[f["parent"]] += 1
    for n in nodes:
        n["files"] = count.get(n["path"], 0)
    keep = used if "folder" not in types else {f["id"] for f in files}
    nodes += [{"id": f["id"], "path": f["path"], "name": f["name"], "group": group_of(f["name"], False), "size": f["size"],
               "degree": degree.get(f["id"], 0), "folder": f["parent"]} for f in files if f["id"] in keep]
    return {"level": "file", "root": root, "nodes": nodes, "edges": out_edges, "types": types, "cut": cut,
            "files": len(files), "indexed": len(indexed)}
