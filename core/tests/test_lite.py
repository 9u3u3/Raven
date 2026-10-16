"""Lite mode (PLAN2 P2-6)."""

import os
import tempfile
import zipfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db, lite  # noqa: E402

db.init()
GB = 1024**3


@pytest.mark.parametrize("ram,vram,threads,want", [
    (16 * GB, 6 * GB, 16, "full"), (16 * GB, 0, 8, "full"), (16 * GB, 0, 4, "lite"), (8 * GB, 4 * GB, 8, "lite"),
    (4 * GB, 0, 2, "lite"), (12 * GB, 4 * GB, 2, "full"),
])
def test_suggest(ram, vram, threads, want):
    assert lite.suggest({"ram": ram, "vram": vram, "threads": threads}) == want


def test_lite_model_by_ram():
    assert lite.lite_model({"ram": 16 * GB}) == "potion-retrieval-32m"
    assert lite.lite_model({"ram": 4 * GB}) == "potion-base-8m"


def test_features_follow_mode_and_switches(monkeypatch):
    monkeypatch.setattr(lite, "apply_priority", lambda: None)
    monkeypatch.setattr(lite, "lite_model", lambda hw=None: "potion-base-8m")
    db.set_setting("embed_model", "embeddinggemma")
    from raven_core import models

    monkeypatch.setattr(models, "download", lambda names: None)
    r = lite.set_mode("lite")
    assert r["embed_model"] == "potion-base-8m" and r["reembed"]
    assert not lite.enabled("llm") and not lite.enabled("reranker")
    lite.set_feature("reranker", True)
    assert lite.enabled("reranker")
    lite.set_feature("reranker", None)
    assert not lite.enabled("reranker")
    r = lite.set_mode("full")
    assert r["embed_model"] == "embeddinggemma"  # back to the model from before lite mode
    assert lite.enabled("llm") and lite.enabled("image")
    with pytest.raises(ValueError):
        lite.set_mode("tiny")
    with pytest.raises(ValueError):
        lite.set_feature("warp", True)


def test_local_model_off_in_lite(monkeypatch):
    from raven_core.router import local

    db.set_setting("features", {"llm": False})
    try:
        assert local.available() is False
        assert "lite" in local.unavailable_reason()
    finally:
        db.set_setting("features", {})


def test_light_office_reader(tmp_path):
    from raven_core.index.extract import _ooxml

    p = tmp_path / "a.docx"
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    doc = (f'<w:document xmlns:w="{W}"><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Budget plan</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Lanterns cost </w:t></w:r><w:r><w:t>1200.</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", doc)
    x = _ooxml(str(p), "docx")
    assert x.title == "Budget plan" and "Lanterns cost 1200." in x.sections[0].text
    q = tmp_path / "b.xlsx"
    S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(q, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{S}"><si><t>Grocery budget</t></si><si><t>Rice</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{S}"><sheetData><row><c t="s"><v>0</v></c></row>'
                                               f'<row><c t="s"><v>1</v></c><c><v>40</v></c></row></sheetData></worksheet>')
    y = _ooxml(str(q), "xlsx")
    assert y.title == "Grocery budget" and "Rice | 40" in y.sections[0].text


def test_extractive_answer():
    from raven_core.rag.ask import extractive
    from raven_core.rag.retrieve import Source

    s = Source.__new__(Source)
    s.n, s.hit, s.text = 1, "The flat is on the 4th floor. The late fee is 500 rupees after the 5th day. Pets are fine.", ""
    out = extractive("what is the late fee", [s])
    assert out == "The late fee is 500 rupees after the 5th day. [1]"


def test_unload_when_idle():
    import threading
    import time

    from raven_core.memory import unload_when_idle

    class M:
        _lock = threading.Lock()
        _sess = object()
        _used = time.monotonic()

    m = M()
    unload_when_idle(m, "test", idle=0.2)
    time.sleep(0.6)
    assert m._sess is None
