"""Groww Mutual Fund Assistant — Streamlit UI."""

from __future__ import annotations

import html
import logging
import os
import random
import re
import traceback
from typing import Any

import streamlit as st

from src import pipeline
from src.config import load_settings
from src.models import Answer, PipelineError
from src.pipeline import pii_hits

EXAMPLE_QUESTIONS = (
    "What is the expense ratio of the HDFC Large Cap fund?",
    "What is the exit load on the HDFC Small Cap fund?",
    "What is the minimum SIP amount for the HDFC ELSS tax saver fund?",
)

EXAMPLE_QUESTIONS_AFTER_REFUSAL = (
    "What is the risk rating of the HDFC Flexi Cap fund?",
    "What is the benchmark of the HDFC Balanced Advantage fund?",
    "What is the minimum SIP amount for the HDFC Large Cap fund?",
)


def example_questions(refused: bool) -> tuple[str, ...]:
    """Return the three chip labels, swapped for factual ones after a refusal."""
    settings = load_settings()
    return (
        settings.ui.example_questions_after_refusal
        if refused
        else settings.ui.example_questions
    )


def escape(text: str) -> str:
    """Return text safe to place inside raw HTML."""
    return html.escape(text, quote=True)


def render_answer(result: Answer) -> list[dict[str, str]]:
    """Return what a bot turn should display, as an ordered list of drawing primitives."""
    from src.config import load_settings
    LINK_LABEL = load_settings().ui.link_label

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

DISCLAIMER = (
    "Facts only, not investment advice. Please don't share PAN, folio or bank details. "
    "Mutual fund investments are subject to market risks."
)

GREETING_TEMPLATES = [
    "Hi there! 👋 Great to see you. I can explain mutual fund basics and share facts about a few HDFC schemes. What would you like to know?",
    "Hello! I'm ready to help with HDFC mutual fund facts — expense ratios, exit loads, SIP minimums, lock-ins, and more. What's on your mind?",
    "Hey! 👋 Happy to assist. Ask me anything about the HDFC schemes I cover.",
    "Hi! I can share verified facts about HDFC mutual fund schemes. What would you like to learn?",
]

UNCLEAR_REPLIES = [
    "I want to make sure I give you the right answer. Could you clarify what you'd like to know? For example: \"What is the exit load for HDFC Small Cap?\" or \"What is NAV?\"",
    "Could you rephrase that? I can help with things like expense ratios, exit loads, minimum SIP amounts, lock-in periods, benchmarks, and risk ratings for HDFC funds.",
    "I'm not sure I caught that. Try asking about a specific fund fact (e.g., \"exit load of HDFC Flexi Cap\") or a general concept (e.g., \"what is NAV?\").",
]

UNCLEAR_EXAMPLES = (
    "What is the expense ratio of HDFC Large Cap?",
    "What is NAV?",
)


def load_css() -> str:
    """Load the compiled CSS from styles.css."""
    path = "styles.css"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f"<style>{f.read()}</style>"
    return ""


def top_bar() -> str:
    """Render the slim top bar with Groww logo and title."""
    return """
    <div class="groww-topbar">
        <span class="groww-logo" aria-label="Groww Home">
            <span class="groww-logo-mark" aria-hidden="true">G</span>
            <span class="groww-logo-text">Groww</span>
            <span class="groww-subtitle">Mutual Fund Assistant</span>
        </span>
    </div>
    """


def disclaimer() -> str:
    """Render the single disclaimer line."""
    return f'<div class="groww-disclaimer">{html.escape(DISCLAIMER)}</div>'


def debug_panel(facts: dict[str, Any]) -> str:
    """Render the debug panel with index stats (only when DEBUG=1)."""
    import json
    content = json.dumps(facts, indent=2, default=str)
    return f"""
    <details class="groww-debug-expander">
        <summary>Debug Info (index stats, model, build time, collection id)</summary>
        <div class="groww-debug-content">{html.escape(content)}</div>
    </details>
    """


