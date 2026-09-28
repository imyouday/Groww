"""The Streamlit UI: one transcript, three chips, one citation per answer.

Two properties of this file are load-bearing rather than stylistic. First, it may import only
`src.pipeline`, `src.config`, `src.models`, `src.templates` and `src.theme` (architecture.md §5.2,
asserted by `tests/test_layering.py`), so the sidebar's index facts, the active generator and the
example chips all arrive as data from `src.pipeline` and `src.templates` instead of as imports of
the stages behind them. Second, the per-answer rendering is computed by `render_answer` and only
then drawn, so the "exactly one link per answer" rule (C5) is asserted in a test rather than
eyebeyed in a browser.

The layout follows the Stitch wireframes in `design/stitch/`: a blurred top nav, a breadcrumb, a
two-column page whose right column is Streamlit's own sidebar restyled as the wireframe's card
stack, a chip row of example questions, and one card per turn. Every fact on screen comes from an
`Answer`, a `SourceRecord` or the config, so nothing here restates a number the pipeline produced.
The wireframe's Axis Bluechip figures are deliberately absent: they are sample data, and the system
never returns a NAV or a 1-day return (AGENTS.md safety invariants).

`main()` is called under `if __name__ == "__main__"`, which is what `streamlit run app.py` does and
what a test import does not, so the module can be imported for its pure helpers without a running
Streamlit server.
"""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from src import pipeline, templates, theme
from src.config import load_settings
from src.models import Answer, PipelineError
from src.theme import Theme

LINK_LABEL = load_settings().ui.link_label

# Answer kinds that are a refusal of some kind, as opposed to a factual answer. The wireframe draws
# these as an amber "Regulatory Compliance Notice" card rather than a plain bubble, and the sidebar
# swaps its chip row after one, because the user has just asked something the system will not answer.
REFUSAL_KINDS = frozenset(
    {
        "refusal",
        "performance_redirect",
        "pii_refusal",
        "out_of_corpus",
        "not_in_corpus",
    }
)

# The compliance framing the wireframe puts on every refusal. The specific reason stays the
# pipeline's to decide, so this is chrome around `Answer.text`, never a replacement for it.
REFUSAL_HEADLINE = "Regulatory Compliance Notice"
REFUSAL_BADGE = "Advice Prohibited"
REFUSAL_FOOTER = "Statutory filter active · Guardrail applied"


def escape(text: str) -> str:
    """Return text safe to place inside raw HTML, since the wireframe's cards are one block."""
    return html.escape(text, quote=True)


def result_scheme(result: Answer) -> str:
    """Return the scheme an answer is about, from the trace, falling back to a retrieved chunk."""
    named = result.trace.get("scheme_name")
    if isinstance(named, str) and named:
        return named
    return result.retrieved[0].chunk.scheme_name if result.retrieved else ""


def result_latency_ms(result: Answer) -> int | None:
    """Return the measured end-to-end time, which the wireframe shows beside the answer."""
    timings = result.trace.get("timings_ms")
    if isinstance(timings, dict):
        total = timings.get("total")
        if isinstance(total, (int, float)):
            return int(total)
    total = result.trace.get("total_ms")
    return int(total) if isinstance(total, (int, float)) else None


def render_answer(result: Answer) -> list[dict[str, str]]:
    """Return what a bot turn should display, as an ordered list of drawing primitives.

    Returning the primitives instead of drawing them is what makes C5 testable: the list can
    contain at most one `link_button`, and that link is `result.citation_url` from the registry,
    never a URL the generator produced (V6).
    """
    entries: list[dict[str, str]] = [{"kind": "markdown", "value": result.text}]
    if result.citation_url:
        entries.append(
            {
                "kind": "link_button",
                "label": LINK_LABEL,
                "value": result.citation_url,
            }
        )
    entries.append(
        {
            "kind": "caption",
            "value": f"Last updated from sources: {result.last_updated or 'n/a'}",
        }
    )
    return entries


