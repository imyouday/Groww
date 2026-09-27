"""Stage 6 (Generation): turn retrieved context into an unvalidated draft (architecture.md §6.1, §13.1).

Two implementations sit behind one protocol. `ExtractiveGenerator` is deterministic, needs no
network, and is therefore both the demo's zero-key mode (D4) and the safety net when the model is
unreachable. `LLMGenerator` is a single hand-written httpx POST against an OpenAI-compatible
endpoint — there is no SDK and no agent loop.

Nothing in this module decides what a user may see. Every path returns a `DraftAnswer`, an
unvalidated intermediate, and only the Phase 8 guardrails may turn one into an `Answer`. Model
output is never trusted for URLs, numbers, sentence count, or advice language.

Sentence scoring reads `ScoredChunk.matched_terms` rather than importing a helper from the
retrieval stage: the fact terms that reached this chunk are already recorded on the chunk, so the
two stages stay coupled only through the dataclasses in src/models.py (architecture.md §5.1).
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx

from src.config import LlmEnv, Settings, load_llm_env, load_settings
from src.models import (
    AssembledContext,
    DraftAnswer,
    GenerationError,
    Intent,
    ScoredChunk,
)
from src.prompts import SENTINELS, SYSTEM_PROMPT, build_user_prompt

MAX_SENTENCES = 3
MIN_SENTENCE_CHARS = 12
LABEL_MAX_WORDS = 6
VALUE_MAX_WORDS = 8
_PLACEHOLDER = "\x00"

# Splitting on "[.!?] whitespace" alone would fracture "1.25%"-style values and initials, and
# would cut "Rs." and "e.g." in half, so those periods are masked out before the split.
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

# Anything here is page furniture, not a fact. The list is deliberately about call-to-action and
# risk-disclaimer phrasing rather than single words: "download" in particular carries the statement
# fact family, so filtering on the word would delete the answer it is meant to protect.
_JUNK_PATTERNS: tuple[str, ...] = (
    r"^note\s*:",
    r"^source\s*:",
    r"^disclaimer",
    r"subject to market risks",
    r"read all scheme related documents",
    r"past performance",
    r"^https?://",
    r"^www\.",
    r"\binvest now\b",
    r"\bstart (a )?sip\b",
    r"\bapply now\b",
    r"\bbuy now\b",
    r"\bclick here\b",
    r"\bsign up\b",
    r"\bget a call back\b",
    r"\bchat with (our|an|expert)",
    r"\bcall toll free\b",
    r"\bregister now\b",
)
_JUNK_RE = re.compile("|".join(_JUNK_PATTERNS), re.IGNORECASE)
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_HAS_DIGIT_RE = re.compile(r"\d")
_ENDS_SENTENCE_RE = re.compile(r"[.!?:;]$")


class _RetryableError(GenerationError):
    """A transport or server-side failure that is worth exactly one more attempt."""


class Generator(Protocol):
    """The single method every generation strategy must provide (architecture.md §6.1)."""

    def generate(
        self, context: AssembledContext, question: str, intent: Intent
    ) -> DraftAnswer:
        """Return an unvalidated draft answer for a question over retrieved context."""
        ...


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences without breaking on abbreviations, initials, or decimals."""
    masked = text
    for abbreviation in _ABBREVIATIONS:
        masked = masked.replace(abbreviation, abbreviation.replace(".", _PLACEHOLDER))
    masked = re.sub(r"(?<=[A-Z])\.(?=\s|$)", _PLACEHOLDER, masked)
    sentences: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", masked):
        cleaned = part.replace(_PLACEHOLDER, ".").strip()
        if cleaned:
            sentences.append(cleaned)
    return sentences


def _is_label(text: str) -> bool:
    """Report whether a unit reads as a field label awaiting its value."""
    return (
        len(text.split()) <= LABEL_MAX_WORDS
        and not _ENDS_SENTENCE_RE.search(text)
        and bool(_HAS_LETTER_RE.search(text))
    )


def _is_value(text: str) -> bool:
    """Report whether a unit reads as the value belonging to the label above it."""
    return (
        len(text.split()) <= VALUE_MAX_WORDS
        and not _ENDS_SENTENCE_RE.search(text)
        and bool(_HAS_DIGIT_RE.search(text))
    )


