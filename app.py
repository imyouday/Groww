"""The Streamlit UI: one transcript, three chips, one citation per answer.

Two properties of this file are load-bearing rather than stylistic. First, it may import only
`src.pipeline`, `src.config`, `src.models`, `src.templates` and `src.theme` (architecture.md §5.2,
asserted by `tests/test_layering.py`), so the sidebar's index facts, the active generator and the
example chips all arrive as data from `src.pipeline` and `src.templates` instead of as imports of
the stages behind them. Second, the per-answer rendering is computed by `render_answer` and only
then drawn, so the "exactly one link per answer" rule (C5) is asserted in a test rather than
eyeballed in a browser.

`main()` is called under `if __name__ == "__main__"`, which is what `streamlit run app.py` does and
what a test import does not, so the module can be imported for its pure helpers without a running
Streamlit server.
"""

from __future__ import annotations

import streamlit as st

from src import pipeline, templates, theme
from src.config import load_settings
from src.models import Answer, PipelineError
from src.theme import Theme

LINK_LABEL = load_settings().ui.link_label


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


def _draw(entries: list[dict[str, str]]) -> None:
    """Draw the primitives that `render_answer` produced."""
    for entry in entries:
        if entry["kind"] == "markdown":
            st.markdown(entry["value"])
        elif entry["kind"] == "link_button":
            st.link_button(entry["label"], url=entry["value"])
        else:
            st.caption(entry["value"])


def _sidebar(facts: dict[str, object]) -> None:
    """Render the sidebar: index state, build facts, active generator, theme, and Clear chat."""
    with st.sidebar:
        st.header("Index state")
        st.metric("Chunks indexed", int(facts.get("count", 0)))
        st.caption(f"{facts.get('collection')} · {facts.get('space')} · built {facts.get('built_at')}")
        st.caption(f"model {facts.get('model_id')}")
        st.caption(f"generator {facts.get('provider')}")
        st.caption(f"{facts.get('scheme_count')} schemes in scope")
        warnings = facts.get("warnings") or []
        if warnings:
            st.warning("Build warnings: " + "; ".join(str(item) for item in warnings))
        st.divider()
        st.header("Appearance")
        current = theme.resolve_theme(st.session_state.get("theme", theme.DEFAULT_THEME.value))
        toggled = st.toggle(theme.toggle_label(current), value=current is Theme.DARK)
        chosen = Theme.LIGHT if toggled else Theme.DARK
        if chosen is not current:
            st.session_state["theme"] = chosen.value
            st.rerun()
        st.divider()
        if st.button("Clear chat", use_container_width=True):
            st.session_state["messages"] = []
            st.session_state["refused"] = False
            st.session_state["pending"] = ""
            st.rerun()


def _missing_index_panel() -> None:
    """Render the actionable panel for a missing index, and nothing else."""
    settings = load_settings()
    st.title(settings.ui.index_missing_title)
    st.error(settings.ui.index_missing_message, icon="🚨")
    st.code("python -m src.pipeline build", language="bash")
    st.caption("The demo runs entirely offline after that; no API key is needed.")


def main() -> None:
    """Render the whole app."""
    st.set_page_config(
        page_title="Mutual Fund FAQ Assistant",
        page_icon="📊",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    settings = load_settings()
    current = theme.resolve_theme(st.session_state.get("theme", theme.DEFAULT_THEME.value))
    st.markdown(theme.stylesheet(current), unsafe_allow_html=True)

    st.title(settings.ui.title)
    st.caption(settings.ui.scope_line)
    st.warning(templates.UI_DISCLAIMER, icon="⚠️")

    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("refused", False)
    st.session_state.setdefault("pending", "")

    try:
        facts = warm()
    except PipelineError as error:
        st.session_state["facts"] = {"count": 0, "error": str(error)}
        _missing_index_panel()
        return

    st.session_state["facts"] = facts
    if int(facts.get("count", 0)) == 0:
        _missing_index_panel()
        return

    chips = example_questions(bool(st.session_state["refused"]))
    for column, question in zip(st.columns(len(chips)), chips, strict=True):
        if column.button(question, use_container_width=True):
            st.session_state["pending"] = question

    typed = st.chat_input(settings.ui.placeholder)
    if typed:
        st.session_state["pending"] = typed

    pending = st.session_state["pending"]
    if pending:
        st.session_state["pending"] = ""
        st.session_state["messages"].append({"role": "user", "text": pending})
        try:
            result = pipeline.answer(pending)
        except PipelineError as error:
            st.error(f"error: {error}")
        else:
            st.session_state["messages"].append({"role": "assistant", "answer": result})
            st.session_state["refused"] = result.kind != "factual"

    for message in st.session_state["messages"]:
        if message["role"] == "user":
            with st.chat_message("user"):
                st.markdown(message["text"])
            continue
        stored: Answer = message["answer"]
        with st.chat_message("assistant"):
            _draw(render_answer(stored))
            with st.expander(sources_label(stored)):
                for row in source_rows(stored):
                    st.markdown(
                        f"**{row['scheme']}** · {row['section']} · score {row['score']} "
                        f"· boost term: {row['matched']}"
                    )
                    st.caption(row["text"])

    _sidebar(facts)
    st.markdown("---")
    st.caption("Facts-only. No investment advice. One source link per answer.")


if __name__ == "__main__":
    main()