def sources_label(result: Answer) -> str:
    """Return the expander caption for a turn's sources."""
    top = result.retrieved[0].final if result.retrieved else 0.0
    unit = "chunk" if len(result.retrieved) == 1 else "chunks"
    return f"Sources used ({len(result.retrieved)} {unit} · top score {top:.2f})"


def source_rows(result: Answer) -> list[dict[str, str]]:
    """Return one row per retrieved chunk, for the sources expander."""
    return [
        {
            "scheme": chunk.chunk.scheme_name,
            "section": chunk.chunk.section,
            "score": f"{chunk.final:.2f}",
            "matched": ", ".join(chunk.matched_terms) or "none",
            "text": chunk.chunk.text,
        }
        for chunk in result.retrieved
    ]


def example_questions(refused: bool) -> tuple[str, ...]:
    """Return the three chip labels, swapped for factual ones after a refusal (PRD FR-41)."""
    settings = load_settings()
    return (
        settings.ui.example_questions_after_refusal
        if refused
        else settings.ui.example_questions
    )


@st.cache_resource(show_spinner="Loading the index and the model. The first start takes a few seconds.")
def warm() -> dict[str, object]:
    """Load the registry, the index and the encoder once, then report the sidebar facts.

    The encoder is loaded and run once here, off the user's critical path, so the first question
    they type is answered at the steady-state speed the sidebar is claiming (NFR-2).
    """
    return pipeline.warm_index()


def _card(inner: str, extra_class: str = "") -> str:
    """Wrap pre-built HTML in the wireframe's card surface."""
    classes = f"mf-card {extra_class}".strip()
    return f'<div class="{classes}">{inner}</div>'


def _badge(text: str, tone: str = "") -> str:
    """Render a pill badge; `tone` is one of is-ok, is-warn, is-error."""
    classes = f"mf-badge {tone}".strip()
    return f'<span class="{classes}">{escape(text)}</span>'


def nav_html(settings: Any) -> str:
    """Render the wireframe's top nav: brand, links with one active, and the search affordance."""
    active = " is-active"
    links = "".join(
        '<span class="mf-nav-link{cls}">{label}</span>'.format(
            cls=active if name == settings.ui.nav_active_link else "", label=escape(name)
        )
        for name in settings.ui.nav_links
    )
    crumbs = ' <span aria-hidden="true">&rsaquo;</span> '.join(
        escape(part) for part in settings.ui.breadcrumb
    )
    return (
        '<a class="mf-skip" href="#mf-thread">Skip to the conversation</a>'
        '<div class="mf-nav"><div class="mf-nav-row">'
        '<span class="mf-brand"><span class="mf-brand-mark">G</span>Groww</span>'
        '<span class="mf-nav-link">Search</span>'
        f'<nav class="mf-nav-links" aria-label="Primary">{links}</nav>'
        "</div></div>"
        f'<div class="mf-crumb" aria-label="Breadcrumb">{crumbs}</div>'
    )


def header_card_html(settings: Any) -> str:
    """Render the assistant header: identity, the verified badge, and the no-advice pill."""
    return _card(
        '<div class="mf-card-head">'
        '<span class="mf-avatar" aria-hidden="true">MF</span>'
        '<div>'
        f'<div class="mf-title">{escape(settings.ui.title)} '
        f"{_badge(settings.ui.badge_verified, 'is-ok')}</div>"
        f'<div class="mf-sub">{escape(settings.ui.scope_line)}</div>'
        "</div>"
        f'<div style="margin-left:auto">{_badge(settings.ui.badge_no_advice, "is-warn")}</div>'
        "</div>"
    )


def hero_card_html(settings: Any) -> str:
    """Render the intro card, with the informational notice inset as the wireframe shows it."""
    disclaimer = templates.UI_DISCLAIMER
    return _card(
        f'<div class="mf-title">{escape(settings.ui.hero_title)}</div>'
        f'<p class="mf-sub">{escape(settings.ui.hero_body)}</p>'
        f'<div class="mf-note">{escape(disclaimer)}</div>'
    )


