"""Background indexer (section 9.1).

Stage A  metadata: walk the roots, keep `files` in step with the disk.
Stage B  text: extract, chunk, write chunks and FTS5 rows.
Stage C  AI: embed chunks into chunk_vec. PII spans are a placeholder until phase 5.

index_stage in `files`: 1 = metadata only, text pending. 2 = text done or no text.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sqlite_vec
from watchfiles import Change, watch

from .. import config, db, events
from ..fs.atime import keep_atime
from ..fs.kinds import ext_of, mime_of
from ..privacy import rules as pii_rules
from . import embed
from .chunk import chunk_section, header
from .extract import can_extract, extract

log = logging.getLogger("raven.index")

BATCH_B = 16
# Raise this when stage B output changes (v2: folder path in the FTS headings column. v3: PII spans).
TEXT_VERSION = 3
PII_VERSION = 2
BATCH_C = 48
BIG_DELETE = 20_000
# One embedding call holds at most this many characters (about 2,000 tokens).
EMBED_CHARS = 8000


def roots() -> list[str]:
    return [str(Path(r)) for r in db.get_setting("index_roots", [])]


def _skip_name(name: str) -> bool:
    if name in config.EXCLUDE_NAMES:
        return True
    return name.startswith(".") and not db_cache.index_hidden


def _parts(rows: list, max_chars: int) -> list[list]:
    out: list[list] = [[]]
    size = 0
    for r in rows:
        n = len(r["text"] or "") + len(r["header"] or "")
        if out[-1] and size + n > max_chars:
            out.append([])
            size = 0
        out[-1].append(r)
        size += n
    return [p for p in out if p]


class _Cache:
    index_hidden = False
    index_code = False


db_cache = _Cache()


def _row_for(path: str, root: str, st: os.stat_result, is_dir: bool, light: bool = False) -> tuple:
    name = os.path.basename(path)
    ext = "" if is_dir else ext_of(name)
    # A names-only file (light) skips the text stage, and so the meaning stage too.
    stage = 1 if (not is_dir and not light and can_extract(ext, st.st_size)) else 2
    return (
        path, os.path.dirname(path), name, ext, None if is_dir else st.st_size,
        int(st.st_mtime), int(st.st_ctime), int(st.st_atime), int(is_dir), int(name.startswith(".")), 0,
        root, None if is_dir else mime_of(name), stage, int(light),
    )


# Downloaded datasets (thousands of sample images or texts): names only, like an SDK.
DATASET_NAMES = {"scikit_learn_data", "nltk_data", "tensorflow_datasets", "torch_datasets", "keras_datasets", "kagglehub",
                 "huggingface", "hf_datasets", "datasets_cache"}


def _full(path: str) -> bool:
    """A full_index folder rule on this folder or a folder above it: list every file of its code projects, by
    name only (PLAN3 P0-7). The "index code" setting also reads their text."""
    from ..fs.props import policy_for

    return policy_for(path, "full_index")


def _names_only_dir(path: str) -> bool:
    """A code project or a software folder (SDK, toolchain, app data): its files keep only their names in the
    index, unless the user asks to read code too. Text, meaning, images and speech of 600,000 source and SDK
    files cost days and give a file manager little."""
    if db_cache.index_code:
        return False
    from ..storage import protect

    return (os.path.basename(path) in DATASET_NAMES or protect.is_project_root(path) or protect.is_tool_dir(path))


def _skip_dir(path: str) -> bool:
    """An SDK, a toolchain, an app's data, a downloaded dataset or a code project: Raven keeps the folder as one
    row and does not go inside. One row per file of a project filled 93% of a real index (620,643 of 665,325
    rows) and all of the name list in RAM (PLAN3 P0-7). Folder search and the project alias work from the
    folder row. A full_index folder rule lists the file names again, and the "index code" setting reads them."""
    from ..fs.props import policy_for

    if policy_for(path, "no_index"):
        return True  # a folder rule (PLAN2 P2-5): the user excluded it
    if db_cache.index_code:
        return False
    from ..storage import protect

    if os.path.basename(path) in DATASET_NAMES or protect.is_tool_dir(path):
        return True  # full_index does not open these: a rule on ~/Codes must not read an SDK
    return protect.is_project_root(path) and not _full(path)


def _inside_skipped(path: str, root: str) -> bool:
    cur = os.path.dirname(path)
    root = root.rstrip("/")
    while cur.startswith(root + "/"):
        if _skip_dir(cur):
            return True
        cur = os.path.dirname(cur)
    return False


def _light_for(path: str, root: str) -> bool:
    """Is `path` below a names-only folder? The root itself never counts: a project chosen as a root is read."""
    if db_cache.index_code:
        return False
    cur = os.path.dirname(path)
    root = root.rstrip("/")
    while cur.startswith(root + "/"):
        if _names_only_dir(cur):
            return True
        cur = os.path.dirname(cur)
    return False


_UPSERT = """
INSERT INTO files(path, parent, name, ext, size, mtime, ctime, atime, is_dir, is_hidden, is_system, root, mime, index_stage, light)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(path) DO UPDATE SET
  size=excluded.size, mtime=excluded.mtime, ctime=excluded.ctime, atime=excluded.atime,
  is_dir=excluded.is_dir, root=excluded.root, mime=excluded.mime, ext=excluded.ext, light=excluded.light,
  index_stage=CASE WHEN excluded.light = 1 THEN 2
                   WHEN files.light = 1 THEN excluded.index_stage
                   WHEN files.size IS excluded.size AND files.mtime = excluded.mtime THEN files.index_stage ELSE excluded.index_stage END,
  category_source=CASE WHEN files.size IS excluded.size AND files.mtime = excluded.mtime THEN files.category_source ELSE NULL END,
  category=CASE WHEN files.size IS excluded.size AND files.mtime = excluded.mtime THEN files.category ELSE NULL END,
  sniffed_kind=CASE WHEN files.size IS excluded.size AND files.mtime = excluded.mtime THEN files.sniffed_kind ELSE NULL END,
  kind_mismatch=CASE WHEN files.size IS excluded.size AND files.mtime = excluded.mtime THEN files.kind_mismatch ELSE 0 END
