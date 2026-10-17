"""Folder names on the whole disk, for folder search by name (section 13.12 item 6).

The content index covers only the index roots. A user also wants to jump to folders above them, such as
~/Codes/work/acme-26, and to a project by its project name ("raven" for a repo folder named fernbox).
This module walks the home folder and the index roots for folder names only (no file reads), in the background.
A folder with a project marker (.git, package.json, pyproject.toml and so on) also gets its project name as an alias.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from rapidfuzz import fuzz, process, utils

from .. import config, db

log = logging.getLogger("raven.diskdirs")

RESCAN_EVERY = 20 * 60
MAX_DIRS = 400_000
MAX_DEPTH = 12
# Build output, caches and package stores: many folders, no folder that a person looks for by name.
SKIP = config.EXCLUDE_NAMES | {
    "build", "dist", "target", "out", ".gradle", ".dart_tool", ".next", ".nuxt", ".svelte-kit", "site-packages",
    "Pods", "DerivedData", ".idea", ".vscode", "bower_components", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "coverage", ".parcel-cache", ".turbo", "obj", "CMakeFiles",
}
# SDKs, toolchains and app stores: the folder itself is listed, but Raven does not go inside.
NO_DESCEND = {
    "Sdk", "sdk", "sdks", "ndk", "toolchains", "flutter", "go", "jdk", "jre", "jbr", "miniconda3", "anaconda3",
    "opt", "Steam", "snap", "Applications", "AppImages", "include", "lib", "lib32", "lib64", "libexec", "sysroot",
    "intermediates", "third_party", "external", "vendor", "dist-packages", ".pub-cache", "Zotero", "VirtualBox VMs",
    "fixtures", "__fixtures__", "__snapshots__", "__tests__",
}
# Test trees of other projects hold many small folders with common names (foo, bar, a). They rank last.
TEST_PARTS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "fixtures", "__fixtures__", "playground",
              "playgrounds", "samples", "examples", "testdata", "__snapshots__", "benchmarks"}
SAME_NAME_MAX = 3
MARKERS = (".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pubspec.yaml", "setup.py", "pom.xml",
           "build.gradle", "build.gradle.kts", "composer.json", "Gemfile", "CMakeLists.txt", "README.md")
PROJECT_MARKERS = set(MARKERS) - {"README.md"}


def _project_names(path: str, present: set[str]) -> list[str]:
    """The names a project gives itself: the README title and the manifest name. Small reads only."""
    names: list[str] = []

    def read(name: str, limit: int = 4096) -> str:
        try:
            with open(os.path.join(path, name), encoding="utf-8", errors="replace") as f:
                return f.read(limit)
        except OSError:
            return ""

    if "package.json" in present:
        try:
            n = json.loads(read("package.json", 65536)).get("name")
            if isinstance(n, str):
                names.append(n.split("/")[-1])
        except (ValueError, AttributeError):
            pass
    for fname, pat in (("pyproject.toml", r'^\s*name\s*=\s*["\']([^"\']+)'), ("Cargo.toml", r'^\s*name\s*=\s*["\']([^"\']+)'),
                       ("pubspec.yaml", r"^name:\s*([\w-]+)"), ("go.mod", r"^module\s+(\S+)"),
                       ("settings.gradle", r"rootProject\.name\s*=\s*['\"]([^'\"]+)")):
        if fname in present:
            m = re.search(pat, read(fname), re.M)
            if m:
                names.append(m.group(1).rsplit("/", 1)[-1])
    if "README.md" in present:
        m = re.search(r"^#\s+(.{2,60}?)\s*#*\s*$", read("README.md", 2048), re.M)
        if m:
            names.append(re.sub(r"[`*_\[\]]", "", m.group(1)).strip())
    base = os.path.basename(path).lower()
    out = []
    for n in names:
        if n and n.lower() != base and n.lower() not in (o.lower() for o in out):
            out.append(n)
    return out[:3]


class DiskDirs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []
        self.keys: list[str] = []
        self.key_path: list[int] = []
        self.alias: dict[int, list[str]] = {}
        self.project: set[int] = set()
        self.scanned_at = 0.0
        self.scanning = False
        self.ms = 0.0
        self.wake = threading.Event()

    def tops(self) -> list[str]:
        tops = [str(Path.home())]
        for r in db.get_setting("index_roots", []):
            if not any(r == t or r.startswith(t.rstrip("/") + "/") for t in tops):
                tops.append(r)
        return tops

    def scan(self) -> None:
        from ..privacy.scope import is_private

        t0 = time.perf_counter()
        self.scanning = True
        paths: list[str] = []
        alias: dict[int, list[str]] = {}
        project: set[int] = set()
        try:
            for top in self.tops():
                # Each stack item: the folder, its depth, and its index in `paths` (None for a top folder).
                stack: list[tuple[str, int, int | None]] = [(top, 0, None)]
                while stack and len(paths) < MAX_DIRS:
                    d, depth, idx = stack.pop()
                    present: set[str] = set()
                    subdirs: list[str] = []
                    try:
                        with os.scandir(d) as it:
                            for de in it:
                                if de.name in MARKERS:
                                    present.add(de.name)
                                if de.name.startswith(".") or de.name in SKIP:
                                    continue
                                try:
                                    if de.is_dir(follow_symlinks=False) and not is_private(de.path):
                                        subdirs.append(de.path)  # a private folder (PLAN4 7.6) is not found
                                except OSError:
                                    continue
                    except OSError:
                        continue
                    if idx is not None and present & PROJECT_MARKERS:
                        project.add(idx)
                        names = _project_names(d, present)
                        if names:
                            alias[idx] = names
                    if depth >= MAX_DEPTH or (idx is not None and os.path.basename(d) in NO_DESCEND):
                        continue
                    for sd in subdirs:
                        paths.append(sd)
                        stack.append((sd, depth + 1, len(paths) - 1))
        finally:
            self.scanning = False
        self._publish(paths, alias, project, (time.perf_counter() - t0) * 1000)

    def _publish(self, paths, alias, project, ms) -> None:
        keys, key_path = [], []
        for i, p in enumerate(paths):
            keys.append(utils.default_process(os.path.basename(p)))
            key_path.append(i)
            for a in alias.get(i, []):
                keys.append(utils.default_process(a))
                key_path.append(i)
        with self.lock:
            self.paths, self.keys, self.key_path, self.alias, self.project = paths, keys, key_path, alias, project
            self.scanned_at = time.time()
            self.ms = ms
        log.info("disk folders: %d in %.0f ms, %d projects", len(paths), ms, len(project))

    def loop(self, stop: threading.Event) -> None:
        stop.wait(3)
        while not stop.is_set():
            try:
                self.scan()
            except Exception:  # noqa: BLE001
                log.exception("disk folder scan failed")
            self.wake.wait(RESCAN_EVERY)
            self.wake.clear()

    def search(self, words: list[str], limit: int = 30) -> list[tuple[str, float, str | None, float]]:
        """(path, score 0..105, the project name that matched or None, the share of the query words it holds), best first."""
        if not words:
            return []
        q = " ".join(words)
        with self.lock:
            keys, key_path, paths, alias, project = self.keys, self.key_path, self.paths, self.alias, self.project
        if not keys:
            return []
        if len(words) == 1:
            hits = process.extract(q, keys, scorer=fuzz.WRatio, limit=limit * 4, score_cutoff=86, processor=None)
        else:
            hits = process.extract(q, keys, scorer=fuzz.token_set_ratio, limit=limit * 4, score_cutoff=90, processor=None)
        best: dict[int, tuple[float, str | None, float]] = {}
        for key, score, k in hits:
            i = key_path[k]
            name = utils.default_process(os.path.basename(paths[i]))
            tokens = key.split()
            # Short keys such as "os" match inside many words. They count only as a whole word.
            if len(key) <= 3 and key not in words:
                continue
            # A match must start a word of the name: "raven" finds "raven_core", not "Wes_Craven".
            if not any(t.startswith(w[:3]) for w in words for t in tokens):
                continue
            hits_n = sum(1 for w in words if any(t.startswith(w) or w.startswith(t) for t in tokens))
            share = hits_n / len(words)
            # A name that explains one word of "a bar chart" is not that folder: 2 words need both,
            # 3 or more words need all but one.
            if len(words) > 1 and hits_n < (len(words) if len(words) == 2 else len(words) - 1):
                continue
            if key == q:
                score += 5
            via = None if key == name else next((a for a in alias.get(i, []) if utils.default_process(a) == key), None)
            if score > best.get(i, (-1, None, 0))[0]:
                best[i] = (score, via, share)
        def in_tests(i: int) -> bool:
            return any(part in TEST_PARTS for part in paths[i].split("/")[:-1])

        # A folder in a test tree last. Ties: a project root first, then a folder whose own name matched,
        # then the shorter path.
        ranked = sorted(best.items(), key=lambda kv: (in_tests(kv[0]), -kv[1][0], kv[0] not in project,
                                                      kv[1][1] is not None, paths[kv[0]].count("/")))
        out = []
        per_name: dict[str, int] = {}
        for i, (s, via, share) in ranked:
            name = os.path.basename(paths[i]).lower()
            per_name[name] = per_name.get(name, 0) + 1
            if per_name[name] > SAME_NAME_MAX:
                continue
            out.append((paths[i], s, via, share))
            if len(out) >= limit:
                break
        return out

    def project_names(self, path: str) -> list[str]:
        with self.lock:
            try:
                i = self.paths.index(path)
            except ValueError:
                return []
            return self.alias.get(i, [])

    def status(self) -> dict:
        return {"dirs": len(self.paths), "projects": len(self.project), "scan_ms": round(self.ms), "scanning": self.scanning,
                "scanned_at": self.scanned_at}


disk_dirs = DiskDirs()
