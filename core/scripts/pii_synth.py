"""Synthetic PII documents for the recall check (PLAN4 7.3). All values are fake but pass their checksums.

Each document type (bank letter, payslip, medical report, rental agreement, chat, .env file, KYC form, mixed
Hindi and English) embeds known values. make_docs(n, seed) gives [(text, {type: [values]})].
"""

from __future__ import annotations

import random
import string

from raven_core.privacy import rules

FIRST = ["Rahul", "Priya", "Anil", "Sneha", "Vikram", "Aisha", "Rohan", "Meera", "Karan", "Divya", "Arjun", "Neha",
         "Suresh", "Lakshmi", "Imran", "Gurpreet", "Fatima", "Venkatesh"]
LAST = ["Sharma", "Nair", "Deshmukh", "Iyer", "Kulkarni", "Khan", "Patil", "Reddy", "Mehta", "Joshi", "Gupta", "Rao",
        "Singh", "Banerjee", "Chatterjee", "Pillai"]
STREETS = ["MG Road", "FC Road", "Linking Road", "Park Street", "Brigade Road", "Anna Salai"]
CITIES = [("Pune", "411"), ("Mumbai", "400"), ("Bengaluru", "560"), ("Chennai", "600"), ("Kolkata", "700")]
UP = string.ascii_uppercase


def _digits(rnd, n: int) -> str:
    return "".join(str(rnd.randint(0, 9)) for _ in range(n))


def _check(rnd, base_len: int, ok, first: str = "") -> str:
    while True:
        base = first + _digits(rnd, base_len - len(first))
        for d in "0123456789":
            if ok(base + d):
                return base + d


def aadhaar(rnd):
    n = _check(rnd, 11, rules.verhoeff_ok, str(rnd.randint(2, 9)))
    return f"{n[:4]} {n[4:8]} {n[8:]}"


def vid(rnd):
    n = _check(rnd, 15, rules.verhoeff_ok, "9")
    return " ".join(n[i:i + 4] for i in range(0, 16, 4))


def card(rnd):
    n = _check(rnd, 15, rules.luhn_ok, "4")
    return " ".join(n[i:i + 4] for i in range(0, 16, 4))


def pan(rnd):
    return "".join(rnd.choice(UP) for _ in range(3)) + "P" + rnd.choice(UP) + _digits(rnd, 4) + rnd.choice(UP)


def gstin(rnd):
    base = f"{rnd.randint(10, 37)}{pan(rnd)}{rnd.randint(1, 9)}Z"
    return next(base + ch for ch in rules._B36 if rules.gstin_ok(base + ch))


def imei(rnd):
    return _check(rnd, 14, rules.luhn_ok, "35")


