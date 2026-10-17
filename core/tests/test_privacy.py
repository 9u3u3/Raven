"""Privacy: layer-1 recognizers, the gate, placeholders, the final check and the router policy."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-privacy-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))

import pytest  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.privacy import gate, review, rules  # noqa: E402
from raven_core.router import cloud, local  # noqa: E402
from raven_core.router import providers as registry  # noqa: E402
from raven_core.router import router as core  # noqa: E402

db.init()


def types(text):
    return {(s.type, text[s.start:s.end]) for s in rules.detect(text)}


def test_checksums():
    assert rules.luhn_ok("4111111111111111") and not rules.luhn_ok("4111111111111112")
    assert rules.verhoeff_ok("234123412346") and not rules.verhoeff_ok("234123412345")
    assert rules.iban_ok("GB82 WEST 1234 5698 7654 32") and not rules.iban_ok("GB82 WEST 1234 5698 7654 33")


def test_layer1_indian_ids():
    t = "PAN ABCPN1234K, IFSC HDFC0001234, UPI priya.nair@okaxis, Aadhaar 2341 2341 2346, A/C 50100234567890"
    got = types(t)
    assert ("PAN", "ABCPN1234K") in got
    assert ("IFSC", "HDFC0001234") in got
    assert ("UPI", "priya.nair@okaxis") in got
    assert ("AADHAAR", "2341 2341 2346") in got
    assert ("ACCOUNT", "50100234567890") in got


def test_layer1_no_false_aadhaar_or_card():
    got = types("Order 1234 5678 9012 shipped. Invoice 4111 1111 1111 1112.")
    assert not any(t in ("AADHAAR", "CARD") for t, _ in got)


def test_secrets_and_passwords():
    got = types("key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 and wifi password: Hunter2024!")
    assert any(t == "SECRET" for t, _ in got)
    assert ("PASSWORD", "Hunter2024!") in got


def test_gate_placeholders_and_restore():
    r = gate.redact_text("Invoice for Mr. Rahul Sharma, A/C 50100234567890, rahul.s@gmail.com. Mail rahul.s@gmail.com again.", use_layer2=False)
    assert "Rahul" not in r.text and "50100234567890" not in r.text and "gmail" not in r.text
    assert r.text.count("[EMAIL_1]") == 2  # same value, same placeholder
    assert not r.blocked
    back = gate.restore("Send the bill to [PERSON_1] at [EMAIL_1].", r.mapping)
    assert back == "Send the bill to Rahul Sharma at rahul.s@gmail.com."


def test_final_check_blocks_high_risk():
    blocked, why = gate.final_check("Card 4111 1111 1111 1111 is still here")
    assert blocked and "CARD" in why
    assert gate.final_check("Card [CARD_1] is redacted")[0] is False


@pytest.fixture()
def cloud_ready(monkeypatch):
    """Two fake providers, a ready Privacy Filter, and the cloud online. Each call records the payload."""
    sent = []

    def chat(pid, system, user, schema):
        assert isinstance(user, gate.Sealed)
        sent.append((pid, str(user)))
        return {"summary": "Resume of [PERSON_1].", "points": []}, f"{pid} · test"

    monkeypatch.setattr(registry, "order", lambda: ["anthropic", "groq"])
    monkeypatch.setattr(registry, "chat", chat)
    monkeypatch.setattr(cloud, "online", lambda: True)
    monkeypatch.setattr(gate, "layer2_ready", lambda: None)
    # The user presses Send in each review (PLAN4 7.7). tests/test_review.py tests the review itself.
    monkeypatch.setattr(review, "confirm", lambda payload, red, feature, providers, system="": payload)
    return sent


def test_router_local_only_never_calls_cloud(tmp_path, monkeypatch, cloud_ready):
    (tmp_path / "a.txt").write_text("hello")
    db.set_setting("ai_mode", "local")
    monkeypatch.setattr(local, "available", lambda: True)
    monkeypatch.setattr(local, "chat", lambda *a, **k: {"summary": "local", "points": []})
    before = len(gate.ledger_list())
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["tier"] == "T2" and res["fallback"] == "Local only mode"
    assert not cloud_ready and len(gate.ledger_list()) == before
    db.set_setting("ai_mode", "hybrid")


def test_router_hybrid_think_harder_redacts(tmp_path, cloud_ready):
    (tmp_path / "Rahul_Sharma_resume.txt").write_text("hello")
    db.set_setting("ai_mode", "hybrid")
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["tier"] == "T3" and res["provider"] == "anthropic"
    payload = cloud_ready[0][1]
    assert "Rahul" not in payload
    assert "Rahul" in res["summary"]
    row = gate.ledger_list(1)[0]
    assert row["payload"] == payload and "Rahul" not in row["payload"]


def test_router_sends_nothing_when_the_user_does_not_send(tmp_path, monkeypatch, cloud_ready):
    (tmp_path / "x.txt").write_text("hello")
    db.set_setting("ai_mode", "ask")  # the old Ask each time mode is hybrid now, with a review for every call
    seen = []

    def no(payload, red, feature, providers, system=""):
        seen.append((feature, str(payload)))
        raise gate.Blocked(review.CANCELLED)

    monkeypatch.setattr(review, "confirm", no)
    monkeypatch.setattr(local, "available", lambda: True)
    monkeypatch.setattr(local, "chat", lambda *a, **k: {"summary": "local", "points": []})
    before = len(gate.ledger_list())
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert seen and seen[0][0] == "Folder summary"
    assert res["tier"] == "T2" and res["fallback"] == review.CANCELLED
    assert len(gate.ledger_list()) == before and not cloud_ready
    db.set_setting("ai_mode", "hybrid")


def test_router_fails_closed_without_the_privacy_filter(tmp_path, monkeypatch, cloud_ready):
    (tmp_path / "x.txt").write_text("hello")
    monkeypatch.setattr(gate, "layer2_ready", lambda: gate.NOT_INSTALLED)
    monkeypatch.setattr(local, "available", lambda: True)
    monkeypatch.setattr(local, "chat", lambda *a, **k: {"summary": "local", "points": []})
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["tier"] == "T2" and "Privacy Filter is not installed" in res["fallback"]
    assert not cloud_ready


def test_names_with_people_are_redacted():
    from raven_core.privacy.model import model

    if not model.available():
        pytest.skip("Privacy Filter model not downloaded")
    r = gate.Redactor()
    assert "Rahul" not in r.redact("Rahul_Sharma_resume.txt", name=True)
    assert "priya" not in r.redact("priya-nair-aadhaar.jpg", name=True)
    assert r.redact("invoice_8812.pdf", name=True) == "invoice_8812.pdf"
    assert r.redact("notes_final_v2.md", name=True) == "notes_final_v2.md"


def test_router_uses_the_user_order(tmp_path, monkeypatch, cloud_ready):
    (tmp_path / "x.txt").write_text("hello")
    db.set_setting("ai_mode", "hybrid")
    monkeypatch.setattr(registry, "order", lambda: ["groq", "anthropic"])
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["provider"] == "groq" and [p for p, _ in cloud_ready] == ["groq"]
