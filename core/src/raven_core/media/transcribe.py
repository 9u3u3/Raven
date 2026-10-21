"""Audio transcripts (sections 9.2.1 step 6 and 13.12): what people say in audio and video files.

Whisper base (faster-whisper, int8 on the CPU) writes timed segments. Raven joins them into chunks of
about CHUNK_WORDS words. Each chunk keeps its start time in `chunks.at`, so Find and Ask can open the
file at the moment. The chunks then go through BM25, embeddings and PII detection like any document.
The plan names whisper.cpp. faster-whisper runs the same Whisper models and installs with pip.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading

import numpy as np

from .. import db
from ..config import MODELS_DIR
from ..fs.atime import keep_atime
from ..index.chunk import header

log = logging.getLogger("raven.transcribe")

VERSION = 1
DIR = MODELS_DIR / "whisper-base"
AUDIO_EXTS = {"mp3", "wav", "flac", "ogg", "m4a", "opus", "aac", "wma"}
VIDEO_EXTS = {"mp4", "mkv", "webm", "mov", "avi", "m4v", "3gp"}
MAX_SECONDS = 3 * 3600
CHUNK_WORDS = 90
MIN_LANG_PROB = 0.5
RATE = 16000


def available() -> bool:
    return (DIR / "model.bin").exists() and shutil.which("ffmpeg") is not None


def fmt_time(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _pcm(path: str) -> np.ndarray:
    """Mono 16 kHz float samples through ffmpeg (no PyAV)."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE), "-t", str(MAX_SECONDS),
         "-f", "f32le", "-"],
        capture_output=True, timeout=1800,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr.decode(errors="replace").strip().splitlines() or ["ffmpeg failed"])[-1][:200])
    return np.frombuffer(r.stdout, dtype=np.float32)


