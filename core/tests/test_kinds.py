"""Kind labels (PLAN2 P2-3.1): the name, then the content."""

import io
import zipfile

import pytest

from raven_core.fs import kinds

NAMES = [
    ("a.tar.gz", "Tar archive (gzip)"), ("a.tar.zst", "Tar archive (zstd)"), ("a.tar.xz", "Tar archive (xz)"),
    ("a.tar.bz2", "Tar archive (bzip2)"), ("x.tgz", "Tar archive (gzip)"), ("pkg-1.0-x86_64.pkg.tar.zst", "Arch package"),
    ("Makefile", "Makefile"), ("Dockerfile", "Dockerfile"), ("README", "Readme"), ("LICENSE", "License"),
    ("LICENSE-MIT", "License"), ("README.md", "Markdown"), (".bashrc", "Shell config"), (".zshrc", "Shell config"),
    (".env", "Environment file"), (".env.local", "Environment file"), (".gitignore", "Git ignore"),
    ("x.mp4.part", "Partial download"), ("x.crdownload", "Partial download"), ("x.mjs", "JavaScript"),
    ("x.vue", "Vue"), ("x.ipynb", "Jupyter notebook"), ("x.m4v", "MP4"), ("x.aac", "AAC"), ("x.psd", "Photoshop"),
    ("x.ttf", "Font"), ("x.woff2", "Web font"), ("x.sqlite", "Database"), ("x.db", "Database"), ("x.pem", "Certificate"),
    ("x.kdbx", "Password database"), ("x.dmg", "macOS disk image"), ("x.apk", "Android app"), ("x.jar", "Java archive"),
    ("x.so", "Shared library"), ("x.stl", "3D model"), ("x.blend", "Blender"), ("x.ts", "TypeScript"),
    ("x.d.ts", "TypeScript types"), ("x.JPG", "JPEG"), ("x.HEIC", "HEIC"), ("x.AppImage", "AppImage"),
    ("x.docx", "Word"), ("x.xlsx", "Excel"), ("x.py", "Python"), ("x.gguf", "Model weights"), ("x.iso", "Disk image"),
    ("id_ed25519", "Private key"), ("x.unknownext", "File"), ("x.abc", "ABC"), ("noext", "File"),
]


@pytest.mark.parametrize("name,label", NAMES)
def test_name_labels(name, label):
    assert kinds.kind_of(name, False) == label


def test_folder():
    assert kinds.kind_info("Photos", True) == ("Folder", "folder")


def _zip(members: dict[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return b.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"1 0 obj\n" * 10
ELF = b"\x7fELF\x02\x01\x01" + b"\x00" * 60
EXE = b"MZ\x90\x00" + b"\x00" * 60
MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" + b"\x00" * 40
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + b"\x00" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 40
DOCX = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<w/>"})
PEM = b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n"
TEXT = b"Hello, this is plain text.\nSecond line.\n"


CONTENT = [
    # name, bytes, kind, mismatch, warning?
    ("notes.txt", PDF, "Text", True, False),
    ("photo", PNG, "PNG", False, False),
    ("download", PDF, "PDF", False, False),
    ("a.png", PNG, "PNG", False, False),
    ("a.jpg", PNG, "JPEG", True, False),
    ("a.webp", WEBP, "WebP", False, False),
    ("a.jpg", WEBP, "JPEG", True, False),
    ("invoice.pdf", EXE, "PDF", True, True),
    ("song.mp3", ELF, "MP3", True, True),
    ("tool", ELF, "Linux program", False, False),
    ("setup.exe", EXE, "Windows app", False, False),
    ("clip.mp4", MP4, "MP4", False, False),
    ("IMG_1.heic", HEIC, "HEIC", False, False),
    ("IMG_1.jpg", HEIC, "JPEG", True, False),
    ("report.docx", DOCX, "Word", False, False),
    ("report.zip", DOCX, "ZIP", False, False),
    ("report.pdf", DOCX, "PDF", True, False),
    ("server.key", PEM, "Certificate or key", False, False),
    ("readme.md", TEXT, "Markdown", False, False),
    ("data.csv", TEXT, "CSV", False, False),
    ("noext", TEXT, "Text", False, False),
    ("x.mp4.part", b"\x00\x00\x00\x20ftypisom", "Partial download", False, False),
    ("a.json", b'{"a": 1}', "JSON", False, False),
    ("script", b"#!/usr/bin/env python3\nprint(1)\n", "Python", False, False),
]


@pytest.mark.parametrize("name,data,kind,mismatch,warn", CONTENT)
def test_content(tmp_path, name, data, kind, mismatch, warn):
    p = tmp_path / name
    p.write_bytes(data)
    r = kinds.check(str(p), name)
    assert r["kind"] == kind, r
    assert r["mismatch"] is mismatch, r
    assert bool(r["warning"]) is warn, r


def test_sniff_text_vs_binary():
    assert kinds.sniff_bytes(TEXT)[0] == "Text"
    assert kinds.sniff_bytes(b"\x00\x01\x02\x03garbage\x00") is None


def test_unreadable_file(tmp_path):
    r = kinds.check(str(tmp_path / "missing.pdf"), "missing.pdf")
    assert r["kind"] == "PDF" and r["sniffed"] is None and not r["mismatch"]
