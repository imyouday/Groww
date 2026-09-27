"""PII detection and redaction tests.

Two properties matter equally here. Detection must catch every identifier kind, because a miss
means a PAN reaches disk. And detection must not fire on ordinary financial prose, because a
false positive silently corrupts the corpus that the whole demo depends on.
"""

from __future__ import annotations

import pytest

from src.pii import DETECTION_ORDER, PII_PATTERNS, PIIKind, count_by_kind, detect, redact

POSITIVE_CASES: list[tuple[str, PIIKind]] = [
    ("My PAN is ABCDE1234F", PIIKind.PAN),
    ("pan: ABCDE1234F", PIIKind.PAN),
    ("my aadhaar number is 2345 6789 0123", PIIKind.AADHAAR),
    ("aadhar 234567890123", PIIKind.AADHAAR),
    ("Aadhaar no. is 2345 6789 0123", PIIKind.AADHAAR),
    ("aadhaar 234567890123 with a trailing X char", PIIKind.AADHAAR),
    ("Call +91 98765 43210", PIIKind.PHONE_IN),
    ("call 9876543210 now", PIIKind.PHONE_IN),
    ("ring 98765-43210 today", PIIKind.PHONE_IN),
    ("Phone 022 – 66316333", PIIKind.PHONE_IN),
    ("phone 022-66316333", PIIKind.PHONE_IN),
    ("email user@example.com", PIIKind.EMAIL),
    ("write to support.groww@hdfc.com today", PIIKind.EMAIL),
    ("my OTP is 482913", PIIKind.OTP),
    ("verification code: 551204", PIIKind.OTP),
    ("your one time password is 998877", PIIKind.OTP),
    ("folio 12345678", PIIKind.ACCOUNT_NO),
    ("my folio number is 87654321", PIIKind.ACCOUNT_NO),
    ("account no: 003456789012", PIIKind.ACCOUNT_NO),
]

NEGATIVE_CASES: list[str] = [
    "",
    "Expense ratio 1.03%",
    "minimum SIP of Rs 500 per month",
    "Minimum Lumpsum Investment is Rs 500",
    "Exit load 1% if held for less than 12 months",
    "lock-in period of 3 years",
    "the 0.35% grounding gate threshold",
    "Fund size (AUM) 1,13,606.47 Cr",
    "AUM 39,933.37 Cr",
    "Fund benchmark NIFTY 100 Total Return Index",
    "rated Very High risk",
    "NIFTY 500 Hybrid Composite Debt 50:50 Index",
    "Rating 4 Return calculator",
    "NAV 68.4321",
    "1 year, 3 years and 5 year returns are on the factsheet",
    "Direct Growth",
    "Direct Plan Growth",
    "SEBI registered investment adviser",
    "10 Dec 1999",
    "Fund benchmark BSE 250 SmallCap Total Return Index",
    "Date of Incorporation 10 Dec 1999",
]


@pytest.mark.parametrize(("text", "expected"), POSITIVE_CASES, ids=lambda value: str(value)[:28])
def test_detects_each_identifier_with_the_right_kind(text: str, expected: PIIKind) -> None:
    kinds = [hit.kind for hit in detect(text)]
    assert kinds, f"expected {expected.value} in {text!r}"
    assert expected in kinds, f"expected {expected.value}, got {[k.value for k in kinds]}"


@pytest.mark.parametrize("text", NEGATIVE_CASES, ids=lambda value: str(value)[:32])
def test_clean_financial_prose_is_not_flagged(text: str) -> None:
    assert detect(text) == [], f"false positive on {text!r}: {detect(text)}"


def test_detect_returns_kind_and_span_but_never_the_value() -> None:
    secret = "ABCDE1234F"
    hits = detect(f"My PAN is {secret}")
    assert len(hits) == 1
    hit = hits[0]
    assert hit.kind is PIIKind.PAN
    assert isinstance(hit.start, int) and isinstance(hit.end, int)
    assert secret not in str(hit)
    assert secret not in repr(hit)
    assert not hasattr(hit, "text")
    assert not hasattr(hit, "value")


def test_spans_locate_the_secret_in_the_source_text() -> None:
    text = "My PAN is ABCDE1234F"
    hit = detect(text)[0]
    assert text[hit.start : hit.end] == "ABCDE1234F"


