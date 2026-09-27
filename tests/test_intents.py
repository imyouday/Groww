"""Intent resolution tests: the ordered rule table is the safety mechanism, so it is pinned."""

from __future__ import annotations

import pytest

from src.intents import (
    FAMILY_LABELS,
    classify,
    has_pii,
    resolve_fact_family,
    resolve_scheme,
)
from src.models import FactFamily, Intent

OUT_OF_SCOPE_PROBES = [
    ("Should I buy the ELSS?", Intent.ADVICE_REQUEST),
    ("Is HDFC ELSS a good ELSS for me?", Intent.ADVICE_REQUEST),
    ("I hold X and Y, rebalance for me?", Intent.ADVICE_REQUEST),
    ("Which of these gave the best 1-year return?", Intent.PERFORMANCE_REQUEST),
    ("What is the expense ratio of Parag Parflex?", Intent.OUT_OF_CORPUS),
    ("my pan is ABCDE1234F", Intent.PII_REQUEST),
    ("aadhaar 1234 5678 9012", Intent.PII_REQUEST),
    ("hi there", Intent.SMALLTALK),
]

FACTUAL_PROBES = [
    ("What is the expense ratio of the HDFC Large Cap fund?", FactFamily.EXPENSE_RATIO),
    ("Is there a lock-in on the ELSS tax saver fund?", FactFamily.LOCK_IN),
    ("What is the minimum sip for small cap?", FactFamily.MIN_SIP),
    ("what is the exit load on the flexi cap fund?", FactFamily.EXIT_LOAD),
    ("What is the benchmark of balanced advantage fund?", FactFamily.BENCHMARK),
    ("how do I download the capital gains statement?", FactFamily.STATEMENTS),
]


@pytest.mark.parametrize(("query", "expected"), OUT_OF_SCOPE_PROBES)
def test_out_of_scope_probes_route_to_their_intent(query: str, expected: Intent) -> None:
    assert classify(query).intent is expected


@pytest.mark.parametrize(("query", "expected"), FACTUAL_PROBES)
def test_factual_questions_carry_the_right_family(query: str, expected: FactFamily) -> None:
    result = classify(query)
    assert result.intent is Intent.FACTUAL_FACT
    assert result.fact_family is expected
    assert result.needs_evidence is False


def test_advice_beats_a_fact_term_when_both_match() -> None:
    """architecture.md §11.2: rule 2 wins for routing, so the fact is never routed as advice-free."""
    result = classify("What is the exit load on the fund I should buy?")
    assert result.intent is Intent.ADVICE_REQUEST
    assert result.matched_rule == "r2_advice"
    assert result.fact_family is FactFamily.EXIT_LOAD


def test_unclassified_query_is_factual_but_needs_evidence() -> None:
    result = classify("tell me something about this fund category")
    assert result.intent is Intent.FACTUAL_FACT
    assert result.needs_evidence is True
    assert result.matched_rule == "r7_needs_evidence"
    assert result.fact_family is FactFamily.OTHER


def test_scheme_alias_resolves_to_the_scheme_id() -> None:
    assert resolve_scheme("expense ratio of the flexi cap fund") == "S2"
    assert resolve_scheme("tell me about elss") == "S3"
    assert resolve_scheme("what about the largecap one") == "S1"
    assert resolve_scheme("balanced advantage") == "S5"
    assert resolve_scheme("something unrelated to schemes") is None


def test_a_longer_alias_containing_a_shorter_one_still_resolves() -> None:
    """The alias scan is longest-first, so a nested alias must not hide behind its own prefix."""
    assert resolve_scheme("hdfc large cap fund") == "S1"
    assert resolve_scheme("large cap") == "S1"
    assert resolve_scheme("elss tax saver fund") == "S3"


def test_out_of_corpus_yields_to_an_in_scope_alias() -> None:
    """PRD §5.3: naming another AMC is only out-of-corpus when no in-scope scheme is named too."""
    assert classify("What is the expense ratio of Parag Parflex?").intent is Intent.OUT_OF_CORPUS
    mixed = classify("compare the expense ratio of the HDFC flexi cap fund with Parag Parflex")
    assert mixed.intent is Intent.FACTUAL_FACT
    assert mixed.scheme_id == "S2"


def test_longest_fact_term_wins_so_exit_load_is_not_just_load() -> None:
    assert resolve_fact_family("what is the exit load") is FactFamily.EXIT_LOAD
    assert resolve_fact_family("is there a load") is FactFamily.EXIT_LOAD
    assert resolve_fact_family("completely unrelated wording") is FactFamily.OTHER


def test_pii_detection_reports_kind_and_position_but_no_value() -> None:
    hits = has_pii("my pan is ABCDE1234F")
    assert hits
    assert all(hit.kind for hit in hits)
    assert "ABCDE1234F" not in str(hits)


def test_factual_query_never_carries_pii_hits() -> None:
    assert has_pii("what is the exit load on the flexi cap fund") == []


def test_every_fact_family_has_a_contextualisation_label() -> None:
    assert set(FAMILY_LABELS) >= set(FactFamily)
    assert all(label for label in FAMILY_LABELS.values())
