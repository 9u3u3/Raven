"""The agent: the fast-path grammar, plan limits, and one undo for a whole command."""

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from raven_core import db  # noqa: E402
from raven_core.agent import grammar  # noqa: E402
from raven_core.agent import plan as agent  # noqa: E402
from raven_core.fs import ops  # noqa: E402

db.init()


def drain():
    while ops.peek()["undo"]:
        ops.undo()


def run(plan_id, keep=None):
    gen = agent.run(plan_id, keep)
    try:
        while True:
            gen.send(None)
    except StopIteration as stop:
        return stop.value


@pytest.fixture()
def home(tmp_path):
    sem = tmp_path / "Documents" / "Sem5"
    (sem / "DBMS").mkdir(parents=True)
    (sem / "marks.csv").write_text("name,mark\na,1\nb,2\n")
    (sem / "DBMS" / "grades.csv").write_text("name,grade\na,A\n")
    (sem / "DBMS" / "notes.txt").write_text("notes")
    (tmp_path / "Downloads").mkdir()
    for i in range(3):
        (tmp_path / "Downloads" / f"Screenshot {i}.png").write_bytes(b"png")
    return tmp_path


def test_grammar_exit_command():
    steps = grammar.parse("convert all DOCX files in Sem5 to PDF and move them to Submissions")
    assert steps == [
        {"op": "find", "folder": "sem5", "types": ["docx"], "name": "", "about": ""},
        {"op": "convert", "input": 1, "to": "pdf"},
        {"op": "move", "input": 2, "to": "Submissions"},
    ]


@pytest.mark.parametrize("cmd,ops_", [
    ("move all pdfs in Downloads to Documents/Scans", ["find", "move"]),
    ("make folder Archive", ["mkdir"]),
    ("trash all .log files in lab", ["find", "trash"]),
    ("zip the Projects folder", ["find", "compress"]),
    ("rename these to photo-{n}", ["selection", "rename"]),
    ("convert the csv files on Desktop to xlsx", ["find", "convert"]),
])
def test_grammar_forms(cmd, ops_):
    assert [s["op"] for s in grammar.parse(cmd)] == ops_


def test_grammar_rejects_other_text():
    assert grammar.parse("what is my rent") is None
    with pytest.raises(grammar.OrganizeRequest):
        grammar.parse("organize my downloads")


def test_convert_move_is_one_undo(home):
    drain()
    ctx = agent.Context(str(home), [], "convert all csv files in Sem5 to xlsx and move them to Submissions")
    p = agent.resolve(grammar.parse(ctx.command), ctx, "fast path", "grammar", 0)
    labels = [s.label for s in p.steps if s.op not in ("find", "selection")]
    assert labels == ["Convert CSV to XLSX", "Create folder Submissions", "Move to Submissions"]
    before = sorted(str(x) for x in home.rglob("*"))
    res = run(p.id)
    sub = home / "Documents" / "Submissions"
    assert sorted(x.name for x in sub.iterdir()) == ["grades.xlsx", "marks.xlsx"]
    assert not res["errors"]
    ops.undo()
    assert sorted(str(x) for x in home.rglob("*")) == before


def test_unchecked_input_drops_out_of_later_steps(home):
    drain()
    ctx = agent.Context(str(home), [], "convert all csv files in Sem5 to xlsx and move them to Out")
    p = agent.resolve(grammar.parse(ctx.command), ctx, "fast path", "grammar", 0)
    conv = next(i for i, s in enumerate(p.steps, 1) if s.op == "convert")
    keep = {conv: [str(home / "Documents" / "Sem5" / "marks.csv")]}
    run(p.id, keep)
    out = home / "Documents" / "Out"
    assert [x.name for x in out.iterdir()] == ["marks.xlsx"]
    assert not (home / "Documents" / "Sem5" / "DBMS" / "grades.xlsx").exists()
    ops.undo()


def test_rename_pattern_and_limits(home):
    drain()
    ctx = agent.Context(str(home / "Downloads"), [], "rename all png files in Downloads to shot-{n}")
    p = agent.resolve(grammar.parse(ctx.command), ctx, "fast path", "grammar", 0)
    run(p.id)
    assert sorted(x.name for x in (home / "Downloads").iterdir()) == ["shot-1.png", "shot-2.png", "shot-3.png"]
    ops.undo()
    assert len(list((home / "Downloads").glob("Screenshot *.png"))) == 3
    # A model plan cannot reach outside the allowed folders.
    ctx = agent.Context(str(home / "Downloads"), [], "x")
    with pytest.raises(agent.PlanError):
        agent.resolve([{"op": "mkdir", "to": "evil", "in": "/etc"}], ctx, "T2", "m", 0)
    with pytest.raises(agent.PlanError):
        agent.resolve([{"op": "mkdir", "to": f"d{i}"} for i in range(agent.MAX_STEPS + 1)], ctx, "T2", "m", 0)


def test_trash_needs_confirm(home):
    ctx = agent.Context(str(home), [], "trash all png files in Downloads")
    p = agent.resolve(grammar.parse(ctx.command), ctx, "fast path", "grammar", 0)
    step = next(s for s in p.steps if s.op == "trash")
    assert step.confirm and len(step.items) == 3
    agent.PLANS.pop(p.id)


def test_new_folder_named_goes_into_the_folder_in_view(home):
    drain()
    d = home / "Downloads"
    ctx = agent.Context(str(d), [], "move all png files to a folder named Shots")
    p = agent.resolve(grammar.parse(ctx.command), ctx, "fast path", "grammar", 0)
    run(p.id)
    assert sorted(x.name for x in (d / "Shots").iterdir()) == ["Screenshot 0.png", "Screenshot 1.png", "Screenshot 2.png"]
    assert not (home / "a folder named Shots").exists()
    ops.undo()
    assert not (d / "Shots").exists() and (d / "Screenshot 0.png").exists()


def test_grammar_reads_polite_words_and_a_place_said_first():
    """The command bar sends what the user typed: polite words and a place said first must still parse."""
    from raven_core.agent import grammar

    mk = [{"op": "mkdir", "to": "harshtest", "in": "desktop"}]
    assert grammar.parse("in desktop make a folder called harshtest") == mk
    assert grammar.parse("create a folder named harshtest on the desktop") == mk
    assert grammar.parse("in my Documents folder, create a folder called notes") == [{"op": "mkdir", "to": "notes", "in": "Documents"}]
    mv = grammar.parse("please, in Downloads move all pdfs to Papers")
    assert mv[0]["folder"] == "downloads" and mv[0]["types"] == ["pdf"] and mv[1] == {"op": "move", "input": 1, "to": "Papers"}
    assert grammar.parse("i want to convert the docx files in Office to pdf")[1] == {"op": "convert", "input": 1, "to": "pdf"}
