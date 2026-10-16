"""End-to-end test of the file graph (PLAN2 P2-7).

1. A Markdown vault with [[wikilinks]] (aliases, headings), Markdown links and orphans: the graph's "ref"
   edges must equal the links in the notes, as Obsidian draws them.
2. 10,000 files: the time to build the graph and to answer /graph (budget: 2 s for the answer).

Usage: core/.venv/bin/python testdata/graph_e2e.py
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import run_device_test as rdt  # noqa: E402


def make_vault(root: Path, n: int = 40, seed: int = 3) -> set[tuple[str, str]]:
    rnd = random.Random(seed)
    names = [f"Note {i:02d}" for i in range(n)]
    want: set[tuple[str, str]] = set()
    for i, name in enumerate(names):
        lines = [f"# {name}", ""]
        if i >= n - 3:
            lines.append("An orphan note with no links.")
        else:
            for t in rnd.sample([x for x in names[: n - 3] if x != name], rnd.randint(1, 4)):
                form = rnd.choice(["[[{t}]]", "[[{t}|an alias]]", "[[{t}#A heading]]", "[text]({f})"])
                lines.append("See " + form.format(t=t, f=t.replace(" ", "%20") + ".md") + " for more.")
                want.add((name, t))
        sub = root / ("deep" if i % 5 == 0 else "")
        sub.mkdir(parents=True, exist_ok=True)
        # Markdown links use the folder of the note: notes in "deep" link by name, which Obsidian resolves too.
        (sub / f"{name}.md").write_text("\n".join(lines) + "\n")
    return want


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="raven-vault-"))
    data = tempfile.mkdtemp(prefix="raven-graph-data-")
    want = make_vault(root)
    sc = rdt.Sidecar(data)
    ok = []

    def check(what, cond, detail=""):
        ok.append(bool(cond))
        print(f"{'PASS' if cond else 'FAIL'}  {what:44} {detail}")

    try:
        sc.post("/index/roots", {"roots": [str(root)]})
        t = time.time()
        while time.time() - t < 120:
            s = sc.get("/index/status")
            if s["files"] >= 40 and s["text_pending"] == 0:
                break
            time.sleep(0.5)
        sc.post("/graph/build", {})
        time.sleep(1)
        t1 = time.perf_counter()
        g = sc.get("/graph", root=str(root), types="ref")
        ms = (time.perf_counter() - t1) * 1000
        name = {n["id"]: n["name"][:-3] for n in g["nodes"]}
        got = {(name[e["src"]], name[e["dst"]]) for e in g["edges"]}
        check("vault: every wikilink is an edge", want <= got, f"{len(want & got)}/{len(want)}")
        check("vault: no extra edges", got <= want, f"{len(got - want)} extra")
        check("vault: orphans are not in the default view", all(f"Note {i}" not in name.values() for i in (37, 38, 39)))
        check("vault: /graph answers fast", ms < 2000, f"{ms:.0f} ms")
        loc = sc.get("/graph/local", path=str(root / "Note 01.md"), depth=1)
        check("local graph of one note", loc["center"] is not None and len(loc["nodes"]) >= 2, f"{len(loc['nodes'])} nodes")
    finally:
        sc.stop()
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)

    # 10,000 files, in-process on a temporary database.
    data2 = tempfile.mkdtemp(prefix="raven-graph-10k-")
    os.environ["RAVEN_DATA_DIR"] = data2
    sys.path.insert(0, str(HERE.parent / "core" / "src"))
    from raven_core import db, graph
    from raven_core.index.indexer import write_chunks

    db.init()
    rnd = random.Random(5)
    N = 10_000
    with db.tx() as c:
        for i in range(N):
            folder = f"/vault/area{i % 40}"
            links = " ".join(f"[[n{rnd.randrange(N)}]]" for _ in range(rnd.randint(0, 3)))
            fid = c.execute("INSERT INTO files(path, parent, name, ext, size, mtime, is_dir, index_stage) VALUES (?,?,?,?,?,?,0,2)",
                            (f"{folder}/n{i}.md", folder, f"n{i}.md", "md", 100, i)).lastrowid
            write_chunks(c, fid, f"n{i}.md", None, [{"page": None, "start": 0, "end": 10, "heading": "", "folder": "",
                                                    "header": "", "text": f"note {i} {links}"}])
    t = time.perf_counter()
    graph.build()
    build_s = time.perf_counter() - t
    t = time.perf_counter()
    big = graph.graph(types=["ref"], limit=3000)
    q1 = time.perf_counter() - t
    t = time.perf_counter()
    full = graph.graph(types=["ref"], limit=20_000)
    q2 = time.perf_counter() - t
    check("10k: build", True, f"{build_s:.1f} s, {graph.status()['edge_count']} edges")
    check("10k: first view (folder level) under 2 s", q1 < 2 and big["level"] == "folder", f"{q1 * 1000:.0f} ms, {len(big['nodes'])} folders")
    check("10k: every file at once under 2 s", q2 < 2 and full["level"] == "file", f"{q2 * 1000:.0f} ms, {len(full['nodes'])} files")
    shutil.rmtree(data2, ignore_errors=True)
    print(f"\n{sum(ok)}/{len(ok)} checks pass")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
