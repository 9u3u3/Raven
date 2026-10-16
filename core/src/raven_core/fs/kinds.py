"""File kind labels for the Kind column (PLAN2 P2-3.1).

1. The name decides first: known file names (Makefile, .bashrc), compound extensions (.tar.gz), partial
   downloads, then the extension.
2. sniff() reads the first bytes and matches magic numbers. It runs for one file in the Inspector, at index
   time, and in a listing for a local file with no extension or an unknown one.
3. check() compares the name and the content. A mismatch gets a mark. An executable with a document name
   gets a warning, because that is a common trick.

Each label has a group (image, video, audio, archive, sheet, doc, code, ...). The renderer uses the group for
the icon and the color, so one table drives the label, the icon and the color.
"""

from __future__ import annotations

import mimetypes
import re

# ext -> (label, group)
_KINDS: dict[str, tuple[str, str]] = {}


def _add(group: str, items: dict[str, str]) -> None:
    for exts, label in items.items():
        for e in exts.split():
            _KINDS[e] = (label, group)


_add("doc", {
    "pdf": "PDF", "doc docx": "Word", "odt": "Document", "rtf": "Rich text", "txt": "Text", "md markdown": "Markdown",
    "rst": "reStructuredText", "tex": "LaTeX", "bib": "BibTeX", "epub": "EPUB", "mobi azw azw3": "Kindle book",
    "djvu": "DjVu", "pages": "Pages", "org": "Org", "adoc": "AsciiDoc", "nfo": "Info", "chm": "Help file",
    "ppt pptx": "PowerPoint", "odp": "Presentation", "key": "Keynote", "xps oxps": "XPS",
})
_add("sheet", {"xls xlsx xlsm": "Excel", "ods": "Spreadsheet", "csv": "CSV", "tsv": "TSV", "numbers": "Numbers"})
_add("log", {"log": "Log", "out": "Output"})
_add("code", {
    "py pyw": "Python", "pyi": "Python stub", "ipynb": "Jupyter notebook", "js mjs cjs": "JavaScript",
    "ts mts cts": "TypeScript", "tsx": "TSX", "jsx": "JSX", "vue": "Vue", "svelte": "Svelte", "c": "C",
    "h": "C header", "cpp cc cxx": "C++", "hpp hh hxx": "C++ header", "java": "Java", "kt kts": "Kotlin",
    "go": "Go", "rs": "Rust", "rb": "Ruby", "php": "PHP", "sh bash zsh fish": "Shell", "ps1": "PowerShell",
    "bat cmd": "Batch", "sql": "SQL", "dart": "Dart", "lua": "Lua", "swift": "Swift", "cs": "C#", "r": "R",
    "m": "Objective-C", "scala": "Scala", "hs": "Haskell", "ex exs": "Elixir", "erl": "Erlang", "zig": "Zig",
    "nim": "Nim", "pl pm": "Perl", "jl": "Julia", "asm s": "Assembly", "html htm xhtml": "HTML", "css": "CSS",
    "scss sass": "Sass", "less": "Less", "json jsonc json5": "JSON", "ndjson jsonl": "JSON lines",
    "yaml yml": "YAML", "toml": "TOML", "xml": "XML", "ini cfg conf": "Config", "properties": "Properties",
    "gradle": "Gradle", "cmake": "CMake", "mk": "Makefile", "lock": "Lock file", "diff patch": "Patch",
    "graphql gql": "GraphQL", "proto": "Protocol Buffers", "service": "systemd unit", "desktop": "Desktop entry",
    "nix": "Nix", "tf": "Terraform", "vim": "Vim script", "el": "Emacs Lisp", "wasm": "WebAssembly",
})
_add("image", {
    "png": "PNG", "jpg jpeg jpe jfif": "JPEG", "gif": "GIF", "webp": "WebP", "bmp": "BMP", "tif tiff": "TIFF",
    "svg svgz": "SVG", "heic heif": "HEIC", "avif": "AVIF", "jxl": "JPEG XL", "ico": "Icon", "icns": "macOS icon",
    "psd": "Photoshop", "xcf": "GIMP image", "kra": "Krita image", "ai": "Illustrator", "eps": "EPS",
    "raw cr2 cr3 nef arw dng orf rw2 raf": "Camera raw", "fig": "Figma", "sketch": "Sketch", "drawio": "draw.io",
    "excalidraw": "Excalidraw", "afphoto afdesign": "Affinity",
})
_add("video", {
    "mp4 m4v": "MP4", "mkv": "MKV", "webm": "WebM", "mov": "QuickTime", "avi": "AVI", "wmv": "WMV", "flv": "Flash video",
    "mpg mpeg": "MPEG", "m2ts mts": "MPEG-TS", "3gp": "3GP", "ogv": "Ogg video",
})
_add("audio", {
    "mp3": "MP3", "wav": "WAV", "flac": "FLAC", "ogg oga": "Ogg", "m4a": "M4A", "opus": "Opus", "aac": "AAC",
    "wma": "WMA", "aiff aif": "AIFF", "mid midi": "MIDI", "amr": "AMR",
})
_add("archive", {
    "zip": "ZIP", "7z": "7-Zip", "rar": "RAR", "tar": "Tar", "gz": "Gzip", "tgz": "Tar archive (gzip)",
    "zst": "Zstd", "xz": "XZ", "txz": "Tar archive (xz)", "bz2": "Bzip2", "tbz2": "Tar archive (bzip2)",
    "lz4": "LZ4", "lzma": "LZMA", "cab": "Cabinet", "jar": "Java archive", "war": "Java web archive",
    "whl": "Python wheel", "crate": "Rust crate",
})
_add("app", {
    "appimage": "AppImage", "deb": "Debian package", "rpm": "RPM package", "exe": "Windows app", "msi": "Windows installer",
    "dmg": "macOS disk image", "pkg": "macOS installer", "apk": "Android app", "aab": "Android bundle", "ipa": "iOS app",
    "flatpak flatpakref": "Flatpak", "snap": "Snap", "run": "Installer", "app": "macOS app",
    "so": "Shared library", "dll": "Windows library", "dylib": "macOS library", "o obj": "Object file",
    "a lib": "Static library", "pyc": "Python bytecode", "class": "Java class",
})
_add("disk", {"iso": "Disk image", "img": "Disk image", "qcow2": "VM disk", "vdi": "VM disk", "vmdk": "VM disk", "vhd vhdx": "VM disk"})
_add("data", {
    "db sqlite sqlite3": "Database", "mdb accdb": "Access database", "parquet": "Parquet", "feather arrow": "Arrow",
    "npy npz": "NumPy array", "h5 hdf5": "HDF5", "pkl pickle": "Pickle", "mat": "MATLAB data",
    "safetensors": "Model weights", "gguf ggml": "Model weights", "onnx": "ONNX model", "pt pth ckpt": "PyTorch model",
    "tflite": "TensorFlow Lite model", "bin": "Binary", "dat": "Data", "dump": "Dump", "bak": "Backup", "old": "Backup",
    "torrent": "Torrent", "ics": "Calendar", "vcf": "Contact card", "eml": "Email", "msg": "Outlook message",
    "mbox": "Mailbox", "gpx": "GPS track", "kml kmz": "Map", "geojson": "GeoJSON",
})
_add("font", {"ttf": "Font", "otf": "Font", "woff woff2": "Web font", "ttc": "Font collection", "pfb": "Font"})
_add("model3d", {"stl": "3D model", "obj3d": "3D model", "fbx": "3D model", "gltf glb": "3D model", "blend": "Blender",
                 "3mf": "3D print", "step stp": "CAD model", "dwg dxf": "CAD drawing", "gcode": "G-code"})
