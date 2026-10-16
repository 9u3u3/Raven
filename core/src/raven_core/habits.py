"""Personal habits (PLAN2 P2-8): Raven learns how you use your files, on this computer only.

What Raven records (table activity): opens, previews, searches, result clicks, and the suggestions you
accept or reject. Moves, renames and trash come from the undo journal (your own actions only).

What Raven does with it:
  - search ranks the files you open often and lately a little higher, and a file you clicked for a query
    comes first for that query again
  - Continue: the files you probably want now, from the time of day and the day of the week
  - Where it goes: the folder a new file probably belongs in, from your past moves
  - your naming style for new names (naming.py)
  - a rejected suggestion stays away for 30 days; a source you always reject stops
  - rule suggestions from repeated renames

Kept 90 days. One switch turns it off (setting "habits"). "Delete what Raven learned" clears it. Nothing here
leaves the computer: no cloud payload builder reads this module.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (ts INTEGER, kind TEXT, path TEXT, folder TEXT, ext TEXT, query TEXT, extra TEXT);
CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts);
CREATE INDEX IF NOT EXISTS activity_path ON activity(path);
"""
KINDS = {"open", "preview", "search", "result_click", "suggestion_accepted", "suggestion_rejected"}
KEEP_DAYS = 90
HALF_LIFE_DAYS = 14
WEIGHT = {"open": 1.0, "result_click": 1.5, "preview": 0.3}
QUIET_DAYS = 30
MUTE_AFTER = 3
DAY = 86400


def init() -> None:
    db.conn().executescript(SCHEMA)


def enabled() -> bool:
    v = db.get_setting("habits")
    return True if v is None else bool(v)


def _since() -> int:
    """Only data after the last "delete what Raven learned", and at most KEEP_DAYS old."""
    return max(int(time.time()) - KEEP_DAYS * DAY, int(db.get_setting("habits_since") or 0))


def record(kind: str, path: str | None = None, query: str | None = None, extra: dict | None = None) -> None:
    if kind not in KINDS or not enabled():
        return
    init()
    folder = os.path.dirname(path) if path else None
    ext = path.rsplit(".", 1)[-1].lower() if path and "." in os.path.basename(path) else None
    with db.tx() as c:
        c.execute("INSERT INTO activity VALUES (?,?,?,?,?,?,?)",
                  (int(time.time()), kind, path, folder, ext, (_norm(query) if kind in ("search", "result_click") else (query or "").strip())[:200]
                   or None, json.dumps(extra) if extra else None))


def prune() -> None:
    init()
    with db.tx() as c:
        c.execute("DELETE FROM activity WHERE ts < ?", (int(time.time()) - KEEP_DAYS * DAY,))


def clear() -> None:
    init()
    with db.tx() as c:
        c.execute("DELETE FROM activity")
    db.set_setting("habits_since", int(time.time()))


def _norm(q: str | None) -> str:
    return re.sub(r"\s+", " ", (q or "").lower()).strip()


# ---------------------------------------------------------------- ranking


def file_scores(paths: list[str] | None = None) -> dict[str, float]:
    """Frecency: each open, preview or result click counts, halved every HALF_LIFE_DAYS."""
    if not enabled():
        return {}
    init()
    now = time.time()
    q = "SELECT path, kind, ts FROM activity WHERE ts >= ? AND kind IN ('open','preview','result_click')"
    args: list = [_since()]
    if paths is not None:
        if not paths:
            return {}
        q += f" AND path IN ({','.join('?' * len(paths))})"
        args += paths
    out: dict[str, float] = defaultdict(float)
    for path, kind, ts in db.conn().execute(q, args):
        out[path] += WEIGHT.get(kind, 0.5) * 0.5 ** ((now - ts) / (HALF_LIFE_DAYS * DAY))
    return dict(out)


def clicks_for(query: str) -> dict[str, int]:
    """Files you clicked in the results of this query before."""
    if not enabled() or not query.strip():
        return {}
    init()
    rows = db.conn().execute("SELECT path, COUNT(*) FROM activity WHERE kind='result_click' AND query IS NOT NULL "
                             "AND ts >= ? AND lower(query)=? GROUP BY path", (_since(), _norm(query))).fetchall()
    return {p: n for p, n in rows}


