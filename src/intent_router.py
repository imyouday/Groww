"""Intent router: classify user message into intent category with LLM + rules fallback.

This module sits between classification and retrieval, adding a second pass that
distinguishes definition/general questions from scheme-specific ones, and handles
greeting/smalltalk, advice, out_of_scope, and PII.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from src.config import LlmEnv, Settings, load_llm_env, load_settings
from src.intents import Intent, classify
from src.models import PipelineError
from src.pii import detect


class RouterIntent(str, Enum):
    GREETING_SMALLTALK = "greeting_smalltalk"
    DEFINITION = "definition"
    SCHEME_FACT = "scheme_fact"
    SCHEME_OVERVIEW = "scheme_overview"
    ADVICE = "advice"
    OUT_OF_SCOPE = "out_of_scope"
    PII = "pii"
    PERFORMANCE = "performance"
    UNCLEAR = "unclear"


GREETING_PATTERNS = (
    r"^\s*(?:hi|hey|hello|thanks|thank\s+you|bye|good\s+(?:morning|evening))\b",
    r"\bwho\s+are\s+you\b",
    r"\bwhat\s+can\s+you\s+do\b",
    r"\bhelp\s+me\s+out\b",
)

DEFINITION_PATTERNS = (
    r"^\s*(?:what\s+is|what\s+are|define|definition\s+of|meaning\s+of|explain)\b",
    r"^\s*(?:tell\s+me\s+about|describe)\b",
    r"\b(?:difference\s+between|vs\.?|versus)\b",
    r"\bhow\s+(?:does|do|to)\b",
    r"\bwhy\s+is\b",
    r"\b(?:lock-?in|lockin)\b",
)

ADVICE_PATTERNS = (
    r"\bshould\s+i\b|\bshould\s+we\b|\bi\s+should\b|\bwould\s+you\b|\bdo\s+you\s+think\b"
    r"|\bis\s+it\s+(?:a\s+|the\s+)?(?:good|safe)\b"
    r"|\ba\s+good\s+(?:fund|elss|scheme|option|investment|choice|plan)\b"
    r"|\bbest\s+(?:fund|option|scheme)\b|\bworth\s+(?:buying|investing)\b"
    r"|\brecommend\w*\b|\bopinion\b|\ballocat\w*\b|\bportfolio\b|\brebalanc\w*\b"
    r"|\btax\s+saving\s+tips\b|\bwhich\s+one\s+should\b"
    r"|\bam\s+i\b[^?]*\btoo\s+(?:young|old|risk\w*)\b|\bsuitable\s+for\s+me\b"
    r"|\bwhich\s+is\s+better\b|\bwhich\s+(?:one|fund)\s+is\s+better\b",
)

OUT_OF_SCOPE_PATTERNS = (
    r"\bweather\b|\bcricket\b|\bstock\s+price\b|\bcrypto\b|\bbitcoin\b",
    r"\bwho\s+won\b|\bwhen\s+is\b|\bwhere\s+is\b",
)

PERFORMANCE_PATTERNS = (
    r"\breturn\w*\b|\bperformance\b|\bcagr\b|\bxirr\b|\bprofit\w*\b|\bloss\b"
    r"|\bgained\b|\bgrowth\s+of\b|\byield\b|\brank\w*\b|\bbest\s+performing\b"
    r"|\btop\s+performer\b|\bwhich\s+gave\b",
)

UNCLEAR_PATTERNS = (
    r"^\s*(?:what|why|how|when|where|who)\s*$",
    r"^\s*(?:ok|okay|\?|tell\s+me|explain)\s*$",
)

SCHEME_OVERVIEW_PATTERNS = (
    r"\bwhat\s+(?:schemes|funds)\s+(?:do\s+you\s+)?(?:know|cover|have)\b",
    r"\blist\s+(?:the\s+)?(?:schemes|funds)\b",
    r"\btell\s+me\s+about\s+(?:hdfc\s+)?(?:funds|schemes)\b",
    r"\bwhich\s+(?:schemes|funds)\s+(?:do\s+you\s+)?cover\b",
    r"\bexplain\s+(?:me\s+)?about\s+(?:hdfc\s+)?(?:mutual\s+fund\s+)?(?:schemes|funds)\b",
)


@dataclass(frozen=True)
class RouterResult:
    intent: RouterIntent
    scheme_id: str | None
    original_intent: Intent
    confidence: float
    reasoning: str


SYSTEM_PROMPT = (
    "You are an intent classifier for a mutual fund FAQ assistant. "
    "Classify the user's message into exactly one category:\n\n"
    "1. greeting_smalltalk - greetings, thanks, bye, 'who are you', 'what can you do'\n"
    "2. definition - general questions about mutual fund concepts (what is NAV, what is exit load, "
    "difference between SIP and lump sum, how does SWP work, etc.) - NO specific fund mentioned\n"
    "3. scheme_fact - specific fact about a named HDFC scheme (expense ratio of HDFC Large Cap, "
    "exit load of HDFC Small Cap, minimum SIP for ELSS, etc.)\n"
    "4. advice - asking for recommendation, opinion, should I buy/sell, which is better, allocate\n"
    "5. out_of_scope - weather, cricket, stocks, crypto, general knowledge, etc.\n"
    "6. pii - contains PAN, Aadhaar, phone, email, OTP, account/folio number\n\n"
    "Return JSON only: {\"intent\": \"category\", \"scheme_id\": \"S1|S2|S3|S4|S5|null\", "
    "\"confidence\": 0.0-1.0, \"reasoning\": \"brief explanation\"}\n"
    "Scheme IDs: S1=HDFC Large Cap, S2=HDFC Flexi Cap, S3=HDFC ELSS, S4=HDFC Small Cap, S5=HDFC Balanced Advantage"
)


def _has_pii(text: str) -> bool:
    return bool(detect(text))


def _rules_classify(text: str) -> RouterResult | None:
    """Fast rule-based classification for obvious cases."""
    lower = text.lower()

    # PII check first (safety)
    if _has_pii(text):
        return RouterResult(
            intent=RouterIntent.PII,
            scheme_id=None,
            original_intent=Intent.PII_REQUEST,
            confidence=0.95,
            reasoning="PII detected",
        )

    # Greeting/smalltalk
    for pattern in GREETING_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            return RouterResult(
                intent=RouterIntent.GREETING_SMALLTALK,
                scheme_id=None,
                original_intent=Intent.SMALLTALK,
                confidence=0.9,
                reasoning="Greeting/smalltalk pattern matched",
            )

    # Advice
    for pattern in ADVICE_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            return RouterResult(
                intent=RouterIntent.ADVICE,
                scheme_id=None,
                original_intent=Intent.ADVICE_REQUEST,
                confidence=0.9,
                reasoning="Advice pattern matched",
            )

    # Out of scope
    for pattern in OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            return RouterResult(
                intent=RouterIntent.OUT_OF_SCOPE,
                scheme_id=None,
                original_intent=Intent.OUT_OF_CORPUS,
                confidence=0.85,
                reasoning="Out of scope pattern matched",
            )

    # Check for known other AMCs first (out of corpus)
    from src.registry import load_registry
    registry = load_registry()
    if registry.mentions_other_amc(text):
        return RouterResult(
            intent=RouterIntent.OUT_OF_SCOPE,
            scheme_id=None,
            original_intent=Intent.OUT_OF_CORPUS,
            confidence=0.9,
            reasoning="Known other AMC mentioned",
        )

    # Unclear questions (very short, vague)
    for pattern in UNCLEAR_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            return RouterResult(
                intent=RouterIntent.UNCLEAR,
                scheme_id=None,
                original_intent=Intent.FACTUAL_FACT,
                confidence=0.9,
                reasoning="Unclear/vague question pattern matched",
            )

    # Scheme overview questions (list schemes, what funds do you cover)
    for pattern in SCHEME_OVERVIEW_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            return RouterResult(
                intent=RouterIntent.SCHEME_OVERVIEW,
                scheme_id=None,
                original_intent=Intent.FACTUAL_FACT,
                confidence=0.9,
                reasoning="Scheme overview pattern matched",
            )

    # Performance questions
    for pattern in PERFORMANCE_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            scheme_id = _resolve_scheme(text)
            return RouterResult(
                intent=RouterIntent.PERFORMANCE,
                scheme_id=scheme_id,
                original_intent=Intent.PERFORMANCE_REQUEST,
                confidence=0.9,
                reasoning="Performance pattern matched",
            )

    # Definition questions (general, no scheme mentioned)
    for pattern in DEFINITION_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            # Check if a scheme is explicitly mentioned
            scheme_id = _resolve_scheme(text)
            # Check fact family - only truly education-only topics (no scheme-specific data)
            # use DEFINITION even when scheme is mentioned
            from src.intents import resolve_fact_family
            from src.config import load_settings
            fact_family = resolve_fact_family(text, load_settings())
            education_only_families = {"lock_in"}
            if scheme_id is None or fact_family.value in education_only_families:
                return RouterResult(
                    intent=RouterIntent.DEFINITION,
                    scheme_id=None,
                    original_intent=Intent.FACTUAL_FACT,
                    confidence=0.85,
                    reasoning="Definition pattern matched, using education retrieval"
                    if fact_family.value in education_only_families
                    else "Definition pattern matched, no scheme mentioned",
                )
            else:
                return RouterResult(
                    intent=RouterIntent.SCHEME_FACT,
                    scheme_id=scheme_id,
                    original_intent=Intent.FACTUAL_FACT,
                    confidence=0.85,
                    reasoning="Definition pattern matched with scheme mentioned",
                )

    return None


def _resolve_scheme(text: str) -> str | None:
    """Resolve scheme ID from text using known aliases."""
    from src.registry import load_registry
    registry = load_registry()
    return registry.resolve_scheme(text)


def _llm_classify(text: str, history: list[dict[str, str]], settings: Settings) -> RouterResult | None:
    """Use LLM for classification when rules are inconclusive."""
    env = load_llm_env()
    if not env.api_key or not env.base_url or not env.model:
        return None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    # Add last 4 turns as context
    for turn in history[-4:]:
        if turn.get("role") == "user":
            messages.append({"role": "user", "content": turn["text"]})
        elif turn.get("role") == "assistant":
            messages.append({"role": "assistant", "content": turn.get("answer", {}).get("text", "")})
    messages.append({"role": "user", "content": text})

    payload = {
        "model": env.model.strip(),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 100,
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                f"{env.base_url.strip().rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {env.api_key}", "Content-Type": "application/json"},
            )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"].strip()
        data = json.loads(content)

        intent_str = data.get("intent", "").lower()
        scheme_id = data.get("scheme_id")
        confidence = float(data.get("confidence", 0.5))
        reasoning = data.get("reasoning", "")

        try:
            intent = RouterIntent(intent_str)
        except ValueError:
            return None

        # Validate scheme_id
        if scheme_id and scheme_id not in ("S1", "S2", "S3", "S4", "S5"):
            scheme_id = None

        original_intent = _map_to_original(intent)
        return RouterResult(
            intent=intent,
            scheme_id=scheme_id,
            original_intent=original_intent,
            confidence=confidence,
            reasoning=reasoning,
        )
    except Exception:
        return None


def _map_to_original(router_intent: RouterIntent) -> Intent:
    mapping = {
        RouterIntent.GREETING_SMALLTALK: Intent.SMALLTALK,
        RouterIntent.DEFINITION: Intent.FACTUAL_FACT,
        RouterIntent.SCHEME_FACT: Intent.FACTUAL_FACT,
        RouterIntent.SCHEME_OVERVIEW: Intent.FACTUAL_FACT,
        RouterIntent.ADVICE: Intent.ADVICE_REQUEST,
        RouterIntent.OUT_OF_SCOPE: Intent.OUT_OF_CORPUS,
        RouterIntent.PII: Intent.PII_REQUEST,
        RouterIntent.PERFORMANCE: Intent.PERFORMANCE_REQUEST,
        RouterIntent.UNCLEAR: Intent.FACTUAL_FACT,
    }
    return mapping.get(router_intent, Intent.FACTUAL_FACT)


def route_intent(
    text: str,
    history: list[dict[str, str]] | None = None,
    settings: Settings | None = None,
) -> RouterResult:
    """Main entry point: classify intent with rules first, then LLM fallback."""
    resolved = settings or load_settings()
    history = history or []

    # Fast rule-based pass
    result = _rules_classify(text)
    if result is not None:
        return result

    # LLM fallback
    result = _llm_classify(text, history, resolved)
    if result is not None:
        return result

    # Default fallback: scheme_fact if scheme mentioned, else definition
    scheme_id = _resolve_scheme(text)
    if scheme_id:
        return RouterResult(
            intent=RouterIntent.SCHEME_FACT,
            scheme_id=scheme_id,
            original_intent=Intent.FACTUAL_FACT,
            confidence=0.6,
            reasoning="Default: scheme mentioned",
        )
    return RouterResult(
        intent=RouterIntent.DEFINITION,
        scheme_id=None,
        original_intent=Intent.FACTUAL_FACT,
        confidence=0.5,
        reasoning="Default: no scheme mentioned",
    )