_add("secret", {
    "pem": "Certificate", "crt cer der": "Certificate", "csr": "Certificate request", "p12 pfx": "Certificate bundle",
    "kdbx kdb": "Password database", "gpg pgp asc": "Encrypted file", "age": "Encrypted file", "ovpn": "VPN profile",
    "keystore jks": "Java keystore",
})
_add("link", {"lnk": "Shortcut", "url": "Web link", "webloc": "Web link"})
_add("partial", {"part": "Partial download", "crdownload": "Partial download", "partial": "Partial download",
                 "download": "Partial download", "opdownload": "Partial download", "tmp": "Temporary file",
                 "swp": "Editor swap file", "temp": "Temporary file"})

COMPOUND = {
    "tar.gz": ("Tar archive (gzip)", "archive"), "tar.zst": ("Tar archive (zstd)", "archive"),
    "tar.xz": ("Tar archive (xz)", "archive"), "tar.bz2": ("Tar archive (bzip2)", "archive"),
    "tar.lz4": ("Tar archive (lz4)", "archive"), "tar.lzma": ("Tar archive (lzma)", "archive"),
    "pkg.tar.zst": ("Arch package", "app"), "pkg.tar.xz": ("Arch package", "app"), "d.ts": ("TypeScript types", "code"),
    "min.js": ("JavaScript", "code"), "user.js": ("JavaScript", "code"),
}

