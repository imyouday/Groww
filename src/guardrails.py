"""Stage 7 — guardrails: the safety and trust boundary (architecture.md §13, §14.3).

Every `DraftAnswer` passes through `validate` before `build_answer` turns one into an `Answer`, and
`route` builds the answers for every question that never reaches a generator at all. V4 is the check
the design rests on: every number in the output must appear verbatim in the assembled context, so no
figure can reach a user that is not present, digit for digit, in a cited chunk.

This module is a policy stage and imports no other policy stage. It cannot import `src.generation`,
because the extractive fallback is what the caller reaches for when validation fails and a cycle
would make that unreachable, and it cannot import `src.retrieval`, because then the grounding gate's
decision would depend on the validator. The abbreviation-aware sentence splitter and the on-topic
vocabulary rule are therefore re-derived here from `config.yaml` and from the `AssembledContext`
dataclass rather than borrowed from the stage that happens to also need them.
"""

from __future__ import annotations

import re
from typing import Any

from src import templates
from src.config import Settings, load_settings
from src.models import (
    Answer,
    AssembledContext,
    DraftAnswer,
    FactFamily,
    GateResult,
    Intent,
)
from src.registry import Registry, load_registry

MIN_ON_TOPIC_WORDS = 5
_PLACEHOLDER = "\x00"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATIONS: tuple[str, ...] = (
    "e.g.",
    "i.e.",
    "vs.",
    "approx.",
    "etc.",
    "w.e.f.",
    "p.a.",
    "a.m.",
    "p.m.",
    "Dr.",
    "Mr.",
    "Mrs.",
    "Ms.",
    "No.",
    "Min.",
    "Max.",
    "Rs.",
    "Cr.",
    "L.",
    "Q.",
)
# A number is a digit run with an optional currency prefix and an optional unit. The unit group is
# optional on purpose: "80" in "80C" and "1" in "1 year" are both claims about a value, and a
# validator that only looked at "%" would let a lock-in period through unchecked.
_NUMBER = (
    r"(?:rs\.?|inr|₹)?\s*\d[\d,]*(?:\.\d+)?"
    r"\s*(?:%|percent|bps|basis points|years?|yrs?|months?|days?|weeks?)?"
)
_NUMBER_RE = re.compile(_NUMBER, re.IGNORECASE)
_URL_RE = re.compile(r"(?:https?://|www\.|\b\w+\.(?:com|in|org|net)\b)", re.IGNORECASE)
_ATTRIBUTION_RE = re.compile(
    r"\b(?:according to|as per|as stated in|as mentioned in|quoted from|sourced from|per the website)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_VOCABULARY_MIN_LEN = 4


def split_sentences(text: str) -> list[str]:
    """Split text into sentences without breaking on abbreviations, initials, or decimals."""
    masked = text
    for abbreviation in _ABBREVIATIONS:
        masked = masked.replace(abbreviation, abbreviation.replace(".", _PLACEHOLDER))
    masked = re.sub(r"(?<=[A-Z])\.(?=\s|$)", _PLACEHOLDER, masked)
    sentences: list[str] = []
    for part in _SENTENCE_SPLIT.split(masked):
        cleaned = part.replace(_PLACEHOLDER, ".").strip()
        if cleaned:
            sentences.append(cleaned)
    return sentences


def _collapse(text: str) -> str:
    """Return text with runs of whitespace reduced to one space, for verbatim containment tests."""
    return re.sub(r"\s+", " ", text).strip()


def _context_text(context: AssembledContext | str | None) -> str:
    """Return the verbatim text a draft's claims are checked against."""
    if context is None:
        return ""
    if isinstance(context, str):
        return context
    if context.context_text:
        return context.context_text
    return "\n".join(item.chunk.text for item in context.chunks)


def V1_sentinels(draft: DraftAnswer) -> str | None:
    """Return "refusal" or "not_in_corpus" when the draft carries a sentinel, else None (V1)."""
    for sentinel in draft.sentinels:
        if sentinel == "REFUSE":
            return "refusal"
        if sentinel == "NOT_IN_CORPUS":
            return "not_in_corpus"
    return None


def V2_length(text: str, max_sentences: int = 3) -> bool:
    """Report whether the text is at most `max_sentences` sentences long (V2, constraint C4)."""
    return len(split_sentences(text)) <= max_sentences


def _context_vocabulary(context: AssembledContext | str | None) -> set[str]:
    """Return the content words of the retrieved text, for the on-topic overlap test."""
    return {
        word.lower()
        for word in _WORD_RE.findall(_context_text(context))
        if len(word) >= _VOCABULARY_MIN_LEN
    }


def V3_on_topic(
    text: str,
    context: AssembledContext | str | None,
    settings: Settings | None = None,
) -> bool:
    """Report whether the draft is long enough and shares vocabulary with the retrieved text (V3)."""
    cfg = settings or load_settings()
    if len(_WORD_RE.findall(text)) < MIN_ON_TOPIC_WORDS:
        return False
    words = {word.lower() for word in _WORD_RE.findall(text)}
    terms = {
        term.lower()
        for term in cfg.retrieval.fact_terms.get(_family_of(context), ())
    }
    if words & terms:
        return True
    return bool(words & _context_vocabulary(context))


def _family_of(context: AssembledContext | str | None) -> str:
    if isinstance(context, AssembledContext):
        return context.fact_family.value
    return FactFamily.OTHER.value


def numeric_tokens(text: str) -> list[str]:
    """Return every number-with-unit token in the text, in order and without duplicates."""
    seen: dict[str, None] = {}
    for match in _NUMBER_RE.finditer(text):
        token = _collapse(match.group(0))
        if token and any(character.isdigit() for character in token):
            seen.setdefault(token, None)
    return list(seen)


def V4_numeric_grounding(
    text: str, context: AssembledContext | str | None
) -> tuple[bool, list[str]]:
    """Require every number in the draft to appear verbatim in the context (V4, constraint C3).

    Containment is over the digit string rather than over a parsed value on purpose: a parsed
    comparison would accept 0.450 for 0.45, and rounding a fee is exactly the failure this check
    exists to catch. The returned tokens are digits, never personal data, so they are safe to log.
    """
    haystack = _collapse(_context_text(context))
    ungrounded = [token for token in numeric_tokens(text) if token not in haystack]
    return (not ungrounded, ungrounded)


def V5_banned_terms(text: str, banned_terms: tuple[str, ...] | list[str]) -> list[str]:
    """Return the advice or performance terms the text uses (V5, constraints C3 and D2)."""
    hits: list[str] = []
    for term in banned_terms:
        if not term:
            continue
        if re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text.lower()):
            hits.append(term)
    return hits


