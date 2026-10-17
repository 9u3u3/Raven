"""PDF reading with pypdfium2 and optimization with pikepdf (PLAN4 B10)."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pikepdf  # noqa: E402
import pytest  # noqa: E402

from raven_core import pdf  # noqa: E402
from raven_core.index import extract  # noqa: E402


def _make(path, lines: list[tuple[int, int, str]], title: str | None = None) -> None:
    """A one-page PDF with Helvetica text. Each line is (font size, y from the bottom, text)."""
    p = pikepdf.new()
    font = p.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    ops = "".join(f"BT /F1 {s} Tf 72 {y} Td ({t}) Tj ET\n" for s, y, t in lines)
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, 612, 792],
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
        Contents=p.make_stream(ops.encode()),
    )
    p.pages.append(pikepdf.Page(page))
    if title:
        p.docinfo["/Title"] = title
    p.save(path)


@pytest.fixture()
def doc(tmp_path):
    f = tmp_path / "paper.pdf"
    _make(f, [(24, 700, "Binary Vectors for Search"), (11, 650, "We measure recall on a laptop."), (11, 630, "Results hold.")])
    return f


def test_page_text(doc):
    with pdf.open_pdf(str(doc)) as d:
        assert len(d) == 1
        text = pdf.page_text(d[0])
    assert "Binary Vectors for Search" in text
    assert "We measure recall on a laptop." in text


def test_text_runs_give_the_largest_line(doc):
    with pdf.open_pdf(str(doc)) as d:
        runs = pdf.text_runs(d[0])
    big = max(runs, key=lambda r: r[1])
    assert big[2].strip() == "Binary Vectors for Search"
    assert big[1] == 24
    assert big[0] < min(r[0] for r in runs if r[1] == 11)  # top down


def test_title_from_metadata_and_from_page_one(doc, tmp_path):
    with pdf.open_pdf(str(doc)) as d:
        assert pdf.title(d) == ""
    assert extract.pdf_title(str(doc)) == "Binary Vectors for Search"
    named = tmp_path / "named.pdf"
    _make(named, [(11, 700, "body")], title="A Real Title")
    with pdf.open_pdf(str(named)) as d:
        assert pdf.title(d) == "A Real Title"


def test_render(doc):
    with pdf.open_pdf(str(doc)) as d:
        img = pdf.render_fit(d[0], 256)
    assert max(img.size) == 256 and img.mode == "RGB"


def test_a_broken_pdf_raises_pdf_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not a pdf")
    with pytest.raises(pdf.PdfError):
        with pdf.open_pdf(str(bad)):
            pass
