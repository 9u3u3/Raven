"""Names for unnamed files (PLAN2 P2-3.2)."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db, naming  # noqa: E402
from raven_core.fs import ops  # noqa: E402

db.init()

GENERIC = [
    "Untitled(1).pdf", "download (7).pdf", "scan_0042.pdf", "IMG_2044.pdf", "IMG_2044.jpg", "document(2).docx",
    "Doc1.docx", "output.txt", "Screenshot_2025-06-01_upi.png", "Screenshot from 2025-06-01 10-22-11.png",
    "Screenshot 2025-06-01 at 10.22.11 AM.png", "WhatsApp Image 2024-03-01 at 10.22.11.jpeg",
    "WhatsApp Video 2024-03-01 at 10.22.11 (1).mp4", "IMG-20240301-WA0001.jpg", "VID_20240301.mp4",
    "VID_20240301_102211.mp4", "PXL_20240101_123456789.jpg", "PXL_20240101_123456789.MP.jpg", "Recording (3).m4a",
    "New Document.docx", "New Text Document.txt", "New Microsoft Word Document.docx", "Book1.xlsx",
    "Presentation1.pptx", "a8f3c2e1b9.pdf", "1.pdf", "file_example_PDF_1MB.pdf", "DSC_0001.JPG", "GOPR0042.MP4",
    "Screen Recording 2024-05-01 at 10.00.00.mov", "voice_001.ogg", "audio (2).mp3", "image.png", "photo (4).jpg",
    "3f2a9c1e-7b4d-4e8a-9c2b-1a2b3c4d5e6f.pdf", "20240301_102211.jpg", "signal-2024-03-01-102211.jpg",
]
NOT_GENERIC = [
    "Resume Priya Nair.pdf", "CS3401 Notes.pdf", "CS3401.pdf", "quarterly_budget_v2.xlsx", "Final Report.pdf",
    "kiosk_pitch.pptx", "forecast.js", "README.md", "trip_docs.zip", "deadbeef notes.txt", "Lecture 5.pdf",
]


@pytest.mark.parametrize("name", GENERIC)
def test_generic(name):
    assert naming.is_generic(name), name


@pytest.mark.parametrize("name", NOT_GENERIC)
def test_not_generic(name):
    assert not naming.is_generic(name), name


@pytest.mark.parametrize("stem,date", [
    ("IMG_20240301_102211", "2024-03-01"), ("WhatsApp Image 2024-03-01 at 10.22.11", "2024-03-01"),
    ("Screenshot_2025-06-01_upi", "2025-06-01"), ("scan 01-03-2024", "2024-03-01"), ("IMG_2044", None),
    ("VID_20241399", None),
])
def test_date_from_name(stem, date):
    assert naming.date_from_name(stem) == date


def test_best_line_skips_clock_and_urls():
    assert naming.best_line("9:41\nhttps://pay.example\nMango Grove Stores\nTotal 450") == "Mango Grove Stores"


def test_compose_styles():
    assert naming.compose("Beach", "2024-03-01", "jpg", True) == "2024-03-01 Beach.jpg"
    assert naming.compose("Deed of Lease", None, "pdf", False) == "Deed of Lease.pdf"
    st = {"sep": "_", "case": "lower", "date": "last"}
    assert naming.compose("Deed of Lease", "2024-03-01", "pdf", False, st) == "deed_of_lease_2024-03-01.pdf"


def _index(path, title=None, category=None, text=None, at=None):
    with db.tx() as c:
        fid = c.execute(
            "INSERT INTO files(path, parent, name, ext, size, mtime, is_dir, title, category, index_stage) VALUES (?,?,?,?,?,?,0,?,?,2)",
            (path, os.path.dirname(path), os.path.basename(path), path.rsplit(".", 1)[-1], 10, 0, title, category),
        ).lastrowid
        if text:
            c.execute("INSERT INTO chunks(file_id, text, at) VALUES (?,?,?)", (fid, text, at))
    return fid


def test_suggest_and_apply_with_undo(tmp_path):
    a = tmp_path / "Untitled(1).pdf"
    b = tmp_path / "Screenshot_2025-06-01_upi.png"
    c = tmp_path / "Recording (3).m4a"
    d = tmp_path / "kiosk_pitch.pptx"
    for p in (a, b, c, d):
        p.write_bytes(b"x")
    _index(str(a), title="DEED OF LEASE", category="Lease")
    _index(str(b), category="Screenshot", text="9:41\nMango Grove Stores\nPaid 450")
    _index(str(c), text="okay so the teal notebook is on the sunday shelf", at=0.0)
    _index(str(d), title="Zephyrine Solar Kiosk")
    plan = naming.scan(str(tmp_path), use_llm=False)
    names = {os.path.basename(i["src"]): i["name"] for i in plan["items"]}
    assert names["Untitled(1).pdf"] == "Deed of Lease.pdf"
    assert names["Screenshot_2025-06-01_upi.png"] == "2025-06-01 Mango Grove Stores.png"
    assert names["Recording (3).m4a"].lower().endswith(" okay so the teal notebook.m4a")
    assert "kiosk_pitch.pptx" not in names  # a real name stays
    res = naming.apply(plan["id"], [{"src": i["src"], "name": i["name"]} for i in plan["items"]])
    assert res["renamed"] == 3 and not res["errors"]
    assert (tmp_path / "Deed of Lease.pdf").exists() and not a.exists()
    ops.undo()
    assert a.exists() and b.exists() and c.exists()


def test_apply_rejects_paths_outside_plan(tmp_path):
    a = tmp_path / "Doc1.docx"
    a.write_bytes(b"x")
    _index(str(a), title="Club Budget Notes")
    plan = naming.scan(str(tmp_path), use_llm=False)
    res = naming.apply(plan["id"], [{"src": "/etc/passwd", "name": "x"}, {"src": str(a), "name": "../evil"}])
    assert res["renamed"] == 0 and len(res["errors"]) == 2


def test_one_file_is_named_even_with_a_real_name(tmp_path):
    a = tmp_path / "notes.txt"
    a.write_bytes(b"x")
    _index(str(a), title="Marigold Committee Minutes")
    plan = naming.scan(str(a), use_llm=False)
    assert [i["name"] for i in plan["items"]] == ["Marigold Committee Minutes.txt"]


@pytest.mark.parametrize("text,title", [
    ("Order placed\nBlue Heron Bookstore\nTotal 640", "Order placed - Blue Heron Bookstore"),
    ("Payment successful\nPaid to Mango Grove Stores", "Payment successful - Paid to Mango Grove Stores"),
    ("Library fine notice\nReturn the books", "Library fine notice"),
    ("9:41\n\x0c", ""),
])
def test_text_title(text, title):
    assert naming.text_title(text) == title