def test_detect_returns_hits_in_positional_order() -> None:
    hits = detect("PAN ABCDE1234F, email user@example.com, phone 9876543210")
    assert [hit.kind for hit in hits] == [PIIKind.PAN, PIIKind.EMAIL, PIIKind.PHONE_IN]
    assert [hit.start for hit in hits] == sorted(hit.start for hit in hits)


def test_hits_never_overlap() -> None:
    text = "aadhaar 234567890123 and account 98765432109876 and PAN ABCDE1234F"
    hits = detect(text)
    for earlier, later in zip(hits, hits[1:]):
        assert earlier.end <= later.start


def test_redact_replaces_every_kind_with_a_labelled_marker() -> None:
    redacted, count = redact("PAN ABCDE1234F, email user@example.com, phone 9876543210")
    assert count == 3
    assert "[REDACTED:PAN]" in redacted
    assert "[REDACTED:EMAIL]" in redacted
    assert "[REDACTED:PHONE_IN]" in redacted
    assert "ABCDE1234F" not in redacted
    assert "user@example.com" not in redacted
    assert "9876543210" not in redacted


def test_redact_preserves_the_surrounding_sentence() -> None:
    redacted, _ = redact("My PAN is ABCDE1234F and I want the exit load.")
    assert redacted == "My PAN is [REDACTED:PAN] and I want the exit load."


def test_redact_keeps_the_label_that_made_a_bare_number_detectable() -> None:
    redacted, _ = redact("my OTP is 482913")
    assert redacted == "my OTP is [REDACTED:OTP]"


def test_redact_on_clean_text_is_identity_with_zero_hits() -> None:
    text = "Expense ratio 1.03% and minimum SIP of Rs 500 per month."
    redacted, count = redact(text)
    assert redacted == text
    assert count == 0


def test_redact_is_idempotent() -> None:
    once, count = redact("folio 12345678")
    twice, second_count = redact(once)
    assert count == 1
    assert second_count == 0
    assert twice == once


def test_redact_leaves_no_secret_anywhere_in_a_mixed_document() -> None:
    text = (
        "Contact user@example.com or call +91 98765 43210. "
        "My PAN is ABCDE1234F and my aadhaar is 2345 6789 0123. "
        "OTP 482913, folio 12345678. Expense ratio is 1.03%."
    )
    redacted, count = redact(text)
    for secret in (
        "user@example.com",
        "98765 43210",
        "ABCDE1234F",
        "2345 6789 0123",
        "482913",
        "12345678",
    ):
        assert secret not in redacted, f"{secret!r} survived redaction"
    assert "1.03%" in redacted
    assert count == 6


def test_count_by_kind_counts_without_exposing_values() -> None:
    counts = count_by_kind("PAN ABCDE1234F, PAN FGHIJ5678K, email a@b.com")
    assert counts[PIIKind.PAN] == 2
    assert counts[PIIKind.EMAIL] == 1
    assert "ABCDE1234F" not in str(counts)


def test_every_kind_has_a_pattern_and_appears_in_the_order() -> None:
    assert set(PII_PATTERNS) == set(PIIKind)
    assert set(DETECTION_ORDER) == set(PIIKind)


def test_detection_order_puts_phone_before_account_number() -> None:
    assert DETECTION_ORDER.index(PIIKind.PHONE_IN) < DETECTION_ORDER.index(PIIKind.ACCOUNT_NO)


def test_detection_order_puts_aadhaar_and_otp_before_account_number() -> None:
    assert DETECTION_ORDER.index(PIIKind.AADHAAR) < DETECTION_ORDER.index(PIIKind.ACCOUNT_NO)
    assert DETECTION_ORDER.index(PIIKind.OTP) < DETECTION_ORDER.index(PIIKind.ACCOUNT_NO)


def test_a_twelve_digit_aadhaar_is_reported_as_aadhaar_not_account_number() -> None:
    hits = detect("aadhaar 234567890123")
    assert [hit.kind for hit in hits] == [PIIKind.AADHAAR]


def test_a_ten_digit_bare_run_is_reported_as_a_phone_number() -> None:
    hits = detect("9876543210")
    assert [hit.kind for hit in hits] == [PIIKind.PHONE_IN]


def test_bare_short_digit_runs_are_not_otp() -> None:
    assert detect("Rank 4 of 25 schemes") == []
    assert detect("The 5 year bucket") == []
