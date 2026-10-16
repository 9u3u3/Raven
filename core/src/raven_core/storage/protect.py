"""Folders that hold software, not user content.

Compression, duplicate removal and the empty-folder rule must never act inside them:
 - tool and SDK folders (Android SDK, NDK toolchains, Flutter, Go, JDKs, bin/lib trees),
   hidden folders, and folders with program files (.so, .jar, executables);
 - project folders (package.json, build.gradle, Cargo.toml, .git ...). A whole cold
   project may be archived as one unit, but nothing inside a project is touched.
Rebuildable junk rules (node_modules, __pycache__, build output) are separate and stay allowed.
"""

from __future__ import annotations

import os
import re
import threading

PROJECT_MARKERS = {
    ".git", "package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew", "pubspec.yaml",
    "CMakeLists.txt", "Makefile", "meson.build", "composer.json", "Gemfile", "mix.exs", "deno.json", "flake.nix",
    "AndroidManifest.xml", "Package.swift", "*.sln", "*.csproj",
    "*.ino", "library.properties", "platformio.ini", "*.pro", "*.xcodeproj",
}
TOOL_NAMES = {
    "Android", "Sdk", "sdk", "sdks", "ndk", "toolchains", "prebuilt", "build-tools", "platform-tools", "platforms",
    "system-images", "emulator", "cmdline-tools", "flutter", "go", "jdk", "jre", "jbr", "node_modules",
    "site-packages", "dist-packages", "Applications", "AppImages", "opt", "miniconda3", "anaconda3", "venv",
    "bin", "sbin", "lib", "lib32", "lib64", "libexec", "include", "share", "sysroot", "build", "intermediates",
    "target", "dist", "out", "vendor", "third_party", "external",
    # App data folders: the app keeps an index of the files inside.
    "Zotero", "Calibre Library", "snap", "Steam", "VirtualBox VMs", "Dictionaries",
}
SDK_MARKERS = {"source.properties", "package.xml", "VERSION", "version", "release"}
PROGRAM_EXTS = {"so", "a", "o", "dll", "exe", "dylib", "jar", "aar", "class", "pyc", "node", "wasm", "ko", "lib",
                # Model weights: a model folder breaks when one of its files goes.
                "safetensors", "gguf", "ggml", "onnx", "pt", "pth", "ckpt", "h5", "tflite", "mlmodel", "npz"}
# Files that only work inside their folder: one of them makes a software folder at once.
PART_EXTS = {"so", "a", "o", "dll", "dylib", "ko", "lib", "node", "safetensors", "gguf", "ggml", "onnx", "pt", "pth", "ckpt",
             "h5", "tflite", "mlmodel", "npz"}
# An installed app or SDK version, such as arduino-1.8.19 or jdk-17.0.2.
VERSIONED = re.compile(r"^[A-Za-z][\w.+-]*?[-_]v?\d+(\.\d+){1,3}([-_.][\w.-]+)?$")
MODEL_FILES = {"tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "merges.txt"}

_lock = threading.Lock()
_project_cache: dict[str, bool] = {}
_software_cache: dict[str, bool] = {}


def _has_marker(path: str, markers: set[str]) -> bool:
    try:
        names = os.listdir(path)
    except OSError:
        return False
    for n in names:
        if n in markers:
            return True
        for m in markers:
            if m.startswith("*.") and n.endswith(m[1:]):
                return True
    return False


def is_project_root(path: str) -> bool:
    with _lock:
        if path in _project_cache:
            return _project_cache[path]
    v = _has_marker(path, PROJECT_MARKERS)
    with _lock:
        _project_cache[path] = v
    return v


def is_cache_name(name: str) -> bool:
    """App caches such as CacheClip (DaVinci Resolve) or GPUCache. Apps manage these themselves."""
    return "cache" in name.lower()


def is_backup_name(name: str) -> bool:
    """Backups are copies on purpose. Removing a backup copy defeats the backup."""
    n = name.lower()
    return "backup" in n or n.endswith((".bak", ".orig"))


def is_software_dir(path: str) -> bool:
    """A tool or SDK folder, a hidden folder, an app cache, or a folder with program files in it."""
    name = os.path.basename(path.rstrip("/"))
    if name.startswith(".") or name in TOOL_NAMES or is_cache_name(name) or VERSIONED.match(name):
        return True
    with _lock:
        if path in _software_cache:
            return _software_cache[path]
    v = False
    if name not in USER_FOLDERS:  # Downloads with one installer is still a user folder
        files = programs = 0
        try:
            with os.scandir(path) as it:
                for i, de in enumerate(it):
                    if de.name in SDK_MARKERS or de.name in MODEL_FILES:
                        v = True
                        break
                    if de.is_file(follow_symlinks=False):
                        files += 1
                        ext = de.name.rsplit(".", 1)[-1].lower() if "." in de.name else ""
                        # The executable bit is not used: on NTFS and FAT drives every file has it.
                        if ext in PART_EXTS:  # a library or model weights: an installed app or a model
                            v = True
                            break
                        programs += ext in PROGRAM_EXTS
                    if i > 400:
                        break
        except OSError:
            pass
        # Program files must be a large share: one installer among documents does not make a software folder.
        v = v or (programs > 0 and programs * 3 >= files)
    with _lock:
        _software_cache[path] = v
    return v


USER_FOLDERS = {"Downloads", "Documents", "Desktop", "Pictures", "Music", "Videos", "Public", "Templates", "Recordings",
                "Screenshots", "Camera", "Photos"}


def is_tool_dir(path: str) -> bool:
    """A strict software check for the index: an SDK, a toolchain, a cache or a versioned app folder, by its
    name or by SDK and model marker files. Not "has a program file": a Downloads folder with one installer is
    a user folder, and the index must read it."""
    name = os.path.basename(path.rstrip("/"))
    if name in USER_FOLDERS:
        return False
    if name.startswith(".") or name in TOOL_NAMES or is_cache_name(name) or VERSIONED.match(name):
        return True
    return _has_marker(path, SDK_MARKERS | MODEL_FILES)


def user_protected(path: str, flag: str = "protect") -> bool:
    """A folder rule (PLAN2 P2-5): the user said never touch it (or never compress it)."""
    from ..fs.props import policy_for

    return policy_for(path, flag)


def blocked(path: str, stop_at: str, allow_project_root: bool = False) -> bool:
    """True when `path` (a file or a folder) lies in software or inside a project.

    Every folder from `path` up to `stop_at` (not included) is checked. With
    `allow_project_root`, `path` itself may be a project root (a whole project as one unit).
    """
    if user_protected(path) or user_protected(path, "no_compress"):
        return True
    stop_at = os.path.abspath(stop_at).rstrip("/") or "/"
    cur = os.path.abspath(path)
    first = True
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
        first = False
    while cur and cur != stop_at and cur.startswith(stop_at.rstrip("/") + "/"):
        if is_software_dir(cur):
            return True
        if is_project_root(cur) and not (first and allow_project_root):
            return True
        first = False
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return False


def forget() -> None:
    """Clear the caches. A new scan can see new marker files."""
    with _lock:
        _project_cache.clear()
        _software_cache.clear()
