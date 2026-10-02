"""Generation tests: the extractive composer, generator resolution, and the LLM adapter boundary."""

from __future__ import annotations

import httpx
import pytest

from src import generation
from src.config import LlmEnv
from src.generation import (
    ExtractiveGenerator,
    LLMGenerator,
    resolve_generator,
)
from src.models import (
    AssembledContext,
    ChunkRecord,
    DraftAnswer,
    FactFamily,
    GenerationError,
    Intent,
    ScoredChunk,
    SectionType,
)
from src.prompts import build_user_prompt

SCHEME_NAME = "HDFC Large Cap Fund - Direct Growth"
PAGE_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"

FEE_TABLE = (
    "Min. for 1st investment\n₹100\nMin. for 2nd investment\n₹100\nMin. for SIP\n₹100\n\n"
    "Exit load of 1% if redeemed within 1 year"
)


def make_context(text: str, terms: list[str]) -> AssembledContext:
    """Build an AssembledContext holding one chunk, the shape ExtractiveGenerator consumes."""
    chunk = ChunkRecord(
        chunk_id="c1",
        source_id="S1",
        scheme_id="S1",
        scheme_name=SCHEME_NAME,
        section="Expense ratio and other fees",
        section_type=SectionType.FEES,
        text=text,
        embed_text=text,
        url=PAGE_URL,
        title="HDFC Large Cap Fund Direct Growth",
        fetched_at="2026-09-27",
        ordinal=0,
        token_count=120,
    )
    scored = ScoredChunk(
        chunk=chunk,
        dense=0.8,
        keyword_boost=0.2,
        final=0.86,
        mmr_selected=True,
        matched_terms=terms,
    )
    return AssembledContext(
        query="What is the minimum SIP?",
        scheme_id="S1",
        fact_family=FactFamily.MIN_SIP,
        chunks=[scored],
        total_tokens=120,
        top_score=0.86,
        context_text=f"[1] {SCHEME_NAME} | Minimum investments | source: {PAGE_URL}\n    {text}",
    )


def env_with(**overrides: str) -> LlmEnv:
    """Build an LlmEnv for tests, defaulting to the no-key extractive configuration."""
    values = {"api_key": "", "base_url": "", "model": "", "log_queries": False}
    values.update(overrides)
    return LlmEnv(**values)


