"""Folder search (section 13.12 item 6): find a folder by its name, its path or a description of what it holds.

Each indexed folder gets:
1. a profile: the folder name, its subfolders, the 20 most common file kinds and up to 50 file names and titles;
2. a 2-sentence summary from the local LLM, embedded with the text model (background, lowest priority);
3. a mean vector: the mean of the file vectors below it, each file weighted by its text length.

A query runs fuzzy names, BM25 on the profile and the summary, and dense search on both vectors.
RRF fuses the lists, as for files (9.2 step 5). A query that is a path goes to the path directly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
import sqlite_vec
from rapidfuzz import fuzz, process, utils

from .. import db, events
from ..fs.kinds import kind_of
from ..index import embed
from .filters import parse
from .hybrid import MIN_SIM, REL_SIM, RRF_K, _fts_query

log = logging.getLogger("raven.folders")

VERSION = 1
PROFILE_NAMES = 50
PROFILE_KINDS = 20
PROFILE_SUBDIRS = 20
# A summary is written again when the file count or the names change by more than this share.
STALE_SHARE = 0.2
# A long file must not decide the folder vector alone.
MAX_WEIGHT_CHARS = 20000
TOP_FILES = 3
REFRESH_EVERY = 10.0
# A folder vector is a mean of many files, so its similarity is lower than a chunk's. Calibrated on testdata.
FOLDER_MIN_SIM = 0.25
FOLDER_REL_SIM = 0.8
ONE_WORD_MIN_SIM = 0.45
# A subfolder that keeps this share of its parent's score is the better answer, so it goes first.
SPECIFIC_SHARE = 0.6
STOP = {"a", "an", "and", "the", "of", "for", "in", "on", "with", "to", "my", "from", "at", "by", "or", "folder",
        "folders", "dir", "directory", "where", "is", "are", "that", "this", "some", "all", "files", "file", "me"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL, parent TEXT, name TEXT NOT NULL, root TEXT,
  files INTEGER, size INTEGER, mtime INTEGER,
  sig TEXT, vec_sig TEXT,
  profile TEXT, names TEXT,
  summary TEXT, summary_basis TEXT, summary_stale INTEGER DEFAULT 1,
  updated INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS folders_fts USING fts5(
  name, path, profile, summary, tokenize='unicode61 remove_diacritics 2'
);
"""

SUMMARY_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
SUMMARY_SYSTEM = (
    "You describe a folder on a student's laptop from its name, its subfolders, the kinds of files and the file names. "
    "Reply with JSON {\"summary\": \"...\"}. The summary is exactly 2 short sentences: what the folder holds, and what "
    "it is for (a project, a course, a trip, bills and so on). Start with \"Holds\". Use the words that a person would "
    "type to find it. Do not invent facts that the names do not show. Do not talk about the task or about yourself."
)
# Signs that the model wrote about the task, not about the folder.
_BAD_SUMMARY = re.compile(r"^(okay|ok,|the user|i need|i should|i will|let me|we need)|\b(the user wants|i need to|i should)\b", re.I)


def init() -> None:
    db.conn().executescript(SCHEMA)
    if db.get_setting("folders_version") != VERSION:
        with db.tx() as c:
            c.execute("UPDATE folders SET sig=NULL, vec_sig=NULL, summary_stale=1")
        db.set_setting("folders_version", VERSION)


def _ensure_vec() -> bool:
    """Create the two folder vector tables for the current text model. Return False before the model is known."""
    dim, model = db.vec_dim(), db.get_setting("vec_model")
    if not dim:
        return False
    c = db.conn()
    have = c.execute("SELECT 1 FROM sqlite_master WHERE name='folder_vec_mean'").fetchone()
    if have and db.get_setting("folder_vec_model") == model:
        return True
    with db.tx() as tc:
        tc.execute("DROP TABLE IF EXISTS folder_vec_mean")
        tc.execute("DROP TABLE IF EXISTS folder_vec_sum")
        tc.execute(f"CREATE VIRTUAL TABLE folder_vec_mean USING vec0(embedding float[{dim}] distance_metric=cosine)")
        tc.execute(f"CREATE VIRTUAL TABLE folder_vec_sum USING vec0(embedding float[{dim}] distance_metric=cosine)")
        tc.execute("UPDATE folders SET vec_sig=NULL, summary_stale=1")
    db.set_setting("folder_vec_model", model)
    return True


