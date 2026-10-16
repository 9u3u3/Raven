"""Privacy: layer-1 recognizers, the gate, placeholders, the final check and the router policy."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-privacy-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))

import pytest  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.privacy import gate, rules  # noqa: E402
from raven_core.router import cloud, local  # noqa: E402
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


def test_router_local_only_never_calls_cloud(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("hello")
    db.set_setting("ai_mode", "local")
    calls = []
    monkeypatch.setattr(cloud, "PROVIDERS", {"claude": lambda *a: calls.append(a), "groq": lambda *a: calls.append(a)})
    monkeypatch.setattr(cloud, "configured", lambda: {"claude": True, "groq": True})
    monkeypatch.setattr(local, "available", lambda: True)
    monkeypatch.setattr(local, "chat", lambda *a, **k: {"summary": "local", "points": []})
    before = len(gate.ledger_list())
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["tier"] == "T2" and res["fallback"] == "Local only mode"
    assert not calls and len(gate.ledger_list()) == before
    db.set_setting("ai_mode", "hybrid")


def test_router_hybrid_think_harder_redacts(tmp_path, monkeypatch):
    (tmp_path / "Rahul_Sharma_resume.txt").write_text("hello")
    db.set_setting("ai_mode", "hybrid")
    sent = []

    def fake_claude(system, user, schema):
        sent.append(user)
        return {"summary": "Resume of [PERSON_1].", "points": []}, "claude -p · test"

    monkeypatch.setattr(cloud, "PROVIDERS", {"claude": fake_claude, "groq": fake_claude})
    monkeypatch.setattr(cloud, "configured", lambda: {"claude": True, "groq": False})
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["tier"] == "T3" and "Rahul" not in sent[0]
    assert res["summary"] == "Resume of Rahul_Sharma_resume." or "Rahul" in res["summary"]
    row = gate.ledger_list(1)[0]
    assert row["payload"] == sent[0] and "Rahul" not in row["payload"]


def test_router_ask_each_time_needs_consent(tmp_path, monkeypatch):
    (tmp_path / "x.txt").write_text("hello")
    db.set_setting("ai_mode", "ask")
    monkeypatch.setattr(cloud, "configured", lambda: {"claude": True, "groq": False})
    before = len(gate.ledger_list())
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["needs_consent"] and len(gate.ledger_list()) == before
    db.set_setting("ai_mode", "hybrid")


def test_names_with_people_are_redacted():
    from raven_core.privacy.model import model

    if not model.available():
        pytest.skip("Privacy Filter model not downloaded")
    r = gate.Redactor()
    assert "Rahul" not in r.redact("Rahul_Sharma_resume.txt", name=True)
    assert "priya" not in r.redact("priya-nair-aadhaar.jpg", name=True)
    assert r.redact("invoice_8812.pdf", name=True) == "invoice_8812.pdf"
    assert r.redact("notes_final_v2.md", name=True) == "notes_final_v2.md"


def test_router_prefers_the_chosen_provider(tmp_path, monkeypatch):
    (tmp_path / "x.txt").write_text("hello")
    db.set_setting("ai_mode", "hybrid")
    db.set_setting("cloud_provider", "groq")
    used = []

    def fake(name):
        def f(system, user, schema):
            used.append(name)
            return {"summary": "ok", "points": []}, name
        return f

    monkeypatch.setattr(cloud, "PROVIDERS", {"claude": fake("claude"), "groq": fake("groq")})
    monkeypatch.setattr(cloud, "configured", lambda: {"claude": True, "groq": True})
    res = core.run("folder_summary", str(tmp_path), think_harder=True)
    assert res["provider"] == "groq" and used == ["groq"]
    db.set_setting("cloud_provider", "auto")
