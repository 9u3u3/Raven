"""File conversion (section 9.9): a registry maps (input type, output type) to a local engine.

Output goes next to the source with the new extension. The source stays. Every new file
goes into the journal, so undo removes it. MD and HTML to PDF need the Electron renderer
(printToPDF): the sidecar returns those as print jobs and the app records them after.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..fs import ops
from ..fs.kinds import ext_of

OFFICE_DOCS = {"docx", "doc", "odt", "rtf"}
OFFICE_SHEETS = {"xlsx", "xls", "ods"}
OFFICE_SLIDES = {"pptx", "ppt", "odp"}
IMAGES = {"png", "jpg", "jpeg", "webp", "heic", "avif", "jxl", "tif", "tiff", "bmp", "gif"}
AUDIO = {"mp3", "wav", "flac", "ogg", "m4a", "opus"}
VIDEO = {"mp4", "mkv", "webm", "mov", "avi"}
TEXT_MARKUP = {"md", "markdown", "html", "htm", "docx", "odt", "rst", "epub"}
TABLES = {"csv", "xlsx", "json"}

TOOL_NAMES = {"soffice": "LibreOffice", "pandoc": "pandoc", "magick": "ImageMagick", "ffmpeg": "ffmpeg"}


class ConvertError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    src: frozenset
    dst: str
    engine: str
    label: str


def _r(src: set[str], dst: str, engine: str, label: str | None = None) -> Route:
    return Route(frozenset(src), dst, engine, label or dst.upper())


# Order matters: the first route for a pair wins.
ROUTES: list[Route] = [
    _r(OFFICE_DOCS | OFFICE_SHEETS | OFFICE_SLIDES, "pdf", "soffice"),
    _r({"docx", "doc", "rtf"}, "odt", "soffice"),
    _r({"odt", "doc", "rtf"}, "docx", "soffice", "Word (DOCX)"),
    _r({"xlsx", "xls"}, "ods", "soffice"),
    _r({"ods", "xls"}, "xlsx", "soffice", "Excel (XLSX)"),
    _r({"pptx", "ppt"}, "odp", "soffice"),
    _r({"odp", "ppt"}, "pptx", "soffice", "PowerPoint (PPTX)"),
    _r({"md", "markdown", "html", "htm"}, "pdf", "print"),
    _r({"md", "markdown", "html", "htm", "odt", "rst", "epub"}, "docx", "pandoc", "Word (DOCX)"),
    _r({"md", "markdown", "docx", "odt", "rst", "epub"}, "html", "pandoc"),
    _r({"html", "htm", "docx", "odt", "rst", "epub"}, "md", "pandoc", "Markdown"),
    _r({"md", "markdown", "html", "htm", "docx"}, "epub", "pandoc"),
    _r({"pdf"}, "md", "pymupdf", "Markdown"),
    _r({"pdf"}, "txt", "pymupdf", "Text"),
    _r({"pdf"}, "png", "pymupdf", "PNG pages"),
    _r({"pdf"}, "jpg", "pymupdf", "JPG pages"),
    *[_r(IMAGES - {e}, e, "magick") for e in ("png", "jpg", "webp", "avif", "jxl", "tiff", "bmp")],
    _r(IMAGES, "pdf", "magick", "PDF"),
    *[_r((AUDIO | VIDEO) - {e}, e, "ffmpeg") for e in ("mp3", "wav", "ogg", "m4a")],
    *[_r(VIDEO - {e}, e, "ffmpeg") for e in ("mp4", "webm")],
    _r(VIDEO, "gif", "ffmpeg"),
    _r({"csv", "json"}, "xlsx", "pandas", "Excel (XLSX)"),
    _r({"xlsx", "json"}, "csv", "pandas"),
    _r({"csv", "xlsx"}, "json", "pandas"),
]


def route_for(ext: str, dst: str) -> Route | None:
    return next((r for r in ROUTES if r.dst == dst and ext in r.src), None)


def targets(paths: list[str]) -> list[dict]:
    """Valid targets for all of `paths` (the right-click menu shows only these)."""
    exts = {ext_of(p) for p in paths}
    out, seen = [], set()
    for r in ROUTES:
        if r.dst in seen:
            continue
        if exts and all(e in r.src for e in exts):
            seen.add(r.dst)
            out.append({"ext": r.dst, "label": r.label, "engine": r.engine, "tool": TOOL_NAMES.get(r.engine)})
    return out


def _need(tool: str) -> None:
    if tool == "soffice":
        from ..platform import soffice

        if soffice():
            return
    if shutil.which(tool) is None:
        name = TOOL_NAMES.get(tool, tool)
        raise ConvertError(f"{name} is not installed, so Raven cannot do this conversion. Install {name}.")


def _run(cmd: list[str], timeout: int = 300, cwd: str | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode(errors="replace").strip().splitlines()
        raise ConvertError(f"{cmd[0]} failed: {msg[-1] if msg else r.returncode}")


def _out(src: str, ext: str) -> str:
    p = Path(src)
    return ops.unique_path(str(p.with_name(p.stem + "." + ext)))


_PRINT_CSS = """
body { font-family: "IBM Plex Sans", "Noto Sans", "DejaVu Sans", sans-serif; font-size: 11pt; line-height: 1.5;
       color: #121212; max-width: 46em; margin: 0 auto; }
h1, h2, h3 { line-height: 1.2; } code, pre { font-family: "Commit Mono", "DejaVu Sans Mono", monospace; font-size: 9.5pt; }
pre { background: #f3f3ef; padding: 8px; white-space: pre-wrap; } table { border-collapse: collapse; }
td, th { border: 1px solid #ccc; padding: 4px 8px; } img { max-width: 100%; }
"""


def _engine(route: Route, src: str, dst_ext: str) -> list[str] | dict:
    """Convert one file. Return the new paths, or a print job for the renderer."""
    ext = ext_of(src)
    from ..fs.network import readable

    inp = readable(src)  # a share: tools read a local copy (gvfs FTP has no random reads)
    if route.engine == "soffice":
        _need("soffice")
        with tempfile.TemporaryDirectory(prefix="raven-lo-") as tmp:
            profile = Path(tempfile.gettempdir()) / "raven-lo-profile"
            # A private profile: the conversion works even when LibreOffice is open.
            from ..platform import soffice

            _run([soffice() or "soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", dst_ext,
                  "--outdir", tmp, inp], timeout=180)
            made = list(Path(tmp).glob(f"*.{dst_ext}"))
            if not made:
                raise ConvertError("LibreOffice made no output file")
            out = _out(src, dst_ext)
            from ..fs.ops import move_safe

            move_safe(str(made[0]), out)
            return [out]
    if route.engine == "pandoc":
        _need("pandoc")
        out = _out(src, dst_ext)
        cmd = ["pandoc", inp, "-o", out]
        if dst_ext == "html":
            cmd += ["--standalone", "--metadata", f"title={Path(src).stem}"]
        if dst_ext == "md":
            cmd += ["-t", "gfm"]
        _run(cmd, cwd=os.path.dirname(src))
        return [out]
    if route.engine == "print":
        if ext in ("md", "markdown"):
            _need("pandoc")
            r = subprocess.run(["pandoc", inp, "--standalone", "--embed-resources", "--metadata", f"title={Path(src).stem}",
                                "-t", "html5"], capture_output=True, timeout=120, cwd=os.path.dirname(src))
            if r.returncode != 0:
                # Older pandoc without --embed-resources: plain standalone HTML.
                r = subprocess.run(["pandoc", inp, "--standalone", "--metadata", f"title={Path(src).stem}", "-t", "html5"],
                                   capture_output=True, timeout=120, cwd=os.path.dirname(src))
            if r.returncode != 0:
                raise ConvertError(f"pandoc failed: {r.stderr.decode(errors='replace')[:200]}")
            html = r.stdout.decode()
        else:
            html = Path(inp).read_text(errors="replace")
        html = html.replace("</head>", f"<style>{_PRINT_CSS}</style><base href=\"file://{os.path.dirname(src)}/\"></head>", 1)
        return {"html": html, "out": _out(src, "pdf"), "src": src}
    if route.engine == "pymupdf":
        import pymupdf

        from ..pdflock import LOCK

        with LOCK, pymupdf.open(inp) as doc:
            if dst_ext in ("md", "txt"):
                parts = []
                for i, page in enumerate(doc):
                    text = page.get_text("text", sort=True).strip()
                    parts.append((f"## Page {i + 1}\n\n" if dst_ext == "md" else f"--- page {i + 1} ---\n") + text)
                out = _out(src, dst_ext)
                Path(out).write_text("\n\n".join(parts) + "\n")
                return [out]
            folder = ops.unique_path(str(Path(src).with_name(Path(src).stem + " pages")))
            os.mkdir(folder)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=150, alpha=False)
                pix.save(os.path.join(folder, f"page-{i + 1:03d}.{dst_ext}"))
            return [folder]
    if route.engine == "magick":
        _need("magick")
        out = _out(src, dst_ext)
        _run(["magick", inp + ("[0]" if ext == "gif" and dst_ext != "pdf" else ""), out])
        return [out]
    if route.engine == "ffmpeg":
        _need("ffmpeg")
        out = _out(src, dst_ext)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", inp]
        if dst_ext in ("mp3", "wav", "ogg", "m4a"):
            cmd += ["-vn"]
        if dst_ext == "gif":
            cmd += ["-vf", "fps=10,scale=480:-1:flags=lanczos"]
        _run(cmd + [out], timeout=1800)
        return [out]
    if route.engine == "pandas":
        import pandas as pd

        if ext == "csv":
            df = pd.read_csv(inp)
        elif ext == "json":
            df = pd.read_json(inp)
        else:
            df = pd.read_excel(inp)
        out = _out(src, dst_ext)
        if dst_ext == "csv":
            df.to_csv(out, index=False)
        elif dst_ext == "json":
            df.to_json(out, orient="records", indent=1, force_ascii=False)
        else:
            df.to_excel(out, index=False)
        return [out]
    raise ConvertError(f"No engine {route.engine}")


def convert(paths: list[str], target: str, combine: bool = False, source: str = "manual", group: ops.Group | None = None) -> dict:
    """Convert files. One journal group covers the whole batch. Print jobs come back for the renderer."""
    paths = [os.path.abspath(p) for p in paths]
    target = target.lower().lstrip(".")
    created: list[str] = []
    errors: list[dict] = []
    print_jobs: list[dict] = []
    g = group or ops.Group(source, f"Convert {len(paths)} file{'s' if len(paths) != 1 else ''} to {target.upper()}")
    if combine:
        # Images -> one PDF ("make one PDF from these 12 photos").
        if target != "pdf" or not all(ext_of(p) in IMAGES for p in paths):
            raise ConvertError("Raven can combine images into one PDF only")
        _need("magick")
        out = ops.unique_path(os.path.join(os.path.dirname(paths[0]), f"{Path(paths[0]).stem} and {len(paths) - 1} more.pdf"))
        _run(["magick", *paths, out], timeout=600)
        created.append(out)
        if group is None:
            g.label = f"Combine {len(paths)} images into one PDF"
    else:
        for p in paths:
            route = route_for(ext_of(p), target)
            if route is None:
                errors.append({"path": p, "error": f"Raven cannot convert {ext_of(p).upper() or 'this file'} to {target.upper()}"})
                continue
            try:
                res = _engine(route, p, target)
            except (ConvertError, OSError, subprocess.TimeoutExpired) as e:
                errors.append({"path": p, "error": str(e)})
                continue
            except Exception as e:  # noqa: BLE001 - pandas and PyMuPDF raise their own types
                errors.append({"path": p, "error": f"{type(e).__name__}: {e}"})
                continue
            if isinstance(res, dict):
                print_jobs.append(res)
            else:
                created.extend(res)
    for c in created:
        g.add("create", None, c)
    if group is None:
        g.commit()
    return {"created": created, "errors": errors, "print_jobs": print_jobs, "group": g.id, "label": g.label}


def record_printed(group: str | None, paths: list[str], label: str) -> str:
    """Add PDFs that the renderer printed to the journal: to the batch's group when it has one."""
    g = ops.Group("manual", label)
    if group:
        g.id = group
    for p in paths:
        g.add("create", None, p)
    g.commit()
    return g.id