def has_audio(path: str) -> bool:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return bool(r.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def chunks_from(segments: list[tuple[float, float, str]], name: str, folder: str) -> list[dict]:
    """Join timed segments into chunks of about CHUNK_WORDS words. Each chunk starts at a segment start."""
    out: list[dict] = []
    cur: list[tuple[float, float, str]] = []
    words = 0

    def flush():
        if not cur:
            return
        text = " ".join(t.strip() for _, _, t in cur).strip()
        start, end = cur[0][0], cur[-1][1]
        heading = f"Transcript {fmt_time(start)}"
        out.append({"page": None, "start": int(start * 1000), "end": int(end * 1000), "heading": heading, "text": text,
                    "header": header(name, folder, "", heading), "folder": folder.replace("/", " "), "at": round(start, 2)})

    for seg in segments:
        cur.append(seg)
        words += len(seg[2].split())
        if words >= CHUNK_WORDS:
            flush()
            cur, words = [], 0
    flush()
    return out


class TranscribeJob:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()
        self.state = {"pending": 0, "done": 0, "current": None, "error": None}

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(str(DIR), device="cpu", compute_type="int8", cpu_threads=4)
        return self._model

    def _pending(self, limit: int):
        exts = ",".join(f"'{e}'" for e in sorted(AUDIO_EXTS | VIDEO_EXTS))
        return db.conn().execute(
            f"SELECT id, path, name, parent, root, ext, size, mtime FROM files WHERE is_dir=0 AND light=0 AND ext IN ({exts}) AND size > 4096 "
            f"AND (transcript_sig IS NULL OR transcript_sig != ('{VERSION}:' || mtime || ':' || size)) "
            # Voice notes and short clips first: they are fast and most likely to hold speech.
            "ORDER BY size LIMIT ?",
            (limit,),
        ).fetchall()

    def loop(self, ix) -> None:
        from .. import lite, models

        # The model can download while Raven runs (features.loop): wait for it, then start.
        while not available() or models.busy("whisper-base"):
            if not self.state.get("error"):
                self.state["error"] = "Whisper model files are not in models/whisper-base, or ffmpeg is not installed"
                log.info(self.state["error"])
            if ix.stop.wait(30):
                return
        self.state["error"] = None

        while not ix.stop.is_set():
            if not lite.enabled("speech") or lite.paused():
                ix.stop.wait(15)
                continue
            try:
                c = db.conn()
                busy = c.execute(
                    "SELECT (SELECT COUNT(*) FROM files WHERE index_stage=1) + (SELECT COUNT(*) FROM chunks WHERE embedded=0)"
                ).fetchone()[0]
                if busy or ix.interactive.is_set():
                    ix.stop.wait(3)
                    continue
                rows = self._pending(2)
                if not rows:
                    self.state.update(current=None, pending=0)
                    if self._model is not None:
                        # Free the memory when the job has no work.
                        self._model = None
                        from .. import platform

                        platform.trim_memory()
                    ix.stop.wait(10)
                    continue
                for r in rows:
                    if ix.stop.is_set() or ix.interactive.is_set():
                        break
                    self._one(r)
                ix.wake_c.set()
            except Exception:
                log.exception("transcribe job failed")
                ix.stop.wait(10)

    def _one(self, r) -> None:
        from ..index.indexer import _drop_chunks, write_chunks

        self.state["current"] = r["path"]
        segments: list[tuple[float, float, str]] = []
        lang = None
        error = None
        try:
            from ..privacy.scope import require_ai

            require_ai(r["path"])  # a private folder (PLAN4 7.6): the speech model never opens it
            if r["ext"] in AUDIO_EXTS or has_audio(r["path"]):
                with keep_atime(r["path"]):
                    audio = _pcm(r["path"])
                if len(audio) > RATE:
                    with self._lock:
                        segs, info = self._load().transcribe(audio, vad_filter=True, beam_size=1)
                        segs = list(segs)
                    lang = info.language
                    # Music or noise: Whisper guesses a language with low confidence and writes junk.
                    if info.language_probability >= MIN_LANG_PROB:
                        segments = [(s.start, s.end, s.text) for s in segs if s.text.strip() and s.no_speech_prob < 0.8]
        except Exception as e:  # noqa: BLE001
            error = str(e)[:200]
            log.info("transcribe: skip %s: %s", r["path"], error)
        folder = os.path.relpath(r["parent"], os.path.dirname(r["root"] or r["parent"]))
        chunks = chunks_from(segments, r["name"], folder)
        with db.tx() as c:
            cur = c.execute("SELECT mtime, size, pii_level FROM files WHERE id=?", (r["id"],)).fetchone()
            if cur is None:
                return
            _drop_chunks(c, [r["id"]])
            level = write_chunks(c, r["id"], r["name"], None, chunks)
            c.execute("UPDATE files SET transcript_sig=?, pii_level=MAX(COALESCE(pii_level,0), ?), index_error=? WHERE id=?",
                      (f"{VERSION}:{cur['mtime']}:{cur['size']}", level, error, r["id"]))
        self.state["done"] += 1
        if chunks:
            log.info("transcript: %s, %s, %d chunks", r["name"], lang, len(chunks))

    def status(self) -> dict:
        exts = ",".join(f"'{e}'" for e in sorted(AUDIO_EXTS | VIDEO_EXTS))
        c = db.conn()
        pending = c.execute(
            f"SELECT COUNT(*) FROM files WHERE is_dir=0 AND light=0 AND ext IN ({exts}) AND size > 4096 "
            f"AND (transcript_sig IS NULL OR transcript_sig != ('{VERSION}:' || mtime || ':' || size))"
        ).fetchone()[0]
        with_text = c.execute(f"SELECT COUNT(DISTINCT c.file_id) FROM chunks c WHERE c.at IS NOT NULL").fetchone()[0]
        return {**self.state, "pending": pending, "files_with_speech": with_text, "available": available()}


job = TranscribeJob()
