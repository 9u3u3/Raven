"""Ask (section 9.2, RAG): answer a question from the user's files, with citations.

The answer streams as NDJSON events:
  {"type": "sources", ...}   the numbered sources, the tier and the reason
  {"type": "token", "t": ...} a piece of the answer
  {"type": "consent", ...}   Ask each time mode: the exact redacted payload, nothing sent yet
  {"type": "done", ...}      timings
  {"type": "error", ...}
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator

from ..privacy import gate
from ..router import cloud, local
from ..router import providers as registry
from ..router import router as core
from .retrieve import Source, content_words, retrieve, window

NOT_FOUND = "I did not find this in your files."
# Section 4.1: large synthesis is heavy. These words ask for many sources at once.
SYNTH = re.compile(r"\b(compare|comparison|summari[sz]e|summary|overview|all|every|across|list|differences?)\b", re.I)
# The local model reads the prompt at a few hundred tokens per second on a laptop GPU, so its
# prompt stays small: the best chunk in full, then the best window of the next ones.
LOCAL_K, LOCAL_BUDGET, LOCAL_WINDOW = 4, 0, 110
CLOUD_K, CLOUD_BUDGET = 10, 6000

SYSTEM = (
    "You answer questions about the user's own files. Use only the numbered sources. "
    "After each fact, cite its source number in square brackets, such as [1] or [2][3]. "
    "If the sources do not hold the answer, write only: " + NOT_FOUND + " "
    "Do not guess. Write the final answer only, in short plain sentences: one to four sentences, "
    "or a short list when the question asks for one. Do not describe your steps. "
    "Placeholders such as [PERSON_1] or [ACCOUNT_2] stand for private values. Copy them exactly. Never guess their values."
)
# The local model answers in JSON (see router/local.py). The example shows the form.
LOCAL_SYSTEM = SYSTEM + (
    " Reply as JSON: the answer text, and the numbers of the sources that you used. Example: "
    '{"answer": "The late fee is Rs 500 for each day of delay [1].", "sources": [1]}'
)


def _ev(type_: str, **kw) -> str:
    return json.dumps({"type": type_, **kw}) + "\n"


def _source_block(s: Source, text: str, name: str) -> str:
    where = f"{name}" + (f", page {s.page}" if s.page else "") + (f", section {s.heading}" if s.heading else "")
    if s.at is not None:
        where += " (spoken audio transcript)"
    return f"[{s.n}] {where}\n{' '.join(text.split())}"


def build_prompt(question: str, sources: list[Source], history: list[dict], red: gate.Redactor | None,
                 short: bool = False) -> str:
    parts = []
    words = content_words(question)
    for s in sources:
        text, name = s.text, s.name
        if short and s.n > 1:
            text = window(s.hit, words, LOCAL_WINDOW)
        if red:
            # Step 4: a lone chunk uses its cached spans from index time. A chunk with its parent context runs the full gate.
            if text == s.hit:
                spans, l2_done = gate.cached_spans(s.chunk_id)
                text = red.redact(text, [x for x in spans if x.end <= len(text)], cached_layer2=l2_done)
            else:
                text = red.redact(text)
            name = red.redact(name, name=True)
        parts.append(_source_block(s, text, name))
    prev = ""
    for h in history[-2:]:
        q, a = h.get("q", ""), h.get("a", "")
        if red:
            q, a = red.redact(q), red.redact(a)
        prev += f"Earlier question: {q}\nEarlier answer: {a}\n\n"
    q = red.redact(question) if red else question
    return "Sources:\n\n" + "\n\n".join(parts) + "\n\n" + prev + f"Question: {q}"


class _Restorer:
    """Put the original values back while the answer streams. A placeholder can arrive in pieces,
    so text after an open "[" waits until its "]" arrives."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.buf = ""

    def feed(self, piece: str) -> str:
        self.buf += piece
        i = self.buf.rfind("[")
        if i >= 0 and "]" not in self.buf[i:] and len(self.buf) - i < 24:
            out, self.buf = self.buf[:i], self.buf[i:]
        else:
            out, self.buf = self.buf, ""
        return gate.restore(out, self.mapping)

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return gate.restore(out, self.mapping)