def riskometer_html(level: str, levels: tuple[str, ...]) -> str:
    """Render the riskometer as a segmented bar plus its level in text.

    The level is spelled out because the bar is a colour gradient on its own: colour alone would
    fail WCAG 1.4.1 and would be unreadable to a colour-blind user, and the wireframe's own risk
    framing is a compliance statement rather than decoration.
    """
    reached = level in levels
    index = levels.index(level) if reached else -1
    segments = "".join(
        f'<span class="mf-risk-seg{" is-on" if reached and position <= index else ""}"></span>'
        for position in range(len(levels))
    )
    label = f"Riskometer: {level}" if reached else "Riskometer: not in the corpus"
    return (
        f'<div class="mf-sub" style="font-weight:600;color:var(--mf-on-surface)">{escape(label)}</div>'
        f'<div class="mf-risk" role="img" aria-label="{escape(label)}">{segments}</div>'
        f'<div class="mf-sub">{escape(" · ".join(levels))}</div>'
    )


def turn_html(result: Answer) -> str:
    """Render one bot turn as the wireframe's card, keyed on the answer's kind.

    A factual answer gets the citation card and the meta row. A refusal gets the compliance banner,
    because from the user's side "I will not answer that" and "that question is not in my sources"
    are the same event: the system declined, and the reason belongs on the card. Anything else — a
    greeting, which retrieved nothing and is not a decline — is plain text, because a compliance
    banner on "hello" would be a lie about what happened.
    """
    latency = result_latency_ms(result)
    scheme = result_scheme(result)
    meta = []
    if result.last_updated:
        meta.append(f"Updated: {result.last_updated}")
    if result.citation_source_id:
        meta.append(f"source {result.citation_source_id}")
    meta.append(f"generator {result.generator}")
    if latency is not None:
        meta.append(f"{latency} ms")
    meta_html = f'<div class="mf-meta">{" · ".join(escape(part) for part in meta)}</div>'
    body = (
        '<div class="mf-sub" style="font-size:14px;color:var(--mf-on-surface)">'
        f"{escape(result.text)}</div>"
    )

    if result.kind == "factual":
        badge = _badge("Source verified", "is-ok")
    elif result.kind in REFUSAL_KINDS:
        badge = _badge("Answer withheld", "is-error")
    else:
        badge = _badge("No source used", "is-warn")
    header = (
        '<div class="mf-card-head">'
        '<span class="mf-avatar" aria-hidden="true">MF</span>'
        f'<div><div class="mf-title">Assistant {badge}</div>'
        + (f'<div class="mf-sub">{escape(scheme)}</div>' if scheme else "")
        + "</div></div>"
    )

    if result.kind == "factual":
        cite = ""
        if result.citation_url:
            cite = (
                f'<a class="mf-cite" href="{escape(result.citation_url)}" target="_blank" '
                f'rel="noopener noreferrer"><span aria-hidden="true">&#128214;</span><span>'
                f'<span class="mf-sub" style="color:var(--mf-on-surface)">Source document</span>'
                f'<div class="mf-cite-loc">{escape(result.last_updated or "source record")}'
                f"</div></span></a>"
            )
        return _card(header + body + cite + meta_html, "is-answer")

    if result.kind not in REFUSAL_KINDS:
        return _card(header + body + meta_html)

    banner = (
        '<div class="mf-note" style="border-left-color:var(--mf-error-container)">'
        f'<strong>{escape(REFUSAL_HEADLINE)}</strong> {_badge(REFUSAL_BADGE, "is-error")}'
        f'<div style="margin-top:.4rem">{escape(result.text)}</div></div>'
    )
    footer = f'<div class="mf-sub">{REFUSAL_FOOTER}</div>'
    return _card(header + banner + meta_html + footer, "is-refusal")


