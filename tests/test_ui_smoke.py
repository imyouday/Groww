"""UI tests: the drawing primitives, the chip set, and the guard the demo depends on.

`app.py` is imported directly, not launched with `streamlit run`, because `main()` is guarded by
`__name__ == "__main__"`. That keeps these tests headless and fast while still exercising the real
rendering code rather than a copy of it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import (
    example_questions,
    escape,
    header_card_html,
    hero_card_html,
    nav_html,
    render_answer,
    result_latency_ms,
    result_scheme,
    riskometer_html,
    source_rows,
    sources_label,
    turn_html,
)
from src import guardrails, templates, theme
from src.config import load_settings
from src.models import Intent, PipelineError
from src.pipeline import answer
from src.registry import load_registry

PROVIDER = "extractive"
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
USER_BUBBLE = '<div class="mf-user-bubble">'


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


def test_the_nav_marks_exactly_one_link_active() -> None:
    """The wireframe's nav highlights the page you are on; two highlights would read as a bug."""
    settings = load_settings()
    nav = nav_html(settings)
    assert nav.count("is-active") == 1
    assert escape(settings.ui.nav_active_link) in nav
    assert f'aria-label="Primary"' in nav
    assert "Breadcrumb" in nav


def test_the_page_offers_a_skip_link_and_a_live_region() -> None:
    """Keyboard and screen-reader scaffolding: skip to the thread, and announce new answers."""
    app_test = _run_app()
    assert not app_test.exception, [error.value for error in app_test.exception]
    markup = "\n".join(block.value for block in app_test.markdown)
    assert 'class="mf-skip" href="#mf-thread"' in markup
    assert 'id="mf-thread"' in markup
    assert 'aria-live="polite"' in markup
    assert 'aria-label="Conversation"' in markup


def test_the_header_carries_both_compliance_badges() -> None:
    """A facts-only assistant has to say so on its face, not only in the footer."""
    settings = load_settings()
    header = header_card_html(settings)
    assert escape(settings.ui.badge_verified) in header
    assert escape(settings.ui.badge_no_advice) in header
    assert "is-warn" in header


def test_the_hero_repeats_the_disclaimer_rather_than_a_second_copy_of_it() -> None:
    """The wireframe insets the notice inside the intro card; the copy comes from config."""
    settings = load_settings()
    hero = hero_card_html(settings)
    assert escape(settings.ui.hero_title) in hero
    assert templates.UI_DISCLAIMER in hero


def test_a_factual_turn_links_only_to_the_registry_url() -> None:
    """C5 at the HTML level: the citation is an href built from the answer's own URL."""
    result = answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER)
    card = turn_html(result)
    hrefs = [part.split('"')[0] for part in card.split('href="')[1:]]
    assert hrefs == [result.citation_url]
    assert load_registry(load_settings()).source_by_url(result.citation_url) is not None


def test_a_refusal_turn_shows_the_compliance_banner_and_no_figure() -> None:
    """A refusal is compliance chrome around the pipeline's own words, never a substitution."""
    result = answer("Should I buy the ELSS?", PROVIDER)
    card = turn_html(result)
    assert "Regulatory Compliance Notice" in card
    assert "Advice Prohibited" in card
    assert "Answer withheld" in card
    assert escape(result.text) in card
    assert "1.03%" not in card


def test_a_smalltalk_turn_does_not_claim_a_verified_source() -> None:
    """Nothing was retrieved, so the card must not badge itself as source-verified."""
    card = turn_html(answer("hi there", PROVIDER))
    assert "Source verified" not in card
    assert "is-refusal" not in card
    assert "No source used" in card


def test_a_turn_reports_the_scheme_and_latency_the_trace_carries() -> None:
    """The wireframe's meta row is only honest if it is fed from the trace, not invented."""
    result = answer("What is the benchmark of the HDFC Small Cap fund?", PROVIDER)
    assert result_scheme(result) == "HDFC Small Cap Fund - Direct Growth"
    latency = result_latency_ms(result)
    assert latency is not None and latency >= 0
    assert f"{latency} ms" in turn_html(result)


def test_a_turn_with_no_latency_omits_the_field_rather_than_printing_none() -> None:
    """Same rule as the last-updated stamp: an absent measurement is dropped, not printed."""
    result = answer("What is the benchmark of the HDFC Small Cap fund?", PROVIDER)
    stripped = replace(result, trace={})
    assert result_latency_ms(stripped) is None
    assert "ms" not in turn_html(stripped)
    assert result_scheme(stripped)


def test_the_riskometer_is_readable_without_its_colour() -> None:
    """WCAG 1.4.1: the bar is a gradient, so the level has to be spelled out too."""
    settings = load_settings()
    levels = settings.ui.riskometer_levels
    assert riskometer_html("not in the corpus", levels).count("is-on") == 0
    at_risk = riskometer_html("High", levels)
    assert at_risk.count("is-on") == levels.index("High") + 1
    assert "Riskometer: High" in at_risk
    assert 'aria-label="Riskometer: High"' in at_risk


def test_the_wireframes_sample_figures_are_never_rendered() -> None:
    """The Stitch mockup's Axis NAV, AUM and 1-day return are sample data, not corpus facts.

    The assistant must never show a NAV or a return, so the redesign dropped those tiles rather
    than wiring the mockup's numbers in; this asserts the drop has not been undone.
    """
    settings = load_settings()
    chrome = "\n".join(
        [
            nav_html(settings),
            header_card_html(settings),
            hero_card_html(settings),
            turn_html(answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER)),
            turn_html(answer("Should I buy the ELSS?", PROVIDER)),
        ]
    )
    for forbidden in ("Axis Bluechip", "1.92%", "+0.64%", "1,428 Cr", "Direct Growth · Groww")  :
        assert forbidden not in chrome, forbidden
    assert "1.92%" not in chrome