def extractive(question: str, sources: list, per_source: int = 1, max_sources: int = 3) -> str:
    """The sentences that share the most words with the question, one per source, each with its [n]."""
    import math

    q = set(content_words(question))
    def sentences(text: str) -> list[str]:
        # A line break inside a paragraph is not the end of a sentence ("a late fee of\nRs. 500").
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n\n+", text) if len(x.strip()) > 20]

    split = {s.n: sentences(s.hit or s.text or "") for s in sources[:max_sources]}
    # A word in few sentences says more than a word in many (IDF over the sentences of these sources).
    bags = [set(content_words(x)) for sents in split.values() for x in sents]
    n = max(1, len(bags))
    idf = {w: math.log(1 + n / (1 + sum(w in b for b in bags))) for w in q}
    out = []
    for s in sources[:max_sources]:
        sents = split.get(s.n) or []
        if not sents:
            continue
        best = sorted(sents, key=lambda x: -sum(idf[w] for w in q & set(content_words(x))))[:per_source]
        if not q & set(content_words(" ".join(best))):
            continue
        out.append(" ".join(best) + f" [{s.n}]")
    return "\n\n".join(out) or NOT_FOUND


def _retrieval_query(question: str, history: list[dict]) -> str:
    """A short follow-up ("and the deposit?") borrows the words of the earlier question."""
    if history and len(content_words(question)) < 4:
        return f"{history[-1].get('q', '')} {question}"
    return question


def ask(question: str, think_harder: bool = False, consent: bool = False, history: list[dict] | None = None,
        local_only: bool = False) -> Iterator[str]:
    """`local_only`: never the cloud tier (the MCP flow, where the client is the cloud model)."""
    from ..index.indexer import indexer

    indexer.interactive.set()
    try:
        yield from _ask(question.strip(), think_harder, consent, history or [], local_only)
    except (local.LocalError, cloud.CloudError) as e:
        yield _ev("error", message=str(e))
    finally:
        indexer.interactive.clear()