def post_process_answer(text: str) -> str:
    """Capitalize first letter, ensure proper punctuation, trim."""
    text = text.strip()
    if not text:
        return text
    # Capitalize first letter
    text = text[0].upper() + text[1:] if len(text) > 1 else text.upper()
    # Ensure ends with punctuation
    if text[-1] not in ".!?":
        text += "."
    return text


def is_greeting_or_smalltalk(text: str) -> bool:
    """Quick check for greeting/smalltalk before calling pipeline."""
    lower = text.lower().strip()
    greeting_patterns = [
        r"^\s*(hi|hey|hello|thanks|thank you|bye|good morning|good evening)\b",
        r"\bwho are you\b",
        r"\bwhat can you do\b",
        r"\bhelp me\b",
    ]
    return any(re.search(p, lower, re.IGNORECASE) for p in greeting_patterns)


def is_unclear(text: str) -> bool:
    """Check if query is unclear (very short, vague)."""
    lower = text.lower().strip()
    # Single word or very short vague queries
    if len(lower.split()) <= 2 and lower in {"what", "why", "how", "when", "where", "who", "ok", "okay", "?", "tell me", "explain"}:
        return True
    return False


def generate_greeting_response() -> str:
    """Generate a warm, varied greeting response."""
    return random.choice(GREETING_TEMPLATES)


def generate_unclear_response() -> tuple[str, tuple[str, ...]]:
    """Generate a clarifying response with example chips."""
    reply = random.choice(UNCLEAR_REPLIES)
    return post_process_answer(reply), UNCLEAR_EXAMPLES


@st.cache_resource(show_spinner="Loading the index and the model…")
def warm_index() -> dict[str, Any]:
    """Load the registry, encoder, and collection once per session."""
    return pipeline.warm_index()


def handle_user_input(user_text: str) -> None:
    """Process a user message: append to history, generate assistant reply, append reply."""
    # Append user message immediately
    st.session_state.messages.append({"role": "user", "text": user_text})
    
    # Check for PII before calling pipeline
    pii_kinds = pii_hits(user_text)
    if pii_kinds:
        refusal_text = (
            "Please don't share personal identifiers like PAN, Aadhaar, account numbers, or OTPs — "
            "I won't store them. For account-specific help, use the official support channel: "
            "https://groww.in/help"
        )
        st.session_state.messages.append({
            "role": "assistant",
            "text": post_process_answer(refusal_text),
            "sources": [],
            "followups": []
        })
        return
    
    # Handle unclear questions before calling pipeline
    if is_unclear(user_text):
        reply, examples = generate_unclear_response()
        st.session_state.messages.append({
            "role": "assistant",
            "text": reply,
            "sources": [],
            "followups": list(examples)
        })
        return
    
    # Generate assistant response inside chat message with spinner
    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Thinking…"):
            try:
                # Build history for context (last 4 turns)
                history = []
                for msg in st.session_state.messages[-8:]:  # last 4 user+assistant pairs
                    if msg["role"] == "user":
                        history.append({"role": "user", "text": msg["text"]})
                    elif msg["role"] == "assistant" and "text" in msg:
                        history.append({"role": "assistant", "text": msg["text"]})
                
                result = pipeline.answer(user_text, history=history)
                
                # Post-process the answer text for tone
                processed_text = post_process_answer(result.text)
                
                # Extract follow-up suggestions based on answer kind
                followups = []
                if result.kind == "factual":
                    followups = [
                        "Tell me more about this scheme",
                        "What about another fund?",
                    ]
                elif result.kind in ("refusal", "performance_redirect", "out_of_corpus", "not_in_corpus"):
                    followups = list(EXAMPLE_QUESTIONS_AFTER_REFUSAL)[:2]
                
                st.session_state.messages.append({
                    "role": "assistant",
                    "text": processed_text,
                    "citation_url": result.citation_url,
                    "last_updated": result.last_updated,
                    "followups": followups
                })
            except Exception as exc:
                # Log full traceback to terminal
                logging.exception("Pipeline error: %s", exc)
                # Friendly error message in UI
                error_msg = (
                    "Sorry, something went wrong on my side. Please try again in a moment."
                )
                st.session_state.messages.append({
                    "role": "assistant",
                    "text": post_process_answer(error_msg),
                    "sources": [],
                    "followups": ["Try again", "Ask a different question"]
                })


