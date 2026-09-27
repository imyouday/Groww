"""End-to-end tests over the real corpus: golden questions, refusal probes, degradation paths.

Every assertion in this module is made against the artifacts the demo actually ships, not against
mocks, because the properties being tested here are exactly the ones a mock would hide. The golden
set is the question set from `eval/golden_questions.csv`, whose `must_include` values were read out
of `data/chunks.jsonl` rather than typed from memory, so a corpus change that invalidates a fact
fails the suite instead of quietly invalidating the demo.

The suite is deterministic by construction: it pins the provider to `extractive`, which is the same
code path the demo uses when no API key is configured. The LLM path is exercised separately in
`tests/test_generation.py`, where a live key is optional.
"""

from __future__ import annotations

import csv
import re
import statistics
import time
from dataclasses import replace
from pathlib import Path

import pytest

from src import guardrails
from src.config import LlmEnv, load_settings
from src.generation import GenerationError
from src.models import DraftAnswer, IndexNotBuiltError, Intent
from src.pipeline import answer
from src.registry import load_registry

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_CSV = ROOT / "eval" / "golden_questions.csv"
PROBES_CSV = ROOT / "eval" / "out_of_scope_probes.csv"
PROVIDER = "extractive"


def _rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV asset into a list of dicts."""
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


GOLDEN = _rows(GOLDEN_CSV)
PROBES = _rows(PROBES_CSV)
GOLDEN_IDS = [row["id"] for row in GOLDEN]
PROBE_IDS = [row["id"] for row in PROBES]


@pytest.fixture(scope="session")
def registry() -> object:
    """Load the source registry once for the whole session."""
    return load_registry(load_settings())


def _allowed_urls(registry: object) -> set[str]:
    """Return every URL the registry permits citing."""
    urls = {source.url for source in registry.sources}
    urls.add(registry.education_url)
    urls.add(registry.help_url)
    return {u for u in urls if u}


def _body_urls(text: str) -> list[str]:
    """Return every URL that appears in an answer body, punctuation trimmed."""
    return [url.rstrip(".,;") for url in re.findall(r"https?://[^\s)\]]+", text)]


def _page_url(registry: object, scheme_name: str) -> str:
    """Return the registered page URL for a scheme name, so tests never hardcode a URL."""
    for scheme in registry.schemes:
        if scheme.scheme_name == scheme_name:
            return scheme.page_url
    raise AssertionError(f"{scheme_name!r} is not in the registry")


@pytest.fixture(scope="session")
def golden_answers() -> dict[str, object]:
    """Answer every golden question once and reuse the results across assertions."""
    return {row["id"]: answer(row["question"], PROVIDER) for row in GOLDEN}


def test_the_golden_set_covers_every_answerable_family_present_in_the_corpus() -> None:
    """The set spans the fact families the corpus can actually support, across all five schemes."""
    assert len(GOLDEN) >= 20
    families = {row["fact_family"] for row in GOLDEN}
    assert families == {"expense_ratio", "exit_load", "min_sip", "risk_rating", "benchmark"}
    assert len({row["expected_scheme"] for row in GOLDEN}) == 5


@pytest.mark.parametrize("row", GOLDEN, ids=GOLDEN_IDS)
def test_a_golden_question_is_answered_from_the_corpus(
    row: dict[str, str], golden_answers: dict[str, object]
) -> None:
    """A factual question yields a grounded, cited answer of at most three sentences."""
    result = golden_answers[row["id"]]
    assert result.kind == "factual"
    assert 1 <= len(guardrails.split_sentences(result.text)) <= 3
    assert row["must_include"] in result.text
    assert result.last_updated
    assert result.generator == "extractive"


@pytest.mark.parametrize("row", GOLDEN, ids=GOLDEN_IDS)
def test_a_golden_answer_cites_only_the_expected_registered_page(
    row: dict[str, str], golden_answers: dict[str, object], registry: object
) -> None:
    """Constraint C5: exactly one citation, and it is the scheme page the question is about."""
    result = golden_answers[row["id"]]
    allowed = _allowed_urls(registry)
    assert result.citation_url == row["expected_url"]
    assert result.citation_url == _page_url(registry, row["expected_scheme"])
    assert result.citation_url in allowed
    assert "http" not in result.text


@pytest.mark.parametrize("row", PROBES, ids=PROBE_IDS)
def test_an_out_of_scope_probe_is_refused_without_calling_a_generator(
    row: dict[str, str], monkeypatch: pytest.MonkeyPatch, registry: object
) -> None:
    """PRD §5.3: advice, performance, PII, and foreign-scheme questions never reach generation."""

    def explode(*args: object, **kwargs: object) -> None:
        """Fail loudly if a refusal path ever reaches the generator stage."""
        raise AssertionError(f"{row['id']} must not construct a generator")

    monkeypatch.setattr("src.pipeline.resolve_generator", explode)
    result = answer(row["query"], PROVIDER)
    assert result.kind == row["expected_kind"]
    assert result.text
    assert result.citation_url in _allowed_urls(registry)
    body_urls = _body_urls(result.text)
    assert len(body_urls) <= 1
    assert all(url in _allowed_urls(registry) for url in body_urls)
    assert result.generator == "none"
    if row["expected_link_type"] == "education":
        assert "amfiindia.com" in (result.citation_url or "")
    if row["expected_link_type"] == "help":
        assert "help" in (result.citation_url or "")
    if row["expected_link_type"] == "scheme_page":
        assert "hdfcfund.com" in (result.citation_url or "") or "groww.in" in (
            result.citation_url or ""
        )


def test_an_advice_probe_states_the_facts_only_boundary() -> None:
    """The refusal says what the assistant will not do, not merely that it cannot help."""
    result = answer("Should I buy the ELSS?", PROVIDER)
    assert result.kind == "refusal"
    assert "recommend" in result.text.lower()
    assert result.generator == "none"


def test_a_performance_probe_never_states_a_return_figure() -> None:
    """Constraint C3: the redirect names what is not covered and links out, without any number."""
    result = answer("Which of these gave the best 1-year return?", PROVIDER)
    assert result.kind == "performance_redirect"
    assert "return" in result.text.lower()
    assert not [character for character in result.text if character.isdigit()]
    assert "%" not in result.text


def test_a_pii_probe_echoes_nothing_back(caplog: pytest.LogCaptureFixture) -> None:
    """Constraint C2: the identifier is neither returned nor logged."""
    pan = "ABCDE1234F"
    result = answer(f"My PAN is {pan}, which folio holds my units?", PROVIDER)
    assert result.kind == "pii_refusal"
    assert pan not in result.text
    assert pan not in (result.citation_url or "")
    assert not [record for record in caplog.records if pan in record.getMessage()]


def test_a_question_the_corpus_cannot_answer_says_so_instead_of_guessing() -> None:
    """A fact family the corpus lacks is a refusal with a link, never a plausible invention."""
    result = answer("What is the lock-in period of the HDFC ELSS tax saver fund?", PROVIDER)
    assert result.kind in {"not_in_corpus", "refusal"}
    assert result.citation_url


def test_the_same_question_is_answered_identically_twice() -> None:
    """The demo is reproducible, so a second ask cannot contradict the first."""
    question = "What is the minimum SIP amount for the HDFC ELSS tax saver fund?"
    first = answer(question, PROVIDER)
    second = answer(question, PROVIDER)
    assert first.text == second.text
    assert first.citation_url == second.citation_url


def test_an_unknown_scheme_is_reported_as_out_of_corpus() -> None:
    """PRD §5.3: another AMC's scheme is refused, with the in-scope scheme links offered."""
    result = answer("What is the expense ratio of Parag Parflex?", PROVIDER)
    assert result.kind == "out_of_corpus"
    assert "HDFC" in result.text
    assert "amfiindia.com" in (result.citation_url or "")