def _segment(text: str) -> list[str]:
    """Split extracted page text into sentence candidates.

    Scheme pages arrive from HTML as one table cell per line, so a naive split on `[.!?]` leaves
    multi-line blobs that are not sentences and buries the line holding the answer. Two repairs
    apply. A line that plainly continues the previous one is rejoined when the previous unit has no
    terminal punctuation and the next begins lower-case, which keeps "the exit load is 1% if" with
    "redeemed within 1 year". And a short unpunctuated label is paired with the short numeric value
    beneath it, because the facts this assistant answers live in exactly that shape: "Expense ratio"
    and "1.03%" are one answer split across two lines, and "Min. for SIP" and "₹100" likewise.
    """
    units: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if units and not _ENDS_SENTENCE_RE.search(units[-1]) and stripped[:1].islower():
            units[-1] = f"{units[-1]} {stripped}"
        else:
            units.append(stripped)

    candidates: list[str] = []
    index = 0
    while index < len(units):
        unit = units[index]
        following = units[index + 1] if index + 1 < len(units) else None
        if following is not None and _is_label(unit) and _is_value(following):
            candidates.append(f"{unit} {following}")
            index += 2
        else:
            candidates.append(unit)
            index += 1

    sentences: list[str] = []
    for candidate in candidates:
        sentences.extend(_split_sentences(candidate))
    return sentences


def _is_junk(sentence: str) -> bool:
    """Report whether a sentence is page furniture, boilerplate, or too thin to be a fact."""
    if len(sentence) < MIN_SENTENCE_CHARS:
        return True
    if not _HAS_LETTER_RE.search(sentence):
        return True
    return _JUNK_RE.search(sentence) is not None


def _overlap(sentence: str, terms: tuple[str, ...]) -> int:
    """Count how many of the chunk's matched fact terms appear in a sentence."""
    lowered = sentence.lower()
    return sum(1 for term in terms if term and term in lowered)


def _score_sentence(sentence: str, index: int, overlap: int) -> float:
    """Score a sentence by fact-term overlap, presence of a value, and how early it appears."""
    carries_value = 1.0 if _HAS_DIGIT_RE.search(sentence) else 0.0
    position_prior = 1.0 / (1.0 + index)
    return 3.0 * overlap + carries_value + position_prior


def _fact_terms_for(chunk: ScoredChunk) -> tuple[str, ...]:
    """Return the fact terms the retrieval stage matched in this chunk."""
    return tuple(term for term in chunk.matched_terms if term)


def _compose(text: str, terms: tuple[str, ...]) -> str:
    """Return at most MAX_SENTENCES usable sentences from a chunk, in document order.

    A sentence is usable only if it repeats one of the fact terms the retrieval stage matched in
    this chunk. That requirement is what stops a passing-but-vague query from being answered with
    whatever the top chunk happens to contain: "What is the weather in Mumbai" retrieves HDFC's
    registered address, whose digits would otherwise satisfy a value-based score on their own. The
    grounding gate already requires term coverage to open the door, and this is the same rule
    applied at the sentence level, so an uncovered chunk yields the NOT_IN_CORPUS sentinel rather
    than a fluent wrong answer.
    """
    scored: list[tuple[float, int, str]] = []
    for index, sentence in enumerate(_segment(text)):
        if _is_junk(sentence):
            continue
        overlap = _overlap(sentence, terms)
        if overlap == 0:
            continue
        scored.append((_score_sentence(sentence, index, overlap), index, sentence))
    if not scored:
        return ""
    best = sorted(scored, key=lambda item: (-item[0], item[1]))[:MAX_SENTENCES]
    ordered = sorted(best, key=lambda item: item[1])
    return " ".join(sentence for _, _, sentence in ordered)