def render_message(msg: dict[str, Any], is_latest: bool = False) -> None:
    """Render a single message from session state."""
    if msg["role"] == "user":
        with st.chat_message("user", avatar="👤"):
            st.markdown(msg["text"])
    else:
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(msg["text"])
            # Source line
            if msg.get("citation_url"):
                source_text = "Source document"
                if msg.get("last_updated"):
                    source_text += f" · Updated {msg['last_updated']}"
                st.markdown(
                    f'<div class="groww-source-line">'
                    f'<a href="{html.escape(msg["citation_url"], quote=True)}" target="_blank" rel="noopener noreferrer">'
                    f'{source_text}</a></div>',
                    unsafe_allow_html=True,
                )
            # Follow-up chips - ONLY for the latest assistant message
            if is_latest:
                followups = msg.get("followups", [])
                if followups:
                    cols = st.columns(len(followups))
                    for idx, followup in enumerate(followups):
                        if cols[idx].button(followup, key=f"followup_{len(st.session_state.messages)}_{idx}", use_container_width=True):
                            handle_user_input(followup)
                            st.rerun()


def main() -> None:
    settings = load_settings()
    st.set_page_config(
        page_title="Groww Mutual Fund Assistant",
        page_icon="📊",
        layout="centered",
        initial_sidebar_state="collapsed",
    )

    st.markdown(load_css(), unsafe_allow_html=True)
    st.markdown(top_bar(), unsafe_allow_html=True)

    debug_mode = os.environ.get("DEBUG") == "1" or st.query_params.get("debug") == "1"

    # Initialize session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "queued_prompt" not in st.session_state:
        st.session_state.queued_prompt = None

    # Consume queued prompt from chip clicks
    if st.session_state.queued_prompt:
        prompt = st.session_state.queued_prompt
        st.session_state.queued_prompt = None
        handle_user_input(prompt)
        st.rerun()

    try:
        facts = warm_index()
    except PipelineError as error:
        st.error(f"Failed to load index: {error}")
        st.markdown(disclaimer(), unsafe_allow_html=True)
        return

    if int(facts.get("count", 0)) == 0:
        st.error("The index was built but contains no chunks.")
        st.markdown(disclaimer(), unsafe_allow_html=True)
        return

    # Render all messages - only show follow-up chips for the latest assistant message
    for i, msg in enumerate(st.session_state.messages):
        is_latest = (i == len(st.session_state.messages) - 1)
        render_message(msg, is_latest=is_latest)

    # Empty state: show greeting and chips only when no messages
    if not st.session_state.messages:
        st.markdown("""
        <div class="groww-empty-state">
            <div class="groww-greeting">Ask me anything about HDFC mutual fund schemes</div>
            <div class="groww-subtext">I can share facts about expense ratios, exit loads, SIP minimums, lock-ins, and more.</div>
            <div class="groww-chips" id="groww-chips"></div>
        </div>
        """, unsafe_allow_html=True)
        
        # Render chips as Streamlit buttons
        cols = st.columns(3)
        for idx, question in enumerate(EXAMPLE_QUESTIONS):
            if cols[idx].button(question, key=f"chip_{idx}", use_container_width=True):
                st.session_state.queued_prompt = question
                st.rerun()

    st.markdown(disclaimer(), unsafe_allow_html=True)

    if debug_mode:
        st.markdown(debug_panel(facts), unsafe_allow_html=True)

    # Chat input
    typed = st.chat_input("Ask about expense ratio, exit load, minimum SIP…")
    if typed:
        handle_user_input(typed)
        st.rerun()


if __name__ == "__main__":
    main()