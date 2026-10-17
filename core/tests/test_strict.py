"""Strict privacy (PLAN4 7.3): the second model, the repeat rule and fail closed.

The fake model tests run everywhere. The tests with the real GLiNER model skip when models/gliner-pii is not there.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db, lite  # noqa: E402
from raven_core.privacy import gate  # noqa: E402
from raven_core.privacy import gliner as gliner_mod  # noqa: E402
from raven_core.privacy.rules import Span  # noqa: E402

db.init()


class FakeStrict:
    """Finds each name in NAMES. error is set to make it fail."""

    NAMES = ["Mohammed Irfan Siddiqui", "Harleen"]

    def __init__(self):
        self.error = None
        self.calls = 0

    def available(self):
        return True

    def detect(self, text):
        self.calls += 1
        out = []
        for n in self.NAMES:
            i = text.find(n)
            if i >= 0:
                out.append(Span("PERSON", i, i + len(n), 0.9, 2))
        return out


@pytest.fixture()
def fake(monkeypatch):
    f = FakeStrict()
    monkeypatch.setattr(gate, "strict", f)
    # A ready Privacy Filter that finds nothing, so only the fake model counts and the test is the same everywhere.
    monkeypatch.setattr(gate.layer2, "available", lambda: True)
    monkeypatch.setattr(gate.layer2, "detect", lambda text: [])
    monkeypatch.setattr(gate.layer2, "error", None)
    lite.set_feature("pii_strict", True)
    yield f
    lite.set_feature("pii_strict", None)


def test_strict_finds_what_layer2_misses(fake):
    r = gate.Redactor()
    out = r.redact("Forwarding the CV of Mohammed Irfan Siddiqui for the backend role.")
    assert "Siddiqui" not in out and "[PERSON_1]" in out
    assert r.strict_state == "on" and fake.calls == 1


def test_strict_switch_off_skips_the_model(fake):
    lite.set_feature("pii_strict", False)
    r = gate.Redactor()
    assert "Siddiqui" in r.redact("CV of Mohammed Irfan Siddiqui.")
    assert fake.calls == 0 and r.strict_state == "off"


def test_a_found_name_is_hidden_in_every_place(fake):
    r = gate.Redactor()
    r.redact("Harleen called.")
    # The model does not see the name in the next part, but the repeat rule does, in text and in a file name.
    FakeStrict.NAMES, old = [], FakeStrict.NAMES
    try:
        text = r.redact("Later HARLEEN wrote again.")
        names = r.redact_names(["harleen_passport_scan.pdf", "budget.xlsx"])
    finally:
        FakeStrict.NAMES = old
    assert "harleen" not in text.lower() and "[PERSON_1]" in text
    assert names == ["[PERSON_1]_passport_scan.pdf", "budget.xlsx"]


def test_a_part_of_a_found_full_name_is_hidden(fake):
    r = gate.Redactor()
    out = r.redact("CV of Mohammed Irfan Siddiqui. Irfan starts on Monday. The irfan_notes folder stays.")
    assert "Irfan starts" not in out and "[PERSON_2] starts" in out
    assert "irfan_notes" in out  # a part only counts where it starts with a capital letter


def test_the_repeat_rule_matches_whole_words_only(fake):
    r = gate.Redactor()
    r.redact("Harleen called.")
    FakeStrict.NAMES, old = [], FakeStrict.NAMES
    try:
        assert r.redact("Harleenpur is a town.") == "Harleenpur is a town."
    finally:
        FakeStrict.NAMES = old


def test_strict_fails_closed_when_its_model_does_not_load(fake, monkeypatch):
    fake.error = "The strict privacy model failed to load: test"
    r = gate.Redactor()
    payload = r.redact("CV of Mohammed Irfan Siddiqui.")
    assert r.strict_state == "error"
    monkeypatch.setattr(gate, "layer2_ready", lambda: None)  # seal() must also see the state of this request
    with pytest.raises(gate.Blocked, match="Strict privacy is on"):
        gate.seal(payload, r)


def test_layer2_ready_names_the_strict_model(fake):
    assert gate.layer2_ready() is None
    fake.error = "failed"
    assert gate.layer2_ready() == gate.STRICT_NOT_READY


def test_strict_is_a_feature_with_a_download():
    from raven_core import features, models

    f = features.BY_ID["strict"]
    assert f["switch"] == "pii_strict" and f["models"] == ["gliner-pii"] and "privacy" in f["needs"]
    assert "pii_strict" in lite.FEATURES
    spec = models.CATALOG["gliner-pii"]
    assert gliner_mod.ONNX in spec["files"] and "tokenizer.json" in spec["files"]


# ---------------------------------------------------------------- the real model

real = pytest.mark.skipif(not gliner_mod.model.available(), reason="models/gliner-pii not downloaded")


@real
def test_real_model_finds_names_the_privacy_filter_missed():
    m = gliner_mod.model
    for text, name in [("Forwarding the CV of Mohammed Irfan Siddiqui for the backend role.", "Mohammed Irfan Siddiqui"),
                       ("Gurpreet ne bola ki kal Harleen ke ghar party hai.", "Gurpreet"),
                       ("Ship to: House no. 17, Sector 21C, Faridabad.", "Sector 21C")]:
        spans = m.detect(text)
        assert any(s.start <= text.index(name) and s.end >= text.index(name) + len(name) for s in spans), (text, spans)
    assert not m.detect("Python 3.12 runs the sidecar with FastAPI, and BM25 ranks the words.")


@real
def test_real_model_reads_long_text_in_windows_and_keeps_lines_apart():
    m = gliner_mod.model
    filler = "The report covers the plan for the next quarter in some detail. " * 40  # about 480 words
    text = filler + "The contract was signed by Mohammed Irfan Siddiqui in Pune."
    assert any(text[s.start:s.end].endswith("Siddiqui") for s in m.detect(text))
    for s in m.detect("Rahul Sharma\nPriya Nair"):
        assert "\n" not in "Rahul Sharma\nPriya Nair"[s.start:s.end]