def boost(query: str, paths: list[str]) -> dict[str, float]:
    """A score per path for search: frecency plus earlier clicks for this query (clicks weigh more)."""
    fs = file_scores(paths)
    ck = clicks_for(query)
    return {p: fs.get(p, 0.0) + 3.0 * ck.get(p, 0) for p in paths if fs.get(p) or ck.get(p)}


# ---------------------------------------------------------------- continue


def continue_files(limit: int = 6, now: float | None = None) -> list[dict]:
    """The files you probably want now: opened lately, more often at this time of day and on this weekday."""
    if not enabled():
        return []
    init()
    now = now or time.time()
    dt = datetime.fromtimestamp(now)
    score: dict[str, float] = defaultdict(float)
    for path, kind, ts in db.conn().execute(
            "SELECT path, kind, ts FROM activity WHERE ts >= ? AND kind IN ('open','result_click','preview')",
            (max(_since(), int(now) - 60 * DAY),)):
        t = datetime.fromtimestamp(ts)
        dh = abs(t.hour - dt.hour)
        dh = min(dh, 24 - dh)
        when = (1.0 if dh <= 2 else 0.0) + (0.5 if t.weekday() == dt.weekday() else 0.0)
        score[path] += WEIGHT.get(kind, 0.5) * 0.5 ** ((now - ts) / (7 * DAY)) * (1 + when)
    out = []
    for path, s in sorted(score.items(), key=lambda kv: -kv[1]):
        if os.path.isfile(path):
            out.append({"path": path, "name": os.path.basename(path), "score": round(s, 3)})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- where it goes


def _manual_moves() -> list[tuple[str, str]]:
    since_ms = _since() * 1000
    rows = db.conn().execute("SELECT src, dst FROM operations WHERE op='move' AND source='manual' AND undone=0 AND ts >= ?",
                             (since_ms,)).fetchall()
    return [(s, d) for s, d in rows if s and d and os.path.dirname(s) != os.path.dirname(d)]


def predict_folder(path: str) -> dict | None:
    """The folder that files like this one (same folder and extension, or category) went to before.
    It needs 2 moves or more, and 60% of them to one folder."""
    if not enabled():
        return None
    src_dir = os.path.dirname(path)
    ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
    c = db.conn()
    row = c.execute("SELECT category FROM files WHERE path=?", (path,)).fetchone()
    cat = row[0] if row else None
    by_ext: Counter = Counter()
    by_cat: Counter = Counter()
    for s, d in _manual_moves():
        if os.path.dirname(s) != src_dir:
            continue
        dst_dir = os.path.dirname(d)
        if dst_dir == src_dir:
            continue
        if ext and s.lower().endswith("." + ext):
            by_ext[dst_dir] += 1
        if cat:
            r = c.execute("SELECT category FROM files WHERE path=?", (d,)).fetchone()
            if r and r[0] == cat:
                by_cat[dst_dir] += 1
    for counts, why in ((by_cat, f"{cat} files" if cat else ""), (by_ext, f".{ext} files")):
        total = sum(counts.values())
        if total >= 2:
            folder, n = counts.most_common(1)[0]
            if n / total >= 0.6 and os.path.isdir(folder):
                return {"folder": folder, "count": n, "total": total, "why": f"{why} from here went there {n} times"}
    return None


# ---------------------------------------------------------------- naming style

_DATE = re.compile(r"(?<!\d)(19|20)\d{2}[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")


def naming_style(min_samples: int = 5) -> dict:
    """From your own renames: where the date goes, the separator, and the case. Empty until min_samples."""
    if not enabled():
        return {}
    since_ms = _since() * 1000
    names = [os.path.basename(d) for s, d in db.conn().execute(
        "SELECT src, dst FROM operations WHERE op='move' AND source='manual' AND undone=0 AND ts >= ?", (since_ms,))
        if s and d and os.path.dirname(s) == os.path.dirname(d)]
    if len(names) < min_samples:
        return {}
    stems = [n.rsplit(".", 1)[0] for n in names]
    date_pos: Counter = Counter()
    seps: Counter = Counter()
    cases: Counter = Counter()
    for st in stems:
        m = _DATE.search(st)
        date_pos["first" if m and m.start() == 0 else "last" if m and m.end() == len(st) else "none"] += 1
        rest = _DATE.sub("", st)
        seps.update({" ": rest.count(" "), "_": rest.count("_"), "-": rest.count("-")})
        letters = re.sub(r"[^A-Za-z]", "", rest)
        if letters:
            cases["lower" if letters.islower() else "title" if all(w[:1].isupper() for w in re.findall(r"[A-Za-z]+", rest)) else "mixed"] += 1
    style: dict = {"samples": len(names)}
    pos, n = date_pos.most_common(1)[0]
    if n / len(stems) >= 0.6:
        style["date"] = pos
    sep = max(seps, key=seps.get) if any(seps.values()) else " "
    style["sep"] = sep
    if cases:
        case, k = cases.most_common(1)[0]
        if case != "mixed" and k / sum(cases.values()) >= 0.6:
            style["case"] = case
    return style