def _has_vec() -> bool:
    return db.conn().execute("SELECT 1 FROM sqlite_master WHERE name='folder_vec_mean'").fetchone() is not None


# ---------------------------------------------------------------- build


def _ancestors(path: str, root: str):
    """Yield the folders above `path`, up to and without the index root."""
    root = root.rstrip("/")
    p = os.path.dirname(path)
    while p and p != root and p.startswith(root + "/"):
        yield p
        p = os.path.dirname(p)


def _aggregate() -> tuple[dict[str, dict], dict[str, list], dict[str, list[str]]]:
    """One pass over `files`: per folder, the subtree counts, the rows of its own files and its subfolders."""
    c = db.conn()
    embedded = dict(c.execute(
        "SELECT file_id, COUNT(*) FROM chunks WHERE embedded=1 GROUP BY file_id"
    ).fetchall())
    agg: dict[str, dict] = {}
    rows_by_dir: dict[str, list] = defaultdict(list)
    dirs: dict[str, str] = {}
    for r in c.execute("SELECT id, path, parent, name, size, mtime, is_dir, title, root FROM files ORDER BY mtime DESC"):
        if r["is_dir"]:
            dirs[r["path"]] = r["root"]
            continue
        rows_by_dir[r["parent"]].append(r)
        for d in _ancestors(r["path"], r["root"] or ""):
            a = agg.get(d)
            if a is None:
                a = agg[d] = {"files": 0, "size": 0, "mtime": 0, "msum": 0, "emb": 0, "root": r["root"]}
            a["files"] += 1
            a["size"] += r["size"] or 0
            a["mtime"] = max(a["mtime"], r["mtime"] or 0)
            a["msum"] = (a["msum"] + (r["mtime"] or 0) + (r["size"] or 0)) % 1_000_000_007
            a["emb"] += embedded.get(r["id"], 0)
    # Folders that hold no files still get a row, so a name search finds them. They have no profile text.
    for d, root in dirs.items():
        if d not in agg:
            agg[d] = {"files": 0, "size": 0, "mtime": 0, "msum": 0, "emb": 0, "root": root}
    children: dict[str, list[str]] = defaultdict(list)
    for d in sorted(dirs, key=str.lower):
        children[os.path.dirname(d)].append(d)
    return agg, rows_by_dir, children


def _subtree_rows(folder: str, rows_by_dir: dict[str, list], children: dict[str, list[str]]) -> list:
    """The file rows below `folder`, the nearest ones first."""
    out = []
    queue = deque([folder])
    while queue:
        d = queue.popleft()
        out.extend(rows_by_dir.get(d, []))
        queue.extend(children.get(d, []))
    return out


def _profile(folder: str, rows: list, subdirs: list[str]) -> tuple[str, list[str]]:
    kinds = Counter(kind_of(r["name"], False) for r in rows)
    names: list[str] = []
    for r in rows:
        if len(names) >= PROFILE_NAMES:
            break
        t = (r["title"] or "").strip()
        label = r["name"] if not t or t.lower() in r["name"].lower() else f"{r['name']} ({t[:80]})"
        names.append(label)
    lines = [Path(folder).name]
    if subdirs:
        lines.append("Subfolders: " + ", ".join(Path(s).name for s in subdirs[:PROFILE_SUBDIRS]))
    if kinds:
        lines.append("Kinds: " + ", ".join(f"{k} x{n}" for k, n in kinds.most_common(PROFILE_KINDS)))
    if names:
        lines.append("Files: " + "; ".join(names))
    return "\n".join(lines), [r["name"] for r in rows[:PROFILE_NAMES]]


def _path_words(path: str, root: str) -> str:
    rel = os.path.relpath(path, os.path.dirname(root.rstrip("/"))) if root else path
    return " ".join(re.split(r"[/_\-.\s]+", rel))


