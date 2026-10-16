"""One lock for every PyMuPDF call. PyMuPDF is not thread-safe: two threads in it at once can crash the
whole sidecar (a segmentation fault in native code). Text extraction, page images, thumbnails and
conversion all take this lock. Other file types still run in parallel."""

from __future__ import annotations

import functools
import threading

LOCK = threading.RLock()

try:
    import pymupdf

    # Broken color profiles in some PDFs print "cmsOpenProfileFromMem failed" for each page. The text is fine.
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
except Exception:  # noqa: BLE001
    pass


def locked(fn):
    @functools.wraps(fn)
    def wrap(*a, **kw):
        with LOCK:
            return fn(*a, **kw)

    return wrap
