"""Folder names on the whole disk: project names as aliases, whole-word matches, and SDK folders kept shallow."""

import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core.search.disk_dirs import DiskDirs  # noqa: E402

HOME = Path(_tmp) / "home"


def make(rel: str, files: dict[str, str] | None = None) -> None:
    d = HOME / rel
    d.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (d / name).write_text(text)


def scanned() -> DiskDirs:
    make("Codes/work/acme-26/fernbox", {"README.md": "# Raven\n\nFinds files.", ".git": ""})
    make("Codes/work/acme-26/fernbox/app", {"package.json": '{"name": "raven"}'})
    make("Codes/work/acme-26/fernbox/app/node_modules/raven-deep")
    make("Photos/Wes_Craven")
    make("Android/Sdk/icons/drone")
    make(".hidden/raven")
    d = DiskDirs()
    d.tops = lambda: [str(HOME)]
    d.scan()
    return d


def rel(results):
    return [os.path.relpath(p, HOME) for p, *_ in results]


def test_project_name_finds_the_repo_first():
    d = scanned()
    r = d.search(["raven"])
    assert rel(r)[0] == "Codes/work/acme-26/fernbox"
    assert r[0][2] == "Raven"


def test_parent_folder_by_name():
    d = scanned()
    assert rel(d.search(["acme"]))[0] == "Codes/work/acme-26"


def test_no_match_inside_a_word():
    d = scanned()
    assert "Photos/Wes_Craven" not in rel(d.search(["raven"]))


def test_skips_build_hidden_and_sdk_insides():
    d = scanned()
    got = rel(d.search(["raven"]))
    assert not any("node_modules" in p or ".hidden" in p for p in got)
    assert rel(d.search(["sdk"])) == ["Android/Sdk"]
    assert d.search(["drone"]) == []


def test_one_word_of_a_description_is_not_a_folder_name():
    from raven_core.search.disk_dirs import DiskDirs

    d = DiskDirs()
    base = "/h/Codes/lib"
    paths = [f"{base}/tests/case{i}/bar" for i in range(10)] + ["/h/Charts/bar charts", "/h/Codes/bar"]
    d._publish(paths, {}, set(), 0)
    got = [p for p, *_ in d.search(["bar", "chart"])]
    assert got == ["/h/Charts/bar charts"]
    one = [p for p, *_ in d.search(["bar"])]
    # Outside test trees first, and the same name at most 3 times.
    assert one[0] == "/h/Codes/bar" and sum(p.endswith("/bar") for p in one) == 3
