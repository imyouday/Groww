"""Stage 5 — retrieval: intent, contextualisation, dense search, boost, MMR, gate, assembly.

The seven sub-stages of architecture.md §11.1, in order. The grounding gate (§11.6) is the
enforcement point for D1: nothing downstream can be reached with an ungrounded context, and this
module never imports the generator, so the gate decision is provable without the model.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import replace
from functools import lru_cache

import numpy as np

from src.config import Settings, load_settings
from src.embedding import embed_query
from src.intents import EXPECTED_SECTION, FAMILY_LABELS, IntentResult, classify
from src.models import AssembledContext, FactFamily, GateResult, Intent, ScoredChunk
from src.registry import load_registry
from src.store import query, vectors_for

DEFAULT_SCHEME_LABEL = "HDFC mutual fund"
NON_FACTUAL = (
    Intent.PII_REQUEST,
    Intent.ADVICE_REQUEST,
    Intent.PERFORMANCE_REQUEST,
    Intent.OUT_OF_CORPUS,
    Intent.SMALLTALK,
)


def contextualise_query(query_text: str, scheme_id: str | None, fact_family: FactFamily) -> str:
    """Prefix the query with corpus vocabulary so a short question embeds like a corpus chunk."""
    label = FAMILY_LABELS.get(fact_family, FAMILY_LABELS[FactFamily.OTHER])
    scheme = scheme_name(scheme_id) or DEFAULT_SCHEME_LABEL
    return f"[{scheme}] {label} — {query_text}"


@lru_cache(maxsize=1)
def _scheme_names() -> dict[str, str]:
    return {scheme.scheme_id: scheme.scheme_name for scheme in load_registry().schemes}


def scheme_name(scheme_id: str | None) -> str | None:
    """Return the display name for a scheme id, or None when the id is unknown."""
    return _scheme_names().get(scheme_id) if scheme_id else None


def _fact_terms(fact_family: FactFamily, settings: Settings) -> tuple[str, ...]:
    return tuple(term.lower() for term in settings.retrieval.fact_terms.get(fact_family.value, ()))


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    """Return the distinct terms evidenced in text, in reading order, without double counting.

    Two separate over-counts are prevented here, because architecture.md §11.4 pays for
    *distinct* fact terms only. First, "exit load" contains the word "load", so a naive substring
    test would credit one chunk with two terms. Second, a term repeated three times in one chunk
    is still one term, and would otherwise collect the additional-term bonus twice — a measured
    bug, not a hypothetical one: the S3 "About" chunk says "tax saver" three times and was being
    paid 0.15 for a family it does not otherwise evidence. A term therefore counts once, and a
    term whose span is already claimed by a longer term never counts at all. The survivors are
    returned in the order they appear, because the sources panel shows them in that order.
    """
    haystack = text.lower()
    claimed: list[tuple[int, int]] = []
    seen: set[str] = set()
    matched: list[tuple[int, str]] = []
    for term in sorted(terms, key=len, reverse=True):
        if term in seen:
            continue
        for hit in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", haystack):
            start, end = hit.span()
            if any(start < seen_end and seen_start < end for seen_start, seen_end in claimed):
                continue
            claimed.append((start, end))
            seen.add(term)
            matched.append((start, term))
            break
    return [term for _start, term in sorted(matched)]


def boost(
    candidates: list[ScoredChunk],
    query_text: str,
    fact_family: FactFamily,
    scheme_id: str | None,
    settings: Settings | None = None,
) -> list[ScoredChunk]:
    """Add the evidence-based lexical and metadata boost of architecture.md §11.4.

    `query_text` is part of the stage interface but not of the formula: the terms counted here
    come from the fact family, which was itself derived from this query, so the raw string is not
    re-inspected. Scoring the query text as well would let one phrase earn a boost twice.
    """
    cfg = settings or load_settings()
    weights = cfg.retrieval.boosts
    terms = _fact_terms(fact_family, cfg)
    expected_section = EXPECTED_SECTION.get(fact_family)
    scored: list[ScoredChunk] = []
    for candidate in candidates:
        chunk = candidate.chunk
        matched = _matched_terms(chunk.text, terms)
        section_bonus = (
            weights.section_type_match
            if expected_section is not None and chunk.section_type == expected_section
            else 0.0
        )
        scheme_bonus = weights.scheme_match if scheme_id and chunk.scheme_id == scheme_id else 0.0
        term_bonus = 0.0
        if matched:
            term_bonus = weights.fact_term + weights.additional_fact_term * (len(matched) - 1)
        total = term_bonus + section_bonus + scheme_bonus
        scored.append(
            replace(
                candidate,
                keyword_boost=round(total, 6),
                final=round(candidate.dense + total, 6),
                matched_terms=matched,
            )
        )
    return sorted(scored, key=lambda item: item.final, reverse=True)


def _text_overlap(left: str, right: str) -> float:
    """Return Jaccard token overlap, the redundancy proxy used when vectors are unavailable."""
    left_tokens = set(re.findall(r"\w+", left.lower()))
    right_tokens = set(re.findall(r"\w+", right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def mmr(
    candidates: list[ScoredChunk],
    top_n: int,
    lambda_: float,
    vectors: dict[str, np.ndarray] | None = None,
) -> list[ScoredChunk]:
    """Select `top_n` chunks trading relevance against redundancy (architecture.md §11.5).

    Without `vectors` the redundancy term falls back to token overlap, which keeps the function
    usable — and testable — in isolation. `retrieve()` always passes the stored embeddings, so the
    deployed path measures redundancy with the same cosine geometry the dense stage used.
    """
    pool = sorted(candidates, key=lambda item: item.final, reverse=True)
    selected: list[ScoredChunk] = []
    while pool and len(selected) < top_n:
        best_index, best_value = 0, float("-inf")
        for index, candidate in enumerate(pool):
            if not selected:
                value = candidate.final
            else:
                redundancy = max(_similarity(candidate, other, vectors) for other in selected)
                value = lambda_ * candidate.final - (1.0 - lambda_) * redundancy
            if value > best_value:
                best_index, best_value = index, value
        selected.append(replace(pool.pop(best_index), mmr_selected=True))
    return selected


def _similarity(
    candidate: ScoredChunk, other: ScoredChunk, vectors: dict[str, np.ndarray] | None
) -> float:
    if vectors is not None:
        left = vectors.get(candidate.chunk.chunk_id)
        right = vectors.get(other.chunk.chunk_id)
        if left is not None and right is not None:
            return min(1.0, max(0.0, float(np.asarray(left) @ np.asarray(right))))
    return _text_overlap(candidate.chunk.text, other.chunk.text)


def grounding_gate(
    candidates: list[ScoredChunk],
    fact_family: FactFamily,
    needs_evidence: bool,
    settings: Settings | None = None,
) -> GateResult:
    """Apply the score threshold and the term-coverage AND-condition (architecture.md §11.6)."""
    cfg = settings or load_settings()
    threshold = cfg.retrieval.gate_threshold
    reason = "score and term coverage satisfied"
    if not candidates:
        return GateResult(False, 0.0, threshold, "no candidates returned", [])
    top_score = candidates[0].final
    if needs_evidence:
        threshold += cfg.retrieval.unclassified_gate_margin
        reason = f"unclassified query, tau raised to {threshold:.2f}"
    if top_score < threshold:
        return GateResult(
            False, top_score, threshold, f"top score {top_score:.3f} below tau {threshold:.2f}", []
        )
    terms = _fact_terms(fact_family, cfg)
    covered = _matched_terms(" ".join(item.chunk.text for item in candidates), terms)
    if cfg.retrieval.require_term_coverage and terms and not covered:
        return GateResult(
            False, top_score, threshold, f"no {fact_family.value} term in the selected chunks", []
        )
    return GateResult(True, top_score, threshold, reason, covered)


def _normalise(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _within_budget(
    context_chunks: list[ScoredChunk], settings: Settings
) -> list[ScoredChunk]:
    """Drop duplicate texts and anything past the token budget, keeping the top-1 chunk whole."""
    budget = settings.retrieval.context_token_budget
    included: list[ScoredChunk] = []
    seen: set[str] = set()
    spent = 0
    for candidate in context_chunks:
        chunk = candidate.chunk
        key = _normalise(chunk.text)
        if key in seen:
            continue
        if included and spent + chunk.token_count > budget:
            continue
        seen.add(key)
        spent += chunk.token_count
        included.append(candidate)
    return included


def assemble(context_chunks: list[ScoredChunk], settings: Settings | None = None) -> str:
    """Render numbered, labelled, verbatim chunks within the context token budget (§11.7).

    Ordering is descending `final`, duplicates are dropped on normalised text, and chunks are
    appended whole until the next would exceed the budget — a half number is how invented digits
    appear. The budget is accounted in stored chunk tokens; the short numbered labels sit outside
    that count, inside the headroom the budget leaves.
    """
    included = _within_budget(context_chunks, settings or load_settings())
    return "\n".join(
        f"[{index}] {item.chunk.scheme_name} | {item.chunk.section} | source: {item.chunk.url}\n"
        f"    {item.chunk.text}"
        for index, item in enumerate(included, start=1)
    )


def _run(
    query_text: str, cfg: Settings
) -> tuple[AssembledContext | None, GateResult, dict]:
    """Execute §11.1's seven sub-stages once, returning context, verdict and trace."""
    started = time.perf_counter()
    intent_result = classify(query_text, cfg)
    trace: dict = {
        "query": query_text,
        "intent": intent_result.intent.value,
        "matched_rule": intent_result.matched_rule,
        "scheme_id": intent_result.scheme_id,
        "scheme_name": scheme_name(intent_result.scheme_id),
        "fact_family": intent_result.fact_family.value,
        "needs_evidence": intent_result.needs_evidence,
        "tau": cfg.retrieval.gate_threshold,
        "embedded_query": "",
        "embed_ms": 0.0,
        "dense_ms": 0.0,
        "candidates": [],
        "mmr_picks": [],
        "context": "",
        "context_tokens": 0,
    }
    if intent_result.intent in NON_FACTUAL:
        gate = GateResult(
            False,
            0.0,
            cfg.retrieval.gate_threshold,
            f"intent {intent_result.intent.value} refused fast at rule "
            f"{intent_result.matched_rule}; no retrieval performed",
            [],
        )
        trace["gate"] = _gate_trace(gate)
        trace["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return None, gate, trace

    embedded = contextualise_query(query_text, intent_result.scheme_id, intent_result.fact_family)
    trace["embedded_query"] = embedded
    clock = time.perf_counter()
    vector = embed_query(embedded, cfg)
    trace["embed_ms"] = round((time.perf_counter() - clock) * 1000, 2)

    clock = time.perf_counter()
    candidates = query(
        vector,
        cfg.retrieval.dense_k,
        scheme_id=intent_result.scheme_id if cfg.retrieval.scheme_filter else None,
        settings=cfg,
    )
    trace["dense_ms"] = round((time.perf_counter() - clock) * 1000, 2)

    boosted = boost(candidates, query_text, intent_result.fact_family, intent_result.scheme_id, cfg)
    trace["candidates"] = [
        {
            "chunk_id": item.chunk.chunk_id,
            "scheme": item.chunk.scheme_id,
            "section": item.chunk.section,
            "section_type": item.chunk.section_type.value,
            "tokens": item.chunk.token_count,
            "dense": round(item.dense, 4),
            "boost": round(item.keyword_boost, 4),
            "final": round(item.final, 4),
            "terms": item.matched_terms,
        }
        for item in boosted
    ]

    vectors = vectors_for([item.chunk.chunk_id for item in boosted], cfg)
    selected = mmr(boosted, cfg.retrieval.top_n, cfg.retrieval.mmr_lambda, vectors)
    trace["mmr_picks"] = [item.chunk.chunk_id for item in selected]

    gate = grounding_gate(selected, intent_result.fact_family, intent_result.needs_evidence, cfg)
    trace["gate"] = _gate_trace(gate)
    included = _within_budget(selected, cfg)
    trace["context"] = assemble(selected, cfg)
    trace["context_tokens"] = sum(item.chunk.token_count for item in included)
    trace["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if not gate.passed:
        return None, gate, trace
    context = AssembledContext(
        query=query_text,
        scheme_id=intent_result.scheme_id,
        fact_family=intent_result.fact_family,
        chunks=included,
        total_tokens=trace["context_tokens"],
        top_score=selected[0].final if selected else 0.0,
    )
    return context, gate, trace


def _gate_trace(gate: GateResult) -> dict:
    return {
        "passed": gate.passed,
        "reason": gate.reason,
        "threshold": gate.threshold,
        "top_score": round(gate.top_score, 4),
        "covered_terms": gate.covered_terms,
    }


def retrieve(query_text: str, settings: Settings | None = None) -> AssembledContext | GateResult:
    """Answer with a grounded context, or with the gate's refusal when the gate does not pass."""
    context, gate, _trace = _run(query_text, settings or load_settings())
    return context if context is not None else gate


def retrieve_with_debug(
    query_text: str, settings: Settings | None = None
) -> tuple[AssembledContext | None, dict]:
    """Return the assembled context (or None) with the full trace of architecture.md §19.1."""
    context, _gate, trace = _run(query_text, settings or load_settings())
    return context, trace


def _format_trace(trace: dict) -> str:
    lines = [
        f"query            {trace['query']}",
        f"intent           {trace['intent']}  (rule {trace['matched_rule']})",
        f"scheme           {trace['scheme_id'] or '-'} {trace['scheme_name'] or ''}".rstrip(),
        f"fact family      {trace['fact_family']}   needs_evidence={trace['needs_evidence']}",
    ]
    if trace["embedded_query"]:
        lines.append(f"embedded query   {trace['embedded_query']}")
    if trace["candidates"]:
        lines += [
            "",
            f"candidates ({len(trace['candidates'])}):",
            f"  {'chunk_id':22} {'sch':4} {'section':18} {'dense':>7} {'boost':>6} {'final':>7}  terms",
        ]
        for item in trace["candidates"]:
            lines.append(
                f"  {item['chunk_id']:22} {item['scheme']:4} {item['section'][:18]:18} "
                f"{item['dense']:7.4f} {item['boost']:6.3f} {item['final']:7.4f}  "
                f"{','.join(item['terms'])}"
            )
        picks = ", ".join(trace["mmr_picks"]) or "none"
        lines += ["", f"MMR picks ({len(trace['mmr_picks'])}): {picks}"]
    gate = trace["gate"]
    lines += [
        "",
        f"gate             {'PASS' if gate['passed'] else 'FAIL'}  "
        f"tau={gate['threshold']:.2f} top_score={gate['top_score']:.4f}",
        f"                 {gate['reason']}",
    ]
    if gate["covered_terms"]:
        lines.append(f"                 covered terms: {', '.join(gate['covered_terms'])}")
    if trace["context"]:
        lines += [
            "",
            f"assembled context ({trace['context_tokens']} chunk tokens):",
            trace["context"],
        ]
    lines += [
        "",
        f"timing           embed {trace['embed_ms']} ms, dense {trace['dense_ms']} ms, "
        f"total {trace['total_ms']} ms",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the full retrieval trace for one query (NFR-9, architecture.md §19.1)."""
    parser = argparse.ArgumentParser(
        prog="python -m src.retrieval",
        description="Trace intent, retrieval, MMR and the grounding gate for one query.",
    )
    parser.add_argument("--query", required=True, help="the user question to trace")
    args = parser.parse_args(argv)
    _context, trace = retrieve_with_debug(args.query)
    print(_format_trace(trace))
    return 0


if __name__ == "__main__":
    sys.exit(main())