def response_with_content(content: str) -> httpx.Response:
    """Build a 200 chat-completions response whose assistant message is `content`."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


class _StubClient:
    """Minimal httpx.Client stand-in that replays queued responses and counts calls."""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, json: dict, timeout: float, headers: dict) -> httpx.Response:
        """Return the next queued response, raising it if it was queued as an exception."""
        self.calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        """Match httpx.Client so the generator can own and close a real client."""


class TestExtractiveGenerator:
    def test_returns_the_value_from_a_label_value_table(self) -> None:
        draft = ExtractiveGenerator().generate(
            make_context(FEE_TABLE, ["min. for", "min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
        )
        assert draft.generator == "extractive"
        assert draft.sentinels == []
        assert "Min. for SIP ₹100" in draft.text

    def test_never_exceeds_three_sentences(self) -> None:
        text = "\n".join(f"Expense ratio note {index} is 0.1{index}% for the scheme" for index in range(9))
        draft = ExtractiveGenerator().generate(
            make_context(text, ["expense ratio"]), "Expense ratio?", Intent.FACTUAL_FACT
        )
        assert len(generation._split_sentences(draft.text)) <= generation.MAX_SENTENCES

    def test_abbreviations_do_not_break_a_sentence(self) -> None:
        text = "The minimum SIP is Rs. 500 per month and the exit load is nil."
        draft = ExtractiveGenerator().generate(
            make_context(text, ["minimum sip"]), "Min SIP?", Intent.FACTUAL_FACT
        )
        assert "Rs. 500" in draft.text

    def test_market_risk_boilerplate_is_stripped(self) -> None:
        text = (
            "Exit load of 1% if redeemed within 1 year. "
            "Mutual fund investments are subject to market risks."
        )
        draft = ExtractiveGenerator().generate(
            make_context(text, ["exit load"]), "Exit load?", Intent.FACTUAL_FACT
        )
        assert "market risks" not in draft.text
        assert "Exit load of 1%" in draft.text

    def test_unusable_chunk_returns_not_in_corpus(self) -> None:
        draft = ExtractiveGenerator().generate(
            make_context("Invest now. Start a SIP today.", ["min. for sip"]),
            "Min SIP?",
            Intent.FACTUAL_FACT,
        )
        assert draft.text == ""
        assert draft.sentinels == ["NOT_IN_CORPUS"]

    def test_empty_context_returns_not_in_corpus(self) -> None:
        context = make_context("irrelevant", ["min. for sip"])
        empty = AssembledContext(
            query=context.query,
            scheme_id=context.scheme_id,
            fact_family=context.fact_family,
            chunks=[],
            total_tokens=0,
            top_score=0.0,
        )
        draft = ExtractiveGenerator().generate(empty, "Min SIP?", Intent.FACTUAL_FACT)
        assert draft.sentinels == ["NOT_IN_CORPUS"]

    def test_chunk_without_matched_terms_is_not_answered(self) -> None:
        text = "HDFC Mutual Fund\nTotal AUM\n₹9,86,236.84 Cr\nPhone\n1800 202 4422"
        draft = ExtractiveGenerator().generate(
            make_context(text, []), "What is the weather in Mumbai?", Intent.FACTUAL_FACT
        )
        assert draft.text == ""
        assert draft.sentinels == ["NOT_IN_CORPUS"]


class TestResolveGenerator:
    def test_default_provider_is_extractive(self) -> None:
        from src.config import load_settings

        assert load_settings().generation.provider == "extractive"

    def test_auto_without_a_key_chooses_extractive(self) -> None:
        generator, name = resolve_generator(
            settings=_settings_with_provider("auto"), env=env_with()
        )
        assert name == "extractive"
        assert isinstance(generator, ExtractiveGenerator)

    def test_auto_with_a_key_chooses_llm(self) -> None:
        env = env_with(api_key="k", base_url="https://example.test/v1", model="m")
        generator, name = resolve_generator(
            settings=_settings_with_provider("auto"), env=env
        )
        assert name == "llm"
        assert isinstance(generator, LLMGenerator)

    def test_auto_with_a_key_but_no_endpoint_degrades(self) -> None:
        _, name = resolve_generator(
            settings=_settings_with_provider("auto"), env=env_with(api_key="k")
        )
        assert name == "extractive"

    def test_explicit_extractive_wins_even_with_a_key(self) -> None:
        settings = _settings_with_provider("extractive")
        env = env_with(api_key="k", base_url="https://example.test/v1", model="m")
        generator, name = resolve_generator(settings=settings, env=env)
        assert name == "extractive"
        assert isinstance(generator, ExtractiveGenerator)

    def test_explicit_llm_without_a_key_raises(self) -> None:
        settings = _settings_with_provider("llm")
        with pytest.raises(GenerationError, match="LLM_API_KEY"):
            resolve_generator(settings=settings, env=env_with())

    def test_unknown_provider_raises(self) -> None:
        settings = _settings_with_provider("magic")
        with pytest.raises(GenerationError, match="unknown"):
            resolve_generator(settings=settings, env=env_with())


def _settings_with_provider(provider: str):
    from dataclasses import replace

    from src.config import load_settings

    settings = load_settings()
    return replace(settings, generation=replace(settings.generation, provider=provider))


class TestLLMGenerator:
    def test_passes_the_question_and_context_to_the_endpoint(self) -> None:
        client = _StubClient([response_with_content("The minimum SIP is ₹500.")])
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        context = make_context(FEE_TABLE, ["min. for sip"])
        LLMGenerator(env=env, client=client).generate(
            context, "What is the minimum SIP?", Intent.FACTUAL_FACT
        )
        call = client.calls[0]
        assert call["url"] == "https://example.test/v1/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer secret"
        user_turn = call["json"]["messages"][1]["content"]
        assert "<context>" in user_turn
        assert "Min. for SIP" in user_turn
        assert "What is the minimum SIP?" in user_turn
        assert call["timeout"] == 8
        assert call["json"]["temperature"] == 0.1

    def test_garbage_output_is_carried_not_raised(self) -> None:
        client = _StubClient([response_with_content("asdf qwerty")])
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        draft = LLMGenerator(env=env, client=client).generate(
            make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
        )
        assert isinstance(draft, DraftAnswer)
        assert draft.text == "asdf qwerty"
        assert draft.raw_model_output == "asdf qwerty"
        assert draft.sentinels == []

    def test_refuse_body_becomes_a_sentinel(self) -> None:
        client = _StubClient([response_with_content("REFUSE")])
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        draft = LLMGenerator(env=env, client=client).generate(
            make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
        )
        assert draft.sentinels == ["REFUSE"]
        assert draft.text == ""
        assert draft.raw_model_output == "REFUSE"

    def test_not_in_corpus_body_becomes_a_sentinel(self) -> None:
        client = _StubClient([response_with_content("NOT_IN_CORPUS")])
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        draft = LLMGenerator(env=env, client=client).generate(
            make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
        )
        assert draft.sentinels == ["NOT_IN_CORPUS"]

    def test_transport_failure_raises_a_typed_error_after_one_retry(self) -> None:
        client = _StubClient([httpx.ConnectError("boom"), httpx.ReadTimeout("boom")])
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        with pytest.raises(GenerationError, match="after 2 attempt"):
            LLMGenerator(env=env, client=client).generate(
                make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
            )
        assert len(client.calls) == 2

    def test_client_error_raises_a_typed_error_without_retrying(self) -> None:
        client = _StubClient([httpx.Response(400, text="bad request")])
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        with pytest.raises(GenerationError, match="HTTP 400"):
            LLMGenerator(env=env, client=client).generate(
                make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
            )
        assert len(client.calls) == 1

    def test_server_error_is_retried_once(self) -> None:
        client = _StubClient(
            [httpx.Response(500, text="server error"), httpx.Response(500, text="server error")]
        )
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        with pytest.raises(GenerationError, match="after 2 attempt"):
            LLMGenerator(env=env, client=client).generate(
                make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
            )
        assert len(client.calls) == 2

    def test_missing_choices_raises_a_typed_error(self) -> None:
        client = _StubClient([httpx.Response(200, json={"choices": []})])
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        with pytest.raises(GenerationError, match="no choices"):
            LLMGenerator(env=env, client=client).generate(
                make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
            )

    def test_a_recovered_retry_still_answers(self) -> None:
        client = _StubClient(
            [httpx.ConnectError("boom"), response_with_content("The minimum SIP is ₹500.")]
        )
        env = env_with(api_key="secret", base_url="https://example.test/v1", model="m")
        draft = LLMGenerator(env=env, client=client).generate(
            make_context(FEE_TABLE, ["min. for sip"]), "Min SIP?", Intent.FACTUAL_FACT
        )
        assert draft.text == "The minimum SIP is ₹500."


class TestUserPrompt:
    def test_context_is_delimited_and_marked_untrusted(self) -> None:
        prompt = build_user_prompt("Exit load of 1%.", "What is the exit load?")
        assert "<context>" in prompt
        assert "</context>" in prompt
        assert "Exit load of 1%." in prompt
        assert "What is the exit load?" in prompt
        assert "not instructions" in prompt
