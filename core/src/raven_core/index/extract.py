"""Text extraction (stage B). Return a title and a list of sections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import shutil
import subprocess

from ..config import DOC_EXTS, MAX_DOC_FILE, MAX_TEXT_FILE, TEXT_EXTS

# OCR (section 13.10 item 5): tesseract reads text in images and in scanned PDF pages.
OCR_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}
MAX_OCR_FILE = 15 * 1024 * 1024
MIN_OCR_IMAGE = 1500  # icons and tiny images hold no useful text
OCR_PAGES = 30  # at most this many scanned pages per PDF


def ocr_ok() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(path: str) -> str:
    """Text in an image, or "" when tesseract finds none."""
    try:
        r = subprocess.run(["tesseract", path, "-", "-l", "eng", "--psm", "3"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = r.stdout.decode(errors="replace") if r.returncode == 0 else ""
    # Keep lines with real words. OCR on photos gives short noise such as "| ~ ee".
    lines = [ln.strip() for ln in text.splitlines() if len(re.findall(r"[A-Za-z]{3,}", ln)) >= 2]
    return "\n".join(lines)


@dataclass
class Section:
    text: str
    page: int | None = None
    heading: str = ""


@dataclass
class Extracted:
    title: str = ""
    sections: list[Section] = field(default_factory=list)


def can_extract(ext: str, size: int) -> bool:
    if ext in OCR_EXTS:
        return MIN_OCR_IMAGE <= size <= MAX_OCR_FILE and ocr_ok()
    if ext in TEXT_EXTS:
        return size <= MAX_TEXT_FILE
    if ext in DOC_EXTS:
        return size <= MAX_DOC_FILE
    return False


_md = None


def _markitdown():
    global _md
    if _md is None:
        from markitdown import MarkItDown

        _md = MarkItDown(enable_plugins=False)
    return _md


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SLIDE = re.compile(r"^<!--\s*Slide number:\s*(\d+)\s*-->\s*$")


def _split_markdown(text: str) -> list[Section]:
    """Split markdown on headings. Slide markers from MarkItDown become page numbers."""
    sections: list[Section] = []
    heading, page, buf = "", None, []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section(body, page, heading))
        buf.clear()

    for line in text.splitlines():
        m = _SLIDE.match(line)
        if m:
            flush()
            page = int(m.group(1))
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            heading = m.group(2).strip().strip("#").strip()
            buf.append(heading)
            continue
        buf.append(line)
    flush()
    return sections


def _odf(path: str) -> Extracted:
    """OpenDocument (ODT, ODS, ODP): MarkItDown does not read it. The text is in content.xml in the zip:
    paragraphs and headings are text:p and text:h, table cells hold text:p too."""
    import xml.etree.ElementTree as ET
    import zipfile

    ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    secs: list[Section] = []
    cur: list[str] = []
    heading = ""
    for el in root.iter():
        if el.tag in (ns + "h", ns + "p"):
            text = "".join(el.itertext()).strip()
            if not text:
                continue
            if el.tag == ns + "h":
                if cur:
                    secs.append(Section("\n".join(cur), heading=heading))
                    cur = []
                heading = text
            cur.append(text)
    if cur:
        secs.append(Section("\n".join(cur), heading=heading))
    title = next((s.heading for s in secs if s.heading), "")
    return Extracted(title, secs)


def _ooxml(path: str, ext: str) -> Extracted:
    """DOCX, PPTX and XLSX without MarkItDown (lite mode, PLAN2 P2-6): their text is in XML inside the zip.
    MarkItDown and its imports (pandas, openpyxl, python-pptx) cost about 75 MB of memory."""
    import xml.etree.ElementTree as ET
    import zipfile

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    secs: list[Section] = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if ext == "docx":
            root = ET.fromstring(z.read("word/document.xml"))
            cur: list[str] = []
            heading = ""
            for p in root.iter(W + "p"):
                text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
                if not text:
                    continue
                style = p.find(f"{W}pPr/{W}pStyle")
                is_head = style is not None and (style.get(W + "val") or "").lower().startswith(("heading", "title"))
                if is_head:
                    if cur:
                        secs.append(Section("\n".join(cur), heading=heading))
                        cur = []
                    heading = text
                cur.append(text)
            if cur:
                secs.append(Section("\n".join(cur), heading=heading))
        elif ext == "pptx":
            slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
            for i, n in enumerate(slides, 1):
                root = ET.fromstring(z.read(n))
                paras = ["".join(t.text or "" for t in p.iter(A + "t")).strip() for p in root.iter(A + "p")]
                paras = [x for x in paras if x]
                if paras:
                    secs.append(Section("\n".join(paras), page=i, heading=paras[0]))
        elif ext == "xlsx":
            shared = []
            if "xl/sharedStrings.xml" in names:
                shared = ["".join(t.text or "" for t in si.iter(S + "t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(S + "si")]
            for n in sorted(x for x in names if re.match(r"xl/worksheets/sheet\d+\.xml$", x)):
                rows = []
                for row in ET.fromstring(z.read(n)).iter(S + "row"):
                    cells = []
                    for c in row.iter(S + "c"):
                        v = c.find(S + "v")
                        if c.get("t") == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared):
                            cells.append(shared[int(v.text)])
                        elif c.get("t") == "inlineStr":
                            cells.append("".join(t.text or "" for t in c.iter(S + "t")))
                        elif v is not None and v.text:
                            cells.append(v.text)
                    if cells:
                        rows.append(" | ".join(cells))
                if rows:
                    secs.append(Section("\n".join(rows), heading=n.rsplit("/", 1)[1].replace(".xml", "")))
    if ext == "xlsx":
        first = secs[0].text.split("\n", 1)[0].split(" | ", 1)[0] if secs else ""
        return Extracted(first[:120], secs)
    title = next((s.heading for s in secs if s.heading), "")
    return Extracted(title, secs)


def _light() -> bool:
    try:
        from .. import lite

        return lite.mode() == "lite"
    except Exception:  # noqa: BLE001
        return False


from ..pdflock import locked as _pdf_locked


_JUNK_TITLE = re.compile(r"^(untitled|microsoft word|document\d*|\S+\.(docx?|pdf|tex|dvi))", re.I)


@_pdf_locked
def pdf_title(path: str) -> str:
    """The title of a PDF: the text in the largest font on page 1. Papers and forms often have no title in their
    metadata, and their first line is a header or a license note, not the title."""
    from .. import pdf

    try:
        with pdf.open_pdf(path) as doc:
            if not len(doc):
                return ""
            # Horizontal lines only (text_runs): arXiv prints its stamp in large rotated text along the margin.
            spans = [(y, size, text) for y, size, text in pdf.text_runs(doc[0]) if sum(ch.isalpha() for ch in text) >= 3]
    except Exception:  # noqa: BLE001 - a broken PDF has no title
        return ""
    if not spans:
        return ""
    big = max(sz for _, sz, _ in spans)
    if big < 11:  # body text only: no clear title
        return ""
    # The first lines in the largest font, top down, while they follow each other (a title over two lines).
    parts, last = [], None
    for y, sz, text in sorted(spans, key=lambda x: x[0]):
        if sz < big - 0.5:
            continue
        if last is not None and y - last > big * 2.2:
            break
        parts.append(text.strip())
        last = y
    return re.sub(r"\s+", " ", " ".join(parts)).strip()[:120]


@_pdf_locked
def _pdf(path: str) -> Extracted:
    from .. import pdf

    out = Extracted()
    with pdf.open_pdf(path) as doc:
        out.title = pdf.title(doc)
        scanned = 0
        for i, page in enumerate(doc):
            text = pdf.page_text(page).strip()
            if len(text) < 20 and scanned < OCR_PAGES and ocr_ok():
                # A scanned page has no text layer: render it and read it with OCR.
                scanned += 1
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                    pdf.render(page, 200 / 72).save(tmp.name)
                    text = ocr_image(tmp.name) or text
            if text:
                out.sections.append(Section(text, i + 1))
    if not out.title or _JUNK_TITLE.match(out.title):
        out.title = pdf_title(path) or out.title
    if not out.title and out.sections:
        out.title = out.sections[0].text.strip().splitlines()[0][:120]
    return out


def _read_text(path: str) -> str:
    raw = Path(path).read_bytes()[:MAX_TEXT_FILE]
    if b"\0" in raw[:4096]:
        return ""
    return raw.decode("utf-8", errors="replace")


def camera_photo(path: str) -> bool:
    """A JPEG with a camera make or model in its EXIF: a photo, not a scan or a screenshot.
    OCR on it costs about a second and finds no text. SigLIP 2 makes photos searchable by appearance."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            return bool(exif.get(271) or exif.get(272))  # Make, Model
    except Exception:  # noqa: BLE001
        return False


def extract(path: str, ext: str) -> Extracted:
    if ext in OCR_EXTS:
        if ext in ("jpg", "jpeg") and camera_photo(path):
            return Extracted("", [])
        text = ocr_image(path)
        return Extracted(text.splitlines()[0][:120] if text else "", [Section(text)] if text else [])
    if ext == "pdf":
        return _pdf(path)
    if ext in ("md", "markdown"):
        text = _read_text(path)
        secs = _split_markdown(text)
        title = next((s.heading for s in secs if s.heading), "")
        return Extracted(title, secs)
    if ext in ("odt", "ods", "odp"):
        return _odf(path)
    if ext in ("docx", "pptx", "xlsx") and _light():
        return _ooxml(path, ext)
    if ext in DOC_EXTS or ext in ("html", "htm"):
        res = _markitdown().convert(path)
        text = res.text_content or ""
        secs = _split_markdown(text)
        title = (res.title or "") or next((s.heading for s in secs if s.heading), "")
        return Extracted(title, secs)
    text = _read_text(path)
    return Extracted("", [Section(text)] if text.strip() else [])
