"""The hybrid router (section 4): each task goes to the cheapest tier that can do it well.

T3 (cloud) only when every condition of section 4.1 is true:
 1. the task is heavy, or the user pressed "Think harder";
 2. the AI mode is Hybrid, or Ask each time and the user accepted;
 3. a provider is configured;
 4. the payload passes the redaction gate.
Otherwise T2 (local LLM), with a "local result" reason. Raven never blocks on the cloud.
"""

from __future__ import annotations

import os
import time

from .. import db
from ..fs.kinds import kind_of
from ..privacy import gate, review
from . import cloud, local
from . import providers as registry

HEAVY_TASKS = {"folder_summary"}
# A folder summary is heavy (section 4.1) when the folder is large. "Think harder" makes any task heavy.
HEAVY_FOLDER_FILES = 40
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Two or three sentences about what the folder holds"},
        "points": {"type": "array", "items": {"type": "string"}, "description": "Four to eight short findings"},
    },
    "required": ["summary", "points"],
}
SUMMARY_SYSTEM = (
    "You describe a folder on the user's computer from a list of its files and short text samples. "
    "Placeholders such as [PERSON_1] or [ACCOUNT_2] stand for private values. Copy placeholders exactly as they are, "
    "never guess their values. Write plain, short sentences. Mention groups of files, main topics, duplicates, "
    "old or large files, and files that hold personal data."
)


def ai_mode() -> str:
    """local or hybrid. The old "ask" (Ask each time) is hybrid now: every cloud call waits for a review (PLAN4 7.7)."""
    m = db.get_setting("ai_mode") or "hybrid"
    return "hybrid" if m == "ask" else m


def _human(n: int | None) -> str:
    n = n or 0
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def build_folder_payload(path: str, red: gate.Redactor | None) -> tuple[str, int]:
    """Metadata lines and text samples for a folder. With a redactor, every part is redacted."""
    path = os.path.abspath(path)
    c = db.conn()
    like = path.rstrip("/").replace("%", "\\%").replace("_", "\\_") + "/%"
    rows = c.execute(
        "SELECT id, path, name, ext, size, mtime, category FROM files WHERE path LIKE ? ESCAPE '\\' AND is_dir=0 "
        "ORDER BY mtime DESC LIMIT 80",
        (like,),
    ).fetchall()
    if not rows:
        # Not indexed: list the folder directly (names only).
        rows = []
        from ..privacy.scope import is_private

        for dp, dns, fns in os.walk(path):
            dns[:] = [d for d in dns if not d.startswith(".") and not is_private(os.path.join(dp, d))]
            for fn in fns[:80]:
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                rows.append({"id": None, "path": p, "name": fn, "ext": fn.rsplit(".", 1)[-1].lower() if "." in fn else "",
                             "size": st.st_size, "mtime": int(st.st_mtime), "category": None})
            if len(rows) >= 80:
                break
    # Relative paths: never the full path with the user name (section 10.2). One layer-2 call for all names.
    rels = [os.path.relpath(r["path"], path) for r in rows] + [os.path.basename(path)]
    safe = dict(zip(rels, red.redact_names(rels))) if red else {x: x for x in rels}
    lines = []
    for r in rows:
        rel = safe[os.path.relpath(r["path"], path)]
        month = time.strftime("%Y-%m", time.localtime(r["mtime"] or 0))
        lines.append(f"- {rel} | {kind_of(r['name'], False)} | {_human(r['size'])} | modified {month}")
    samples = []
    from ..privacy.scope import cloud_allowed

    for r in rows:
        if r["id"] is None or len(samples) >= 12:
            continue
        if red and not cloud_allowed(r["path"]):
            continue  # a cloud payload holds no text from photos, videos or audio (PLAN4 7.6)
        ch = c.execute("SELECT id, text FROM chunks WHERE file_id=? ORDER BY id LIMIT 1", (r["id"],)).fetchone()
        if not ch:
            continue
        text = ch["text"][:400]
        if red:
            spans, l2_done = gate.cached_spans(ch["id"])
            text = red.redact(text, [s for s in spans if s.end <= len(text)], cached_layer2=l2_done)
        rel = safe[os.path.relpath(r["path"], path)]
        samples.append(f"### {rel}\n{' '.join(text.split())}")
    name = safe[os.path.basename(path)]
    payload = (
        f"Folder: {name}\nFiles ({len(lines)} shown, newest first):\n" + "\n".join(lines)
        + "\n\nText samples (the start of some files):\n" + "\n\n".join(samples)
        + "\n\nDescribe this folder."
    )
    return payload, len(rows)


