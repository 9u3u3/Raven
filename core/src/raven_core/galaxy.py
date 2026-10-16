"""File Galaxy (section 13.12 item 2): a 2D map of the indexed files by meaning.

1. Each file's vector is the mean of its first chunk vectors (the vectors that search already uses).
2. UMAP (cosine) puts the vectors on a plane. Files with close meaning land close together.
3. HDBSCAN finds the groups on the plane. Points in no group stay grey.
4. The local LLM names each group from its file names and titles, in one JSON call.
   Without the model, a group gets its most common category or folder name.
5. The map is cached. It is built again on request when the index changed.

A group can become a folder: `cluster_plan` lists the moves, and `cluster_apply` runs them as one undo.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter

import numpy as np

from . import db, events
from .fs import ops

log = logging.getLogger("raven.galaxy")

MAX_POINTS = 20_000
CHUNKS_PER_FILE = 4

_state = {"building": False, "error": None, "progress": ""}
_lock = threading.Lock()


def _signature() -> str:
    c = db.conn()
    n = c.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks WHERE embedded=1").fetchone()
    return f"{n[0]}:{n[1]}"


def _vectors() -> tuple[list[dict], np.ndarray]:
    c = db.conn()
    rows = c.execute(
        "SELECT f.id, f.path, f.name, f.title, f.category, f.parent FROM files f "
        "WHERE f.is_dir=0 AND EXISTS (SELECT 1 FROM chunks ch WHERE ch.file_id=f.id AND ch.embedded=1) "
        "ORDER BY f.mtime DESC LIMIT ?",
        (MAX_POINTS,),
    ).fetchall()
    files, vecs = [], []
    for r in rows:
        blobs = c.execute(
            "SELECT v.embedding FROM chunks ch JOIN chunk_vec v ON v.rowid = ch.id WHERE ch.file_id=? ORDER BY ch.id LIMIT ?",
            (r["id"], CHUNKS_PER_FILE),
        ).fetchall()
        if not blobs:
            continue
        m = np.mean([np.frombuffer(b[0], dtype=np.float32) for b in blobs], axis=0)
        m /= np.linalg.norm(m) or 1.0
        files.append(dict(r))
        vecs.append(m)
    return files, np.array(vecs, dtype=np.float32)


def _layout(X: np.ndarray) -> np.ndarray:
    n = len(X)
    if n < 5:
        # Too few files for UMAP: a small circle.
        ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return np.stack([np.cos(ang), np.sin(ang)], axis=1) if n else np.zeros((0, 2))
    import umap

    import warnings

    warnings.filterwarnings("ignore", message="n_jobs value", category=UserWarning)
    reducer = umap.UMAP(n_neighbors=min(15, n - 1), min_dist=0.08, metric="cosine", random_state=7, init="random" if n < 30 else "spectral")
    return reducer.fit_transform(X)


def _clusters(Y: np.ndarray) -> np.ndarray:
    n = len(Y)
    if n < 6:
        return np.zeros(n, dtype=int) if n else np.zeros(0, dtype=int)
    from sklearn.cluster import HDBSCAN

    # "leaf" picks the small dense groups (bills, lecture notes, logs), not two halves of the map.
    return HDBSCAN(min_cluster_size=max(3, n // 60), min_samples=2, cluster_selection_method="leaf", copy=True).fit_predict(Y)


NAME_SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {"type": "object", "properties": {
        "i": {"type": "integer"}, "name": {"type": "string"}}, "required": ["i", "name"]}}},
    "required": ["groups"],
}
NAME_SYSTEM = ("You name groups of files on a student's laptop. For each numbered group you get some file names and titles. "
               "Give each group a short folder-style name of 2 to 4 words, such as \"Tax 2025\", \"Sem 3 code\" or \"Train tickets\". "
               "Use the words that the files share. Do not use the word 'files'.")


def _fallback_name(members: list[dict]) -> str:
    cats = Counter(m["category"] for m in members if m.get("category") and m["category"] != "Other")
    if cats and cats.most_common(1)[0][1] >= len(members) / 2:
        return cats.most_common(1)[0][0]
    folders = Counter(os.path.basename(m["parent"]) for m in members)
    return folders.most_common(1)[0][0] or "Group"


def _names(groups: dict[int, list[dict]]) -> dict[int, str]:
    from .router import local

    out = {g: _fallback_name(ms) for g, ms in groups.items()}
    if not groups or not local.available():
        return out
    lines = []
    for g, ms in groups.items():
        sample = ", ".join((m["title"] or m["name"])[:60] for m in ms[:8])
        lines.append(f"{g}. {sample}")
    try:
        data = local.chat(NAME_SYSTEM, "\n".join(lines), NAME_SCHEMA, max_tokens=24 * len(groups) + 40)
        for it in data.get("groups", []):
            name = " ".join(str(it.get("name", "")).split())[:40].strip(" ./")
            if int(it.get("i", -1)) in out and name:
                out[int(it["i"])] = name
    except Exception as e:  # noqa: BLE001
        log.info("galaxy names: %s", e)
    return out


def build() -> dict:
    from . import lite

    if not lite.enabled("galaxy"):
        raise ValueError("The file map by meaning is off (lite mode). The link graph still works.")
    with _lock:
        if _state["building"]:
            return status()
        _state.update(building=True, error=None, progress="reading vectors")
    try:
        t0 = time.perf_counter()
        sig = _signature()
        files, X = _vectors()
        _state["progress"] = f"placing {len(files)} files"
        Y = _layout(X)
        labels = _clusters(Y)
        if len(Y):
            Y = (Y - Y.mean(axis=0)) / (np.abs(Y - Y.mean(axis=0)).max() or 1.0)
        groups: dict[int, list[dict]] = {}
        for f, lab in zip(files, labels):
            if lab >= 0:
                groups.setdefault(int(lab), []).append(f)
        _state["progress"] = f"naming {len(groups)} groups"
        names = _names(groups)
        points = [{"id": f["id"], "path": f["path"], "name": f["name"], "category": f["category"], "x": round(float(y[0]), 4),
                   "y": round(float(y[1]), 4), "c": int(lab)} for f, y, lab in zip(files, Y, labels)]
        clusters = []
        for g, ms in groups.items():
            idx = [i for i, lab in enumerate(labels) if lab == g]
            cx, cy = Y[idx].mean(axis=0)
            clusters.append({"id": g, "name": names[g], "count": len(ms), "x": round(float(cx), 4), "y": round(float(cy), 4)})
        data = {"sig": sig, "built": int(time.time()), "ms": round((time.perf_counter() - t0) * 1000), "points": points,
                "clusters": sorted(clusters, key=lambda c: -c["count"])}
        db.set_setting("galaxy", data)
        events.publish("galaxy_ready", points=len(points), clusters=len(clusters))
        return data
    except Exception as e:  # noqa: BLE001
        log.exception("galaxy build failed")
        _state["error"] = str(e)[:200]
        raise
    finally:
        _state.update(building=False, progress="")


def get() -> dict:
    data = db.get_setting("galaxy") or {"points": [], "clusters": [], "sig": None}
    return {**data, "stale": data.get("sig") != _signature(), **status()}


def status() -> dict:
    return {"building": _state["building"], "error": _state["error"], "progress": _state["progress"]}


def build_async() -> dict:
    from . import lite

    if not lite.enabled("galaxy"):
        raise ValueError("The file map by meaning is off (lite mode). The link graph still works.")
    if not _state["building"]:
        threading.Thread(target=lambda: _safe(build), name="galaxy", daemon=True).start()
        _state["building"] = True
        _state["progress"] = "starting"
    return status()


def _safe(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


def cluster_files(cluster: int) -> list[dict]:
    data = db.get_setting("galaxy") or {}
    return [p for p in data.get("points", []) if p["c"] == cluster and os.path.exists(p["path"])]


def cluster_plan(cluster: int, parent: str | None = None, name: str | None = None) -> dict:
    """Moves that put one group's files into one new folder, next to most of them (or in `parent`)."""
    data = db.get_setting("galaxy") or {}
    meta = next((c for c in data.get("clusters", []) if c["id"] == cluster), None)
    if meta is None:
        raise ValueError("No such group. Build the map again.")
    files = cluster_files(cluster)
    if not files:
        raise ValueError("The files of this group are gone.")
    parent = parent or Counter(os.path.dirname(f["path"]) for f in files).most_common(1)[0][0]
    folder = os.path.join(parent, ops._valid_name((name or meta["name"]).replace("/", "-")))
    moves = [{"src": f["path"], "dst": os.path.join(folder, os.path.basename(f["path"]))} for f in files if os.path.dirname(f["path"]) != folder]
    return {"cluster": cluster, "name": meta["name"], "folder": folder, "moves": moves}


def cluster_apply(folder: str, moves: list[dict]) -> dict:
    g = ops.Group("galaxy", f"Group into {os.path.basename(folder)}")
    done = 0
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
            g.add("create", None, folder)
        for m in moves:
            if os.path.dirname(os.path.abspath(m["dst"])) != os.path.abspath(folder) or not os.path.lexists(m["src"]):
                continue
            ops.move([m["src"]], folder, "keep_both", "galaxy", group=g)
            done += 1
    finally:
        g.commit()
    return {"moved": done, "label": g.label, "journal": ops.peek()}
