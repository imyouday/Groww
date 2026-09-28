"""UI tests: the drawing primitives, the chip set, and the guard the demo depends on.

`app.py` is imported directly, not launched with `streamlit run`, because `main()` is guarded by
`__name__ == "__main__"`. That keeps these tests headless and fast while still exercising the real
rendering code rather than a copy of it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app import example_questions, render_answer, source_rows, sources_label
from src import guardrails
from src.config import load_settings
from src.models import Intent, PipelineError
from src.pipeline import answer
from src.registry import load_registry

PROVIDER = "extractive"


def _kinds(entries: list[dict[str, str]]) -> list[str]:
    """Return the primitive kinds in render order."""
    return [entry["kind"] for entry in entries]


def _links(entries: list[dict[str, str]]) -> list[str]:
    """Return every link button URL in a rendered turn."""
    return [entry["value"] for entry in entries if entry["kind"] == "link_button"]


def test_a_factual_answer_renders_one_link_and_the_last_updated_stamp() -> None:
    """C5 and C6: exactly one citation, and the stamp naming the source date."""
    result = answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER)
    entries = render_answer(result)
    assert _kinds(entries) == ["markdown", "link_button", "caption"]
    assert _links(entries) == [result.citation_url]
    assert entries[0]["value"] == result.text
    assert "1.03%" in entries[0]["value"]
    assert entries[2]["value"] == f"Last updated from sources: {result.last_updated}"


def test_the_answer_body_never_carries_a_url_of_its_own() -> None:
    """V6: the body is prose; the only link is the one the renderer adds from the registry."""
    result = answer("What is the minimum SIP amount for the HDFC Small Cap fund?", PROVIDER)
    assert "http" not in render_answer(result)[0]["value"]


def test_a_refusal_renders_one_link_and_no_figure() -> None:
    """A refusal is a rendered turn like any other: one link, no invented number."""
    result = answer("Should I buy the ELSS?", PROVIDER)
    entries = render_answer(result)
    assert _kinds(entries) == ["markdown", "link_button", "caption"]
    assert _links(entries) == ["https://www.amfiindia.com/"]
    assert not [character for character in entries[0]["value"] if character.isdigit()]


def test_a_pii_refusal_is_rendered_without_echoing_the_identifier() -> None:
    """C2: the PAN is refused, and the transcript must not contain it either."""
    pan = "ZZZZ9999Q"
    result = answer(f"My Aadhaar number is {pan}, update my folio please", PROVIDER)
    entries = render_answer(result)
    assert result.kind == "pii_refusal"
    assert pan not in "".join(entry["value"] for entry in entries)


def test_a_smalltalk_answer_renders_no_link_button() -> None:
    """A greeting has nothing to cite, so the turn has a body and a stamp but no button."""
    result = answer("hi there", PROVIDER)
    entries = render_answer(result)
    assert "link_button" not in _kinds(entries)
    assert entries[0]["value"]


def test_an_answer_with_no_last_updated_says_so_instead_of_printing_none() -> None:
    """The stamp is always rendered; an absent date must not read as a literal `None`."""
    result = answer("hello", PROVIDER)
    assert render_answer(result)[-1]["value"] == "Last updated from sources: n/a"


def test_the_sources_expander_reports_real_chunks_and_scores() -> None:
    """The sources panel is the audit trail, so it must carry the chunk text and the score."""
    result = answer("What is the benchmark of the HDFC Small Cap fund?", PROVIDER)
    assert sources_label(result).startswith("Sources used (")
    assert "top score" in sources_label(result)
    rows = source_rows(result)
    assert rows
    assert len(rows) == len(result.retrieved)
    first = rows[0]
    assert first["scheme"] == result.retrieved[0].chunk.scheme_name
    assert first["text"] == result.retrieved[0].chunk.text
    assert float(first["score"]) == pytest.approx(result.retrieved[0].final, abs=0.005)
    assert "NIFTY" in first["text"] or "BSE" in first["text"]


def test_the_sources_expander_copes_with_a_turn_that_retrieved_nothing() -> None:
    """A template answer has no chunks, and the caption must still render."""
    registry = load_registry(load_settings())
    refusal = guardrails.route(Intent.SMALLTALK, None, None, registry, load_settings())
    assert source_rows(refusal) == []
    assert sources_label(refusal) == "Sources used (0 chunks · top score 0.00)"


def test_there_are_exactly_three_chips_and_they_swap_after_a_refusal() -> None:
    """PRD FR-41: three examples on load, factual examples after a refusal."""
    on_load = example_questions(False)
    after_refusal = example_questions(True)
    assert len(on_load) == 3
    assert len(after_refusal) == 3
    assert on_load != after_refusal
    for question in on_load + after_refusal:
        assert question.endswith("?")


def test_the_chip_questions_are_ones_the_assistant_can_actually_answer() -> None:
    """A chip that leads to a refusal is a bad demo, so every chip must be a factual question."""
    for question in example_questions(False) + example_questions(True):
        assert answer(question, PROVIDER).kind == "factual"


def test_rendering_an_answer_is_pure() -> None:
    """The render step computes nothing, so it cannot add a claim to a validated answer."""
    result = answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER)
    assert render_answer(result) == render_answer(result)


def test_a_pipeline_error_is_a_typed_error_the_ui_can_catch(tmp_path: Path) -> None:
    """The UI shows the message rather than a traceback, so the failure has to be typed."""
    settings = load_settings()
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    broken = replace(
        settings, paths=replace(settings.paths, chroma_dir=str(blocker / "chroma"))
    )
    with pytest.raises(PipelineError):
        answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER, broken)
