"""PII detection and redaction, applied to queries and to every byte of ingested text.

This module never returns a matched substring. `detect` reports the kind and the span, so a
caller can log a count (architecture.md §14.3) without ever putting a PAN or an OTP into a log
line. `redact` is the only way text leaves this module, and it replaces the secret before the
text reaches disk (architecture.md §14.2).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import NamedTuple

_LABELLED_NUMBER = (
    r"(?:aadhaar|aadhar|uidai|account|acct|folio|consumer\s*account|customer\s*id|client\s*id)"
)
# A label is often followed by more than one filler word: "aadhaar number is", "folio no. is".
_LABEL_LINK = r"\s*(?:(?:is|are|number|no\.?)\s*){0,2}"
# The separator class includes the en and em dashes because the corpus writes "022 - 66316333"
# with typographic dashes, not ASCII hyphens.
_SEP = r"[\s\-\u2010-\u2015]?"
_DASH = r"(?:\s*[-\u2010-\u2015]\s*|\s+)"
_PHONE_BODY = rf"(?:\+?91{_SEP})?[6-9]\d{{4}}{_SEP}\d{{5}}"
# An Indian landline (022-66316333) is also a contact number, not an account number. Without this
# the 8-18 digit ACCOUNT_NO rule claims it and the redacted text misreports what was removed.
_LANDLINE = rf"0\d{{2,4}}{_DASH}\d{{6,8}}"
_OTP_CONTEXT = r"(?:otp|one[\s\-]?time\s*password|verification\s*code|auth(?:entication)?\s*code|access\s*code)"


class PIIKind(str, Enum):
    """The categories of personal identifier this project refuses to store (PRD C2)."""

    PAN = "PAN"
    AADHAAR = "AADHAAR"
    ACCOUNT_NO = "ACCOUNT_NO"
    OTP = "OTP"
    EMAIL = "EMAIL"
    PHONE_IN = "PHONE_IN"


PII_PATTERNS: dict[PIIKind, re.Pattern[str]] = {
    PIIKind.PAN: re.compile(r"\b(?P<secret>[A-Z]{5}\d{4}[A-Z])\b"),
    PIIKind.AADHAAR: re.compile(
        rf"{_LABELLED_NUMBER}{_LABEL_LINK}(?::\s*)?"
        rf"(?P<secret>[2-9]\d{{3}}{_SEP}\d{{4}}{_SEP}\d{{4}}(?:{_SEP}[Xx])?)\b",
        re.IGNORECASE,
    ),
    PIIKind.OTP: re.compile(
        rf"{_OTP_CONTEXT}\s*(?:is|number|no\.?|:)?\s*(?P<secret>\d{{4,6}})\b",
        re.IGNORECASE,
    ),
    PIIKind.ACCOUNT_NO: re.compile(
        rf"(?:{_LABELLED_NUMBER}{_LABEL_LINK}(?::\s*)?)?"
        r"(?<![\d.,])(?P<secret>\d{8,18})(?!\d)(?!\.\d)\b"
    ),
    PIIKind.EMAIL: re.compile(
        r"\b(?P<secret>[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"
    ),
    PIIKind.PHONE_IN: re.compile(
        rf"(?<![\d.])(?P<secret>{_PHONE_BODY}|{_LANDLINE})(?!\d)(?!\.\d)"
    ),
}

# Applied in this order so a more specific pattern claims its span first. PAN leads because it is
# the only pattern that must not be eaten by the 8-18 digit account rule. AADHAAR precedes
# ACCOUNT_NO because a 12-digit Aadhaar is also 12 digits. OTP precedes ACCOUNT_NO because a
# 6-digit code is not a valid account number but a labelled 6-digit code must still be caught.
# PHONE_IN precedes ACCOUNT_NO because a bare 10-digit run is far more often an Indian mobile
# number than an account number, and the account rule's 8-18 digit range would otherwise claim
# it and report the wrong kind.
DETECTION_ORDER: tuple[PIIKind, ...] = (
    PIIKind.PAN,
    PIIKind.AADHAAR,
    PIIKind.OTP,
    PIIKind.EMAIL,
    PIIKind.PHONE_IN,
    PIIKind.ACCOUNT_NO,
)


class PIIHit(NamedTuple):
    """Where a personal identifier was found. Carries no substring, by design."""

    kind: PIIKind
    start: int
    end: int


def _claim_spans(text: str) -> list[PIIHit]:
    """Return non-overlapping hits, first pattern in DETECTION_ORDER winning each span.

    Only the `secret` group of each match is claimed, so the label that made a bare number
    detectable ("my OTP is 482913") survives redaction and the redacted sentence still reads
    like a sentence.
    """
    taken: list[bool] = [False] * len(text)
    hits: list[PIIHit] = []
    for kind in DETECTION_ORDER:
        for match in PII_PATTERNS[kind].finditer(text):
            start, end = match.span("secret")
            if any(taken[start:end]):
                continue
            hits.append(PIIHit(kind, start, end))
            for index in range(start, end):
                taken[index] = True
    hits.sort(key=lambda hit: hit.start)
    return hits


def detect(text: str) -> list[PIIHit]:
    """Return every personal identifier found in text, ordered by position and carrying no value."""
    return _claim_spans(text)


def redact(text: str) -> tuple[str, int]:
    """Return text with every identifier replaced by a kind-labelled marker, and the hit count."""
    hits = _claim_spans(text)
    if not hits:
        return text, 0
    pieces: list[str] = []
    cursor = 0
    for hit in hits:
        pieces.append(text[cursor : hit.start])
        pieces.append(f"[REDACTED:{hit.kind.value}]")
        cursor = hit.end
    pieces.append(text[cursor:])
    return "".join(pieces), len(hits)


def count_by_kind(text: str) -> dict[PIIKind, int]:
    """Return how many identifiers of each kind text contains, for logs that carry counts only."""
    counts: dict[PIIKind, int] = {}
    for hit in _claim_spans(text):
        counts[hit.kind] = counts.get(hit.kind, 0) + 1
    return counts
