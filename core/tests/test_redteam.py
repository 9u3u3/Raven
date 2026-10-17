"""Red-team test for the gate (PLAN4 7.5): each type in hard places. A table, split over two lines, with spaces,
in a file name, in a URL. Layer 1 alone must catch every one, so CI checks it without the model.

Also: custom words (7.4), the place of a blocked item (7.5), and the layer-1 recall check on the synthetic set
(7.3, step 5)."""

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.privacy import gate, rules  # noqa: E402

db.init()
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from pii_synth import covered, make_docs  # noqa: E402

# (type, context label, value). The label is the word a person writes next to the value.
CASES = [
    ("AADHAAR", "Aadhaar", "2341 2341 2346"),
    ("VID", "VID", "9123 4567 8901 2346"),
    ("PAN", "PAN", "ABCPN1234K"),
    ("GSTIN", "GSTIN", "27ABCDE1234F1Z0"),
    ("PASSPORT", "Passport", "K1234567"),
    ("VOTERID", "Voter ID", "ABC1234567"),
    ("LICENCE", "DL", "MH12 20110012345"),
    ("UAN", "UAN", "100123456789"),
    ("ABHA", "ABHA", "91-1234-5678-9012"),
    ("RATIONCARD", "Ration card", "MH1234567890"),
    ("ACCOUNT", "Account number", "50100234567890"),
    ("CARD", "Card", "4111 1111 1111 1111"),
    ("CVV", "CVV", "123"),
    ("EXPIRY", "Expiry", "09/27"),
    ("IBAN", "IBAN", "GB82WEST12345698765432"),
    ("WALLET", "BTC", "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"),
    ("OTP", "OTP", "482913"),
    ("MRN", "MRN", "H-2023-4471"),
    ("SECRET", "key", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"),
    ("SECRET", "stripe", "sk_live_4eC39HqLyjWDarjtT1zdp7dc"),
    ("PASSWORD", "password", "Hunter2024!"),
    ("PHONE", "Mobile", "+91 98765 43210"),
    ("EMAIL", "Email", "rahul.s@gmail.com"),
]
# Types whose digits a person can split over two lines or write with a space after each digit.
NUMBERS = {"AADHAAR", "VID", "CARD", "ACCOUNT", "UAN"}


def _compact(v: str) -> str:
    return re.sub(r"[\s\-]", "", v)


def places(typ: str, label: str, v: str) -> dict[str, str]:
    slug = label.lower().replace(" ", "_")
    out = {
        "table": f"| Name | {label} | City |\n|---|---|---|\n| R. S. | {v} | Pune |",
        "file name": f"scan_{slug}_{_compact(v) if typ in NUMBERS else v.replace(' ', '_')}.pdf",
        "URL": f"https://forms.example.com/kyc?{slug}={quote(_compact(v) if typ in NUMBERS else v)}&step=2",
    }
    if typ in NUMBERS:
        d = _compact(v)
        half = len(d) // 2
        out["two lines"] = f"{label}:\n{d[:half]}\n{d[half:]}"
        out["spaces"] = f"{label}: {' '.join(d)}"
    return out


def _leaked(red_text: str, v: str) -> bool:
    flat = re.sub(r"[\s\-|]", "", red_text)
    return _compact(v) in flat or v in red_text


@pytest.mark.parametrize("typ,label,v", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_every_type_is_caught_in_every_place(typ, label, v):
    for where, text in places(typ, label, v).items():
        if where in ("file name", "URL") and typ in ("PHONE", "EXPIRY", "CVV"):
            continue  # a phone, a CVV or an expiry date does not stand in a file name or a URL
        r = gate.redact_text(text, use_layer2=False)
        assert not _leaked(r.text, v), f"{typ} leaked in {where}: {r.text!r}"
        assert not r.blocked, f"{typ} in {where}: {r.reason}"


def test_custom_words_are_redacted_in_names_and_text():
    db.set_setting("privacy_words", ["Acme Corp", "Project Falcon", "Gaonkar"])
    gate.forget_words_cache()
    try:
        r = gate.redact_text("Acme Corp signed. See acme_corp_project_falcon.pdf and project-falcon_v2.md. Mr GAONKAR agreed.",
                             use_layer2=False)
        low = r.text.lower()
        assert "acme" not in low and "falcon" not in low and "gaonkar" not in low, r.text
        assert "[CUSTOM_1]" in r.text and "CUSTOM" in rules.HIGH_RISK
        blocked, why = gate.final_check("The client is Acme Corp.")
        assert blocked and "CUSTOM" in why
    finally:
        db.set_setting("privacy_words", [])
        gate.forget_words_cache()


def test_a_block_says_where_the_item_is_without_the_value():
    blocked, why = gate.final_check("Hello.\nPlease charge card 4111 1111 1111 1111 for the order today.")
    assert blocked and "CARD" in why
    assert "charge card [hidden CARD] for the order" in why
    assert "4111" not in why


def test_no_false_hits_in_plain_text():
    text = ("Version 2.3 of the plan ships on 12 March. The meeting is at 10:30:45 in room 4. Order 1234 5678 9012 "
            "shipped. Terms and conditions apply. Run `for dx in range(4)` to test. IMG_20250412_101500.jpg")
    found = {s.type for s in rules.detect(text)}
    assert not found & rules.HIGH_RISK, found


def test_layer1_recall_on_the_synthetic_set():
    """PLAN4 7.3 step 5: CI fails when layer 1 drops below 0.99 on a high-risk type it covers."""
    total, hit = {}, {}
    for text, vals in make_docs(40, seed=7):
        spans = rules.detect(text)
        for typ, vs in vals.items():
            if typ not in rules.HIGH_RISK:
                continue
            for v in vs:
                total[typ] = total.get(typ, 0) + 1
                hit[typ] = hit.get(typ, 0) + covered(spans, text, v)
    low = {t: round(hit[t] / total[t], 3) for t in total if hit[t] / total[t] < 0.99}
    assert not low, f"layer-1 recall below 0.99: {low}"


def test_tables_find_values_by_column_and_stay_fast():
    import time

    rows = "\n".join(f"{i},Student {i},{80 + i % 20}" for i in range(5000))
    t = time.perf_counter()
    rules.detect("Roll,Remark,Marks\n" + rows)
    assert time.perf_counter() - t < 1.0  # one pass over each table, not one per line
    csv = "Name,PAN,Mobile\nRahul Sharma,ABCPN1234K,9876543210\nPriya Nair,BCDPN2345L,9123456780"
    got = {(s.type, csv[s.start:s.end]) for s in rules.detect(csv)}
    assert {("PERSON", "Rahul Sharma"), ("PAN", "BCDPN2345L"), ("PHONE", "9123456780")} <= got
    code = 'r = urllib.request.Request(url, data, {"token": token})\nreturn json.load(urllib.request.urlopen(r, timeout=9))'
    assert not rules.detect(code)