# ---------------------------------------------------------------- quiet suggestions


def suggestion_feedback(row_id: str, accepted: bool) -> None:
    record("suggestion_accepted" if accepted else "suggestion_rejected", query=row_id)


def quiet() -> dict:
    """{"hidden": ids rejected in the last QUIET_DAYS, "muted": sources rejected MUTE_AFTER times and never accepted}."""
    if not enabled():
        return {"hidden": set(), "muted": set()}
    init()
    rows = db.conn().execute("SELECT kind, query, ts FROM activity WHERE kind IN ('suggestion_accepted','suggestion_rejected') AND ts >= ?",
                             (_since(),)).fetchall()
    last: dict[str, tuple[str, int]] = {}
    rej: Counter = Counter()
    acc: Counter = Counter()
    for kind, rid, ts in rows:
        if not rid:
            continue
        src = rid.split(":", 1)[0]
        if kind == "suggestion_rejected":
            rej[src] += 1
        else:
            acc[src] += 1
        if rid not in last or ts > last[rid][1]:
            last[rid] = (kind, ts)
    cut = time.time() - QUIET_DAYS * DAY
    hidden = {rid for rid, (kind, ts) in last.items() if kind == "suggestion_rejected" and ts >= cut}
    muted = {src for src, n in rej.items() if n >= MUTE_AFTER and not acc[src]}
    return {"hidden": hidden, "muted": muted}


# ---------------------------------------------------------------- rules from renames


def rename_rule_suggestions() -> list[dict]:
    """You renamed 3 or more generic files in one folder: suggest a "name" rule for that folder."""
    if not enabled():
        return []
    from .naming import is_generic

    since_ms = _since() * 1000
    per: Counter = Counter()
    for s, d in db.conn().execute("SELECT src, dst FROM operations WHERE op='move' AND source='manual' AND undone=0 AND ts >= ?", (since_ms,)):
        if s and d and os.path.dirname(s) == os.path.dirname(d) and is_generic(os.path.basename(s)) and not is_generic(os.path.basename(d)):
            per[os.path.dirname(s)] += 1
    from . import rules

    rules.init()
    have = {(r["folder"], r["action"]) for r in rules.list_rules()}
    return [{"folder": f, "match": "generic", "value": "*", "action": "name", "target": "auto", "times": n,
             "text": f"You gave {n} files in {os.path.basename(f)} a real name. Raven can do it for new files."}
            for f, n in per.most_common(3) if n >= 3 and (f, "name") not in have]


# ---------------------------------------------------------------- what Raven learned


def summary() -> dict:
    init()
    c = db.conn()
    kinds = dict(c.execute("SELECT kind, COUNT(*) FROM activity WHERE ts >= ? GROUP BY kind", (_since(),)).fetchall())
    folders = c.execute("SELECT folder, COUNT(*) n FROM activity WHERE ts >= ? AND kind IN ('open','result_click') AND folder IS NOT NULL "
                        "GROUP BY folder ORDER BY n DESC LIMIT 5", (_since(),)).fetchall()
    first = c.execute("SELECT MIN(ts) FROM activity WHERE ts >= ?", (_since(),)).fetchone()[0]
    q = quiet()
    return {
        "enabled": enabled(), "since": first, "keep_days": KEEP_DAYS, "counts": kinds,
        "moves": len(_manual_moves()), "top_folders": [{"folder": f, "opens": n} for f, n in folders],
        "naming_style": naming_style(), "continue": continue_files(), "muted": sorted(q["muted"]), "hidden": len(q["hidden"]),
    }