def V6_no_urls(text: str) -> bool:
    """Report whether the draft is free of URLs and external attributions (V6, constraint C5)."""
    return _URL_RE.search(text) is None and _ATTRIBUTION_RE.search(text) is None


def validation_report(
    draft: DraftAnswer,
    context: AssembledContext | str | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run V1 to V6 and return every verdict, in order, for the trace and for the first failure."""
    cfg = settings or load_settings()
    grounded, ungrounded = V4_numeric_grounding(draft.text, context)
    return {
        "v1_sentinel": V1_sentinels(draft),
        "v2_length": V2_length(draft.text, cfg.guardrails.max_sentences),
        "v3_on_topic": bool(draft.text.strip())
        and V3_on_topic(draft.text, context, cfg),
        "v4_numeric": grounded if cfg.guardrails.enforce_numeric_grounding else True,
        "v4_ungrounded": ungrounded,
        "v5_banned": V5_banned_terms(draft.text, cfg.guardrails.banned_terms),
        "v6_no_urls": V6_no_urls(draft.text),
    }


def first_failure(report: dict[str, Any]) -> str:
    """Return the verdict key of the first failing validator, or "passed"."""
    if report["v1_sentinel"] is not None:
        return f"v1_{report['v1_sentinel']}"
    for key in ("v2_length", "v3_on_topic", "v4_numeric", "v6_no_urls"):
        if not report[key]:
            return key
    if report["v5_banned"]:
        return "v5_banned_terms"
    return "passed"


def validate(
    draft: DraftAnswer,
    context: AssembledContext | str | None,
    intent: Intent | None = None,
    settings: Settings | None = None,
) -> tuple[DraftAnswer, str]:
    """Return the draft and the verdict key of the first failing check, in V1 to V6 order."""
    report = validation_report(draft, context, settings)
    return draft, first_failure(report)


def _registry_or_default(registry: Registry | None) -> Registry:
    return registry if registry is not None else load_registry()


def _registered_url(registry: Registry, url: str) -> bool:
    """Report whether a URL is a full-string match to a row of the registry, citable or not."""
    return any(source.url == url for source in registry.sources)


def _scheme_page(registry: Registry, scheme_id: str | None, fallback: str = "") -> str:
    """Return the registered page URL of a scheme, or the caller's fallback when none was resolved."""
    scheme = registry.scheme(scheme_id) if scheme_id else None
    if scheme is None:
        return fallback or registry.education_url
    return scheme.page_url


def _render(template: str, settings: Settings, **values: str) -> str:
    """Fill a copy template, collapsing the empty-link artefacts a missing value would leave."""
    return re.sub(r"\s{2,}", " ", re.sub(r"\s*:\s*\.", ".", template.format(**values))).strip()


def build_answer(
    draft: DraftAnswer,
    context: AssembledContext,
    intent: Intent | None = None,
    registry: Registry | None = None,
    settings: Settings | None = None,
    guardrail: str = "passed",
    report: dict[str, Any] | None = None,
    timings_ms: dict[str, float] | None = None,
    extra_trace: dict[str, Any] | None = None,
) -> Answer:
    """Construct the user-visible Answer: the only place one is ever built (architecture.md §13.5).

    The citation URL is taken from the top chunk and then checked against the registry, never read
    out of the draft, so a model that prints a URL cannot put one in front of a user. When the top
    chunk's URL is not a registered, citable source the citation is None rather than a best guess.
    """
    cfg = settings or load_settings()
    reg = _registry_or_default(registry)
    top = context.chunks[0] if context.chunks else None
    citable = top is not None and reg.is_citation_allowed(top.chunk.url)
    citation_url = top.chunk.url if citable else None
    source_id = top.chunk.source_id if top is not None and reg.source_by_id(top.chunk.source_id) else None
    trace: dict[str, Any] = {
        "stage": "guardrails",
        "kind": "factual",
        "intent": intent.value if intent is not None else "unknown",
        "generator": draft.generator,
        "guardrail": guardrail,
        "validators": report or {},
        "scheme_id": context.scheme_id,
        "fact_family": context.fact_family.value,
        "top_score": round(context.top_score, 4),
        "context_tokens": context.total_tokens,
        "chunks": [item.chunk.chunk_id for item in context.chunks],
        "chunk_sections": [item.chunk.section for item in context.chunks],
        "citation_source_id": source_id,
        "link_type": "citation" if citation_url else "none",
        "timings_ms": timings_ms or {},
    }
    if extra_trace:
        trace = {**extra_trace, **trace}
    return Answer(
        text=draft.text,
        kind="factual",
        citation_url=citation_url,
        citation_source_id=source_id,
        last_updated=top.chunk.fetched_at if top is not None else None,
        generator=draft.generator,
        retrieved=list(context.chunks),
        trace=trace,
    )


def route(
    intent: Intent,
    gate: GateResult | None = None,
    context: AssembledContext | None = None,
    registry: Registry | None = None,
    settings: Settings | None = None,
    scheme_id: str | None = None,
) -> Answer:
    """Build the Answer for every question that must not reach a generator (architecture.md §13.4).

    Refusal precision is a guarantee rather than a score because this function needs no corpus, no
    model and no retrieved text: the caller's only job is to decide it is not a factual question and
    to stop. Each kind gets the copy from config.yaml and one link that is a full-string match to a
    row of data/sources.csv, or no link at all.
    """
    cfg = settings or load_settings()
    reg = _registry_or_default(registry)
    resolved_scheme = scheme_id if scheme_id is not None else (
        context.scheme_id if context is not None else None
    )
    count = len(reg.schemes)
    schemes = ", ".join(reg.scheme_names())
    if intent is Intent.ADVICE_REQUEST:
        kind, text, link = (
            "refusal",
            _render(templates.REFUSAL_MESSAGE, cfg, link=reg.education_url),
            reg.education_url,
        )
    elif intent is Intent.PERFORMANCE_REQUEST:
        link = reg.factsheet_index_url or _scheme_page(reg, resolved_scheme)
        kind, text = "performance_redirect", _render(
            templates.PERFORMANCE_REDIRECT, cfg, link=link
        )
    elif intent is Intent.PII_REQUEST:
        kind, text, link = (
            "pii_refusal",
            _render(templates.PII_REFUSAL, cfg, link=reg.help_url),
            reg.help_url,
        )
    elif intent is Intent.OUT_OF_CORPUS:
        kind, text, link = (
            "out_of_corpus",
            _render(
                templates.OUT_OF_CORPUS_MESSAGE,
                cfg,
                link=reg.education_url,
                scheme_count=count,
                schemes=schemes,
            ),
            reg.education_url,
        )
    elif intent is Intent.SMALLTALK:
        kind = "smalltalk"
        text = _render(templates.SMALLTALK_MESSAGE, cfg, scheme_count=count)
        link = ""
    else:
        link = _scheme_page(reg, resolved_scheme, fallback=reg.help_url)
        kind, text = "not_in_corpus", _render(
            templates.NOT_IN_CORPUS_MESSAGE, cfg, link=link, scheme_count=count
        )
    if link and not _registered_url(reg, link):
        link = ""
    return Answer(
        text=text,
        kind=kind,
        citation_url=link or None,
        citation_source_id=None,
        last_updated=None,
        generator="none",
        retrieved=list(context.chunks) if context is not None else [],
        trace={
            "stage": "guardrails",
            "kind": kind,
            "intent": intent.value,
            "generator": "none",
            "guardrail": "routed",
            "routed": True,
            "scheme_id": resolved_scheme,
            "gate_reason": gate.reason if gate is not None else "",
            "link_type": "redirect" if link else "none",
            "citation_source_id": None,
            "timings_ms": {},
        },
    )