class _BrokenGenerator:
    """A generator that always fails, standing in for a timeout or a 5xx from the LLM endpoint."""

    def generate(self, context: object, query: str, intent: Intent) -> DraftAnswer:
        """Raise the typed generation error the pipeline is expected to absorb."""
        raise GenerationError("simulated upstream failure")


def test_an_llm_failure_degrades_to_the_extractive_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """architecture.md §15.3: a failing generator falls back to the composer, not to an error."""
    monkeypatch.setattr(
        "src.pipeline.resolve_generator", lambda *a, **k: (_BrokenGenerator(), "llm")
    )
    result = answer("What is the expense ratio of the HDFC Large Cap fund?", "llm")
    assert result.kind == "factual"
    assert result.generator == "extractive"
    assert "1.03%" in result.text
    assert result.trace["timings_ms"]["generator_error"] == "GenerationError"


def test_auto_provider_without_a_key_still_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demo runs with no credentials at all; `auto` simply means the composer."""
    monkeypatch.setattr("src.generation.load_llm_env", lambda: LlmEnv("", "", "", False))
    result = answer(
        "What is the minimum SIP amount for the HDFC Small Cap fund?",
        "auto",
    )
    assert result.kind == "factual"
    assert result.generator == "extractive"


class _HallucinatingGenerator:
    """A generator that invents a return figure, standing in for a model that ignored the prompt."""

    def generate(self, context: object, query: str, intent: Intent) -> DraftAnswer:
        """Return a fluent, well-formed, entirely ungrounded draft."""
        return DraftAnswer(
            text="The HDFC Large Cap Fund has given 32% returns over one year.",
            generator="llm",
            sentinels=[],
        )


def test_a_provider_that_misbehaves_does_not_reach_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft that fails the validators is replaced, so the user never sees an unvalidated one."""
    monkeypatch.setattr(
        "src.pipeline.resolve_generator", lambda *a, **k: (_HallucinatingGenerator(), "llm")
    )
    result = answer("What is the expense ratio of the HDFC Large Cap fund?", "llm")
    assert result.kind == "factual"
    assert result.generator == "extractive"
    assert "32%" not in result.text
    assert result.trace["guardrail"] == "v4_numeric"