def provider_order(task: str) -> list[str]:
    """The cloud providers that can run, in the user's order (PLAN4 4). The first one that works answers.
    `task` stays for the callers. The order is the same for every task."""
    _ = task
    # Local only makes no outbound connection, not even the online check.
    if ai_mode() == "local":
        return []
    ids = registry.order()
    return ids if ids and cloud.online() else []


def _file_count(path: str) -> int:
    like = os.path.abspath(path).rstrip("/").replace("%", "\\%").replace("_", "\\_") + "/%"
    return db.conn().execute("SELECT COUNT(*) FROM files WHERE path LIKE ? ESCAPE '\\' AND is_dir=0", (like,)).fetchone()[0]


def run(task: str, path: str, think_harder: bool = False) -> dict:
    """Run a task. While it runs, background indexing yields the GPU and the PII model."""
    from ..index.indexer import indexer

    indexer.interactive.set()
    try:
        return _run(task, path, think_harder)
    finally:
        indexer.interactive.clear()


def _run(task: str, path: str, think_harder: bool = False) -> dict:
    if task not in HEAVY_TASKS:
        raise ValueError(f"Unknown task {task}")
    from ..privacy import scope

    scope.require_ai(path)  # a private folder (PLAN4 7.6): no summary, local or cloud
    t0 = time.perf_counter()
    mode = ai_mode()
    heavy = think_harder or _file_count(path) >= HEAVY_FOLDER_FILES
    providers = provider_order(task)
    reason = ""
    tier = "T3"
    from ..fs.props import policy_for

    if not heavy:
        tier, reason = "T2", "a small folder runs on the local model. Think harder sends it to the cloud"
    elif policy_for(path, "no_cloud"):
        tier, reason = "T2", "a folder rule keeps this folder off the cloud"
    elif mode == "local":
        tier, reason = "T2", "Local only mode"
    elif not providers:
        tier, reason = "T2", "no cloud provider is set up" if cloud.online() else "this computer is offline, so the local model answers"

    if tier == "T3":
        gate.warm()  # PLAN4 B7: load the models before the many short name checks
        red = gate.Redactor()
        payload, n = build_folder_payload(path, red)
        try:
            payload = gate.seal(payload, red)
            why = ""
        except gate.Blocked as e:
            why = str(e)
        if why:
            tier, reason = "T2", f"the redaction gate blocked the request: {why}"
        else:
            try:
                # PLAN4 7.7: the user sees this exact payload and sends it, or not.
                payload = review.confirm(payload, red, "Folder summary", providers, SUMMARY_SYSTEM)
            except gate.Blocked as e:
                tier, reason = "T2", str(e)
        if tier == "T3":
            errors = []
            for prov in providers:
                lid = gate.ledger_write(task, "T3", prov, payload, len(red.mapping))
                try:
                    data, model = registry.chat(prov, SUMMARY_SYSTEM, payload, SUMMARY_SCHEMA)
                except cloud.CloudError as e:
                    errors.append(f"{prov}: {e}")
                    with db.tx() as c:
                        c.execute("UPDATE ledger SET feature=? WHERE id=?", (f"{task} (failed)", lid))
                    continue
                # Step 9: original values go back into the answer, on this machine only.
                summary = gate.restore(str(data.get("summary", "")), red.mapping)
                points = [gate.restore(str(p), red.mapping) for p in data.get("points", [])]
                return {
                    "tier": "T3", "provider": prov, "model": model, "summary": summary, "points": points,
                    "redacted": len(red.mapping), "entities": red.entities, "layer2": red.layer2_state, "ledger_id": lid,
                    "files": n, "ms": round((time.perf_counter() - t0) * 1000), "fallback": None,
                }
            tier, reason = "T2", "the cloud failed (" + "; ".join(errors) + ")"

    # T2: the local model sees the real payload. Nothing leaves the machine, so nothing goes to the ledger.
    if not local.available():
        # Say why the cloud did not answer too (a block, fail closed), not only why the local model cannot.
        why = local.unavailable_reason()
        raise local.LocalError(f"{why} The cloud did not answer: {reason}." if heavy and reason else why)
    payload, n = build_folder_payload(path, None)
    data = local.chat(SUMMARY_SYSTEM, payload, SUMMARY_SCHEMA)
    return {
        "tier": "T2", "provider": "ollama", "model": local.LLM, "summary": str(data.get("summary", "")),
        "points": [str(p) for p in data.get("points", [])], "redacted": 0, "entities": [], "layer2": None,
        "ledger_id": None, "files": n, "ms": round((time.perf_counter() - t0) * 1000), "fallback": reason,
    }