def _sidebar(facts: dict[str, object], active_scheme: str) -> None:
    """Render the wireframe's right column: grounding context, sources, and index provenance.

    The wireframe's right column is a card stack about one scheme, and it is filled in from what
    the index actually holds: the schemes in the registry, the chunk count, the collection, the
    generator, the embedding model, and when the index was built. The scheme at the top is the one
    the last answer was about, so it is empty until the first question rather than hard-coded to
    whichever fund happens to be first in the registry.
    """
    settings = load_settings()
    warnings = facts.get("warnings") or []
    schemes = facts.get("schemes") or ()
    scheme_rows = "".join(
        f"<dt>{escape(str(name))}</dt><dd>indexed</dd>" for name in schemes
    ) or "<dt>schemes</dt><dd>0</dd>"

    st.sidebar.markdown(
        _card(
            f'<div class="mf-title">{escape(settings.ui.sidebar_scheme_title)}</div>'
            f'<div class="mf-sub" style="margin-top:.35rem">'
            f"{escape(active_scheme or 'Ask a question to see its source scheme')}</div>"
            f'<div class="mf-sub">{escape(str(facts.get("collection")))}</div>'
        ),
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        _card(
            f'<div class="mf-title">{escape(settings.ui.sidebar_factsheet_title)}</div>'
            f'<dl class="mf-rows" style="margin-top:.5rem">'
            f"<dt>chunks indexed</dt><dd>{int(facts.get('count', 0))}</dd>"
            f"<dt>schemes indexed</dt><dd>{int(facts.get('scheme_count', 0))}</dd>"
            f"<dt>generator</dt><dd>{escape(str(facts.get('provider')))}</dd>"
            f'<dt>model</dt><dd style="font-size:11px">{escape(str(facts.get("model_id")))}</dd>'
            f'<dt>built</dt><dd style="font-size:11px">{escape(str(facts.get("built_at")))}</dd>'
            f"{scheme_rows}</dl>"
        ),
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        _card(
            f'<div class="mf-title">{escape(settings.ui.sidebar_riskometer_title)}</div>'
            '<div class="mf-sub">Only shown when a retrieved document states it; the assistant '
            "never derives a risk level itself.</div>"
            + riskometer_html("not in the corpus", settings.ui.riskometer_levels)
        ),
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        _card(
            f'<div class="mf-title">{escape(settings.ui.sidebar_sources_title)}</div>'
            f'<div class="mf-src">{int(facts.get("scheme_count", 0))} schemes · '
            f"citations restricted to the source registry</div>"
        ),
        unsafe_allow_html=True,
    )
    if warnings:
        st.sidebar.warning("Build warnings: " + "; ".join(str(item) for item in warnings))


def _pii_warning(kinds: str) -> str:
    """Name the identifier kinds that were blocked, and nothing about their values (C2)."""
    return (
        f"That message was not sent: it contains {kinds}. Nothing typed here is stored, and the "
        f"assistant never needs an account number to answer a fund question. {settings_privacy()}"
    )


def settings_privacy() -> str:
    """Return the config's privacy note, kept here so the warning and the footer agree."""
    return load_settings().ui.privacy_note


def _missing_index_panel(detail: str) -> None:
    """Render the actionable panel for a missing or empty index, and nothing else."""
    settings = load_settings()
    st.title(settings.ui.index_missing_title)
    st.error(f"{settings.ui.index_missing_message} ({detail})", icon="🚨")
    st.code("python -m src.pipeline build", language="bash")
    st.caption("The demo runs entirely offline after that; no API key is needed.")


