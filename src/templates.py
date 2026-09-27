"""User-visible copy, loaded once from config.yaml (architecture.md §20, PRD.md §12).

The strings live in config.yaml rather than here so that the UI, the refusal paths and the
README cannot drift apart: every one of them is rendered from the same source of truth. These
constants are module-level so a caller can write `templates.PII_REFUSAL` without first loading
settings, and `render` fails loudly on a missing placeholder instead of leaking a literal
"{link}" into an answer.
"""

from __future__ import annotations

from string import Formatter

from src.config import load_settings
from src.models import PipelineError

_COPY = load_settings().copy

UI_DISCLAIMER: str = _COPY.ui_disclaimer
REFUSAL_MESSAGE: str = _COPY.refusal_message
PERFORMANCE_REDIRECT: str = _COPY.performance_redirect
PII_REFUSAL: str = _COPY.pii_refusal
OUT_OF_CORPUS_MESSAGE: str = _COPY.out_of_corpus_message
NOT_IN_CORPUS_MESSAGE: str = _COPY.not_in_corpus_message
SMALLTALK_MESSAGE: str = _COPY.smalltalk_message

COPY_CONSTANTS: dict[str, str] = {
    "UI_DISCLAIMER": UI_DISCLAIMER,
    "REFUSAL_MESSAGE": REFUSAL_MESSAGE,
    "PERFORMANCE_REDIRECT": PERFORMANCE_REDIRECT,
    "PII_REFUSAL": PII_REFUSAL,
    "OUT_OF_CORPUS_MESSAGE": OUT_OF_CORPUS_MESSAGE,
    "NOT_IN_CORPUS_MESSAGE": NOT_IN_CORPUS_MESSAGE,
    "SMALLTALK_MESSAGE": SMALLTALK_MESSAGE,
}


def placeholders(template: str) -> tuple[str, ...]:
    """Return the placeholder names a copy template expects, in order of appearance."""
    return tuple(name for _, name, _, _ in Formatter().parse(template) if name)


def render(template: str, **values: object) -> str:
    """Fill a copy template, raising a typed error rather than exposing an unfilled placeholder."""
    expected = set(placeholders(template))
    missing = sorted(expected - set(values))
    if missing:
        raise PipelineError(
            f"copy template expects {sorted(expected)} but was given no value for {missing}"
        )
    return template.format(**values)