def refresh_profiles() -> int:
    """Bring folder rows, profiles and BM25 rows in step with `files`. Return the number of changed folders."""
    agg, rows_by_dir, children = _aggregate()
    c = db.conn()
    known = {r["path"]: dict(r) for r in c.execute("SELECT id, path, sig, files, names, summary_basis FROM folders")}
    changed = 0
    upserts = []
    for d, a in agg.items():
        sig = f"{a['files']}:{a['msum']}"
        k = known.get(d)
        if k and k["sig"] == sig:
            continue
        subdirs = [s for s in children.get(d, []) if s in agg]
        rows = _subtree_rows(d, rows_by_dir, children) if a["files"] else []
        profile, names = _profile(d, rows, subdirs) if a["files"] else (Path(d).name, [])
        stale = 1
        if k and k.get("summary_basis"):
            basis = json.loads(k["summary_basis"])
            old_n, old_names = basis.get("files", 0), set(basis.get("names", []))
            count_moved = abs(a["files"] - old_n) > STALE_SHARE * max(old_n, 1)
            overlap = len(old_names & set(names)) / max(len(old_names | set(names)), 1)
            stale = int(count_moved or overlap < 1 - STALE_SHARE)
        upserts.append((d, os.path.dirname(d), Path(d).name, a["root"], a["files"], a["size"], a["mtime"], sig,
                        profile, json.dumps(names), stale, int(time.time())))
        changed += 1
    gone = [p for p in known if p not in agg]
    if not upserts and not gone:
        return 0
    has_vec = _has_vec()
    with db.tx() as tc:
        for p in gone:
            fid = known[p]["id"]
            tc.execute("DELETE FROM folders_fts WHERE rowid=?", (fid,))
            if has_vec:
                tc.execute("DELETE FROM folder_vec_mean WHERE rowid=?", (fid,))
                tc.execute("DELETE FROM folder_vec_sum WHERE rowid=?", (fid,))
            tc.execute("DELETE FROM folders WHERE id=?", (fid,))
        for u in upserts:
            tc.execute(
                "INSERT INTO folders(path, parent, name, root, files, size, mtime, sig, profile, names, summary_stale, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET parent=excluded.parent, name=excluded.name, "
                "root=excluded.root, files=excluded.files, size=excluded.size, mtime=excluded.mtime, sig=excluded.sig, "
                "profile=excluded.profile, names=excluded.names, "
                "summary_stale=MAX(folders.summary_stale, excluded.summary_stale), updated=excluded.updated",
                u,
            )
            row = tc.execute("SELECT id, summary FROM folders WHERE path=?", (u[0],)).fetchone()
            tc.execute("DELETE FROM folders_fts WHERE rowid=?", (row["id"],))
            tc.execute(
                "INSERT INTO folders_fts(rowid, name, path, profile, summary) VALUES (?,?,?,?,?)",
                (row["id"], u[2], _path_words(u[0], u[3] or ""), u[8], row["summary"] or ""),
            )
    return changed + len(gone)


def _file_vectors(file_ids: list[int]) -> dict[int, tuple[np.ndarray, float]]:
    """Per file: the mean of its chunk vectors (weighted by chunk length) and the file text length."""
    out: dict[int, tuple[np.ndarray, float]] = {}
    c = db.conn()
    for i in range(0, len(file_ids), 400):
        part = file_ids[i:i + 400]
        rows = c.execute(
            f"SELECT c.file_id fid, length(c.text) n, v.embedding e FROM chunks c JOIN chunk_vec v ON v.rowid=c.id "
            f"WHERE c.file_id IN ({','.join('?' * len(part))})",
            part,
        ).fetchall()
        acc: dict[int, list] = {}
        for r in rows:
            vec = np.frombuffer(r["e"], dtype=np.float32)
            n = float(r["n"] or 1)
            a = acc.get(r["fid"])
            if a is None:
                acc[r["fid"]] = [vec * n, n]
            else:
                a[0] = a[0] + vec * n
                a[1] += n
        for fid, (s, n) in acc.items():
            out[fid] = (s / n, min(n, MAX_WEIGHT_CHARS))
    return out