def test_answer_text_is_escaped_before_it_reaches_raw_html() -> None:
    """The cards are one raw-HTML block, so an unescaped `&` or `<` from a chunk is an injection."""
    assert escape("a & b <script>") == "a &amp; b &lt;script&gt;"
    result = replace(
        answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER),
        text="<img src=x onerror=alert(1)>",
    )
    assert "<img" not in turn_html(result)
    assert "&lt;img" in turn_html(result)


def test_every_card_is_well_formed_html() -> None:
    """A malformed card renders as visible garbage, and nothing about a parse error is loud.

    The first draft of the nav had a nested same-quote f-string, which Python 3.11 accepts and
    then silently turns the tail of the string into literal text: the nav rendered one link per
    character of the tuple. Asserting the tags balance is what turns that class of mistake into a
    red test rather than a demo that looks slightly wrong.
    """
    settings = load_settings()
    cards = [
        nav_html(settings),
        header_card_html(settings),
        hero_card_html(settings),
        riskometer_html("High", settings.ui.riskometer_levels),
        turn_html(answer("What is the expense ratio of the HDFC Large Cap fund?", PROVIDER)),
        turn_html(answer("Should I buy the ELSS?", PROVIDER)),
        turn_html(answer("hi there", PROVIDER)),
    ]
    for card in cards:
        assert "{" not in card and "}" not in card, card[:120]
        assert card.count("<div") == card.count("</div>"), card[:200]
        assert card.count("<span") == card.count("</span>"), card[:200]
        assert "<a " not in card or card.count("<a ") == card.count("</a>"), card[:200]


def _run_app() -> AppTest:
    """Run `app.py` headlessly, the way Streamlit runs it for a browser."""
    return AppTest.from_file(APP_PATH, default_timeout=300).run()


def test_the_whole_page_renders_without_raising() -> None:
    """`main()` is the only untested path that touches the Streamlit API itself.

    A wrong keyword to `st.chat_input` is not caught by any helper test, and the page fails to
    paint at all, so the script is executed end to end here and its exceptions asserted empty.
    """
    app_test = _run_app()
    assert not app_test.exception, [error.value for error in app_test.exception]
    assert [button.label for button in app_test.button] == list(example_questions(False))
    assert len(app_test.sidebar.markdown) == 4
    assert any("mf-brand-mark" in block.value for block in app_test.markdown)


def test_asking_a_question_draws_a_grounded_answer_and_keeps_the_chips() -> None:
    """A factual answer adds a card, keeps the factual chips, and does not paint the hero twice."""
    app_test = _run_app()
    app_test.chat_input[0].set_value("What is the expense ratio of the HDFC Large Cap fund?").run()
    assert not app_test.exception, [error.value for error in app_test.exception]
    cards = [block.value for block in app_test.markdown]
    assert sum("mf-card" in block for block in cards) >= 4
    assert any(USER_BUBBLE in block for block in cards)
    assert any("is-answer" in block and "Source verified" in block for block in cards)
    assert not any("is-refusal" in block for block in cards)
    assert [button.label for button in app_test.button] == list(example_questions(False))
    assert not any("Groww MF Intelligence" in block for block in cards)


def test_asking_for_advice_draws_the_compliance_card_and_swaps_the_chips() -> None:
    """PRD FR-41 and C4: a refusal is visibly a refusal, and the next suggestions are factual."""
    app_test = _run_app()
    app_test.chat_input[0].set_value("Should I buy the ELSS tax saver?").run()
    assert not app_test.exception, [error.value for error in app_test.exception]
    cards = [block.value for block in app_test.markdown]
    assert any("is-refusal" in block and "Regulatory Compliance Notice" in block for block in cards)
    assert [button.label for button in app_test.button] == list(example_questions(True))


def test_an_identifier_is_never_sent_and_never_echoed() -> None:
    """C2 at the input: the message is dropped before the pipeline, and the value is not shown.

    The warning is asserted to name the kind of identifier and not the identifier itself, because a
    refusal that repeats the PAN has already leaked it into the transcript and the browser cache.
    """
    app_test = _run_app()
    app_test.chat_input[0].set_value("My email is bob@example.com, please update my folio").run()
    assert not app_test.exception, [error.value for error in app_test.exception]
    assert any("EMAIL" in item.value for item in app_test.warning)
    assert not any("bob@example.com" in item.value for item in app_test.warning)
    assert not any(USER_BUBBLE in block.value for block in app_test.markdown)
    assert not any("is-answer" in block.value for block in app_test.markdown)


def test_the_theme_toggle_settles_in_a_headless_run() -> None:
    """The rerun loop that froze the demo was invisible to every unit test but the browser.

    Flipping the switch has to land in a second, stable render rather than cycling, so run the
    toggle twice and assert the sidebar reports the new theme with no third state to reach.
    """
    app_test = _run_app()
    app_test.sidebar.toggle[0].set_value(True).run()
    assert not app_test.exception, [error.value for error in app_test.exception]
    assert app_test.sidebar.toggle[0].label == theme.toggle_label(theme.Theme.DARK)
    app_test.sidebar.toggle[0].set_value(True).run()
    assert app_test.sidebar.toggle[0].value is True
    assert not app_test.exception
