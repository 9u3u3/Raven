"""Watch folders with rules (section 6.2, P2).

A rule watches one folder (not its subfolders) and acts on files that arrive there after the rule exists:
  match   category (from the classification cascade), extension list, or a word in the name;
  action  move to a folder, rename with a template ({name} {ext} {date} {category}), or "name": give a file
          with a generic name a real name (naming.py, PLAN2 P2-3.2). The match "generic" takes those files.
A file must be stable for STABLE seconds (a download in progress is not touched). One check of one
rule is one journal group with source "rule", so one undo reverses it, and Raven says what it did in
the status line. A file that a rule handled once is never handled again by that rule, even after undo.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime

from . import db, events
from .fs import ops

log = logging.getLogger("raven.rules")

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_rules (
  id INTEGER PRIMARY KEY, folder TEXT NOT NULL, match TEXT NOT NULL, value TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT NOT NULL, enabled INTEGER DEFAULT 1, created INTEGER, applied INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rule_log (rule_id INTEGER, path TEXT, size INTEGER, mtime INTEGER, ts INTEGER);
CREATE INDEX IF NOT EXISTS rule_log_rule ON rule_log(rule_id, path);
"""
STABLE = 20
NAME_WAIT = 600  # a "name" rule waits up to 10 minutes for the index to read a new file
CHECK_EVERY = 10
PARTIAL = (".part", ".crdownload", ".tmp", ".download", ".partial")


def init() -> None:
    db.conn().executescript(SCHEMA)


def list_rules() -> list[dict]:
    return [dict(r) for r in db.conn().execute("SELECT * FROM watch_rules ORDER BY id")]


def _check(rule: dict) -> dict:
    folder = os.path.abspath(os.path.expanduser(rule["folder"]))
    if not os.path.isdir(folder):
        raise ValueError(f"Folder not found: {folder}")
    if rule["match"] not in ("category", "ext", "name", "generic"):
        raise ValueError("match must be category, ext, name or generic")
    if rule["action"] not in ("move", "rename", "name"):
        raise ValueError("action must be move, rename or name")
    if rule["match"] == "generic" and not str(rule["value"]).strip():
        rule = {**rule, "value": "*"}
    if not str(rule["value"]).strip():
        raise ValueError("The rule needs a value to match")
    target = str(rule["target"]).strip()
    if rule["action"] == "move":
        target = os.path.abspath(os.path.expanduser(target))
        if target == folder:
            raise ValueError("The target is the watched folder itself")
    elif rule["action"] == "name":
        target = "auto"
    elif "/" in target or not target:
        raise ValueError("A rename template is a file name, such as {date} {name}{ext}")
    return {**rule, "folder": folder, "target": target}


def add_rule(rule: dict) -> dict:
    r = _check(rule)
    with db.tx() as c:
        rid = c.execute(
            "INSERT INTO watch_rules(folder, match, value, action, target, enabled, created) VALUES (?,?,?,?,?,1,?)",
            (r["folder"], r["match"], str(r["value"]).strip(), r["action"], r["target"], int(time.time())),
        ).lastrowid
        # The files that are there now are not new: note them (ts 0), so only later arrivals count.
        # "Run now" takes them too.
        c.executemany("INSERT INTO rule_log VALUES (?,?,?,?,0)",
                      [(rid, de.path, 0, 0) for de in os.scandir(r["folder"]) if de.is_file(follow_symlinks=False)])
    return dict(db.conn().execute("SELECT * FROM watch_rules WHERE id=?", (rid,)).fetchone())


def update_rule(rule_id: int, enabled: bool | None = None) -> None:
    with db.tx() as c:
        if enabled is not None:
            c.execute("UPDATE watch_rules SET enabled=? WHERE id=?", (int(enabled), rule_id))