def refresh_vectors(stop: threading.Event | None = None) -> int:
    """Write the mean vector of each folder whose files or embeddings changed."""
    if not _ensure_vec():
        return 0
    agg, rows_by_dir, children = _aggregate()
    c = db.conn()
    rows = c.execute("SELECT id, path, vec_sig FROM folders WHERE files > 0").fetchall()
    todo = []
    for r in rows:
        a = agg.get(r["path"])
        if not a:
            continue
        vsig = f"{a['files']}:{a['msum']}:{a['emb']}"
        if r["vec_sig"] != vsig:
            todo.append((r["id"], r["path"], vsig))
    if not todo:
        return 0
    done = 0
    cache: dict[int, tuple[np.ndarray, float]] = {}
    for fid_folder, path, vsig in todo:
        if stop is not None and stop.is_set():
            break
        files = _subtree_rows(path, rows_by_dir, children)
        need = [r["id"] for r in files if r["id"] not in cache]
        if need:
            cache.update(_file_vectors(need))
        vecs = [cache[r["id"]] for r in files if r["id"] in cache]
        with db.tx() as tc:
            tc.execute("DELETE FROM folder_vec_mean WHERE rowid=?", (fid_folder,))
            if vecs:
                total = sum(w for _, w in vecs)
                mean = sum(v * w for v, w in vecs) / total
                norm = float(np.linalg.norm(mean))
                if norm > 0:
                    tc.execute("INSERT INTO folder_vec_mean(rowid, embedding) VALUES (?, ?)",
                               (fid_folder, sqlite_vec.serialize_float32((mean / norm).astype(np.float32).tolist())))
            tc.execute("UPDATE folders SET vec_sig=? WHERE id=?", (vsig, fid_folder))
        done += 1
        # Keep memory flat on a large home folder.
        if len(cache) > 20000:
            cache.clear()
    return done


def summarize_some(indexer, batch: int = 4) -> int:
    """Write the LLM summary of a few stale folders. Background work: it stops for any interactive request."""
    from ..router import local

    c = db.conn()
    rows = c.execute(
        "SELECT id, path, name, files, profile, names FROM folders WHERE summary_stale=1 AND files >= 2 "
        "ORDER BY files DESC LIMIT ?",
        (batch,),
    ).fetchall()
    if not rows or not local.background_allowed() or not local.available() or not indexer.state.get("embed_ready"):
        return 0
    done = 0
    for r in rows:
        if indexer.interactive.is_set() or indexer.stop.is_set():
            break
        try:
            data = local._chat(SUMMARY_SYSTEM, r["profile"][:3500] + "\n/no_think", SUMMARY_SCHEMA, 110, local.KEEP_BACKGROUND)
            summary = " ".join(str(data.get("summary", "")).split())[:400]
            if _BAD_SUMMARY.search(summary):
                summary = ""
        except Exception as e:  # noqa: BLE001
            log.warning("folder summary failed for %s: %s", r["path"], e)
            return done
        vec = None
        if summary:
            try:
                vec = embed.embed_docs([(r["name"], summary)])[0]
            except Exception as e:  # noqa: BLE001
                log.warning("folder summary embedding failed: %s", e)
        basis = json.dumps({"files": r["files"], "names": json.loads(r["names"] or "[]")})
        with db.tx() as tc:
            tc.execute("UPDATE folders SET summary=?, summary_basis=?, summary_stale=0 WHERE id=?", (summary, basis, r["id"]))
            tc.execute("UPDATE folders_fts SET summary=? WHERE rowid=?", (summary, r["id"]))
            if vec is not None and _has_vec():
                tc.execute("DELETE FROM folder_vec_sum WHERE rowid=?", (r["id"],))
                tc.execute("INSERT INTO folder_vec_sum(rowid, embedding) VALUES (?, ?)", (r["id"], sqlite_vec.serialize_float32(vec)))
        done += 1
    if done:
        events.publish("folders_summarized", count=done)
    return done


