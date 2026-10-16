"""PLAN.md 10.7: recall per PII type for layer 1, layer 2 and both, on 60 synthetic documents.

All values are fake. Each document embeds known values in plain sentences. A value counts as
found when a detected span covers it.

Usage: uv run python scripts/pii_recall.py [--no-layer2]
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict

from raven_core.privacy import rules
from raven_core.privacy.model import model as layer2

FIRST = ["Rahul", "Priya", "Anil", "Sneha", "Vikram", "Aisha", "Rohan", "Meera", "Karan", "Divya", "Arjun", "Neha"]
LAST = ["Sharma", "Nair", "Deshmukh", "Iyer", "Kulkarni", "Khan", "Patil", "Reddy", "Mehta", "Joshi", "Gupta", "Rao"]
STREETS = ["MG Road", "FC Road", "Linking Road", "Park Street", "Brigade Road", "Anna Salai"]
CITIES = [("Pune", "4110"), ("Mumbai", "4000"), ("Bengaluru", "5600"), ("Chennai", "6000"), ("Kolkata", "7000")]


def aadhaar(rnd):
    while True:
        base = str(rnd.randint(2, 9)) + "".join(str(rnd.randint(0, 9)) for _ in range(10))
        for d in "0123456789":
            if rules.verhoeff_ok(base + d):
                n = base + d
                return f"{n[:4]} {n[4:8]} {n[8:]}"


def card(rnd):
    base = "4" + "".join(str(rnd.randint(0, 9)) for _ in range(14))
    for d in "0123456789":
        if rules.luhn_ok(base + d):
            n = base + d
            return " ".join(n[i:i + 4] for i in range(0, 16, 4))


def pan(rnd):
    up = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(rnd.choice(up) for _ in range(3)) + "P" + rnd.choice(up) + f"{rnd.randint(1000, 9999)}" + rnd.choice(up)


def make_doc(rnd):
    name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
    user = name.lower().replace(" ", ".")
    city, pin = rnd.choice(CITIES)
    vals = {
        "PERSON": name,
        "EMAIL": f"{user}{rnd.randint(1, 99)}@gmail.com",
        "PHONE": f"+91 9{rnd.randint(100000000, 999999999)}",
        "AADHAAR": aadhaar(rnd),
        "PAN": pan(rnd),
        "ACCOUNT": str(rnd.randint(10**11, 10**14)),
        "CARD": card(rnd),
        "IFSC": f"HDFC0{rnd.randint(100000, 999999)}",
        "UPI": f"{user}@okaxis",
        "ADDRESS": f"Flat {rnd.randint(1, 900)}, {rnd.choice(STREETS)}, {city} {pin}{rnd.randint(10, 99)}",
        "SECRET": "sk-proj-" + "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(32)),
    }
    templates = [
        f"Dear {vals['PERSON']},\nYour account statement is ready. A/C {vals['ACCOUNT']}, IFSC {vals['IFSC']}.",
        f"Customer contact: {vals['EMAIL']} or call {vals['PHONE']}.",
        f"KYC details on file: Aadhaar {vals['AADHAAR']} and PAN {vals['PAN']}.",
        f"The card {vals['CARD']} was charged Rs 1,499 on the 3rd.",
        f"Pay with UPI to {vals['UPI']}. Deliver to {vals['ADDRESS']}.",
        f"Deploy note: the API key is {vals['SECRET']} until rotation.",
    ]
    rnd.shuffle(templates)
    text = "\n".join(templates)
    return text, vals


def covered(spans, text, value):
    i = text.find(value)
    if i < 0:
        return False
    j = i + len(value)
    return any(s.start <= i + 1 and s.end >= j - 1 for s in spans) or any(
        s.start < j and s.end > i and (min(j, s.end) - max(i, s.start)) >= 0.8 * len(value) for s in spans
    )


def main() -> None:
    use_l2 = "--no-layer2" not in sys.argv and layer2.available()
    rnd = random.Random(42)
    hit = {k: defaultdict(int) for k in ("layer1", "layer2", "both")}
    total = defaultdict(int)
    for _ in range(60):
        text, vals = make_doc(rnd)
        s1 = rules.detect(text)
        s2 = layer2.detect(text) if use_l2 else []
        for typ, v in vals.items():
            total[typ] += 1
            a, b = covered(s1, text, v), covered(s2, text, v)
            hit["layer1"][typ] += a
            hit["layer2"][typ] += b
            hit["both"][typ] += a or b
    print(f"{'type':10} {'layer 1':>8} {'layer 2':>8} {'both':>8}")
    for typ in total:
        row = [hit[k][typ] / total[typ] * 100 for k in ("layer1", "layer2", "both")]
        l2 = f"{row[1]:7.0f}%" if use_l2 else "     n/a"
        print(f"{typ:10} {row[0]:7.0f}% {l2} {row[2]:7.0f}%")
    n = sum(total.values())
    print(f"{'all':10} {sum(hit['layer1'].values()) / n * 100:7.0f}% "
          f"{(sum(hit['layer2'].values()) / n * 100) if use_l2 else 0:7.0f}% {sum(hit['both'].values()) / n * 100:7.0f}%")


if __name__ == "__main__":
    main()
