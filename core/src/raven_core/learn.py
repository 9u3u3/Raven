"""Learn from user corrections (section 6.2, P2).

1. Categories. "Set category" stores the file's vector as a labeled example. A new file whose vector is
   very close to an example gets the same category before the prototype and LLM steps (classify.py).
   The reason names the example, so the user sees why.
2. Organize folders. When the user changes the folder of an item in an Organize plan, Raven keeps the
   folder for that category. The next plan uses it.
3. Rules from habits. When the user moves files of one category from one folder to the same folder three
   times, Raven suggests a watch rule (rules.py). The user adds it or ignores it.
"""

from __future__ import annotations

import json
import os
import struct
import time
from collections import Counter

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY, path TEXT, name TEXT, category TEXT, vec BLOB, ts INTEGER
);
"""
# A file this close to a labeled example gets its category. Higher than the prototype threshold (0.32),
# because an example is one file, not a description of a whole category.
NEAR = 0.80
HABIT_MIN = 3
HABIT_DAYS = 30


def init() -> None:
    db.conn().executescript(SCHEMA)


def _unpack(b: bytes) -> list[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b))


def set_category(path: str, category: str) -> dict:
    from . import classify

    valid = set(classify.CATEGORIES) | set(classify.RULE_ONLY) | {"Other"}
    if category not in valid:
        raise ValueError(f"Unknown category: {category}")
    c = db.conn()
    row = c.execute("SELECT id, name FROM files WHERE path=?", (path,)).fetchone()
    if row is None:
        raise ValueError("This file is not indexed")
    vec = classify.file_vector(row["id"])
    with db.tx() as tc:
        tc.execute("UPDATE files SET category=?, category_source='user', category_conf=1.0, category_reason=? WHERE id=?",
                   (category, "you set it", row["id"]))
        tc.execute("DELETE FROM corrections WHERE path=?", (path,))
        if vec is not None:
            tc.execute("INSERT INTO corrections(path, name, category, vec, ts) VALUES (?,?,?,?,?)",
                       (path, row["name"], category, struct.pack(f"{len(vec)}f", *vec), int(time.time())))
    # Files that are very close to this one and that a weaker step classified follow the correction now.
    followed = 0
    if vec is not None:
        for r in c.execute("SELECT id FROM files WHERE category_source IN ('embedding','llm') AND id != ?", (row["id"],)).fetchall():
            v = classify.file_vector(r["id"])
            if v is not None and classify._cos(v, vec) >= NEAR:
                with db.tx() as tc:
                    tc.execute("UPDATE files SET category=?, category_source='learned', category_conf=?, category_reason=? WHERE id=?",
                               (category, NEAR, f"like {row['name']}, which you marked {category.lower()}", r["id"]))
                followed += 1
    return {"category": category, "learned": vec is not None, "followed": followed}


def by_example(vec: list[float]) -> tuple[str, float, str] | None:
    """Classification step 1b: the nearest labeled example, when it is close enough."""
    from . import classify

    best = None
    for name, cat, blob in db.conn().execute("SELECT name, category, vec FROM corrections"):
        s = classify._cos(vec, _unpack(blob))
        if s >= NEAR and (best is None or s > best[1]):
            best = (cat, s, name)
    if best is None:
        return None
    cat, s, name = best
    return cat, round(s, 3), f"like {name}, which you marked {cat.lower()} ({s:.2f})"


def examples() -> list[dict]:
    return [dict(r) for r in db.conn().execute("SELECT id, path, name, category, ts FROM corrections ORDER BY ts DESC")]


def forget(example_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM corrections WHERE id=?", (example_id,))


# ---------------------------------------------------------------- organize preferences


def folder_prefs() -> dict[str, str]:
    return db.get_setting("organize_folders", {}) or {}


def note_plan_edits(root: str, moves: list, applied: list[dict]) -> dict[str, str]:
    """Compare the plan with what the user applied. A changed folder for a category is kept."""
    suggested = {m.src: m for m in moves}
    prefs = folder_prefs()
    changed = {}
    for it in applied:
        m = suggested.get(os.path.abspath(it["src"]))
        if m is None or not m.category or not m.move:
            continue
        want = os.path.relpath(os.path.dirname(os.path.abspath(it["dst"])), root)
        had = os.path.relpath(os.path.dirname(m.dst), root)
        if want != had and want != "." and not want.startswith(".."):
            prefs[m.category] = want
            changed[m.category] = want
    if changed:
        db.set_setting("organize_folders", prefs)
    return changed


# ---------------------------------------------------------------- habits -> rule suggestions


def habit_suggestions() -> list[dict]:
    """Manual moves in the journal: the same category from one folder to one folder, HABIT_MIN times."""
    c = db.conn()
    since = int((time.time() - HABIT_DAYS * 86400) * 1000)
    rows = c.execute(
        "SELECT src, dst FROM operations WHERE op='move' AND source='manual' AND undone=0 AND ts >= ?", (since,)
    ).fetchall()
    counts: Counter = Counter()
    for r in rows:
        if not r["src"] or not r["dst"] or os.path.dirname(r["src"]) == os.path.dirname(r["dst"]):
            continue
        f = c.execute("SELECT category FROM files WHERE path=?", (r["dst"],)).fetchone()
        cat = f[0] if f else None
        if not cat or cat == "Other":
            continue
        counts[(os.path.dirname(r["src"]), cat, os.path.dirname(r["dst"]))] += 1
    have = {(x["folder"], x["value"], x["target"]) for x in json.loads(json.dumps(_rules()))}
    out = []
    for (src_dir, cat, dst_dir), n in counts.most_common(5):
        if n >= HABIT_MIN and (src_dir, cat, dst_dir) not in have:
            out.append({"folder": src_dir, "match": "category", "value": cat, "action": "move", "target": dst_dir, "times": n,
                        "text": f"You moved {n} {cat.lower()} files from {os.path.basename(src_dir)} to {os.path.basename(dst_dir)}."})
    from . import habits

    return out + habits.rename_rule_suggestions()


def naming_style() -> dict:
    """The user's naming style from their renames (habits.py, PLAN2 P2-8.4)."""
    from . import habits

    return habits.naming_style()


def _rules() -> list[dict]:
    from . import rules

    return rules.list_rules()