class ExtractiveGenerator:
    """Deterministic composer: lifts sentences from the top chunk, never inventing text."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else load_settings()

    @property
    def name(self) -> str:
        """Return the provider name recorded in DraftAnswer.generator."""
        return "extractive"

    def generate(
        self, context: AssembledContext, question: str, intent: Intent
    ) -> DraftAnswer:
        """Compose a draft from the highest-scoring chunk, or return the NOT_IN_CORPUS sentinel."""
        if not context.chunks:
            return DraftAnswer(text="", generator="extractive", sentinels=["NOT_IN_CORPUS"])
        top = context.chunks[0]
        text = _compose(top.chunk.text, _fact_terms_for(top))
        if not text:
            return DraftAnswer(text="", generator="extractive", sentinels=["NOT_IN_CORPUS"])
        return DraftAnswer(text=text, generator="extractive")


class LLMGenerator:
    """One hand-written POST to an OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        settings: Settings | None = None,
        env: LlmEnv | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings if settings is not None else load_settings()
        self._env = env if env is not None else load_llm_env()
        self._client = client

    @property
    def name(self) -> str:
        """Return the provider name recorded in DraftAnswer.generator."""
        return "llm"

    @property
    def model(self) -> str:
        """Return the configured model id, preferring config.yaml over the environment."""
        configured = self._settings.generation.model.strip()
        return configured if configured else self._env.model.strip()

    def _endpoint(self) -> str:
        """Return the fully-qualified chat completions URL."""
        base = self._env.base_url.strip().rstrip("/")
        if not base:
            raise GenerationError("LLM_BASE_URL is not set; cannot build a chat completions URL")
        return f"{base}/chat/completions"

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST the payload with the configured timeout, returning the decoded body."""
        if not self._env.api_key:
            raise GenerationError("LLM_API_KEY is not set; cannot call the LLM endpoint")
        generation = self._settings.generation
        client = self._client if self._client is not None else httpx.Client()
        owns_client = self._client is None
        try:
            response = client.post(
                self._endpoint(),
                json=payload,
                timeout=generation.timeout_s,
                headers={
                    "Authorization": f"Bearer {self._env.api_key}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code >= 400:
                message = f"LLM endpoint returned HTTP {response.status_code}"
                if response.status_code >= 500 or response.status_code == 429:
                    raise _RetryableError(message)
                raise GenerationError(message)
            body = response.json()
        except _RetryableError:
            raise
        except GenerationError:
            raise
        except httpx.HTTPError as exc:
            raise _RetryableError(f"LLM request failed: {type(exc).__name__}") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise GenerationError("LLM response body was not valid JSON") from exc
        finally:
            if owns_client:
                client.close()
        if not isinstance(body, dict):
            raise GenerationError("LLM response body was not a JSON object")
        return body

    def _extract_content(self, body: dict[str, Any]) -> str:
        """Pull the assistant message out of an OpenAI-compatible response body."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise GenerationError("LLM response contained no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise GenerationError("LLM response contained no message object")
        content = message.get("content")
        if not isinstance(content, str):
            raise GenerationError("LLM response contained no string content")
        return content

    def generate(
        self, context: AssembledContext, question: str, intent: Intent
    ) -> DraftAnswer:
        """Call the model, mapping a sentinel body to DraftAnswer.sentinels and keeping the raw text."""
        generation = self._settings.generation
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(context.context_text, question),
                },
            ],
            "temperature": generation.temperature,
            "max_tokens": generation.max_tokens,
        }
        attempts = max(1, generation.retries + 1)
        last_error: GenerationError | None = None
        for _ in range(attempts):
            try:
                body = self._post(payload)
                content = self._extract_content(body)
            except _RetryableError as exc:
                last_error = exc
                continue
            except GenerationError:
                raise
            text = content.strip()
            if text in SENTINELS:
                return DraftAnswer(
                    text="", generator="llm", sentinels=[text], raw_model_output=content
                )
            return DraftAnswer(text=text, generator="llm", raw_model_output=content)
        raise GenerationError(
            f"LLM generation failed after {attempts} attempt(s): {last_error}"
        )


def resolve_generator(
    settings: Settings | None = None, env: LlmEnv | None = None
) -> tuple[Generator, str]:
    """Pick a generator from config.generation.provider, degrading to extractive when unset.

    `auto` selects the LLM only when the environment can actually reach one: a key with no base
    URL or no model id is a half-finished setup, and failing over to the deterministic composer
    keeps the demo working instead of surfacing a configuration error to a user mid-question.
    """
    resolved = settings if settings is not None else load_settings()
    environment = env if env is not None else load_llm_env()
    provider = resolved.generation.provider.strip().lower()

    if provider == "extractive":
        return ExtractiveGenerator(resolved), "extractive"
    if provider == "llm":
        if not environment.api_key:
            raise GenerationError(
                "config.generation.provider is 'llm' but LLM_API_KEY is not set; "
                "set it in .env or the environment, or use provider 'auto'"
            )
        return LLMGenerator(resolved, environment), "llm"
    if provider == "auto":
        usable = bool(environment.api_key and environment.base_url and environment.model)
        if usable:
            return LLMGenerator(resolved, environment), "llm"
        return ExtractiveGenerator(resolved), "extractive"
    raise GenerationError(
        f"unknown config.generation.provider {provider!r}; expected auto, llm, or extractive"
    )

