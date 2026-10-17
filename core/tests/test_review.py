"""Review before send (PLAN4 7.7): the user sees each cloud payload, changes it, and sends it or not."""

import os
import tempfile
import threading
import time

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db, events  # noqa: E402
from raven_core.privacy import gate, review  # noqa: E402
from raven_core.router import local  # noqa: E402

db.init()

TEXT = "Invoice for Rahul Sharma, PAN ABCPN1234K. Project Falcon pays order 88123456 to vendor KX99ZT4417. Zentrix Labs signed."


@pytest.fixture()
def window(monkeypatch):
    """One open window. Each published event goes to the list."""
    got = []
    monkeypatch.setattr(events, "clients", lambda: 1)
    monkeypatch.setattr(events, "publish", lambda kind, **d: got.append((kind, d)))
    monkeypatch.setattr(local, "available", lambda: False)
    monkeypatch.setattr(gate.strict, "available", lambda: False)  # the same everywhere, with or without models/gliner-pii
    return got


def _sealed():
    red = gate.Redactor(use_layer2=False)
    payload = red.redact(TEXT)
    red.mapping["[PERSON_1]"] = "Rahul Sharma"  # as layer 2 would find it
    payload = payload.replace("Rahul Sharma", "[PERSON_1]")
    return gate.Sealed(payload), red


def _start(window):
    payload, red = _sealed()
    out = {}

    def run():
        try:
            out["sent"] = review.confirm(payload, red, "Ask", ["groq"], "system text")
        except gate.Blocked as e:
            out["blocked"] = str(e)

    t = threading.Thread(target=run)
    t.start()
    for _ in range(100):
        if any(k == "cloud_review" for k, _ in window):
            break
        time.sleep(0.02)
    rv = [d["review"] for k, d in window if k == "cloud_review"][-1]
    return t, out, rv


def test_no_window_sends_nothing(monkeypatch):
    monkeypatch.setattr(events, "clients", lambda: 0)
    payload, red = _sealed()
    with pytest.raises(gate.Blocked, match="no Raven window"):
        review.confirm(payload, red, "Ask", ["groq"])


def test_the_review_shows_the_payload_and_each_hidden_value(window):
    t, out, rv = _start(window)
    try:
        assert rv["payload"].startswith("Invoice for [PERSON_1], PAN [PAN_1]") and rv["feature"] == "Ask"
        items = {i["placeholder"]: i for i in rv["items"]}
        assert items["[PERSON_1]"]["value"] == "Rahul Sharma" and items["[PERSON_1]"]["can_show"]
        assert items["[PAN_1]"]["value"] == "ABCPN1234K" and not items["[PAN_1]"]["can_show"]
        assert rv["bytes"] == len(("system text\n\n" + rv["payload"]).encode())
        assert [r["id"] for r in review.pending()] == [rv["id"]]
    finally:
        review.decide(rv["id"], False)
        t.join(5)
    assert out["blocked"] == review.CANCELLED and not review.pending()
    assert window[-1][0] == "cloud_review_closed"


def test_send_sends_the_payload_with_the_users_changes(window):
    t, out, rv = _start(window)
    after = review.update(rv["id"], hide=["Project Falcon"], show=["[PERSON_1]"])
    assert "Falcon" not in after["payload"] and "[HIDDEN_1]" in after["payload"] and "Rahul Sharma" in after["payload"]
    with pytest.raises(ValueError, match="never goes to the cloud"):
        review.update(rv["id"], show=["[PAN_1]"])
    with pytest.raises(ValueError, match="not in the payload"):
        review.update(rv["id"], hide=["Gaonkar"])
    review.decide(rv["id"], True)
    t.join(5)
    assert isinstance(out["sent"], gate.Sealed) and str(out["sent"]) == after["payload"]
    with pytest.raises(KeyError):
        review.decide(rv["id"], True)


def test_a_change_that_leaves_a_high_risk_value_is_refused(window):
    t, out, rv = _start(window)
    try:
        # A user cannot show a hidden PAN, and a hide can never put one back: the final check runs on each change.
        with pytest.raises(ValueError):
            review.update(rv["id"], show=["[PAN_1]"])
        assert "ABCPN1234K" not in review.pending()[0]["payload"]
    finally:
        review.decide(rv["id"], False)
        t.join(5)


def test_nobody_answers(window, monkeypatch):
    monkeypatch.setattr(review, "WAIT_S", 1)
    payload, red = _sealed()
    with pytest.raises(gate.Blocked, match="5 minutes"):
        review.confirm(payload, red, "Ask", ["groq"])


def test_candidates_are_names_in_capitals_and_money():
    bare = "Call Ramesh about Project Falcon. Pay Kiran Rs 45,000 by Friday. The PDF is 2026-10, 109 B. Zentrix Labs uses Python."
    assert review.candidates(bare) == ["Ramesh", "Project Falcon", "Kiran", "Zentrix Labs", "Python", "Rs 45,000"]


def test_fast_suspects_find_long_numbers_and_codes_but_not_dates(monkeypatch):
    monkeypatch.setattr(gate.strict, "available", lambda: False)
    found = {s["text"]: s for s in review.fast_suspects("Order 88123456 to vendor KX99ZT4417 on IMG_20250412.jpg, [PERSON_1] 2026-10. "
                                                        "See Project Falcon budget.")}
    assert set(found) == {"88123456", "KX99ZT4417", "Project Falcon"}
    assert found["88123456"]["source"] == "pattern"


def test_the_local_model_suspects_come_later_and_are_checked(window, monkeypatch):
    monkeypatch.setattr(local, "available", lambda: True)
    items = [{"text": "Zentrix Labs", "private": True, "why": "a company name"},
             {"text": "Mumbai office", "private": True, "why": "not a candidate"},
             {"text": "Invoice", "private": False, "why": "a common word"}]
    asked = []

    def chat(system, user, schema, max_tokens=0):
        asked.append(user)
        return {"items": items}

    monkeypatch.setattr(local, "chat", chat)
    t, out, rv = _start(window)
    try:
        for _ in range(100):
            last = [d["review"] for k, d in window if k == "cloud_review"][-1]
            if last["llm"] == "done":
                break
            time.sleep(0.02)
        ai = [s for s in last["suspects"] if s["source"] == "local AI"]
        assert [s["text"] for s in ai] == ["Zentrix Labs"]  # the model cannot add a word that Raven did not give
        assert "- Zentrix Labs:" in asked[0] and "Rahul" not in asked[0]
        assert "- Project Falcon:" not in asked[0]  # the project rule found it already
        # A hidden suspect leaves the list.
        after = review.update(rv["id"], hide=["Zentrix Labs"])
        assert not [s for s in after["suspects"] if s["text"] == "Zentrix Labs"]
    finally:
        review.decide(rv["id"], False)
        t.join(5)
