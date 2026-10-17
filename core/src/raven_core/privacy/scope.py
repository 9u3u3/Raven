"""What an AI may see (owner rules, 2026-10-17, PLAN4 section 7.6).

1. Private folders. A "private" folder rule keeps a folder and everything in it away from every AI and every
   model, local ones too: no index, no names in search, no text, no meaning, no photo or speech model, no
   Privacy Filter, no Ask, no summary, no organize plan, no naming, no agent, no MCP, no habits, no cached
   thumbnails. The files work as in a plain file manager: open, copy, move, rename, delete, preview.
2. Photos, videos and audio never go to a cloud AI. Not the files, and not text that Raven took from them
   (OCR of a photo, a transcript). Only the local models may read them, and only outside private folders.

Every AI entry point calls require_ai() or ai_allowed(). Every cloud payload builder calls cloud_allowed().
"""

from __future__ import annotations

import os

from ..fs.kinds import group_of
from ..fs.props import policy_for

MEDIA_GROUPS = {"image", "video", "audio"}


class PrivateError(PermissionError):
    """An AI feature asked for a private path."""


def is_private(path: str) -> bool:
    return bool(path) and policy_for(path, "private")


def is_media(path: str) -> bool:
    return group_of(os.path.basename(path), False) in MEDIA_GROUPS


def ai_allowed(path: str) -> bool:
    """May any AI or model (local or cloud) read this path?"""
    return not is_private(path)


def cloud_allowed(path: str) -> bool:
    """May text from this file go to a cloud AI (after redaction)?"""
    return ai_allowed(path) and not policy_for(path, "no_cloud") and not is_media(path)


def require_ai(*paths: str) -> None:
    """Raise PrivateError when one of the paths is private."""
    for p in paths:
        if p and is_private(p):
            raise PrivateError(f"{os.path.basename(p.rstrip('/')) or p} is in a private folder. Raven's AI does not read it.")


def _table(c, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def forget(path: str) -> dict:
    """Delete everything that Raven's AI keeps about a folder that becomes private: index rows, text, vectors,
    photo and video data, page images and frames, folder rows, habits, corrections, graph edges and cached
    thumbnails. The files themselves stay as they are."""
    from .. import config, db
    from ..index.indexer import _like_prefix, _remove_paths

    path = os.path.abspath(os.path.expanduser(path)).rstrip("/") or "/"
    like = _like_prefix(path)
    out = {"files": 0, "images": 0, "thumbs": 0}
    c = db.conn()
    rows = c.execute("SELECT id, path FROM files WHERE path=? OR path LIKE ? ESCAPE '\\'", (path, like)).fetchall()
    ids = [r[0] for r in rows]
    with db.tx() as tc:
        if _table(tc, "visual"):
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                q = ",".join("?" * len(part))
                vis = tc.execute(f"SELECT id, thumb FROM visual WHERE file_id IN ({q})", part).fetchall()
                for vid, thumb in vis:
                    if thumb:
                        try:
                            os.remove(thumb)
                            out["images"] += 1
                        except OSError:
                            pass
                vids = [v[0] for v in vis]
                if vids and _table(tc, "visual_vec"):
                    tc.execute(f"DELETE FROM visual_vec WHERE rowid IN ({','.join('?' * len(vids))})", vids)
                tc.execute(f"DELETE FROM visual WHERE file_id IN ({q})", part)
        out["files"] = _remove_paths(tc, [path])
        if _table(tc, "activity"):
            tc.execute("DELETE FROM activity WHERE path=? OR path LIKE ? ESCAPE '\\' OR folder=? OR folder LIKE ? ESCAPE '\\'",
                       (path, like, path, like))
        tc.execute("DELETE FROM folder_visits WHERE path=? OR path LIKE ? ESCAPE '\\'", (path, like))
        if _table(tc, "corrections"):
            tc.execute("DELETE FROM corrections WHERE path=? OR path LIKE ? ESCAPE '\\'", (path, like))
        if _table(tc, "folders"):
            vec = _table(tc, "folder_vec_mean")
            for (fid,) in tc.execute("SELECT id FROM folders WHERE path=? OR path LIKE ? ESCAPE '\\'", (path, like)).fetchall():
                tc.execute("DELETE FROM folders_fts WHERE rowid=?", (fid,))
                if vec:
                    tc.execute("DELETE FROM folder_vec_mean WHERE rowid=?", (fid,))
                    tc.execute("DELETE FROM folder_vec_sum WHERE rowid=?", (fid,))
                tc.execute("DELETE FROM folders WHERE id=?", (fid,))
        if _table(tc, "graph_edges"):
            tc.execute("DELETE FROM graph_edges WHERE src NOT IN (SELECT id FROM files) OR dst NOT IN (SELECT id FROM files)")
        if ids:
            tc.execute("DELETE FROM settings WHERE key='galaxy'")  # the map holds names and points of these files
    for _, p in rows:
        for px in (256,):
            try:
                f = config.thumb_file(p, os.stat(p), px)
                if f.exists():
                    f.unlink()
                    out["thumbs"] += 1
            except OSError:
                pass
    return out