def loop(indexer) -> None:
    """Background folder work, after the file index. Cheap steps first, the LLM last."""
    init()
    seen_version = -1
    last = 0.0
    while not indexer.stop.is_set():
        try:
            did = False
            now = time.monotonic()
            if indexer.names_version != seen_version and now - last >= REFRESH_EVERY:
                seen_version = indexer.names_version
                last = now
                if refresh_profiles():
                    did = True
            busy = db.conn().execute(
                "SELECT (SELECT COUNT(*) FROM files WHERE index_stage=1) + (SELECT COUNT(*) FROM chunks WHERE embedded=0)"
            ).fetchone()[0]
            if not busy and not indexer.interactive.is_set() and indexer.state.get("embed_ready"):
                if refresh_vectors(indexer.stop):
                    did = True
                elif summarize_some(indexer):
                    did = True
        except Exception:  # noqa: BLE001
            log.exception("folder step failed")
            did = False
        indexer.stop.wait(0.2 if did else 3)


def status() -> dict:
    c = db.conn()
    try:
        r = c.execute(
            "SELECT COUNT(*) n, SUM(files>0) withfiles, SUM(summary IS NOT NULL AND summary != '') summaries, "
            "SUM(summary_stale=1 AND files>=2) pending FROM folders"
        ).fetchone()
    except Exception:  # noqa: BLE001
        return {"folders": 0, "summaries": 0, "pending": 0, "vectors": 0}
    vecs = c.execute("SELECT COUNT(*) FROM folder_vec_mean").fetchone()[0] if _has_vec() else 0
    return {"folders": r["n"] or 0, "with_files": r["withfiles"] or 0, "summaries": r["summaries"] or 0,
            "pending": r["pending"] or 0, "vectors": vecs}


# ---------------------------------------------------------------- search


PATHLIKE = re.compile(r"^(~|/|\\\\|[a-z]+://)", re.I)
NET = re.compile(r"^(smb|sftp|ssh|ftp|ftps|dav|davs|webdav|nfs|afp)://|^\\\\", re.I)


def path_results(q: str, home: str, hidden: bool = False, limit: int = 12) -> list[dict]:
    """A typed path: the folder itself, or completions of its last part."""
    q = q.strip()
    if NET.match(q):
        return [{"path": q, "name": q, "parent": "", "why": "connect", "files": None, "size": None, "mtime": 0,
                 "summary": "Connect to this server and open it", "top": []}]
    p = q
    if p == "~" or p.startswith("~/"):
        p = home + p[1:]
    p = os.path.expandvars(p)
    out = []
    if os.path.isdir(p) and (q.endswith("/") or p == "/"):
        base, prefix = p, ""
    else:
        base, prefix = os.path.dirname(p) or "/", os.path.basename(p)
    if os.path.isdir(p) and not q.endswith("/"):
        out.append(_path_row(p, "path"))
    try:
        with os.scandir(base) as it:
            names = sorted(
                (de.name for de in it if _is_dir(de) and de.name.lower().startswith(prefix.lower())
                 and (hidden or prefix.startswith(".") or not de.name.startswith("."))),
                key=str.lower,
            )
    except OSError:
        names = []
    for n in names:
        full = os.path.join(base, n)
        if full == p:
            continue
        out.append(_path_row(full, "path"))
        if len(out) >= limit:
            break
    return out


def _is_dir(de) -> bool:
    try:
        return de.is_dir()
    except OSError:
        return False


def _path_row(p: str, why: str) -> dict:
    r = db.conn().execute("SELECT files, size, mtime, summary FROM folders WHERE path=?", (p,)).fetchone()
    try:
        mtime = int(os.stat(p).st_mtime)
    except OSError:
        mtime = 0
    return {
        "path": p, "name": os.path.basename(p) or p, "parent": os.path.dirname(p), "why": why,
        "files": r["files"] if r else None, "size": r["size"] if r else None,
        "mtime": ((r["mtime"] if r and r["mtime"] else mtime) or 0) * 1000,
        "summary": (r["summary"] if r else None) or None, "top": [],
    }