def test_a_missing_index_is_reported_with_build_instructions(
    tmp_path: Path,
) -> None:
    """Degradation row one: no index means a typed error, not a wrong answer."""
    base = load_settings()
    settings = replace(
        base, paths=replace(base.paths, chroma_dir=str(tmp_path / "empty-chroma"))
    )
    with pytest.raises(IndexNotBuiltError) as caught:
        answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER, settings)
    assert "build" in str(caught.value).lower()


def test_the_golden_set_is_larger_than_the_documented_minimum() -> None:
    """PRD §7.1 asks for at least 20 questions; the shipped set stays comfortably above it."""
    assert len(GOLDEN) >= 20
    assert len({row["question"] for row in GOLDEN}) == len(GOLDEN)


def _timed_answers(count: int) -> list[float]:
    """Answer the first `count` golden questions and return per-question milliseconds."""
    samples: list[float] = []
    for row in GOLDEN[:count]:
        started = time.perf_counter()
        answer(row["question"], PROVIDER)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def test_twenty_golden_questions_stay_inside_the_latency_budget() -> None:
    """architecture.md §9.2: p95 under 150ms for 20 sequential questions, warm index.

    The first call loads the embedding model and is excluded, because the budget describes the
    steady-state cost of answering a question and not the one-off cost of starting the process.
    """
    warmup = _timed_answers(2)
    assert warmup, "the golden set must not be empty"
    samples = _timed_answers(20)
    p95 = statistics.quantiles(samples, n=20)[-1]
    assert p95 < 150, f"p95 {p95:.0f}ms over {samples}"


def test_golden_answers_are_stable_across_a_fresh_pipeline_call() -> None:
    """A restart cannot change a fact, because the answer is composed from the corpus."""
    row = next(item for item in GOLDEN if item["fact_family"] == "exit_load")
    settings = load_settings()
    first = answer(row["question"], PROVIDER, settings)
    second = answer(row["question"], PROVIDER, replace(settings))
    assert first.text == second.text