_COMPOUND_LONGEST_FIRST = sorted(COMPOUND.items(), key=lambda kv: -len(kv[0]))

# Exact file names (lower case). A name with no extension or a dot file.
NAMES: dict[str, tuple[str, str]] = {
    "makefile": ("Makefile", "code"), "gnumakefile": ("Makefile", "code"), "dockerfile": ("Dockerfile", "code"),
    "containerfile": ("Dockerfile", "code"), "vagrantfile": ("Vagrantfile", "code"), "jenkinsfile": ("Jenkinsfile", "code"),
    "pkgbuild": ("PKGBUILD", "code"), "cmakelists.txt": ("CMake", "code"), "gemfile": ("Gemfile", "code"),
    "procfile": ("Procfile", "code"), "justfile": ("Justfile", "code"), "rakefile": ("Rakefile", "code"),
    "license": ("License", "doc"), "licence": ("License", "doc"), "copying": ("License", "doc"),
    "readme": ("Readme", "doc"), "changelog": ("Changelog", "doc"), "authors": ("Authors", "doc"),
    "notice": ("Notice", "doc"), "todo": ("To-do", "doc"),
    ".bashrc": ("Shell config", "code"), ".bash_profile": ("Shell config", "code"), ".profile": ("Shell config", "code"),
    ".zshrc": ("Shell config", "code"), ".zprofile": ("Shell config", "code"), ".bash_logout": ("Shell config", "code"),
    ".bash_history": ("Shell history", "log"), ".zsh_history": ("Shell history", "log"), ".python_history": ("Shell history", "log"),
    ".env": ("Environment file", "secret"), ".gitignore": ("Git ignore", "code"), ".gitattributes": ("Git config", "code"),
    ".gitconfig": ("Git config", "code"), ".gitmodules": ("Git config", "code"), ".editorconfig": ("Config", "code"),
    ".npmrc": ("Config", "code"), ".vimrc": ("Vim config", "code"), ".tmux.conf": ("Config", "code"),
    ".xinitrc": ("Shell config", "code"), ".xprofile": ("Shell config", "code"), ".dockerignore": ("Docker ignore", "code"),
    ".prettierrc": ("Config", "code"), ".eslintrc": ("Config", "code"), ".netrc": ("Credentials", "secret"),
    "id_rsa": ("Private key", "secret"), "id_ed25519": ("Private key", "secret"), "id_ecdsa": ("Private key", "secret"),
    "id_dsa": ("Private key", "secret"), "authorized_keys": ("SSH keys", "secret"), "known_hosts": ("SSH hosts", "secret"),
    ".ds_store": ("Finder data", "data"), "thumbs.db": ("Thumbnail cache", "data"), "desktop.ini": ("Folder settings", "data"),
}
# A name that starts with these words is that kind, whatever follows (README.md is Markdown: the extension wins).
_NAME_PREFIX = re.compile(r"^(readme|license|licence|changelog|copying|authors)([-_.].*)?$", re.I)
_ENV_FILE = re.compile(r"^\.env(\..+)?$", re.I)


def ext_of(name: str) -> str:
    if "." not in name.lstrip("."):
        return ""
    return name.rsplit(".", 1)[1].lower()


def _by_name(name: str) -> tuple[str, str] | None:
    low = name.lower()
    if low in NAMES:
        return NAMES[low]
    if _ENV_FILE.match(low):
        return ("Environment file", "secret")
    for comp, v in _COMPOUND_LONGEST_FIRST:
        if low.endswith("." + comp) and len(low) > len(comp) + 1:
            return v
    return None


