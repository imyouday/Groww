"""Stage 5.1 — intent resolution (architecture.md §11.2).

Rule-first, ordered and exhaustive: the first matching rule wins, so the table order *is*
the precedence, and safety rules sit above helpfulness ones. Classification never calls the
LLM (D1), which is what makes refusal precision an engineering guarantee.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from src.config import Settings, load_settings
from src.models import FactFamily, Intent, SectionType
from src.pii import PIIHit, detect
from src.registry import Registry, load_registry

PII_LITERAL = re.compile(
    r"\b(?:my\s+)?(?:pan|pan\s?number|pan\s?card|aadhaar|aadhar|folio(?:\s+number)?"
    r"|account(?:\s+number)?|acct|ifsc|upi\s?id|customer\s?id|passcode)\b"
    r"|\botp\b|\bone\s?time\s?password"
    r"|\b(?:phone|mobile)\s+number\b",
    re.IGNORECASE,
)

ADVICE = re.compile(
    r"\bshould\s+i\b|\bshould\s+we\b|\bi\s+should\b|\bwould\s+you\b|\bdo\s+you\s+think\b"
    r"|\bis\s+it\s+(?:a\s+|the\s+)?(?:good|safe)\b"
    r"|\ba\s+good\s+(?:fund|elss|scheme|option|investment|choice|plan)\b"
    r"|\bbest\s+(?:fund|option|scheme)\b|\bworth\s+(?:buying|investing)\b"
    r"|\brecommend\w*\b|\bopinion\b|\ballocat\w*\b|\bportfolio\b|\brebalanc\w*\b"
    r"|\btax\s+saving\s+tips\b|\bwhich\s+one\s+should\b"
    r"|\bam\s+i\b[^?]*\btoo\s+(?:young|old|risk\w*)\b|\bsuitable\s+for\s+me\b",
    re.IGNORECASE,
)

PERFORMANCE = re.compile(
    r"\breturn\w*\b|\bperformance\b|\bnav\b|\bcagr\b|\bxirr\b|\bprofit\w*\b|\bloss\b"
    r"|\bgained\b|\bgrowth\s+of\b|\byield\b|\brank\w*\b|\bbest\s+performing\b"
    r"|\btop\s+performer\b|\bwhich\s+gave\b",
    re.IGNORECASE,
)

SMALLTALK = re.compile(
    r"^\s*(?:hi|hey|hello|thanks|thank\s+you|bye|good\s+morning|good\s+evening)\b"
    r"|\bwho\s+are\s+you\b|\bwhat\s+can\s+you\s+do\b|\bhelp\s+me\s+out\b",
    re.IGNORECASE,
)

ADVICE_RULE = "r2_advice"
PERFORMANCE_RULE = "r3_performance"
OUT_OF_CORPUS_RULE = "r4_other_amc"
SMALLTALK_RULE = "r5_smalltalk"
FACT_TERM_RULE = "r6_fact_family"
SCHEME_ALIAS_RULE = "r6_scheme_alias"
FALLBACK_RULE = "r7_needs_evidence"

FACT_TERMS: dict[FactFamily, tuple[str, ...]] = {
    FactFamily.EXPENSE_RATIO: ("expense ratio", "expense ratio and other fees", "ter", "charges"),
    FactFamily.EXIT_LOAD: ("exit load", "exit charges", "load"),
    FactFamily.MIN_SIP: ("minimum sip", "min sip", "minimum investment", "minimum amount"),
    FactFamily.LOCK_IN: ("lock-in", "lock in", "3 years", "three years", "80c", "tax saver"),
    FactFamily.RISKOMETER: ("riskometer", "risk"),
    FactFamily.BENCHMARK: ("benchmark", "index"),
    FactFamily.STATEMENTS: ("capital gains", "statement", "tax report", "download"),
}

FAMILY_LABELS: dict[FactFamily, str] = {
    FactFamily.EXPENSE_RATIO: "expense ratio and fees",
    FactFamily.EXIT_LOAD: "exit load",
    FactFamily.MIN_SIP: "minimum SIP amount",
    FactFamily.LOCK_IN: "lock-in period",
    FactFamily.RISKOMETER: "riskometer and benchmark",
    FactFamily.BENCHMARK: "riskometer and benchmark",
    FactFamily.STATEMENTS: "tax statements and reports",
    FactFamily.OTHER: "scheme facts",
}

EXPECTED_SECTION: dict[FactFamily, SectionType] = {
    FactFamily.EXPENSE_RATIO: SectionType.FEES,
    FactFamily.EXIT_LOAD: SectionType.FEES,
    FactFamily.MIN_SIP: SectionType.FEES,
    FactFamily.LOCK_IN: SectionType.LOCK_IN,
    FactFamily.RISKOMETER: SectionType.RISK,
    FactFamily.BENCHMARK: SectionType.RISK,
    FactFamily.STATEMENTS: SectionType.TAX,
    FactFamily.OTHER: SectionType.GENERAL,
}


class IntentResult(NamedTuple):
    """What the rule table decided, and which rule decided it (architecture.md §11.2)."""

    intent: Intent
    scheme_id: str | None
    fact_family: FactFamily
    needs_evidence: bool
    matched_rule: str


def has_pii(query: str) -> list[PIIHit]:
    """Return every personal identifier in the query, carrying no value (C2)."""
    return detect(query)


def resolve_scheme(query: str, registry: Registry | None = None) -> str | None:
    """Resolve a scheme alias by longest alias match; None means no filter (recall preserved)."""
    reg = registry or load_registry()
    haystack = query.lower()
    best_id, best_len = None, 0
    for scheme in reg.schemes:
        for alias in scheme.aliases:
            length = len(alias)
            if length > best_len and re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", haystack):
                best_id, best_len = scheme.scheme_id, length
    return best_id


def _fact_term_index(settings: Settings) -> tuple[tuple[FactFamily, str], ...]:
    """Every configured fact term, longest first, so "exit load" wins over "load"."""
    return tuple(
        sorted(
            (
                (FactFamily(name), term.lower())
                for name, terms in settings.retrieval.fact_terms.items()
                for term in terms
            ),
            key=lambda pair: len(pair[1]),
            reverse=True,
        )
    )


def resolve_fact_family(query: str, settings: Settings | None = None) -> FactFamily:
    """Detect the fact family via longest-match synonym search (architecture.md §20)."""
    haystack = query.lower()
    for family, term in _fact_term_index(settings or load_settings()):
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack):
            return family
    return FactFamily.OTHER


def _first_fact_family_match(
    query: str, settings: Settings
) -> tuple[FactFamily, str] | None:
    haystack = query.lower()
    for family, term in _fact_term_index(settings):
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack):
            return family, term
    return None


def classify(query: str, settings: Settings | None = None) -> IntentResult:
    """Resolve intent, scheme and fact family with the ordered first-match-wins rule table."""
    cfg = settings or load_settings()
    text = query.strip()
    scheme_id = resolve_scheme(text)
    family = resolve_fact_family(text, cfg)

    if has_pii(text) or PII_LITERAL.search(text):
        return IntentResult(Intent.PII_REQUEST, scheme_id, family, False, "r1_pii")

    if ADVICE.search(text):
        return IntentResult(Intent.ADVICE_REQUEST, scheme_id, family, False, ADVICE_RULE)

    if PERFORMANCE.search(text):
        return IntentResult(Intent.PERFORMANCE_REQUEST, scheme_id, family, False, PERFORMANCE_RULE)

    reg = load_registry()
    if reg.mentions_other_amc(text) and scheme_id is None:
        return IntentResult(Intent.OUT_OF_CORPUS, None, family, False, OUT_OF_CORPUS_RULE)

    if SMALLTALK.search(text):
        return IntentResult(Intent.SMALLTALK, scheme_id, family, False, SMALLTALK_RULE)

    match = _first_fact_family_match(text, cfg)
    if match is not None:
        family, _term = match
        return IntentResult(Intent.FACTUAL_FACT, scheme_id, family, False, FACT_TERM_RULE)

    if scheme_id is not None:
        return IntentResult(Intent.FACTUAL_FACT, scheme_id, family, False, SCHEME_ALIAS_RULE)

    return IntentResult(Intent.FACTUAL_FACT, None, FactFamily.OTHER, True, FALLBACK_RULE)
