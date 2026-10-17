"""PDF reading and page images with pypdfium2 (PDFium: Apache-2.0, BSD-3-Clause). PLAN4 B10: PyMuPDF is AGPL-3.0
or a paid license, so a closed-source Raven cannot ship it.

PDFium is not thread-safe. Every function here takes pdflock.LOCK, so text extraction, thumbnails, page
images and conversion never run in PDFium at the same time.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator

from .pdflock import LOCK


class PdfError(Exception):
    pass


@contextlib.contextmanager
def open_pdf(path: str) -> Iterator:
    """A PdfDocument under the lock. A broken or locked PDF raises PdfError."""
    import pypdfium2 as pdfium

    with LOCK:
        try:
            doc = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as e:
            raise PdfError(str(e)) from e
        try:
            yield doc
        finally:
            doc.close()


def page_text(page) -> str:
    """The text of one page, in reading order, with plain line ends."""
    tp = page.get_textpage()
    try:
        return tp.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n")
    finally:
        tp.close()


def render(page, scale: float):
    """One page as a PIL image (RGB). scale 1 is 72 dpi."""
    bitmap = page.render(scale=scale)
    try:
        return bitmap.to_pil().convert("RGB")
    finally:
        bitmap.close()


def render_fit(page, px: int):
    """One page as a PIL image whose long side is px."""
    w, h = page.get_size()
    return render(page, px / max(w, h, 1))


def title(doc) -> str:
    return (doc.get_metadata_dict().get("Title") or "").strip()


def text_runs(page) -> list[tuple[float, float, str]]:
    """The horizontal lines of a page as (baseline, font size, text), top down from the page top. Rotated text
    (an arXiv stamp along the margin) is left out. pdf_title() reads the largest lines of page 1."""
    import pypdfium2.raw as raw

    tp = page.get_textpage()
    try:
        height = page.get_height()
        runs: list[list] = []
        cur: list | None = None
        for i in range(tp.count_chars()):
            ch = chr(raw.FPDFText_GetUnicode(tp.raw, i))
            if ch in "\r\n":
                cur = None
                continue
            size = round(raw.FPDFText_GetFontSize(tp.raw, i), 1)
            angle = raw.FPDFText_GetCharAngle(tp.raw, i)
            flat = angle < 0.01 or abs(angle - 2 * math.pi) < 0.01
            ox, oy = _origin(raw, tp, i)
            y = height - oy  # the baseline: a "." and a "T" on one line share it
            if cur is not None and cur[1] == size and cur[3] == flat and (ch == " " or abs(cur[0] - y) < max(size, 1) * 0.6):
                cur[2] += ch
                continue
            if ch == " ":
                continue
            cur = [y, size, ch, flat]
            runs.append(cur)
        return [(y, s, t) for y, s, t, flat in runs if flat]
    finally:
        tp.close()


def _origin(raw, tp, i: int) -> tuple[float, float]:
    import ctypes

    x, y = ctypes.c_double(), ctypes.c_double()
    raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(x), ctypes.byref(y))
    return x.value, y.value