def kind_info(name: str, is_dir: bool) -> tuple[str, str]:
    """(label, group) from the name only."""
    if is_dir:
        if name.lower().endswith(".app"):
            return ("macOS app", "app")
        return ("Folder", "folder")
    v = _by_name(name)
    if v:
        return v
    ext = ext_of(name)
    if not ext:
        if _NAME_PREFIX.match(name):
            return NAMES.get(_NAME_PREFIX.match(name).group(1).lower(), ("File", "other"))
        return ("File", "other")
    if ext in _KINDS:
        return _KINDS[ext]
    # A backup copy: notes.txt~ or report.docx.orig.
    if ext.endswith("~") or ext in ("orig", "rej", "save"):
        return ("Backup", "data")
    return (ext.upper() if len(ext) <= 5 else "File", "other")


def kind_of(name: str, is_dir: bool) -> str:
    return kind_info(name, is_dir)[0]


def group_of(name: str, is_dir: bool) -> str:
    return kind_info(name, is_dir)[1]


def known_ext(name: str) -> bool:
    """True when the name alone gives a real kind (no sniff needed)."""
    return kind_info(name, False)[1] != "other"


def mime_of(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# ---------------------------------------------------------------- content (magic bytes)

# (offset, bytes, label, group, the extensions that fit this content)
_MAGIC: list[tuple[int, bytes, str, str, set[str]]] = [
    (0, b"%PDF-", "PDF", "doc", {"pdf", "ai"}),
    (0, b"\x89PNG\r\n\x1a\n", "PNG", "image", {"png", "apng"}),
    (0, b"\xff\xd8\xff", "JPEG", "image", {"jpg", "jpeg", "jpe", "jfif"}),
    (0, b"GIF87a", "GIF", "image", {"gif"}),
    (0, b"GIF89a", "GIF", "image", {"gif"}),
    (0, b"BM", "BMP", "image", {"bmp", "dib"}),
    (0, b"II*\x00", "TIFF", "image", {"tif", "tiff", "dng", "cr2", "nef", "arw", "orf"}),
    (0, b"MM\x00*", "TIFF", "image", {"tif", "tiff", "dng", "nef"}),
    (0, b"\x00\x00\x01\x00", "Icon", "image", {"ico"}),
    (0, b"\xff\x0a", "JPEG XL", "image", {"jxl"}),
    (0, b"\x00\x00\x00\x0cJXL ", "JPEG XL", "image", {"jxl"}),
    (0, b"8BPS", "Photoshop", "image", {"psd"}),
    (0, b"gimp xcf", "GIMP image", "image", {"xcf"}),
    (0, b"PK\x03\x04", "ZIP", "archive", set()),  # refined below: docx, xlsx, jar, apk ...
    (0, b"PK\x05\x06", "ZIP", "archive", {"zip"}),
    (0, b"7z\xbc\xaf\x27\x1c", "7-Zip", "archive", {"7z"}),
    (0, b"Rar!\x1a\x07", "RAR", "archive", {"rar"}),
    (0, b"\x1f\x8b", "Gzip", "archive", {"gz", "tgz", "svgz"}),
    (0, b"\x28\xb5\x2f\xfd", "Zstd", "archive", {"zst", "tzst"}),
    (0, b"\xfd7zXZ\x00", "XZ", "archive", {"xz", "txz"}),
    (0, b"BZh", "Bzip2", "archive", {"bz2", "tbz2"}),
    (0, b"\x04\x22\x4d\x18", "LZ4", "archive", {"lz4"}),
    (257, b"ustar", "Tar", "archive", {"tar"}),
    (0, b"MSCF", "Cabinet", "archive", {"cab"}),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "Office (old format)", "doc", {"doc", "xls", "ppt", "msi", "msg", "vsd"}),
    (0, b"{\\rtf", "Rich text", "doc", {"rtf"}),
    (0, b"AT&TFORM", "DjVu", "doc", {"djvu"}),
    (0, b"\x7fELF", "Linux program", "app", {"so", "o", "bin", "run", "appimage", "elf", "ko", ""}),
    (0, b"MZ", "Windows program", "app", {"exe", "dll", "msi", "sys", "scr", "com", "efi", "cpl", "ocx", "mui"}),
    (0, b"\xcf\xfa\xed\xfe", "macOS program", "app", {"dylib", "bundle", "o", ""}),
    (0, b"\xca\xfe\xba\xbe", "macOS or Java binary", "app", {"class", "dylib", ""}),
    (0, b"!<arch>\n", "Debian package", "app", {"deb", "a", "lib"}),
    (0, b"\xed\xab\xee\xdb", "RPM package", "app", {"rpm"}),
    (0, b"SQLite format 3\x00", "Database", "data", {"db", "sqlite", "sqlite3", "db3", "gpkg", "mbtiles"}),
    (0, b"PAR1", "Parquet", "data", {"parquet"}),
    (0, b"\x93NUMPY", "NumPy array", "data", {"npy"}),
    (0, b"\x89HDF\r\n\x1a\n", "HDF5", "data", {"h5", "hdf5", "nc"}),
    (0, b"GGUF", "Model weights", "data", {"gguf"}),
    (0, b"fLaC", "FLAC", "audio", {"flac"}),
    (0, b"ID3", "MP3", "audio", {"mp3"}),
    (0, b"\xff\xfb", "MP3", "audio", {"mp3"}),
    (0, b"\xff\xf3", "MP3", "audio", {"mp3"}),
    (0, b"\xff\xf1", "AAC", "audio", {"aac"}),
    (0, b"\xff\xf9", "AAC", "audio", {"aac"}),
    (0, b"OggS", "Ogg", "audio", {"ogg", "oga", "opus", "ogv", "spx"}),
    (0, b"MThd", "MIDI", "audio", {"mid", "midi"}),
    (0, b"#!AMR", "AMR", "audio", {"amr"}),
    (0, b"\x1aE\xdf\xa3", "MKV", "video", {"mkv", "webm", "mka", "mk3d"}),
    (0, b"FLV", "Flash video", "video", {"flv"}),
    (0, b"\x00\x00\x01\xba", "MPEG", "video", {"mpg", "mpeg", "vob"}),
    (0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "Windows media", "video", {"wmv", "wma", "asf"}),
    (0, b"wOFF", "Web font", "font", {"woff"}),
    (0, b"wOF2", "Web font", "font", {"woff2"}),
    (0, b"\x00\x01\x00\x00\x00", "Font", "font", {"ttf", "ttc"}),
    (0, b"OTTO", "Font", "font", {"otf"}),
    (0, b"ttcf", "Font collection", "font", {"ttc"}),
    (0, b"-----BEGIN ", "Certificate or key", "secret", {"pem", "crt", "cer", "key", "csr", "pub", "asc", "gpg", ""}),
    (0, b"openssh-key-v1", "Private key", "secret", {"key", ""}),
    (0, b"\x03\xd9\xa2\x9a", "Password database", "secret", {"kdbx", "kdb"}),
    (0, b"age-encryption.org", "Encrypted file", "secret", {"age"}),
    (0, b"d8:announce", "Torrent", "data", {"torrent"}),
    (0, b"BEGIN:VCALENDAR", "Calendar", "data", {"ics", "ical"}),
    (0, b"BEGIN:VCARD", "Contact card", "data", {"vcf", "vcard"}),
    (0, b"\x00asm", "WebAssembly", "code", {"wasm"}),
    (0, b"glTF", "3D model", "model3d", {"glb"}),
    (0, b"BLENDER", "Blender", "model3d", {"blend"}),
    (0, b"solid ", "3D model", "model3d", {"stl"}),
    (0, b"\x4c\x00\x00\x00\x01\x14\x02\x00", "Shortcut", "link", {"lnk"}),
    (0, b"QFI\xfb", "VM disk", "disk", {"qcow2", "qcow", "img"}),
    (0, b"conectix", "VM disk", "disk", {"vhd"}),
    (0, b"KDMV", "VM disk", "disk", {"vmdk"}),
    (32769, b"CD001", "Disk image", "disk", {"iso", "img"}),
]
# ZIP containers: a member name tells the real kind.
_ZIP_MEMBERS = [
    (b"word/", "Word", "doc", {"docx", "docm", "dotx"}),
    (b"xl/", "Excel", "sheet", {"xlsx", "xlsm", "xltx"}),
    (b"ppt/", "PowerPoint", "doc", {"pptx", "pptm", "potx"}),
    (b"mimetypeapplication/vnd.oasis.opendocument.text", "Document", "doc", {"odt", "ott"}),
    (b"mimetypeapplication/vnd.oasis.opendocument.spreadsheet", "Spreadsheet", "sheet", {"ods", "ots"}),
    (b"mimetypeapplication/vnd.oasis.opendocument.presentation", "Presentation", "doc", {"odp", "otp"}),
    (b"mimetypeapplication/epub+zip", "EPUB", "doc", {"epub"}),
    (b"AndroidManifest.xml", "Android app", "app", {"apk", "aab", "xapk"}),
    (b"META-INF/MANIFEST.MF", "Java archive", "archive", {"jar", "war", "ear"}),
    (b".dist-info/", "Python wheel", "archive", {"whl"}),
]
_ZIP_LABELS = {m[1] for m in _ZIP_MEMBERS}
# Any ZIP-based name is fine for a ZIP with no clear member.
_ZIP_EXTS = {"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "apk", "jar", "whl", "kmz", "3mf", "xpi", "crx",
             "ipa", "aab", "sketch", "fig", "kra", "ora", "cbz", "nupkg", "vsix", "docm", "xlsm", "pptm", "war", "ear",
             "xps", "oxps", "pages", "numbers", "key", "drawio", "excalidraw", "mcpack", "aar", "apkg", "xapk"}
