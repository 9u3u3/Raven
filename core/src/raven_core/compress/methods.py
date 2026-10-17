"""Compression methods (section 9.8 step 2).

archive methods (7z, tar.zst, zip): many files -> one archive with .raven-manifest.json inside. Exact.
file methods (zst, jxl): one file -> one file. The manifest lives in the DB only. Exact.
lossy methods (avif, jxl-q, hevc): one file -> one smaller file that is NOT the same data. Suggest mode
only, never Auto mode. The caller must pass accept_loss. Raven checks that the new file decodes and has
the same size in pixels (or the same duration) before the original goes to the trash.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..storage.dupes import full_hash

MANIFEST = ".raven-manifest.json"
ARCHIVE_METHODS = {"7z": ".7z", "tar.zst": ".tar.zst", "zip": ".zip"}
FILE_METHODS = {"zst": ".zst", "jxl": ".jxl"}
LOSSY_METHODS = {"avif": ".avif", "jxl-q": ".jxl", "hevc": ".hevc.mp4"}
LOSSY_TOOLS = {"avif": "avifenc", "jxl-q": "cjxl", "hevc": "ffmpeg"}
# Mean absolute difference of two 64x64 grey copies, 0..255. Above this, the lossy copy looks wrong.
LOSSY_MAX_DIFF = 6.0


class CompressError(Exception):
    pass


@dataclass
class Item:
    rel: str  # path relative to the unit root
    size: int
    blake3: str | None  # None for folders and links
    mtime_ns: int
    atime_ns: int
    mode: int
    kind: str  # file | dir | link


def _run(cmd: list[str], cwd: str | None = None, stdin=None, timeout: int = 3600) -> None:
    r = subprocess.run(cmd, cwd=cwd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode(errors="replace").strip().splitlines()
        raise CompressError(f"{cmd[0]} failed: {msg[-1] if msg else r.returncode}")


def tool_ok(name: str) -> bool:
    return shutil.which(name) is not None


# ---------------------------------------------------------------- manifest


def collect(root: str, paths: list[str]) -> list[Item]:
    """List every file, folder and link under `paths`, with hashes and times."""
    items: list[Item] = []

    def add(p: str) -> None:
        st = os.lstat(p)
        rel = os.path.relpath(p, root)
        if stat.S_ISLNK(st.st_mode):
            items.append(Item(rel, 0, None, st.st_mtime_ns, st.st_atime_ns, stat.S_IMODE(st.st_mode), "link"))
        elif stat.S_ISDIR(st.st_mode):
            items.append(Item(rel, 0, None, st.st_mtime_ns, st.st_atime_ns, stat.S_IMODE(st.st_mode), "dir"))
            for de in sorted(os.scandir(p), key=lambda d: d.name):
                add(de.path)
        elif stat.S_ISREG(st.st_mode):
            items.append(Item(rel, st.st_size, full_hash(p), st.st_mtime_ns, st.st_atime_ns, stat.S_IMODE(st.st_mode), "file"))

    for p in paths:
        add(p)
    return items


def manifest_json(method: str, root: str, items: list[Item], root_mtime: int | None = None) -> str:
    # The root folder's time changes when Raven adds the archive. Restore puts it back.
    root_mtime = root_mtime or os.stat(root).st_mtime_ns
    return json.dumps(
        {"raven": 1, "created": int(time.time()), "method": method, "root": root, "root_mtime_ns": root_mtime,
         "items": [asdict(i) for i in items]},
        indent=1,
    )


# ---------------------------------------------------------------- create


def create_archive(method: str, root: str, rels: list[str], dst: str, manifest: str) -> None:
    """Pack `rels` (relative to `root`) and the manifest into `dst`. On a share, Raven packs into a local
    scratch folder and then moves the archive (zip writers jump back in the file, PLAN2 P2-9.4)."""
    from ..fs.network import is_network, scratch_for

    if is_network(dst):
        with tempfile.TemporaryDirectory(prefix=".raven-", dir=scratch_for(dst)) as t:
            local = os.path.join(t, os.path.basename(dst))
            _create_archive(method, root, rels, local, manifest)
            from ..fs.ops import move_safe

            move_safe(local, dst)
        return
    _create_archive(method, root, rels, dst, manifest)


def _create_archive(method: str, root: str, rels: list[str], dst: str, manifest: str) -> None:
    with tempfile.TemporaryDirectory(prefix=".raven-", dir=os.path.dirname(dst)) as tmp:
        mpath = os.path.join(tmp, MANIFEST)
        Path(mpath).write_text(manifest, encoding="utf-8")
        if method == "7z":
            _run(["7z", "a", "-t7z", "-mx=7", "-snl", "-mtm=on", "-y", dst, "--", *rels], cwd=root)
            _run(["7z", "a", "-t7z", "-y", dst, MANIFEST], cwd=tmp)
        elif method == "tar.zst":
            tar_path = os.path.join(tmp, "a.tar")
            _run(["tar", "-cf", tar_path, "-C", root, "--", *rels])
            _run(["tar", "-rf", tar_path, "-C", tmp, MANIFEST])
            _run(["zstd", "-q", "-10", "-T0", "-f", tar_path, "-o", dst])
        elif method == "zip":
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                for rel in rels:
                    base = os.path.join(root, rel)
                    if os.path.isdir(base) and not os.path.islink(base):
                        for dp, dns, fns in os.walk(base):
                            z.write(dp, os.path.relpath(dp, root))
                            for fn in fns:
                                fp = os.path.join(dp, fn)
                                if not os.path.islink(fp):
                                    z.write(fp, os.path.relpath(fp, root))
                    elif not os.path.islink(base):
                        z.write(base, rel)
                z.write(mpath, MANIFEST)
        else:
            raise CompressError(f"Unknown archive method {method}")


def create_file(method: str, src: str, dst: str) -> None:
    if method == "zst":
        _run(["zstd", "-q", "-19", "-T0", "-f", src, "-o", dst])
    elif method == "jxl":
        _run(["cjxl", src, dst, "--lossless_jpeg=1", "--quiet"])
    else:
        raise CompressError(f"Unknown file method {method}")


def create_lossy(method: str, src: str, dst: str) -> None:
    if method == "avif":
        _run(["avifenc", "--jobs", "all", "-q", "60", "-s", "6", src, dst])
    elif method == "jxl-q":
        # Distance 1 is "visually lossless" in the libjxl guide.
        _run(["cjxl", src, dst, "-d", "1", "--lossless_jpeg=0", "--quiet"])
    elif method == "hevc":
        _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-map", "0", "-c:v", "libx265", "-crf", "26",
              "-preset", "medium", "-tag:v", "hvc1", "-c:a", "copy", "-c:s", "copy", dst], timeout=6 * 3600)
    else:
        raise CompressError(f"Unknown lossy method {method}")


def _grey(path: str):
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        size = im.size
        g = np.asarray(im.convert("L").resize((64, 64)), dtype=np.float32)
    return size, g


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    try:
        return float(r.stdout.decode().strip())
    except ValueError as e:
        raise CompressError(f"ffprobe cannot read {os.path.basename(path)}") from e


def check_lossy(method: str, src: str, dst: str) -> None:
    """The lossy copy must decode, keep the size in pixels (or the duration) and look like the original."""
    if method == "hevc":
        a, b = _duration(src), _duration(dst)
        if abs(a - b) > max(1.0, a * 0.01):
            raise CompressError(f"Verify failed: the new video is {b:.1f} s long, the original is {a:.1f} s")
        return
    with tempfile.TemporaryDirectory(prefix=".raven-verify-", dir=os.path.dirname(dst)) as tmp:
        back = os.path.join(tmp, "check.png")
        _run(["avifdec", dst, back] if method == "avif" else ["djxl", dst, back, "--quiet"])
        (sw, sh), a = _grey(src)
        (dw, dh), b = _grey(back)
    if (sw, sh) != (dw, dh) and (sh, sw) != (dw, dh):
        raise CompressError(f"Verify failed: the new image is {dw}x{dh}, the original is {sw}x{sh}")
    diff = float(abs(a - b).mean())
    if diff > LOSSY_MAX_DIFF:
        raise CompressError(f"Verify failed: the new image differs too much from the original ({diff:.1f})")


# ---------------------------------------------------------------- extract and verify


def test_archive(method: str, dst: str) -> None:
    from ..fs.network import readable

    dst = readable(dst)
    if method == "7z":
        _run(["7z", "t", dst])
    elif method in ("tar.zst", "zst"):
        _run(["zstd", "-q", "-t", dst])
    elif method == "zip":
        with zipfile.ZipFile(dst) as z:
            bad = z.testzip()
            if bad:
                raise CompressError(f"zip test failed at {bad}")


def extract_archive(method: str, dst: str, out: str) -> None:
    from ..fs.network import readable

    dst = readable(dst)
    from ..fs.archive import check_room, totals_7z

    os.makedirs(out, exist_ok=True)
    if method == "7z":
        check_room(*totals_7z(dst), out)
        _run(["7z", "x", "-y", "-snl", f"-o{out}", dst])
    elif method == "tar.zst":
        with subprocess.Popen(["zstd", "-q", "-dc", dst], stdout=subprocess.PIPE) as z:
            _run(["tar", "-xf", "-", "-C", out], stdin=z.stdout)
            z.wait()
            if z.returncode:
                raise CompressError("zstd failed while it read the archive")
    elif method == "zip":
        with zipfile.ZipFile(dst) as z:
            infos = z.infolist()
            check_room(sum(i.file_size for i in infos), len(infos), out)
            z.extractall(out)
    else:
        raise CompressError(f"Unknown archive method {method}")


def extract_file(method: str, dst: str, out_file: str) -> None:
    if method == "zst":
        _run(["zstd", "-q", "-d", "-f", dst, "-o", out_file])
    elif method == "jxl":
        _run(["djxl", dst, out_file, "--quiet"])
    else:
        raise CompressError(f"Unknown file method {method}")


def verify_tree(out: str, items: list[Item]) -> None:
    """Compare the BLAKE3 hash of every extracted file with the manifest."""
    for it in items:
        if it.kind != "file":
            continue
        p = os.path.join(out, it.rel)
        if not os.path.isfile(p):
            raise CompressError(f"Verify failed: {it.rel} is missing from the archive")
        if os.path.getsize(p) != it.size or full_hash(p) != it.blake3:
            raise CompressError(f"Verify failed: {it.rel} does not match the original")


def apply_meta(path: str, it: Item) -> None:
    """Put back permissions and times from the manifest."""
    if it.kind == "link":
        return
    os.chmod(path, it.mode)
    os.utime(path, ns=(it.atime_ns, it.mtime_ns))