def _ask(question: str, think: bool, consent: bool, history: list[dict], local_only: bool = False) -> Iterator[str]:
    t0 = time.perf_counter()
    mode = "local" if local_only else core.ai_mode()
    providers = core.provider_order("ask")
    want_cloud = think or bool(SYNTH.search(question))
    k, budget = (CLOUD_K, CLOUD_BUDGET) if want_cloud else (LOCAL_K, LOCAL_BUDGET)
    if want_cloud and providers and mode != "local":
        gate.layer2.warm_in_background()  # PLAN4 B7: the model loads while the search runs
    sources, info = retrieve(_retrieval_query(question, history), k=k, budget=budget)
    retrieve_ms = round((time.perf_counter() - t0) * 1000)

    if not sources or not info["found"]:
        yield _ev("sources", sources=[], tier="none", provider=None, model=None, reason="no file matches the question well",
                  retrieval=info)
        yield _ev("token", t=NOT_FOUND)
        yield _ev("done", not_found=True, ms=round((time.perf_counter() - t0) * 1000), first_ms=retrieve_ms, retrieve_ms=retrieve_ms)
        return

    files = len({s.file_id for s in sources})
    heavy = think or (want_cloud and files >= 4)
    tier, reason = "T3", ""
    from ..fs.props import policy_for
    from ..privacy.scope import is_media

    sources = [s for s in sources if not policy_for(s.path, "private")]  # an index from before the rule
    if not heavy:
        tier, reason = "T2", "a short question over a few sources runs on the local model"
    elif any(policy_for(s.path, "no_cloud") for s in sources):
        tier, reason = "T2", "a source is in a folder that never goes to the cloud"
    elif any(is_media(s.path) for s in sources):
        # PLAN4 7.6: text from a photo, a video or an audio file (OCR, a transcript) never goes to the cloud.
        tier, reason = "T2", "a source is a photo, video or audio file, so the local model answers"
    elif mode == "local":
        tier, reason = "T2", "Local only mode"
    elif not providers:
        tier, reason = "T2", "no cloud provider is set up" if cloud.online() else "this computer is offline, so the local model answers"

    if tier == "T3":
        red = gate.Redactor()
        payload = build_prompt(question, sources, history, red)
        try:
            payload = gate.seal(payload, red)
            why = ""
        except gate.Blocked as e:
            why = str(e)
        if why:
            tier, reason = "T2", f"the redaction gate blocked the request: {why}"
        elif mode == "ask" and not consent:
            yield _ev("sources", sources=[s.public() for s in sources], tier="T3", provider=providers[0], model=None,
                      reason="", retrieval=info)
            yield _ev("consent", payload=SYSTEM + "\n\n" + payload, provider=providers[0], redacted=len(red.mapping),
                      entities=red.entities)
            return
        else:
            yield _ev("sources", sources=[s.public() for s in sources], tier="T3", provider=providers[0], model=None,
                      reason="", retrieval=info, redacted=len(red.mapping))
            errors = []
            for prov in providers:
                lid = gate.ledger_write("ask", "T3", prov, SYSTEM + "\n\n" + payload, len(red.mapping))
                rest = _Restorer(red.mapping)
                first_ms = None
                sent_any = False
                try:
                    gen = registry.stream(prov, SYSTEM, payload)
                    model = next(gen)
                    for piece in gen:
                        out = rest.feed(piece)
                        if out:
                            if first_ms is None:
                                first_ms = round((time.perf_counter() - t0) * 1000)
                            sent_any = True
                            yield _ev("token", t=out)
                    tail = rest.flush()
                    if tail:
                        yield _ev("token", t=tail)
                except cloud.CloudError as e:
                    errors.append(f"{prov}: {e}")
                    gate.ledger_mark_failed(lid, "ask")
                    if sent_any:
                        yield _ev("error", message=str(e))
                        return
                    continue
                yield _ev("done", tier="T3", provider=prov, model=model, redacted=len(red.mapping), entities=red.entities,
                          ledger_id=lid, ms=round((time.perf_counter() - t0) * 1000), first_ms=first_ms,
                          retrieve_ms=retrieve_ms, fallback=None)
                return
            tier, reason = "T2", "the cloud failed (" + "; ".join(errors) + ")"
        if tier == "T2":
            # The local model works on fewer sources, to keep the first token fast.
            sources = sources[:LOCAL_K]
            for s in sources:
                s.text = s.hit

    # T2: the real text stays on this machine. Nothing goes to the ledger.
    if not local.available():
        from .. import lite

        if lite.enabled("llm"):
            why = local.unavailable_reason()
            raise local.LocalError(f"{why} The cloud did not answer: {reason}." if heavy and reason else why)
        # Lite mode (PLAN2 P2-6): no model writes an answer. Show the best passages, each with its citation.
        yield _ev("sources", sources=[s.public() for s in sources], tier="T1", provider="extract", model=None,
                  reason="lite mode: the best passages, no generated answer", retrieval=info)
        text = extractive(question, sources)
        yield _ev("token", t=text)
        yield _ev("done", tier="T1", provider="extract", model=None, redacted=0, ms=round((time.perf_counter() - t0) * 1000),
                  first_ms=retrieve_ms, retrieve_ms=retrieve_ms, fallback=None)
        return
    yield _ev("sources", sources=[s.public() for s in sources], tier="T2", provider="ollama", model=local.LLM,
              reason=reason, retrieval=info)
    prompt = build_prompt(question, sources, history, None, short=True)
    first_ms = None
    stats: dict = {}
    text = ""
    for piece in local.stream(LOCAL_SYSTEM, [{"role": "user", "content": prompt}]):
        if isinstance(piece, dict):
            stats = piece
            continue
        if first_ms is None:
            first_ms = round((time.perf_counter() - t0) * 1000)
        text += piece
        yield _ev("token", t=piece)
    # A small model sometimes lists its sources but forgets the [n] marks in the text.
    used = [n for n in stats.get("sources", []) if 1 <= n <= len(sources)]
    if used and not re.search(r"\[\d+\]", text) and NOT_FOUND not in text:
        yield _ev("token", t=" " + "".join(f"[{n}]" for n in dict.fromkeys(used)))
    yield _ev("done", tier="T2", provider="ollama", model=local.LLM, redacted=0, ms=round((time.perf_counter() - t0) * 1000),
              first_ms=first_ms, retrieve_ms=retrieve_ms, fallback=reason if heavy or think else None,
              prompt_tokens=stats.get("prompt_eval_count"),
              prompt_ms=round((stats.get("prompt_eval_duration") or 0) / 1e6))
