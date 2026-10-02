"""Regressions for the local-run hardening pass.

Each test here pins a bug that was live in the app: a refused identifier being retained in the
transcript, the intent classifier being asked to label nothing on the first question, and a
citation URL that was read off a chunk rather than the registry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src import pii
from src.intent_router import _llm_classify
from src.models import SourceRecord, SourceType
from src.registry import Registry
from src.config import Settings, load_settings

APP = Path(__file__).resolve().parents[1] / "app.py"


def _app_source() -> str:
    return APP.read_text(encoding="utf-8")


def _pii_branch() -> ast.AST:
    """Return the AST of the `if pii_kinds:` branch, which is what must not touch the raw text."""
    module = ast.parse(_app_source())
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == "handle_user_input":
            for statement in ast.walk(node):
                if (
                    isinstance(statement, ast.If)
                    and isinstance(statement.test, ast.Name)
                    and statement.test.id == "pii_kinds"
                ):
                    return statement
    raise AssertionError("app.py has no `if pii_kinds:` guard in handle_user_input")


def test_pii_refusal_does_not_store_the_raw_message() -> None:
    """The refusal says "I won't store them", so the raw text must not reach session_state.

    Guards a bug where the PII branch appended `user_text` verbatim, leaving a PAN in the
    transcript for the rest of the session and contradicting the message shown to the user.
    """
    branch = _pii_branch()
    assert "redact" in ast.dump(branch), "the PII branch must redact before storing the turn"

    stored: list[ast.AST] = []
    for node in ast.walk(branch):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "text":
                stored.append(value)
    assert stored, "the PII branch should still record a turn, just not the raw one"
    for value in stored:
        assert not (isinstance(value, ast.Name) and value.id == "user_text"), (
            "the PII branch stores raw user_text as a message body"
        )


def test_pii_refusal_stores_a_redacted_turn() -> None:
    branch = ast.dump(_pii_branch())
    assert "redacted_user_text" in branch
    # The redacted turn is what keeps the transcript's turn structure intact.
    assert "messages" in branch



def test_redact_removes_the_value_not_just_labels_it() -> None:
    redacted, hits = pii.redact("PAN ABCDE1234F and call 9876543210")
    assert hits == 2
    assert "ABCDE1234F" not in redacted
    assert "9876543210" not in redacted
    assert "[REDACTED:PAN]" in redacted
    # A redacted turn must not trip detection again on the next turn.
    assert pii.detect(redacted) == []


def test_classifier_receives_the_question_when_there_is_no_history(monkeypatch) -> None:
    """A first inconclusive question must still be classified, not answered from the system prompt.

    Guards a bug where the current query was appended only if history already held a user turn, so
    with an empty history the classifier saw no question at all.
    """
    settings = _settings_with_llm_fallback(True)
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"intent": "definition", "confidence": 0.9, '
                            '"reasoning": "asks what a term means"}'
                        }
                    }
                ]
            }

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *args) -> bool:
            return False

        def post(self, url: str, json: dict | None = None, headers: dict | None = None) -> _Response:
            captured["payload"] = json
            return _Response()

    monkeypatch.setattr("src.intent_router.httpx.Client", _Client)
    monkeypatch.setattr(
        "src.intent_router.load_llm_env",
        lambda: type(
            "Env", (), {"api_key": "k", "base_url": "https://example.test", "model": "m"}
        )(),
    )

    result = _llm_classify("What is NAV?", [], settings)

    payload = captured.get("payload")
    assert payload is not None, "the classifier did not make a call at all"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1] == {"role": "user", "content": "What is NAV?"}
    assert result is not None


def test_classifier_appends_the_question_exactly_once() -> None:
    """Prior turns are context; the current question is the one being labelled.

    Guards against a double send now that the caller passes history without the current turn.
    """
    from src import intent_router

    source = Path(intent_router.__file__).read_text(encoding="utf-8")
    assert source.count('messages.append({"role": "user", "content": text})') == 1
    assert 'any(message["role"] == "user" for message in messages[1:])' not in source



def _settings_with_llm_fallback(enabled: bool) -> Settings:
    settings = load_settings()
    object.__setattr__(settings.intent_router, "llm_fallback", enabled)
    return settings


def test_citable_url_prefers_citation_url() -> None:
    source = SourceRecord(
        source_id="EDU01",
        scheme_id="",
        scheme_name="",
        source_type=SourceType.EDUCATION,
        title="What is NAV",
        url="file://data/raw/EDU01.md",
        publisher="AI summary of AMFI",
        allowed_for_citation=True,
        fetched_at="2026-01-15",
        citation_url="https://www.amfiindia.com/investor-corner/nav",
    )
    assert source.citable_url == "https://www.amfiindia.com/investor-corner/nav"


def test_citable_url_falls_back_to_url_when_absent() -> None:
    source = SourceRecord(
        source_id="S1",
        scheme_id="S1",
        scheme_name="Scheme",
        source_type=SourceType.SCHEME_PAGE,
        title="Scheme page",
        url="https://groww.in/mutual-funds/scheme",
        publisher="Groww",
        allowed_for_citation=True,
        fetched_at="2026-09-27",
    )
    assert source.citable_url == "https://groww.in/mutual-funds/scheme"


def test_a_local_path_is_never_a_citation() -> None:
    source = SourceRecord(
        source_id="EDU01",
        scheme_id="",
        scheme_name="",
        source_type=SourceType.EDUCATION,
        title="What is NAV",
        url="file://data/raw/EDU01.md",
        publisher="AI summary of AMFI",
        allowed_for_citation=True,
        fetched_at="2026-01-15",
        citation_url="https://www.amfiindia.com/investor-corner/nav",
    )
    registry = Registry(
        sources=(source,),
        schemes=(),
        csv_path=Path("data/sources.csv"),
        known_other_amcs=(),
        education_url="https://www.amfiindia.com/",
        help_url="https://groww.in/help",
        factsheet_index_url="",
    )
    assert registry.citation_url_for("EDU01") == source.citation_url
    assert registry.is_citation_allowed("file://data/raw/EDU01.md") is False


def test_suggestion_chips_are_real_widgets_not_raw_html() -> None:
    """The empty-state chips must be st.button widgets, never raw <button> markup.

    They were raw HTML in a st.markdown block with an inline <script> to forward the click into
    the chat input. Streamlit strips <script> tags out of markdown, so the pills rendered but
    nothing happened when they were clicked.