def _footer(settings: Any) -> None:
    """Render the compliance footer, which the wireframe puts below both columns."""
    links = " · ".join(escape(name) for name in settings.ui.footer_links)
    st.markdown(
        f'<div class="mf-foot">{escape(settings.ui.footer_disclaimer)}'
        f'<div style="margin-top:.35rem">{links}</div></div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    """Render the whole app, in the order the wireframe stacks it.

    The script asks, draws, and only then draws what depends on the answer. A chip click is
    reported to the run that drew it, so the buttons have to be drawn before the pending question
    is answered; but the chip labels, the hero and the sidebar's scheme all depend on the answer,
    so the run that produces one ends in a rerun. That rerun is invisible — Streamlit never paints
    the intermediate frame — and it is what lets a refusal swap the chip row in the same paint as
    the refusal itself rather than one interaction later.
    """
    settings = load_settings()
    st.set_page_config(
        page_title=settings.ui.title,
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    current = theme.resolve_theme(st.session_state.get("theme", theme.DEFAULT_THEME.value))
    st.markdown(theme.stylesheet(current), unsafe_allow_html=True)

    st.markdown(nav_html(settings), unsafe_allow_html=True)

    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("refused", False)
    st.session_state.setdefault("pending", "")

    try:
        facts = warm()
    except PipelineError as error:
        _missing_index_panel(error)
        return

    if int(facts.get("count", 0)) == 0:
        _missing_index_panel("The index was built but contains no chunks.")
        return

    st.markdown(header_card_html(settings), unsafe_allow_html=True)

    typed = st.chat_input(settings.ui.placeholder)
    if typed:
        st.session_state["pending"] = typed

    st.markdown(
        f'<div class="mf-sub" style="font-weight:600;margin-bottom:.4rem">'
        f'{escape(settings.ui.chip_row_label)}</div>',
        unsafe_allow_html=True,
    )
    chips = example_questions(bool(st.session_state["refused"]))
    columns = st.columns(len(chips))
    for column, question in zip(columns, chips, strict=True):
        if column.button(question, width="stretch"):
            st.session_state["pending"] = question
    st.caption(settings.ui.explore_all_label)

    pending = st.session_state["pending"]
    answered = False
    if pending:
        st.session_state["pending"] = ""
        kinds = pipeline.pii_hits(pending)
        if kinds:
            st.session_state["last_pii_kinds"] = kinds
        else:
            st.session_state["messages"].append({"role": "user", "text": pending})
            try:
                result = pipeline.answer(pending)
            except PipelineError as error:
                st.error(f"error: {error}")
            else:
                st.session_state["messages"].append({"role": "assistant", "answer": result})
                st.session_state["refused"] = result.kind != "factual"
                answered = True

    if st.session_state.get("last_pii_kinds"):
        st.warning(_pii_warning(", ".join(st.session_state["last_pii_kinds"])), icon="🚫")
        st.session_state["last_pii_kinds"] = []

    if answered:
        st.rerun()

    if not st.session_state["messages"]:
        st.markdown(hero_card_html(settings), unsafe_allow_html=True)

    st.markdown(
        '<div id="mf-thread" role="log" aria-label="Conversation" aria-live="polite">',
        unsafe_allow_html=True,
    )
    for message in st.session_state["messages"]:
        if message["role"] == "user":
            st.markdown(
                f'<div class="mf-user"><div class="mf-user-bubble">'
                f"{escape(message['text'])}</div></div>",
                unsafe_allow_html=True,
            )
            continue
        stored: Answer = message["answer"]
        st.markdown(turn_html(stored), unsafe_allow_html=True)
        with st.expander(sources_label(stored)):
            for row in source_rows(stored):
                st.markdown(
                    f"**{row['scheme']}** · {row['section']} · score {row['score']} "
                    f"· boost term: {row['matched']}"
                )
                st.caption(row["text"])
    st.markdown("</div>", unsafe_allow_html=True)

    st.caption(settings.ui.privacy_note)
    st.caption(settings.ui.enter_hint)

    active_scheme = ""
    for message in reversed(st.session_state["messages"]):
        if message["role"] == "assistant":
            active_scheme = result_scheme(message["answer"])
            break

    _sidebar(facts, active_scheme)

    toggled = st.sidebar.toggle(theme.toggle_label(current), value=current is Theme.DARK)
    chosen = theme.from_toggle(toggled)
    if chosen is not current:
        st.session_state["theme"] = chosen.value
        st.rerun()

    _footer(settings)


if __name__ == "__main__":
    main()
