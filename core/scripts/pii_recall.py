"""PLAN4 7.3: recall per PII type for layer 1, layer 2 and both, on synthetic documents (scripts/pii_synth.py).

All values are fake. A value counts as found when detected spans cover it in every place it stands. The run
also gives the time per 1,000 words for each layer.

Usage: uv run python scripts/pii_recall.py [--no-layer2] [--docs 60] [--check]

The "strict" column adds the Strict mode model (privacy/gliner.py) when models/gliner-pii is there.

--check: exit with code 1 when a high-risk type is below its pass mark (CI runs it with --no-layer2).
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pii_synth import covered, make_docs  # noqa: E402

from raven_core.privacy import rules  # noqa: E402
from raven_core.privacy.gliner import model as strict  # noqa: E402
from raven_core.privacy.model import model as layer2  # noqa: E402

# PLAN4 7.3 pass marks: 0.99 for each high-risk type (patterns plus model), 0.95 for names and addresses.
PASS = {t: 0.99 for t in rules.HIGH_RISK} | {"PERSON": 0.95, "ADDRESS": 0.95}


def hard_set(use_strict: bool) -> None:
    """Hand-written texts (scripts/pii_hard.json): names, addresses and health words in many forms. The
    synthetic set uses fixed forms, so it cannot show what Strict mode adds (PLAN4 7.3)."""
    import json

    docs = json.loads((Path(__file__).parent / "pii_hard.json").read_text(encoding="utf-8"))
    total, today, strict_hit = defaultdict(int), defaultdict(int), defaultdict(int)
    for d in docs:
        base = rules.detect(d["text"]) + layer2.detect(d["text"])
        more = strict.detect(d["text"]) if use_strict else []
        for typ, vs in d["vals"].items():
            for v in vs:
                total[typ] += 1
                today[typ] += covered(base, d["text"], v)
                strict_hit[typ] += covered(base + more, d["text"], v)
    print(f"\nhard set, {len(docs)} texts: layers 1 and 2, then with Strict mode")
    for typ in sorted(total):
        st = f"{strict_hit[typ] / total[typ] * 100:7.0f}%" if use_strict else "     n/a"
        print(f"{typ:11} {today[typ] / total[typ] * 100:7.0f}% {st}   ({total[typ]} values)")


def main() -> None:
    use_l2 = "--no-layer2" not in sys.argv and layer2.available()
    use_strict = use_l2 and strict.available()  # Strict mode (PLAN4 7.3): the second model
    n = int(sys.argv[sys.argv.index("--docs") + 1]) if "--docs" in sys.argv else 60
    docs = make_docs(n)
    hit = {k: defaultdict(int) for k in ("layer1", "layer2", "both", "strict")}
    total = defaultdict(int)
    t1 = t2 = t3 = 0.0
    words = sum(len(t.split()) for t, _ in docs)
    if use_l2:
        layer2.detect("warm up")
    if use_strict:
        strict.detect("warm up")
    for text, vals in docs:
        a0 = time.perf_counter()
        s1 = rules.detect(text)
        t1 += time.perf_counter() - a0
        a0 = time.perf_counter()
        s2 = layer2.detect(text) if use_l2 else []
        t2 += time.perf_counter() - a0
        a0 = time.perf_counter()
        s3 = strict.detect(text) if use_strict else []
        t3 += time.perf_counter() - a0
        for typ, vs in vals.items():
            for v in vs:
                total[typ] += 1
                a, b = covered(s1, text, v), covered(s2, text, v)
                both = covered(s1 + s2, text, v)
                hit["layer1"][typ] += a
                hit["layer2"][typ] += b
                hit["both"][typ] += both
                hit["strict"][typ] += covered(s1 + s2 + s3, text, v)
    print(f"{n} documents, {words} words")
    print(f"{'type':11} {'layer 1':>8} {'layer 2':>8} {'both':>8} {'strict':>8} {'pass':>6}")
    bad = []
    for typ in sorted(total):
        row = [hit[k][typ] / total[typ] for k in ("layer1", "layer2", "both", "strict")]
        mark = PASS.get(typ)
        ok = "" if mark is None else ("ok" if row[2] >= mark else "FAIL")
        if ok == "FAIL":
            bad.append(typ)
        l2 = f"{row[1] * 100:7.0f}%" if use_l2 else "     n/a"
        st = f"{row[3] * 100:7.0f}%" if use_strict else "     n/a"
        print(f"{typ:11} {row[0] * 100:7.0f}% {l2} {row[2] * 100:7.0f}% {st} {ok:>6}")
    print(f"layer 1: {t1 / words * 1000 * 1000:.1f} ms per 1,000 words")
    if use_l2:
        print(f"layer 2: {t2 / words * 1000 * 1000:.1f} ms per 1,000 words")
    if use_strict:
        print(f"strict model: {t3 / words * 1000 * 1000:.1f} ms per 1,000 words")
    if use_l2:
        hard_set(use_strict)
    if bad:
        print("below the pass mark:", ", ".join(bad))
    if "--check" in sys.argv and set(bad) & rules.HIGH_RISK:
        sys.exit(1)


if __name__ == "__main__":
    main()