"""
    source = _app_source()
    assert "groww-chip" not in source, "chips must not be emitted as raw .groww-chip markup"
    assert "data-question" not in source, "chips must not rely on a data-question click relay"

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "markdown":
            continue
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            text = arg.value if isinstance(arg, ast.Constant) else None
            if isinstance(text, str):
                assert "<script" not in text, "markdown must not carry an inline script"


def test_suggestion_chips_route_through_the_shared_handler() -> None:
    """Every chip click must go through the same handle_user_input entry point."""
    tree = ast.parse(_app_source())
    fn = next(
        n for n in ast.walk(tree)
if isinstance(n, ast.FunctionDef) and n.name == "render_suggestion_chips"
    )
    called = {
        node.func.id for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    attrs = {
        node.attr for node in ast.walk(fn)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
    }
    assert "button" in attrs, "chips must be rendered with st.button"
    assert "handle_user_input" in called, "chip clicks must call handle_user_input"

    fields = {
        p.value.id
        for p in ast.walk(fn)
        if isinstance(p, ast.FormattedValue) and isinstance(p.value, ast.Name)
    }
    assert {"key_prefix", "index"} <= fields, (
        "chip key must combine a row prefix and the chip index so every chip is unique"
    )


def test_empty_state_chips_retire_once_a_conversation_starts() -> None:
    """Chips must stay behind the empty-state guard so they vanish after the first turn."""
    source = _app_source()
    guard = "if not st.session_state.messages:"
    assert guard in source
    guarded = source.split(guard, 1)[1]
    assert "render_suggestion_chips(" in guarded.split("st.chat_input", 1)[0], (
        "chips must render inside the empty-state guard and before the chat input"
    )
