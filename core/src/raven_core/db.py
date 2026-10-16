"""SQLite access. One file holds metadata, FTS5, vectors, the journal and settings (section 11)."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import sqlite_vec

from . import config
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  parent TEXT, name TEXT NOT NULL, ext TEXT,
  size INTEGER, mtime INTEGER, ctime INTEGER, atime INTEGER,
  is_dir INTEGER, is_hidden INTEGER, is_system INTEGER,
  root TEXT, mime TEXT,
  quick_hash TEXT, full_hash TEXT, phash TEXT,
  category TEXT, category_source TEXT, category_conf REAL, category_reason TEXT,
  pii_level INTEGER DEFAULT 0,
  index_stage INTEGER DEFAULT 0,
  index_error TEXT,
  title TEXT,
  last_opened_in_raven INTEGER,
  archived_in INTEGER REFERENCES compress_jobs(id)
);
CREATE INDEX IF NOT EXISTS files_parent ON files(parent);
CREATE INDEX IF NOT EXISTS files_root ON files(root);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
  page INTEGER, start INTEGER, end INTEGER,
  parent_chunk INTEGER,
  header TEXT, text TEXT,
  embedded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  name, title, headings, body, content='', contentless_delete=1,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS pii_spans (chunk_id INTEGER, type TEXT, start INTEGER, end INTEGER, score REAL, layer INTEGER);

CREATE TABLE IF NOT EXISTS operations (
  id INTEGER PRIMARY KEY, group_id TEXT, source TEXT,
  op TEXT, src TEXT, dst TEXT, ts INTEGER, undone INTEGER DEFAULT 0,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS operations_group ON operations(group_id);

CREATE TABLE IF NOT EXISTS compress_jobs (
  id INTEGER PRIMARY KEY, mode TEXT, method TEXT,
  archive_path TEXT, files_count INTEGER,
  size_before INTEGER, size_after INTEGER, reversible INTEGER,
  status TEXT, error TEXT, created INTEGER, restored INTEGER
);
CREATE TABLE IF NOT EXISTS compress_items (
  job_id INTEGER, original_path TEXT, size INTEGER, blake3 TEXT,
  mtime INTEGER, mode_bits INTEGER
);
CREATE TABLE IF NOT EXISTS compress_rules (
  id INTEGER PRIMARY KEY, root TEXT, min_cold_days INTEGER, min_size INTEGER,
  methods TEXT, daily_limit_bytes INTEGER, enabled INTEGER
);

CREATE TABLE IF NOT EXISTS agent_runs (id INTEGER PRIMARY KEY, ts INTEGER, prompt TEXT, tier TEXT, plan_json TEXT, status TEXT, op_group TEXT);
CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY, ts INTEGER, feature TEXT, tier TEXT, provider TEXT, bytes INTEGER, payload TEXT, pii_count INTEGER);
CREATE TABLE IF NOT EXISTS size_snapshots (ts INTEGER, path TEXT, size INTEGER);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS folder_visits (path TEXT PRIMARY KEY, count INTEGER, last INTEGER);
"""

_local = threading.local()
write_lock = threading.RLock()


def _open() -> sqlite3.Connection:
    from . import secure

    if secure.enabled():
        # Encrypted index (secure.py): SQLCipher with the passphrase that unlocked it.
        pw = secure.passphrase()
        if pw is None:
            raise RuntimeError("The index is locked")
        import sqlcipher3

        conn = secure._cipher_connect(DB_PATH, pw)
        conn.row_factory = sqlcipher3.Row
    else:
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    # SQLite makes the file, the WAL and the SHM with mode 0644. The data folder is 0700 already, but the
    # files get 0600 too (PLAN3 P0-2). This also covers the file that an encrypt or a decrypt swaps in.
    config.protect_data_files()
    return conn


def conn() -> sqlite3.Connection:
    """Return the connection of the current thread."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = _open()
        _local.conn = c
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Run a write transaction. One writer at a time."""
    c = conn()
    with write_lock:
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


# Columns added after the first schema. init() adds the ones an older DB lacks.
MIGRATIONS = [
    # 1: a file in a code project or a software folder below an index root: names only (indexer.py).
    ("files", "light", "INTEGER DEFAULT 0"),
    ("compress_jobs", "root", "TEXT"),
    ("compress_jobs", "label", "TEXT"),
    ("compress_jobs", "op_group", "TEXT"),
    ("compress_jobs", "restore_group", "TEXT"),
    ("compress_jobs", "manifest", "TEXT"),
    ("compress_items", "kind", "TEXT"),
    ("compress_items", "atime", "INTEGER"),
    ("chunks", "pii_done", "INTEGER DEFAULT 0"),
    # Seconds from the start of an audio or video file, for transcript chunks.
    ("chunks", "at", "REAL"),
    ("files", "transcript_sig", "TEXT"),
    # PLAN2 P2-3.1: the kind from the content, and 1 (mismatch) or 2 (a program with a document name).
    ("files", "sniffed_kind", "TEXT"),
    ("files", "kind_mismatch", "INTEGER DEFAULT 0"),
]


def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def visit(path: str) -> None:
    """Count a folder visit for frecency (section 6.1)."""
    import time as _t

    with tx() as c:
        c.execute(
            "INSERT INTO folder_visits(path, count, last) VALUES (?, 1, ?) "
            "ON CONFLICT(path) DO UPDATE SET count = count + 1, last = excluded.last",
            (path, int(_t.time())),
        )


def frecent(limit: int = 500) -> dict[str, float]:
    """Frecency of visited folders, 0 to 1: visits weighted by how recent the last one is (as `z` does)."""
    import time as _t

    now = _t.time()
    out = {}
    for path, count, last in conn().execute("SELECT path, count, last FROM folder_visits"):
        age = now - (last or 0)
        w = 4 if age < 3600 else 2 if age < 86400 else 0.5 if age < 7 * 86400 else 0.25
        out[path] = count * w
    top = sorted(out.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    best = top[0][1] if top else 1
    return {p: v / best for p, v in top}


def vec_dim() -> int | None:
    v = get_setting("vec_dim")
    return int(v) if v else None


def ensure_vec_table(dim: int, model: str) -> None:
    """Create chunk_vec for this dimension. A model change drops old vectors."""
    with tx() as c:
        cur_dim = c.execute("SELECT value FROM settings WHERE key='vec_dim'").fetchone()
        cur_model = c.execute("SELECT value FROM settings WHERE key='vec_model'").fetchone()
        same = cur_dim and int(json.loads(cur_dim[0])) == dim and cur_model and json.loads(cur_model[0]) == model
        if same:
            return
        c.execute("DROP TABLE IF EXISTS chunk_vec")
        # The binary copies belong to the old model too.
        c.execute("DROP TABLE IF EXISTS chunk_bin")
        c.execute("DROP TABLE IF EXISTS chunk_f16")
        c.execute(f"CREATE VIRTUAL TABLE chunk_vec USING vec0(embedding float[{dim}] distance_metric=cosine)")
        c.execute("UPDATE chunks SET embedded=0")
        c.execute("INSERT OR REPLACE INTO settings VALUES ('vec_dim', ?)", (json.dumps(dim),))
        c.execute("INSERT OR REPLACE INTO settings VALUES ('vec_model', ?)", (json.dumps(model),))


def has_vec_table() -> bool:
    return conn().execute("SELECT 1 FROM sqlite_master WHERE name='chunk_vec'").fetchone() is not None


def get_setting(key: str, default: Any = None) -> Any:
    row = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key: str, value: Any) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