WHERE files.size IS NOT excluded.size OR files.mtime != excluded.mtime OR files.light != excluded.light
   OR files.is_dir != excluded.is_dir OR files.root IS NOT excluded.root OR abs(files.atime - excluded.atime) > 86400
"""
# A scan of a home folder sees about a million unchanged rows. The WHERE above skips their write: only a
# changed file (or an access time older by a day, for cold-file detection) is written again.


def _drop_chunks(c, file_ids: list[int]) -> None:
    """Remove the chunks of these files and everything that hangs on them (FTS, vectors, PII spans), in bulk."""
    if not file_ids:
        return
    if len(file_ids) > 20:
        _drop_chunks_bulk(c, file_ids)
        return
    has_vec = db.has_vec_table()
    from ..search import vectors

    binary = vectors.tables_exist()
    for fid in file_ids:
        ids = [r[0] for r in c.execute("SELECT id FROM chunks WHERE file_id=?", (fid,))]
        for cid in ids:
            c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            if has_vec:
                c.execute("DELETE FROM chunk_vec WHERE rowid=?", (cid,))
            if binary:
                vectors.remove(c, cid)
        c.execute("DELETE FROM pii_spans WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id=?)", (fid,))
        c.execute("DELETE FROM chunks WHERE file_id=?", (fid,))


def _drop_chunks_bulk(c, file_ids: list[int]) -> None:
    has_vec = db.has_vec_table()
    from ..search import vectors

    binary = vectors.tables_exist()
    for i in range(0, len(file_ids), 500):
        part = file_ids[i:i + 500]
        q = ",".join("?" * len(part))
        cids = [r[0] for r in c.execute(f"SELECT id FROM chunks WHERE file_id IN ({q})", part)]
        for j in range(0, len(cids), 500):
            cp = cids[j:j + 500]
            cq = ",".join("?" * len(cp))
            c.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({cq})", cp)
            if has_vec:
                c.execute(f"DELETE FROM chunk_vec WHERE rowid IN ({cq})", cp)
            if binary:
                c.execute(f"DELETE FROM chunk_bin WHERE rowid IN ({cq})", cp)
                c.execute(f"DELETE FROM chunk_f16 WHERE id IN ({cq})", cp)
            c.execute(f"DELETE FROM pii_spans WHERE chunk_id IN ({cq})", cp)
        c.execute(f"DELETE FROM chunks WHERE file_id IN ({q})", part)


def _remove_ids(c, ids: list[int]) -> None:
    """Remove file rows by id, with their chunks. One pass, no per-path LIKE scan."""
    _drop_chunks(c, ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        c.execute(f"DELETE FROM files WHERE id IN ({','.join('?' * len(part))})", part)


def write_chunks(c, fid: int, name: str, title: str | None, chunks: list[dict]) -> int:
    """Write chunks, their FTS rows and PII layer 1 spans. Return the file's PII level (0, 1 or 2)."""
    level = 0
    for ch in chunks:
        cur = c.execute(
            "INSERT INTO chunks(file_id, page, start, end, header, text, at) VALUES (?,?,?,?,?,?,?)",
            (fid, ch["page"], ch["start"], ch["end"], ch["header"], ch["text"], ch.get("at")),
        )
        # PII layer 1 at index time: the redaction gate reuses these spans (section 5.2 rule 2).
        spans = pii_rules.detect(ch["text"])
        if spans:
            c.executemany(
                "INSERT INTO pii_spans(chunk_id, type, start, end, score, layer) VALUES (?,?,?,?,?,1)",
                [(cur.lastrowid, s.type, s.start, s.end, s.score) for s in spans],
            )
            level = max(level, 2 if any(s.type in pii_rules.HIGH_RISK for s in spans) else 1)
        c.execute(
            "INSERT INTO chunks_fts(rowid, name, title, headings, body) VALUES (?,?,?,?,?)",
            (cur.lastrowid, name, title, f'{ch["heading"]} {ch["folder"]}'.strip(), ch["text"]),
        )
    return level