_names_state: dict = {"key": None, "names": [], "paths": [], "rel": []}
_names_lock = threading.Lock()


def _dir_names():
    from ..index.indexer import indexer

    with _names_lock:
        if _names_state["key"] == indexer.names_version:
            return _names_state
        rows = db.conn().execute("SELECT path, name FROM files WHERE is_dir=1").fetchall()
        _names_state.update(
            key=indexer.names_version,
            paths=[r["path"] for r in rows],
            names=[utils.default_process(r["name"]) for r in rows],
            rel=[utils.default_process(r["path"]) for r in rows],
        )
        return _names_state


def _words(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if t not in STOP]


def _name_hits(text: str, limit: int = 50) -> list[tuple[str, float]]:
    """Folder paths whose name matches, each with a weight: the share of the query that the name explains."""
    s = _dir_names()
    if "/" in text:
        # A path fragment such as "sem3/drone": match the end of the full path.
        q = utils.default_process(text)
        hits = process.extract(q, s["rel"], scorer=fuzz.partial_ratio, limit=limit, score_cutoff=80, processor=None)
        return [(s["paths"][i], 1.0) for _, _, i in hits]
    words = _words(text)
    if not words:
        return []
    q = " ".join(words)
    # One word: a typo-tolerant match. More words: a folder name that shares whole words with the query.
    scorer = fuzz.WRatio if len(words) == 1 else fuzz.token_set_ratio
    hits = process.extract(q, s["names"], scorer=scorer, limit=limit * 2, score_cutoff=72 if len(words) == 1 else 80, processor=None)
    out = []
    for _, score, i in hits:
        name = s["names"][i]
        # Short names such as "OS" match inside many words. They count only as a whole word.
        if len(name) <= 3 and name not in words:
            continue
        # A typo keeps the first letter: "dmbs" finds "DBMS", but "raven" does not find "Travel".
        if score < 90 and not any(t[:1] == w[:1] for w in words for t in name.split()):
            continue
        # "code" for "drone project with code and report" explains 1 word of 4.
        name_words = set(name.split())
        share = 1.0 if len(words) == 1 else sum(1 for w in words if w in name_words) / len(words)
        out.append((s["paths"][i], max(share, 0.25)))
    return out[:limit]