# ISO BMFF (MP4, MOV, HEIC, AVIF): the brand at offset 8 says which.
_FTYP = {
    b"heic": ("HEIC", "image", {"heic", "heif"}), b"heix": ("HEIC", "image", {"heic", "heif"}),
    b"mif1": ("HEIF", "image", {"heic", "heif", "avif"}), b"avif": ("AVIF", "image", {"avif"}),
    b"avis": ("AVIF", "image", {"avif"}), b"qt  ": ("QuickTime", "video", {"mov", "qt"}),
    b"M4A ": ("M4A", "audio", {"m4a", "m4b", "mp4", "aac"}), b"M4B ": ("M4A", "audio", {"m4a", "m4b"}),
    b"3gp4": ("3GP", "video", {"3gp", "mp4"}), b"3gp5": ("3GP", "video", {"3gp", "mp4"}),
    b"crx ": ("Camera raw", "image", {"cr3"}), b"jxl ": ("JPEG XL", "image", {"jxl"}),
}
_MP4_EXTS = {"mp4", "m4v", "m4a", "mov", "3gp", "m4b", "f4v", "m4p", "heic", "avif"}
_DOC_LIKE = {"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp", "epub",
             "jpg", "jpeg", "png", "gif", "webp", "mp3", "mp4", "mkv", "mov", "avi", "wav", "zip", "html", "htm"}