def make_doc(rnd) -> tuple[str, dict[str, list[str]]]:
    first, last = rnd.choice(FIRST), rnd.choice(LAST)
    name = f"{first} {last}"
    user = name.lower().replace(" ", ".")
    city, pin3 = rnd.choice(CITIES)
    pin = f"{pin3}{rnd.randint(0, 99):03d}"
    v = {
        "PERSON": name, "EMAIL": f"{user}{rnd.randint(1, 99)}@gmail.com", "PHONE": f"+91 9{rnd.randint(100000000, 999999999)}",
        "AADHAAR": aadhaar(rnd), "VID": vid(rnd), "PAN": pan(rnd), "GSTIN": gstin(rnd),
        "ACCOUNT": str(rnd.randint(10**11, 10**14)), "CARD": card(rnd), "CVV": f"{rnd.randint(100, 999)}",
        "EXPIRY": f"{rnd.randint(1, 12):02d}/{rnd.randint(26, 31)}", "IFSC": f"HDFC0{rnd.randint(100000, 999999)}",
        "UPI": f"{user}@okaxis", "PASSPORT": f"{rnd.choice('JKLMNPRSTUVWZ')}{rnd.randint(1, 9)}{_digits(rnd, 5)}{rnd.randint(1, 9)}",
        "VOTERID": "".join(rnd.choice(UP) for _ in range(3)) + _digits(rnd, 7),
        "LICENCE": f"MH{rnd.randint(10, 50)} {rnd.randint(2000, 2024)}{_digits(rnd, 7)}",
        "UAN": f"10{_digits(rnd, 10)}", "ABHA": f"91-{_digits(rnd, 4)}-{_digits(rnd, 4)}-{_digits(rnd, 4)}",
        "OTP": _digits(rnd, 6), "MRN": f"H-{rnd.randint(2019, 2025)}-{_digits(rnd, 5)}",
        "HEALTH": rnd.choice(["type 2 diabetes mellitus", "essential hypertension", "bronchial asthma", "hypothyroidism"]),
        "DOB": f"{rnd.randint(1, 28):02d}/{rnd.randint(1, 12):02d}/{rnd.randint(1960, 2005)}",
        "POSTCODE": pin, "IMEI": imei(rnd), "IP": f"{rnd.randint(11, 220)}.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}.{rnd.randint(1, 254)}",
        "SECRET": "sk-proj-" + "".join(rnd.choice(string.ascii_lowercase + string.digits) for _ in range(32)),
        "PASSWORD": f"{rnd.choice(['mango', 'river', 'tiger'])}-{rnd.choice(['blue', 'red'])}-{rnd.randint(10, 9999)}",
        "ADDRESS": f"Flat {rnd.randint(1, 900)}, {rnd.choice(STREETS)}, {city}",
    }
    docs = [
        # bank letter
        f"Dear {v['PERSON']},\nYour account statement is ready. A/C {v['ACCOUNT']}, IFSC {v['IFSC']}.\n"
        f"The card {v['CARD']} (CVV {v['CVV']}, expiry {v['EXPIRY']}) was charged Rs 1,499. Your OTP is {v['OTP']}.",
        # KYC form
        f"KYC details on file: Aadhaar {v['AADHAAR']}, VID {v['VID']}, PAN {v['PAN']}.\n"
        f"Passport No: {v['PASSPORT']}. Voter ID {v['VOTERID']}. DL No {v['LICENCE']}. Date of birth: {v['DOB']}.",
        # payslip
        f"PAYSLIP Employee: {v['PERSON']}\nUAN: {v['UAN']} | PAN: {v['PAN']} | Bank A/C: {v['ACCOUNT']}\n"
        f"Employer GSTIN {v['GSTIN']}",
        # medical report
        f"Patient: {v['PERSON']}\nMRN: {v['MRN']}  ABHA: {v['ABHA']}\nDiagnosis: {v['HEALTH']}.",
        # rental agreement
        f"This agreement is made between the landlord and the tenant {v['PERSON']}, residing at {v['ADDRESS']}, "
        f"pincode {v['POSTCODE']}. Contact {v['EMAIL']} or {v['PHONE']}.",
        # chat
        f"[10:42] {first}: bhai UPI kar de {v['UPI']} pe\n[10:43] {first}: wifi password: {v['PASSWORD']}\n"
        f"[10:44] {first}: mera naya phone ka IMEI {v['IMEI']} hai",
        # .env file
        f"OPENAI_API_KEY={v['SECRET']}\nDB_HOST={v['IP']}\nDEBUG=false",
        # mixed Hindi and English
        f"नाम: {v['PERSON']}\nआधार: {v['AADHAAR']}\nमोबाइल: {v['PHONE']}\nपता: {v['ADDRESS']}, पिन {v['POSTCODE']}",
    ]
    rnd.shuffle(docs)
    text = "\n\n".join(docs)
    vals: dict[str, list[str]] = {}
    for k, val in v.items():
        vals.setdefault(k, []).append(val)
    return text, vals


def make_docs(n: int = 60, seed: int = 42) -> list[tuple[str, dict[str, list[str]]]]:
    rnd = random.Random(seed)
    return [make_doc(rnd) for _ in range(n)]


def covered(spans, text: str, value: str) -> bool:
    """A value counts as found when spans cover at least 80 % of every place it stands."""
    i, hits, places = 0, 0, 0
    while True:
        i = text.find(value, i)
        if i < 0:
            break
        j = i + len(value)
        places += 1
        got = sum(max(0, min(j, s.end) - max(i, s.start)) for s in spans)
        hits += got >= 0.8 * len(value)
        i = j
    return places > 0 and hits == places