def _bm25(text: str) -> list[int]:
    q = _fts_query(" ".join(_words(text)))
    if not q:
        return []
    try:
        rows = db.conn().execute(
            "SELECT rowid, bm25(folders_fts, 6.0, 2.0, 1.0, 2.0) s FROM folders_fts WHERE folders_fts MATCH ? ORDER BY s LIMIT 60",
            (q,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [r[0] for r in rows]


def _knn(table: str, blob: bytes, floor: float = FOLDER_MIN_SIM) -> list[tuple[int, float]]:
    rows = db.conn().execute(
        f"SELECT rowid, distance FROM {table} WHERE embedding MATCH ? AND k = 60 ORDER BY distance", (blob,)
    ).fetchall()
    hits = [(r[0], 1 - r[1]) for r in rows if 1 - r[1] >= floor]
    if hits:
        floor = hits[0][1] * FOLDER_REL_SIM
        hits = [h for h in hits if h[1] >= floor]
    return hits


def search(q: str, limit: int = 12, home: str = "") -> dict:
    t0 = time.perf_counter()
    timings: dict = {}
    if PATHLIKE.match(q.strip()):
        res = path_results(q, home or str(Path.home()))
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return {"query": q, "mode": "path", "results": res, "timings": timings}

    text = parse(q).text or q.strip()
    c = db.conn()
    t = time.perf_counter()
    name_paths = _name_hits(text)
    timings["name_ms"] = round((time.perf_counter() - t) * 1000, 1)
    from .disk_dirs import disk_dirs

    t = time.perf_counter()
    disk = disk_dirs.search(_words(text)) if "/" not in text else []
    timings["disk_ms"] = round((time.perf_counter() - t) * 1000, 1)
    t = time.perf_counter()
    kw = _bm25(text)
    timings["bm25_ms"] = round((time.perf_counter() - t) * 1000, 1)

    vec = None
    summ: list[tuple[int, float]] = []
    mean: list[tuple[int, float]] = []
    if _has_vec() and len(text) > 2:
        try:
            t = time.perf_counter()
            vec = embed.embed_query(text)
            timings["embed_ms"] = round((time.perf_counter() - t) * 1000, 1)
            blob = sqlite_vec.serialize_float32(vec)
            t = time.perf_counter()
            # One word is a name, not a description: only a strong meaning match counts.
            floor = FOLDER_MIN_SIM if len(_words(text)) > 1 else ONE_WORD_MIN_SIM
            summ = _knn("folder_vec_sum", blob, floor)
            mean = _knn("folder_vec_mean", blob, floor)
            timings["dense_ms"] = round((time.perf_counter() - t) * 1000, 1)
        except Exception as e:  # noqa: BLE001
            log.debug("folder dense search failed: %s", e)

    # Fuse on paths: name hits can be empty folders that have no row in `folders`.
    id_to_path: dict[int, str] = {}
    ids = set(kw) | {i for i, _ in summ} | {i for i, _ in mean}
    if ids:
        id_to_path = dict(c.execute(f"SELECT id, path FROM folders WHERE id IN ({','.join('?' * len(ids))})", list(ids)).fetchall())
    fused: dict[str, dict] = {}

    def add(path: str | None, rank: int, source: str, sim: float | None = None, weight: float = 1.0):
        if not path:
            return
        e = fused.setdefault(path, {"score": 0.0, "ranks": {}, "sim": None})
        e["score"] += weight / (RRF_K + rank)
        e["ranks"][source] = rank
        if sim is not None:
            e["sim"] = max(e["sim"] or 0, sim)

    for i, (p, w) in enumerate(name_paths):
        add(p, i + 1, "name", weight=w)
    # Folder names on the whole disk, also above the index roots. A project name counts as a name.
    projects: dict[str, str] = {}
    disk_share: dict[str, float] = {}
    for i, (p, score, via, share) in enumerate(disk):
        add(p, i + 1, "project" if via else "disk", weight=(1.2 if score >= 100 else 1.0) * share)
        disk_share[p] = share
        if via:
            projects[p] = via
    for i, fid in enumerate(kw):
        add(id_to_path.get(fid), i + 1, "keyword")
    for i, (fid, sim) in enumerate(summ):
        add(id_to_path.get(fid), i + 1, "summary", sim)
    for i, (fid, sim) in enumerate(mean):
        add(id_to_path.get(fid), i + 1, "content", sim)

    cover = _coverage(text, list(fused))
    for path, e in fused.items():
        # A folder that holds only some of the query words ranks below one that holds them all.
        # A folder outside the index has no profile: its name match is its coverage.
        c = cover.get(path, disk_share.get(path, 0.0))
        e["score"] *= max(c, 0.2) ** 2
    # Frecency: among folders that match, the ones you open often and lately go up (up to 1.6 times).
    frec = db.frecent()
    for path, e in fused.items():
        if path in frec:
            e["score"] *= 1 + 0.6 * frec[path]
            e["ranks"]["frecent"] = 1
    _prefer_specific(fused, cover)
    ordered = sorted(fused.items(), key=lambda kv: kv[1]["score"], reverse=True)[:limit]
    t = time.perf_counter()
    results = []
    for path, e in ordered:
        row = _path_row(path, min(e["ranks"], key=lambda s: e["ranks"][s]))
        row.update(score=round(e["score"], 5), ranks=e["ranks"], sim=round(e["sim"], 3) if e["sim"] else None)
        row["project"] = projects.get(path)
        row["top"] = _top_files(path, text, vec) if (row["files"] or 0) > 0 else _entries(path)
        results.append(row)
    timings["top_ms"] = round((time.perf_counter() - t) * 1000, 1)
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return {"query": q, "mode": "describe", "results": results, "timings": timings}


def _coverage(text: str, paths: list[str]) -> dict[str, float]:
    """Share of the query words that each folder's name, path, profile or summary holds."""
    words = _words(text)
    if not words or not paths:
        return {}
    rows = db.conn().execute(
        f"SELECT path, root, profile, summary FROM folders WHERE path IN ({','.join('?' * len(paths))})", paths
    ).fetchall()
    out = {}
    for r in rows:
        hay = " ".join([_path_words(r["path"], r["root"] or ""), r["profile"] or "", r["summary"] or ""]).lower()
        hay = " ".join(re.split(r"[_\-.]+", hay))
        out[r["path"]] = sum(1 for w in words if re.search(r"\b" + re.escape(w), hay)) / len(words)
    return out


def _prefer_specific(fused: dict[str, dict], cover: dict[str, float]) -> None:
    """A parent folder holds the names of all its subfolders, so it often matches as well as the right subfolder.
    When a subfolder keeps most of the parent's score and holds as many of the query words, the subfolder is
    the answer: it goes just above the parent."""
    for path in sorted(fused, key=lambda p: p.count("/"), reverse=True):
        e = fused[path]
        p = os.path.dirname(path)
        while p and p != os.path.dirname(p):  # the root is its own parent ("/", "C:\\")
            a = fused.get(p)
            # A parent that matched by its own name or project name stays the answer.
            named = any(k in a["ranks"] for k in ("name", "disk", "project")) if a is not None else False
            if a is not None and not named and e["score"] >= SPECIFIC_SHARE * a["score"] and a["score"] >= e["score"] \
                    and cover.get(path, 0.0) >= cover.get(p, 0.0):
                a["score"] = e["score"] * 0.999
            p = os.path.dirname(p)


def _entries(path: str, n: int = TOP_FILES) -> list[dict]:
    """For a folder outside the index: its first entries, the README first, so the user sees what it is."""
    try:
        with os.scandir(path) as it:
            names = sorted((de.name for de in it if not de.name.startswith(".")),
                           key=lambda x: (not x.lower().startswith("readme"), x.lower()))
    except OSError:
        return []
    return [{"path": os.path.join(path, x), "name": x, "page": None} for x in names[:n]]


def _like(p: str) -> str:
    return p.rstrip("/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def _top_files(folder: str, text: str, vec: list[float] | None) -> list[dict]:
    """The files below `folder` that match the query best: meaning first, then words, then the newest."""
    c = db.conn()
    out: list[dict] = []
    seen: set[str] = set()

    def push(rows):
        for r in rows:
            if r["path"] in seen:
                continue
            seen.add(r["path"])
            out.append({"path": r["path"], "name": r["name"], "page": r["page"]})
            if len(out) >= TOP_FILES:
                return

    words = " ".join(_words(text))
    if vec is not None and db.has_vec_table():
        rows = c.execute(
            "SELECT f.path, f.name, c.page, MIN(vec_distance_cosine(v.embedding, ?)) d FROM chunk_vec v "
            "JOIN chunks c ON c.id=v.rowid JOIN files f ON f.id=c.file_id WHERE f.path LIKE ? ESCAPE '\\' "
            "GROUP BY f.id ORDER BY d LIMIT ?",
            (sqlite_vec.serialize_float32(vec), _like(folder), TOP_FILES),
        ).fetchall()
        push([r for r in rows if 1 - r["d"] >= MIN_SIM])
    if len(out) < TOP_FILES:
        q = _fts_query(words)
        if q:
            rows = c.execute(
                "SELECT f.path, f.name, c.page FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid JOIN files f ON f.id=c.file_id "
                "WHERE chunks_fts MATCH ? AND f.path LIKE ? ESCAPE '\\' ORDER BY bm25(chunks_fts) LIMIT 10",
                (q, _like(folder)),
            ).fetchall()
            push(rows)
    if len(out) < TOP_FILES:
        rows = c.execute(
            "SELECT path, name, NULL page FROM files WHERE is_dir=0 AND path LIKE ? ESCAPE '\\' ORDER BY mtime DESC LIMIT 6",
            (_like(folder),),
        ).fetchall()
        push(rows)
    return out