def delete_rule(rule_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM watch_rules WHERE id=?", (rule_id,))
        c.execute("DELETE FROM rule_log WHERE rule_id=?", (rule_id,))


def _matches(rule: dict, path: str, name: str) -> bool:
    v = rule["value"].lower()
    if rule["match"] == "generic":
        from .naming import is_generic

        return is_generic(name)
    if rule["match"] == "ext":
        exts = {e.strip().lstrip(".") for e in v.split(",") if e.strip()}
        return name.lower().rsplit(".", 1)[-1] in exts if "." in name else False
    if rule["match"] == "name":
        return v in name.lower()
    row = db.conn().execute("SELECT category FROM files WHERE path=?", (path,)).fetchone()
    return bool(row and row[0] and row[0].lower() == v)


def _new_name(template: str, path: str, name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    d = datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%Y-%m-%d")
    row = db.conn().execute("SELECT category FROM files WHERE path=?", (path,)).fetchone()
    cat = (row[0] if row and row[0] else "File")
    out = template.replace("{name}", stem).replace("{ext}", f".{ext}" if ext else "").replace("{date}", d).replace("{category}", cat)
    return re.sub(r"\s+", " ", out).strip()


def run_rule(rule: dict, existing: bool = False) -> dict:
    """Apply one rule now. `existing` also takes files that were there before the rule (the "Run now" button)."""
    folder = rule["folder"]
    if not os.path.isdir(folder):
        return {"moved": 0, "error": f"Folder not found: {folder}"}
    done = {r[0] for r in db.conn().execute(
        "SELECT path FROM rule_log WHERE rule_id=? AND (ts > 0 OR ?)", (rule["id"], int(not existing)))}
    now = time.time()
    picked = []
    for de in os.scandir(folder):
        if not de.is_file(follow_symlinks=False) or de.name.startswith(".") or de.name.endswith(PARTIAL):
            continue
        st = de.stat(follow_symlinks=False)
        if now - st.st_mtime < STABLE or de.path in done:
            continue
        if _matches(rule, de.path, de.name):
            from .storage.protect import user_protected

            if rule["action"] == "move" and user_protected(rule["target"]):
                continue  # a folder rule protects the target
            picked.append((de.path, de.name, st))
    if not picked:
        return {"moved": 0}
    label = (f"Rule: move {rule['value']} files to {os.path.basename(rule['target'])}" if rule["action"] == "move"
             else "Rule: name new files" if rule["action"] == "name" else f"Rule: rename {rule['value']} files")
    g = ops.Group("rule", label)
    n, errors = 0, []
    log_rows = []
    try:
        if rule["action"] == "move":
            os.makedirs(rule["target"], exist_ok=True)
        for path, name, st in picked:
            try:
                if rule["action"] == "move":
                    ops.move([path], rule["target"], "keep_both", "rule", group=g)
                elif rule["action"] == "name":
                    from . import naming
                    from .privacy.scope import is_private

                    if is_private(path):
                        continue  # a private folder (PLAN4 7.6): no model names its files
                    row = db.conn().execute("SELECT id, title, category, mtime, index_stage FROM files WHERE path=?",
                                            (path,)).fetchone()
                    if (row is None or (row["index_stage"] or 0) < 2) and now - st.st_mtime < NAME_WAIT:
                        continue  # wait for the index to read the text (OCR, title); not logged, so a later check runs
                    sug = naming.suggest_one(path, dict(row) if row else None, use_llm=True)
                    if sug:
                        target = ops.unique_path(os.path.join(folder, sug["name"]))
                        ops.rename(path, os.path.basename(target), "rule", group=g)
                    else:
                        log_rows.append((rule["id"], path, st.st_size, int(st.st_mtime), int(now)))
                        continue
                else:
                    nn = _new_name(rule["target"], path, name)
                    if nn and nn != name:
                        target = ops.unique_path(os.path.join(folder, nn))
                        ops.rename(path, os.path.basename(target), "rule", group=g)
                n += 1
            except (OSError, ops.OpError) as e:
                errors.append(f"{name}: {e}")
            log_rows.append((rule["id"], path, st.st_size, int(st.st_mtime), int(now)))
    finally:
        g.commit()
        with db.tx() as c:
            c.executemany("INSERT INTO rule_log VALUES (?,?,?,?,?)", log_rows)
            c.execute("UPDATE watch_rules SET applied = applied + ? WHERE id=?", (n, rule["id"]))
    if n:
        events.publish("rule_applied", label=g.label, count=n)
    return {"moved": n, "errors": errors, "label": g.label, "journal": ops.peek()}


def loop(stop) -> None:
    while not stop.is_set():
        for r in list_rules():
            if not r["enabled"]:
                continue
            try:
                run_rule(r)
            except Exception:  # noqa: BLE001
                log.exception("rule %s failed", r["id"])
        stop.wait(CHECK_EVERY)
