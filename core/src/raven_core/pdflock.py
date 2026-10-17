"""One lock for every PDFium call (pdf.py). PDFium is not thread-safe: two threads in it at once can crash the
whole sidecar (a segmentation fault in native code). Text extraction, page images, thumbnails and
conversion all take this lock. Other file types still run in parallel."""

from __future__ import annotations

import functools
import threading

LOCK = threading.RLock()


def locked(fn):
    @functools.wraps(fn)
    def wrap(*a, **kw):
        with LOCK:
            return fn(*a, **kw)

    return wrap