# The same extension for two kinds: the content picks the label.
_AMBIGUOUS = {"key", "img", "bin", "o", "m", "ts"}
_TEXT_GROUPS = {"doc", "sheet", "code", "log", "data", "secret", "partial", "link", "other"}
SNIFF_BYTES = 4096


def _read_head(path: str, n: int = SNIFF_BYTES) -> bytes:
    with open(path, "rb") as f:
        head = f.read(n)
        if len(head) == n and n < 32774:
            # The ISO 9660 mark sits at 32769. Read it only when the file is large enough.
            try:
                f.seek(32769)
                iso = f.read(5)
                if iso == b"CD001":
                    return head + b"\x00" * (32769 - len(head)) + iso
            except OSError:
                pass
        return head


def _looks_text(head: bytes) -> bool:
    if not head:
        return True
    if b"\x00" in head[:1024]:
        return False
    try:
        head[:2048].decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        # A cut multi-byte character at the end is still text.
        if e.start >= len(head[:2048]) - 3:
            return True
        ctrl = sum(1 for b in head[:1024] if b < 9 or 13 < b < 32)
        return ctrl < 8


def sniff_bytes(head: bytes) -> tuple[str, str, set[str]] | None:
    """(label, group, fitting extensions) from the first bytes, or None when nothing matches."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _FTYP:
            return _FTYP[brand]
        return ("MP4", "video", _MP4_EXTS)
    if len(head) >= 12 and head[:4] == b"RIFF":
        sub = head[8:12]
        if sub == b"WEBP":
            return ("WebP", "image", {"webp"})
        if sub == b"WAVE":
            return ("WAV", "audio", {"wav", "wave"})
        if sub == b"AVI ":
            return ("AVI", "video", {"avi"})
        return ("RIFF", "data", {"riff"})
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return ("AIFF", "audio", {"aiff", "aif", "aifc"})
    for off, sig, label, group, exts in _MAGIC:
        if head[off:off + len(sig)] == sig:
            if sig == b"PK\x03\x04":
                for member, l2, g2, e2 in _ZIP_MEMBERS:
                    if member in head:
                        return (l2, g2, e2)
                return ("ZIP", "archive", _ZIP_EXTS)
            return (label, group, exts)
    stripped = head.lstrip()
    if stripped[:2] == b"#!":
        line = stripped.split(b"\n", 1)[0].decode("utf-8", "replace")
        if "python" in line:
            return ("Python", "code", {"py", "pyw", ""})
        if any(s in line for s in ("bash", "/sh", "zsh", "fish", "env sh")):
            return ("Shell", "code", {"sh", "bash", "zsh", "fish", "run", "command", ""})
        if "node" in line:
            return ("JavaScript", "code", {"js", "mjs", "cjs", ""})
        return ("Script", "code", {""})
    low = stripped[:256].lower()
    if low.startswith((b"<!doctype html", b"<html")):
        return ("HTML", "code", {"html", "htm", "xhtml", "php", "asp", "aspx", "jsp", "shtml", "mhtml"})
    if low.startswith(b"<svg") or (low.startswith(b"<?xml") and b"<svg" in stripped[:1024].lower()):
        return ("SVG", "image", {"svg"})
    if low.startswith(b"<?xml"):
        return ("XML", "code", {"xml", "svg", "plist", "xsd", "xsl", "rss", "atom", "kml", "gpx", "drawio", "csproj", "fodt", "xhtml", "xaml", "musicxml"})
    if low.startswith((b"{", b"[")) and _looks_text(head):
        return ("JSON", "code", {"json", "jsonc", "json5", "geojson", "ipynb", "jsonl", "ndjson", "excalidraw", "har", "map", "lock", "js", "babelrc", "eslintrc", "prettierrc", "webmanifest", "txt", "log"})
    if _looks_text(head):
        return ("Text", "doc", set())  # any text name fits
    return None


def sniff(path: str) -> tuple[str, str, set[str]] | None:
    from .atime import keep_atime

    try:
        with keep_atime(path):
            return sniff_bytes(_read_head(path))
    except OSError:
        return None


def check(path: str, name: str) -> dict:
    """The kind from the name and from the content, and whether they disagree.

    Returns {kind, group, sniffed, mismatch, warning}. `kind` is the content's label when the name gives none.
    """
    label, group = kind_info(name, False)
    res = sniff(path)
    out = {"kind": label, "group": group, "sniffed": None, "mismatch": False, "warning": None}
    if res is None:
        return out
    s_label, s_group, s_exts = res
    out["sniffed"] = s_label
    ext = ext_of(name)
    by_name = _by_name(name)
    if group == "other" and not by_name:
        # No extension or an unknown one: the content decides the label.
        if s_label != "Text" or not ext:
            out["kind"], out["group"] = (s_label, s_group) if s_label != "Text" else ("Text", "doc")
        return out
    if s_label == "Text":
        # A text file can carry almost any text name. Only binary names on text are wrong.
        if group in _TEXT_GROUPS or ext in ("svg", "html", "htm", "xml", "json", "csv", "tsv", "eml", "ics", "vcf"):
            return out
        if group == "partial":
            return out
        out["mismatch"] = True
        return out
    if by_name and by_name[1] == "partial":
        return out
    if ext in s_exts or (by_name and s_group == by_name[1]):
        if ext in _AMBIGUOUS and s_group != "archive" and s_label != "Text":
            out["kind"], out["group"] = s_label, s_group
        return out
    if ext in _ZIP_EXTS and (s_label == "ZIP" or s_label in _ZIP_LABELS):
        # An Office file or an app is a ZIP inside. Either name is right.
        return out
    if group == "partial" or ext in ("bak", "old", "bin", "dat", "tmp", "dump"):
        return out
    out["mismatch"] = True
    if s_group == "app" and (ext in _DOC_LIKE or group in ("doc", "sheet", "image", "video", "audio")):
        out["warning"] = f"This is a program ({s_label}), but its name says .{ext}. Do not open it unless you trust it."
    return out