def _remove_paths(c, paths: list[str]) -> int:
    """Remove rows for these paths and everything below them."""
    n = 0
    for p in paths:
        rows = c.execute(
            "SELECT id FROM files WHERE path=? OR path LIKE ? ESCAPE '\\'",
            (p, _like_prefix(p)),
        ).fetchall()
        ids = [r[0] for r in rows]
        _drop_chunks(c, ids)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            c.execute(f"DELETE FROM files WHERE id IN ({','.join('?' * len(part))})", part)
        n += len(ids)
    return n


def _like_prefix(p: str) -> str:
    esc = p.rstrip("/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + "/%"


class Indexer:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.wake_b = threading.Event()
        self.wake_c = threading.Event()
        # Set while an interactive LLM call runs. Background embedding waits, so the user waits less.
        self.interactive = threading.Event()
        self.rescan = threading.Event()
        self.priority_dir: str | None = None
        self.names_version = 0
        self.state = {"stage": "idle", "scanned": 0, "embed_ready": False, "embed_error": None}
        self._last_progress: dict | None = None
        self._watch_stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        db_cache.index_hidden = bool(db.get_setting("index_hidden", False))
        # Layer 2 results from before PII_VERSION may be empty because of a load failure. Run them again.
        if db.get_setting("pii_version") != PII_VERSION:
            with db.tx() as c:
                c.execute("DELETE FROM pii_spans WHERE layer=2")
                c.execute("UPDATE chunks SET pii_done=0")
            db.set_setting("pii_version", PII_VERSION)
        # A change to what stage B writes means every text row must be built again.
        # OCR arrived after these files were indexed: read images and text-less PDFs again.
        from .extract import OCR_EXTS, ocr_ok

        if ocr_ok() and db.get_setting("ocr_version") != 1:
            exts = ",".join(f"'{e}'" for e in sorted(OCR_EXTS | {"pdf"}))
            with db.tx() as c:
                c.execute(f"UPDATE files SET index_stage=1 WHERE index_stage=2 AND ext IN ({exts}) AND id NOT IN (SELECT DISTINCT file_id FROM chunks)")
            db.set_setting("ocr_version", 1)
        if db.get_setting("text_version") != TEXT_VERSION:
            with db.tx() as c:
                c.execute("UPDATE files SET index_stage=1 WHERE index_stage=2 AND id IN (SELECT DISTINCT file_id FROM chunks)")
            db.set_setting("text_version", TEXT_VERSION)
        for target, name in (
            (self._scan_loop, "index-a"),
            (self._text_loop, "index-b"),
            (self._embed_loop, "index-c"),
            (self._progress_loop, "index-progress"),
            (self._pii_loop, "index-pii"),
            (self._classify_loop, "index-classify"),
            (self._folder_loop, "index-folders"),
            (self._disk_dirs_loop, "disk-dirs"),
            (self._visual_loop, "index-visual"),
            (self._transcribe_loop, "index-transcribe"),
            (self._warm_loop, "index-warm"),
            (self._binary_loop, "index-binary"),
        ):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        self._start_watcher()
        self.rescan.set()

    def shutdown(self) -> None:
        self.stop.set()
        self._watch_stop.set()
        self.wake_b.set()
        self.wake_c.set()
        self.rescan.set()

    def roots_changed(self) -> None:
        with db.tx() as c:
            current = roots()
            stale = [r[0] for r in c.execute("SELECT DISTINCT root FROM files") if r[0] not in current]
            for r in stale:
                _remove_paths(c, [r])
                c.execute("DELETE FROM files WHERE root=?", (r,))
        self._start_watcher()
        self.rescan.set()
        self._names_changed()
        from ..search.disk_dirs import disk_dirs

        disk_dirs.wake.set()

    def prioritize(self, folder: str) -> None:
        self.priority_dir = folder
        self.wake_b.set()

    # ------------------------------------------------------------ stage A

    def _scan_loop(self) -> None:
        while not self.stop.is_set():
            self.rescan.wait()
            self.rescan.clear()
            if self.stop.is_set():
                return
            try:
                self.state["stage"] = "metadata"
                for root in roots():
                    self.scan_root(root)
                self.state["stage"] = "idle"
            except Exception:
                log.exception("stage A failed")
            self.wake_b.set()

    def scan_root(self, root: str) -> None:
        t0 = time.perf_counter()
        seen: set[str] = set()
        batch: list[tuple] = []
        self.state["scanned"] = 0

        def flush():
            if batch:
                with db.tx() as c:
                    c.executemany(_UPSERT, batch)
                batch.clear()

        db_cache.index_code = bool(db.get_setting("index_code", False))
        # Each stack item: a folder, and whether it lies in a names-only folder (a project or software).
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack and not self.stop.is_set():
            d, light = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for de in it:
                    if _skip_name(de.name):
                        continue
                    try:
                        st = de.stat(follow_symlinks=False)
                        is_dir = de.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if de.is_symlink():
                        continue
                    seen.add(de.path)
                    if is_dir and _skip_dir(de.path):
                        # Kept as one names-only folder. Raven does not list its contents.
                        batch.append(_row_for(de.path, root, st, True, True))
                        continue
                    batch.append(_row_for(de.path, root, st, is_dir, light))
                    if is_dir:
                        stack.append((de.path, light or _names_only_dir(de.path)))
            self.state["scanned"] = len(seen)
            if len(batch) >= 1000:
                flush()
        flush()
        # Remove rows for items that are gone. Then reset text for changed files.
        c = db.conn()
        known = c.execute("SELECT id, path FROM files WHERE root=?", (root,)).fetchall()
        gone = [i for i, p in known if p not in seen]
        if self.stop.is_set():
            return  # a stopped scan did not see everything: nothing is gone
        if len(gone) >= BIG_DELETE:
            # Many rows went at once (the first scan after PLAN3 P0-7 drops the files inside code projects).
            # The idle upkeep then gives the space back without its one-day wait.
            db.set_setting("index_compact_due", True)
        with db.tx() as tc:
            if gone:
                _remove_ids(tc, gone)
            # Changed files lose their old text. Names-only files lose any text from an earlier run.
            changed = [r[0] for r in tc.execute(
                "SELECT f.id FROM files f WHERE f.root=? AND (f.index_stage=1 OR f.light=1) "
                "AND EXISTS (SELECT 1 FROM chunks WHERE file_id=f.id)",
                (root,),
            )]
            _drop_chunks(tc, changed)
        log.info("stage A %s: %d items in %.0f ms", root, len(seen), (time.perf_counter() - t0) * 1000)
        self._names_changed()

    def update_paths(self, paths: list[str]) -> None:
        """Bring single paths up to date (used by the watcher)."""
        rs = roots()
        rows, gone, new_dirs = [], [], []
        for p in paths:
            root = next((r for r in rs if p == r or p.startswith(r.rstrip("/") + "/")), None)
            if root is None or p == root:
                continue
            rel_parts = Path(p).relative_to(root).parts
            if any(_skip_name(x) for x in rel_parts) or _inside_skipped(p, root):
                continue
            try:
                st = os.lstat(p)
            except OSError:
                gone.append(p)
                continue
            if os.path.islink(p):
                continue
            is_dir = os.path.isdir(p)
            rows.append(_row_for(p, root, st, is_dir, _light_for(p, root)))
            if is_dir:
                new_dirs.append((p, root))
        with db.tx() as c:
            if gone:
                _remove_paths(c, gone)
            if rows:
                c.executemany(_UPSERT, rows)
                ids = [r[0] for r in c.execute(
                    f"SELECT id FROM files WHERE index_stage=1 AND path IN ({','.join('?' * len(rows))})",
                    [r[0] for r in rows],
                )]
                _drop_chunks(c, ids)
        for d, root in new_dirs:
            self._scan_subtree(d, root)
        self._names_changed()
        self.wake_b.set()

    def _scan_subtree(self, top: str, root: str) -> None:
        batch = []
        stack = [top]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for de in it:
                        if _skip_name(de.name) or de.is_symlink():
                            continue
                        try:
                            st = de.stat(follow_symlinks=False)
                            is_dir = de.is_dir(follow_symlinks=False)
                        except OSError:
                            continue
                        batch.append(_row_for(de.path, root, st, is_dir))
                        if is_dir:
                            stack.append(de.path)
            except OSError:
                continue
        if batch:
            with db.tx() as c:
                c.executemany(_UPSERT, batch)

    # ------------------------------------------------------------ stage B

    def _next_text_batch(self) -> list:
        c = db.conn()
        pd = self.priority_dir
        if pd:
            return c.execute(
                "SELECT id, path, name, parent, ext, root FROM files WHERE index_stage=1 "
                "ORDER BY (parent = ?) DESC, (path LIKE ? ESCAPE '\\') DESC, mtime DESC LIMIT ?",
                (pd, _like_prefix(pd), BATCH_B),
            ).fetchall()
        return c.execute(
            "SELECT id, path, name, parent, ext, root FROM files WHERE index_stage=1 ORDER BY mtime DESC LIMIT ?",
            (BATCH_B,),
        ).fetchall()

    def _text_loop(self) -> None:
        from .. import lite

        # Lite mode (PLAN2 P2-6): one extraction worker, and a pause on battery.
        pool = ThreadPoolExecutor(max_workers=1 if lite.mode() == "lite" else 4, thread_name_prefix="extract")
        while not self.stop.is_set():
            if lite.paused():
                self.stop.wait(30)
                continue
            rows = self._next_text_batch()
            if not rows and self._sniff_batch():
                continue
            if not rows:
                self.wake_b.wait(timeout=5)
                self.wake_b.clear()
                continue
            results = list(pool.map(self._extract_one, rows))
            with db.tx() as c:
                for row, res in zip(rows, results):
                    fid = row["id"]
                    _drop_chunks(c, [fid])
                    if isinstance(res, Exception):
                        c.execute("UPDATE files SET index_stage=2, index_error=? WHERE id=?", (str(res)[:300], fid))
                        continue
                    title, chunks = res
                    level = write_chunks(c, fid, row["name"], title, chunks)
                    c.execute("UPDATE files SET index_stage=2, index_error=NULL, title=?, pii_level=? WHERE id=?", (title, level, fid))
            self.wake_c.set()

    def _sniff_batch(self) -> bool:
        """Read the first bytes of files that have no content kind yet (PLAN2 P2-3.1). True when it did work."""
        from ..fs import kinds
        from ..fs.network import is_network

        rows = db.conn().execute(
            "SELECT id, path, name FROM files WHERE is_dir=0 AND sniffed_kind IS NULL AND size > 0 LIMIT 500"
        ).fetchall()
        if not rows:
            return False
        out = []
        for r in rows:
            if self.stop.is_set():
                break
            if is_network(r["path"]):
                out.append(("", 0, r["id"]))
                continue
            k = kinds.check(r["path"], r["name"])
            level = 2 if k["warning"] else 1 if k["mismatch"] else 0
            out.append((k["sniffed"] or "", level, r["id"]))
        with db.tx() as c:
            c.executemany("UPDATE files SET sniffed_kind=?, kind_mismatch=? WHERE id=?", out)
        return True

    @staticmethod
    def _extract_one(row):
        try:
            with keep_atime(row["path"]):
                ex = extract(row["path"], row["ext"])
            name = row["name"]
            folder = os.path.relpath(row["parent"], os.path.dirname(row["root"]))
            chunks = []
            for sec in ex.sections:
                for ch in chunk_section(sec):
                    chunks.append({
                        "page": ch.page, "start": ch.start, "end": ch.end, "heading": ch.heading,
                        "text": ch.text, "header": header(name, folder, ex.title, ch.heading),
                        "folder": folder.replace("/", " "),
                    })
            # A document with text but no chunks (very short) still gets one chunk.
            return ex.title, chunks
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the indexer
            return e

    # ------------------------------------------------------------ stage C

    def _embed_loop(self) -> None:
        from .. import lite

        while not self.stop.is_set():
            if lite.paused():
                self.stop.wait(30)
                continue
            if self.interactive.is_set():
                self.stop.wait(0.2)
                continue
            if not self.state["embed_ready"]:
                dim = embed.warm()
                if dim is None:
                    self.state["embed_error"] = f"Ollama model '{embed.model()}' is not available"
                    self.stop.wait(10)
                    continue
                db.ensure_vec_table(dim, embed.model())
                self.state.update(embed_ready=True, embed_error=None)
            c = db.conn()
            rows = c.execute(
                "SELECT c.id, c.header, c.text, f.title FROM chunks c JOIN files f ON f.id=c.file_id "
                "WHERE c.embedded=0 ORDER BY (f.parent = ?) DESC, c.id LIMIT ?",
                (self.priority_dir or "", BATCH_C),
            ).fetchall()
            if not rows:
                from ..memory import trim

                trim()  # the index is idle: give the memory of the last burst back
                self.wake_c.wait(timeout=5)
                self.wake_c.clear()
                continue
            # Small GPU calls: an Ask or a summary waits for at most one of them.
            vecs: list = []
            done_rows: list = []
            failed = False
            for part in _parts(rows, EMBED_CHARS):
                if self.interactive.is_set() or self.stop.is_set():
                    break
                try:
                    vecs += embed.embed_docs([(r["title"] or "", f"{r['header']}\n{r['text']}") for r in part])
                    done_rows += part
                except Exception as e:  # noqa: BLE001
                    log.warning("embedding failed: %s", e)
                    self.state.update(embed_ready=False, embed_error=str(e)[:200])
                    failed = True
                    break
            rows = done_rows
            if failed and not rows:
                self.stop.wait(5)
                continue
            from ..search import vectors

            binary = vectors.tables_exist()
            with db.tx() as tc:
                for r, v in zip(rows, vecs):
                    # The chunk may have been dropped while the batch was on the GPU.
                    if tc.execute("SELECT 1 FROM chunks WHERE id=?", (r["id"],)).fetchone() is None:
                        continue
                    tc.execute("DELETE FROM chunk_vec WHERE rowid=?", (r["id"],))
                    tc.execute("INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)", (r["id"], sqlite_vec.serialize_float32(v)))
                    if binary:
                        vectors.add(tc, r["id"], v)
                    tc.execute("UPDATE chunks SET embedded=1 WHERE id=?", (r["id"],))

    # ------------------------------------------------------------ PII layer 2

    def _classify_loop(self) -> None:
        from .. import classify

        classify.loop(self)

    def _disk_dirs_loop(self) -> None:
        from ..search.disk_dirs import disk_dirs

        disk_dirs.loop(self.stop)

    def _visual_loop(self) -> None:
        from ..media import visual

        visual.job.loop(self)

    def _transcribe_loop(self) -> None:
        from ..media import transcribe

        transcribe.job.loop(self)

    def _binary_loop(self) -> None:
        from ..search import vectors

        vectors.loop(self.stop)

    def _warm_loop(self) -> None:
        """Load the small search models once at start, so the first query does not pay for it."""
        from ..search import rerank

        if rerank.model.available():
            rerank.model.warm()
        from ..media import siglip

        if siglip.available() and db.conn().execute("SELECT 1 FROM visual LIMIT 1").fetchone():
            try:
                siglip.model.embed_text("warm up")
            except Exception:  # noqa: BLE001
                log.exception("SigLIP warm-up failed")

    def _folder_loop(self) -> None:
        from ..search import folders

        folders.loop(self)

    def _pii_loop(self) -> None:
        """Run the Privacy Filter on chunks in the background, when its model is present."""
        from ..privacy.model import model as layer2

        from .. import lite

        while not self.stop.is_set():
            if not layer2.available() or not lite.enabled("pii_index") or lite.paused():
                # Lite mode: layer 2 runs only on cloud payloads (the redaction gate), not on the whole index.
                self.stop.wait(30)
                continue
            # Layer 2 is background work: it waits until text and meaning are indexed.
            busy = db.conn().execute(
                "SELECT (SELECT COUNT(*) FROM files WHERE index_stage=1) + (SELECT COUNT(*) FROM chunks WHERE embedded=0)"
            ).fetchone()[0]
            if busy:
                self.stop.wait(5)
                continue
            rows = db.conn().execute(
                "SELECT c.id, c.text, c.file_id FROM chunks c WHERE c.pii_done=0 ORDER BY c.id LIMIT 8"
            ).fetchall()
            if not rows:
                self.wake_c.wait(timeout=10)
                continue
            if self.interactive.is_set():
                self.stop.wait(0.2)
                continue
            results = []
            for r in rows:
                # An interactive request needs the model now: stop this batch at once.
                if self.interactive.is_set():
                    break
                try:
                    spans = layer2.detect(r["text"])
                except Exception:  # noqa: BLE001
                    log.exception("privacy filter failed on chunk %s", r["id"])
                    break
                # A model that failed to load returns nothing. That is not "no PII", so the chunk stays pending.
                if layer2.error:
                    break
                results.append((r, spans))
            with db.tx() as c:
                for r, spans in results:
                    if c.execute("SELECT 1 FROM chunks WHERE id=?", (r["id"],)).fetchone() is None:
                        continue
                    c.executemany(
                        "INSERT INTO pii_spans(chunk_id, type, start, end, score, layer) VALUES (?,?,?,?,?,2)",
                        [(r["id"], s.type, s.start, s.end, s.score) for s in spans],
                    )
                    c.execute("UPDATE chunks SET pii_done=1 WHERE id=?", (r["id"],))
                    if spans:
                        lvl = 2 if any(s.type in pii_rules.HIGH_RISK for s in spans) else 1
                        c.execute("UPDATE files SET pii_level=MAX(pii_level, ?) WHERE id=?", (lvl, r["file_id"]))
            if layer2.error:
                self.stop.wait(60)
            else:
                self.stop.wait(0.3)  # leave CPU time for interactive work

    # ------------------------------------------------------------ progress

    def progress(self) -> dict:
        c = db.conn()
        if self.state["stage"] == "metadata" and getattr(self, "_last_full", None):
            # Listing: only the found count changes. The full counts read a million rows; reuse the last ones.
            return {**self._last_full, "stage": "metadata", "scanned": self.state["scanned"]}
        files = c.execute("SELECT COUNT(*), SUM(index_stage=1) FROM files").fetchone()
        chunks = c.execute("SELECT COUNT(*), SUM(embedded=0) FROM chunks").fetchone()
        total, text_pending = files[0] or 0, files[1] or 0
        ctotal, cpending = chunks[0] or 0, chunks[1] or 0
        stage = self.state["stage"]
        from ..media import visual

        vis = visual.job.state
        # The extra counts for the progress panel scan the files table, so they refresh at most every 5 s.
        now = time.time()
        extra = getattr(self, "_extra", None)
        if extra is None or now - extra[0] > 5:
            from ..media import transcribe

            try:
                vtotal, vpend = visual.job.count_total(), visual.job.count_pending()
                speech = transcribe.job.status()["pending"]
            except Exception:  # noqa: BLE001
                vtotal, vpend, speech = 0, vis["pending"], 0
            # Chunks per file, measured: the chunks so far over the readable files that stage B finished.
            # The time estimate uses it for files still waiting for text (most files give few chunks).
            from .extract import DOC_EXTS, OCR_EXTS, TEXT_EXTS

            exts = ",".join(f"'{e}'" for e in sorted(TEXT_EXTS | DOC_EXTS | OCR_EXTS))
            read = c.execute(f"SELECT COUNT(*) FROM files WHERE is_dir=0 AND light=0 AND index_stage=2 AND ext IN ({exts})").fetchone()[0]
            per_file = round(ctotal / read, 2) if read >= 50 else None
            extra = self._extra = (now, vtotal, vpend, speech, per_file)
        if stage == "idle":
            if text_pending:
                stage = "text"
            elif cpending and self.state["embed_ready"]:
                stage = "meaning"
            elif extra[2] and vis["current"]:
                stage = "appearance"
        out = {
            "stage": stage,
            "files": total,
            "scanned": self.state["scanned"],
            "text_pending": text_pending,
            "chunks": ctotal,
            "embed_pending": cpending,
            "embed_ready": self.state["embed_ready"],
            "embed_error": self.state["embed_error"],
            "embed_model": embed.model(),
            "visual_pending": extra[2],
            "visual_total": extra[1],
            "visual_current": vis.get("current"),
            "speech_pending": extra[3],
            "chunks_per_file": extra[4],
            "roots": roots(),
        }
        self._last_full = out
        return out

    def _progress_loop(self) -> None:
        while not self.stop.is_set():
            try:
                p = self.progress()
                if p != self._last_progress:
                    self._last_progress = p
                    events.publish("index_progress", **p)
            except Exception:
                log.exception("progress failed")
            self.stop.wait(0.7)

    def _names_changed(self) -> None:
        self.names_version += 1
        events.publish("names_changed", version=self.names_version)

    # ------------------------------------------------------------ watcher

    def _start_watcher(self) -> None:
        self._watch_stop.set()
        self._watch_stop = threading.Event()
        from ..fs.network import is_network

        all_roots = [r for r in roots() if os.path.isdir(r)]
        # Inotify does not see a change that another computer makes on a share. Network roots get a poll.
        net = [r for r in all_roots if is_network(r)]
        rs = [r for r in all_roots if r not in net]
        stop = self._watch_stop
        if net:
            threading.Thread(target=self._poll_network, args=(net, stop), name="index-net-poll", daemon=True).start()
        if not rs:
            return

        def keep(change: Change, path: str) -> bool:
            for r in rs:
                if path.startswith(r.rstrip("/") + "/"):
                    return not any(_skip_name(p) for p in Path(path).relative_to(r).parts)
            return False

        def run():
            # One watch per folder, with no recursion: a recursive watch also adds node_modules and caches
            # (the OS limit), and it fails as a whole on one folder that Raven cannot read (lost+found).
            dirs, full = watch_dirs(rs, watch_budget())
            if full:
                log.warning("watcher: %d folders, the limit for Raven. Raven sees changes in deeper folders at its next start", len(dirs))
            if stop.is_set():
                return
            try:
                for changes in watch(*dirs, watch_filter=keep, stop_event=stop, debounce=400, recursive=False):
                    paths = sorted({p for _, p in changes})
                    try:
                        self.update_paths(paths)
                    except Exception:
                        log.exception("watch update failed")
                    # A new folder needs its own watch: make the folder list again, at most every 30 s.
                    if any(c == Change.added and os.path.isdir(p) for c, p in changes):
                        self._rewatch_later()
                        return
            except Exception:
                log.exception("watcher stopped")

        threading.Thread(target=run, name="index-watch", daemon=True).start()

    def _poll_network(self, net_roots: list[str], stop: threading.Event) -> None:
        """PLAN2 P2-9.4: every NET_POLL seconds, stat each indexed folder of a network root. A folder whose
        modified time changed is read again. Some shares (S3) keep no folder time, so a few folders are also
        read in turn on each poll (NET_ROUND_ROBIN)."""
        seen: dict[str, int] = {}
        turn = 0
        while not stop.wait(NET_POLL):
            if self.stop.is_set():
                return
            for r in net_roots:
                try:
                    dirs = [r] + [row[0] for row in db.conn().execute(
                        "SELECT path FROM files WHERE root=? AND is_dir=1 AND light=0 ORDER BY path LIMIT ?", (r, NET_POLL_DIRS))]
                except Exception:  # noqa: BLE001
                    continue
                changed = []
                for d in dirs:
                    if stop.is_set():
                        return
                    try:
                        m = os.stat(d).st_mtime_ns
                    except OSError:
                        continue
                    if d in seen and seen[d] != m:
                        changed.append(d)
                    seen[d] = m
                timed = set(changed)
                if dirs:
                    k = turn % len(dirs)
                    changed += [d for d in dirs[k:k + NET_ROUND_ROBIN] if d not in timed]
                    turn += NET_ROUND_ROBIN
                for d in changed:
                    try:
                        with os.scandir(d) as it:
                            now = {de.path for de in it if not _skip_name(de.name) and not de.is_symlink()}
                        known = {row[0] for row in db.conn().execute("SELECT path FROM files WHERE parent=?", (d,))}
                    except OSError:
                        continue
                    # A folder with a new time: every item (sizes, times). A folder read in turn: only new or gone items.
                    if d in timed or now != known:
                        try:
                            self.update_paths(sorted(now | known) if d in timed else sorted(now ^ known))
                        except Exception:
                            log.exception("network poll update failed")

    def _rewatch_later(self) -> None:
        wait = max(0.0, getattr(self, "_last_rewatch", 0.0) + 30 - time.time())
        self._last_rewatch = time.time() + wait
        stop = self._watch_stop
        threading.Timer(wait, lambda: None if self.stop.is_set() or stop is not self._watch_stop else self._start_watcher()).start()


NET_POLL = float(os.environ.get("RAVEN_NET_POLL", "30"))  # seconds between polls of a network root
NET_POLL_DIRS = 5000  # folders per network root that a poll checks
NET_ROUND_ROBIN = 20  # folders per poll that Raven reads again, whatever their time says


def watch_budget() -> int:
    """At most half of the OS limit of watches (other apps need watches too), and at most 200,000."""
    try:
        with open("/proc/sys/fs/inotify/max_user_watches") as f:
            limit = int(f.read())
    except (OSError, ValueError):
        limit = 65536
    return max(1000, min(200_000, limit // 2))


def watch_dirs(roots_: list[str], budget: int) -> tuple[list[str], bool]:
    """The folders to watch, top folders first: no skipped names (node_modules, caches, hidden folders),
    no symbolic links and no folder that Raven cannot read. The flag says the budget cut the list."""
    out: list[str] = []
    queue = list(roots_)
    seen: set[str] = set()
    while queue:
        d = queue.pop(0)
        if d in seen:
            continue
        seen.add(d)
        try:
            with os.scandir(d) as it:
                subs = [e.path for e in it if not _skip_name(e.name) and e.is_dir(follow_symlinks=False) and not _skip_dir(e.path)]
        except OSError:
            continue  # cannot read it: no watch, and no error for the rest
        out.append(d)
        if len(out) >= budget:
            return out, True
        queue.extend(subs)
    return out, False


indexer = Indexer()
