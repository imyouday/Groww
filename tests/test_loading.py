"""Loading stage tests: cleaning, the allowlist gate, redaction-before-disk, and idempotence.

The allowlist test monkeypatches httpx.Client so that any network call fails the test. That is
the point of the test: constraint C1 is only meaningful if the check provably happens before a
socket is opened, not after a response comes back.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.config import load_settings
from src.loading import (
    assert_fact_coverage,
    assert_url_allowed,
    clean,
    fetch,
    load_all,
    load_source,
)
from src.models import (
    FactFamily,
    ParseEmptyError,
    SourceFetchError,
    SourceNotAllowed,
    SourceRecord,
    SourceType,
)
from src.registry import load_registry

FIXTURE_HTML = """
<html><body>
  <header>Mutual Funds | Groww | Login</header>
  <h1>HDFC Large Cap Fund</h1>
  <h2>Fund Overview</h2>
  <p>Expense ratio is 1.03% for the direct growth plan.</p>
  <script>window.trackingId = "abc";</script>
  <style>.wrapper { color: red; }</style>
  <nav><a href="/x">Related funds</a><a href="/y">Tax</a></nav>
  <ul><li>Direct Growth</li><li>Direct Plan</li></ul>
  <table>
    <tr><th>Label</th><th>Value</th></tr>
    <tr><td>Exit Load</td><td>1%</td></tr>
    <tr><td>Min. for SIP</td><td>Rs 100</td></tr>
  </table>
  <div class="returnStats_tickerContainer__x">+8.71 % 3Y annualised</div>
  <div>NAV: 25 Sep '26</div>
  <p>Fund benchmark NIFTY 100 Total Return Index</p>
  <footer>Read more | Know more | Download app</footer>
</body></html>
"""


@pytest.fixture(scope="module")
def settings():
    """The repository's real settings."""
    return load_settings()


@pytest.fixture(scope="module")
def registry(settings):
    """The repository's real registry."""
    return load_registry(settings)


def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the inter-request and retry delays so failure-path tests do not idle for minutes."""
    monkeypatch.setattr("src.loading.time.sleep", lambda seconds: None)


def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any attempt to open an HTTP connection raise, so the test proves ordering."""

    class Tripwire(httpx.Client):
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("the network must not be reached in this test")

    monkeypatch.setattr(httpx, "Client", Tripwire)


@pytest.fixture(scope="module")
def corpus(settings, registry):
    """The real processed corpus, loaded once and reused by the assertion-only tests."""
    docs, _ = load_all(registry, settings)
    return docs


def test_clean_emits_heading_markers(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "# HDFC Large Cap Fund" in text
    assert "## Fund Overview" in text


def test_clean_emits_one_table_line_per_row(settings) -> None:
    rows = [line for line in clean(FIXTURE_HTML, settings, min_chars=50).splitlines() if line.startswith("|")]
    assert rows == [
        "| Label | Value |",
        "| Exit Load | 1% |",
        "| Min. for SIP | Rs 100 |",
    ]


def test_clean_emits_list_items_as_dashes(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "- Direct Growth" in text
    assert "- Direct Plan" in text


def test_clean_drops_script_and_style_content(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "window.trackingId" not in text
    assert "trackingId" not in text
    assert "color: red" not in text


def test_clean_drops_header_nav_and_footer_boilerplate(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "Login" not in text
    assert "Related funds" not in text
    assert "Download app" not in text


def test_clean_keeps_the_facts(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "Expense ratio is 1.03%" in text
    assert "NIFTY 100 Total Return Index" in text


def test_clean_drops_performance_figures(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "3Y annualised" not in text
    assert "+8.71" not in text
    assert "NAV: 25 Sep" not in text


def test_clean_raises_on_a_near_empty_page(settings) -> None:
    with pytest.raises(ParseEmptyError, match="extracted only"):
        clean("<html><body><p>hi</p></body></html>", settings)


def test_clean_raises_on_a_page_that_is_only_boilerplate(settings) -> None:
    html = "<html><body><header>" + ("nav junk " * 200) + "</header></body></html>"
    with pytest.raises(ParseEmptyError):
        clean(html, settings, min_chars=400)


def test_clean_collapses_runs_of_blank_lines(settings) -> None:
    html = "<html><body><p>a</p><br><br><br><p>b</p>" + ("<p>filler text</p>" * 60) + "</body></html>"
    text = clean(html, settings, min_chars=50)
    assert "\n\n\n" not in text


def test_clean_is_deterministic(settings) -> None:
    first = clean(FIXTURE_HTML, settings, min_chars=50)
    second = clean(FIXTURE_HTML, settings, min_chars=50)
    assert first == second


def test_url_on_a_non_allowlisted_host_is_refused_before_any_request(settings, monkeypatch) -> None:
    no_network(monkeypatch)
    with pytest.raises(SourceNotAllowed, match="not in loading.allowed_hosts"):
        assert_url_allowed("https://example.com/mutual-funds", settings)


def test_url_with_a_lookalike_host_is_refused(settings, monkeypatch) -> None:
    no_network(monkeypatch)
    with pytest.raises(SourceNotAllowed):
        assert_url_allowed("https://groww.in.evil.example/x", settings)


def test_plain_http_url_is_refused(settings, monkeypatch) -> None:
    no_network(monkeypatch)
    with pytest.raises(SourceNotAllowed, match="non-https"):
        assert_url_allowed("http://groww.in/mutual-funds/x", settings)


def test_fetch_refuses_a_disallowed_host_without_calling_the_client(settings, monkeypatch) -> None:
    no_network(monkeypatch)
    with pytest.raises(SourceNotAllowed):
        fetch("https://example.com/", settings)


def test_allowlisted_host_is_accepted(settings) -> None:
    assert assert_url_allowed("https://groww.in/mutual-funds/x", settings) == "groww.in"


def test_fetch_reports_http_errors_as_a_typed_error(settings, monkeypatch) -> None:
    no_sleep(monkeypatch)

    class Failing(httpx.Client):
        def get(self, url, **kwargs):
            raise httpx.ConnectError("no route to host")

    with pytest.raises(SourceFetchError, match="could not fetch"):
        fetch("https://groww.in/mutual-funds/x", settings, client=Failing())


def test_fetch_retries_then_gives_up(settings, monkeypatch) -> None:
    no_sleep(monkeypatch)
    attempts: list[str] = []

    class Counting(httpx.Client):
        def get(self, url, **kwargs):
            attempts.append(url)
            raise httpx.ConnectError("no route to host")

    with pytest.raises(SourceFetchError, match="ConnectError"):
        fetch("https://groww.in/mutual-funds/x", settings, client=Counting())
    assert len(attempts) == settings.loading.retries


def test_fetch_returns_the_body_on_success(settings) -> None:
    class Ok(httpx.Client):
        def get(self, url, **kwargs):
            return httpx.Response(200, text="<html>ok</html>")

    assert fetch("https://groww.in/x", settings, client=Ok()) == "<html>ok</html>"


def test_every_registered_citation_source_loads(settings, registry) -> None:
    docs, warnings = load_all(registry, settings)
    assert warnings == []
    assert [doc.source.source_id for doc in docs] == ["S1", "S2", "S3", "S4", "S5"]


def test_every_loaded_doc_has_a_processed_file(settings, registry) -> None:
    docs, _ = load_all(registry, settings)
    for doc in docs:
        assert Path(doc.text_path).is_file()
        assert doc.char_count > settings.loading.min_extracted_chars


def test_every_loaded_doc_states_the_fact_families_the_spike_found(corpus) -> None:
    present = {
        FactFamily.EXPENSE_RATIO,
        FactFamily.EXIT_LOAD,
        FactFamily.MIN_SIP,
        FactFamily.BENCHMARK,
        FactFamily.RISKOMETER,
    }
    assert assert_fact_coverage(corpus, tuple(present)) == []


def test_coverage_reports_the_families_the_spike_found_absent(corpus) -> None:
    missing = assert_fact_coverage(corpus, (FactFamily.LOCK_IN, FactFamily.STATEMENTS))
    assert len(missing) == 2
    assert "lock_in" in missing[0]
    assert "statements" in missing[1]


def test_coverage_ignores_the_other_bucket(corpus) -> None:
    assert assert_fact_coverage(corpus, (FactFamily.OTHER,)) == []


def test_processed_text_never_contains_a_pii_pattern(corpus) -> None:
    from src.pii import detect

    for doc in corpus:
        assert detect(doc.text) == [], f"{doc.source.source_id} still holds PII"


def test_raw_snapshot_is_not_overwritten_by_a_second_run(settings, registry, corpus) -> None:
    docs = corpus
    snapshot = Path(docs[0].raw_path)
    stamp = snapshot.stat().st_mtime_ns
    again, warnings = load_all(registry, settings)
    assert warnings == []
    assert snapshot.stat().st_mtime_ns == stamp
    assert again[0].text == docs[0].text


def test_second_run_makes_no_network_call(settings, registry, monkeypatch, corpus) -> None:
    no_network(monkeypatch)
    docs, warnings = load_all(registry, settings)
    assert warnings == []
    assert len(docs) == 5


def test_education_sources_are_never_ingested(settings, registry, corpus) -> None:
    education = [source for source in registry.sources if source.source_type is SourceType.EDUCATION]
    assert education
    assert {doc.source.source_id for doc in corpus}.isdisjoint(
        {source.source_id for source in education}
    )


def test_loading_an_education_source_directly_is_refused(settings, registry) -> None:
    education = next(
        source for source in registry.sources if source.source_type is SourceType.EDUCATION
    )
    with pytest.raises(SourceNotAllowed, match="never ingested"):
        load_source(education, settings)


def test_a_failing_source_becomes_a_warning_not_an_exception(settings, registry, monkeypatch) -> None:
    no_sleep(monkeypatch)

    class Failing(httpx.Client):
        def get(self, url, **kwargs):
            raise httpx.ConnectError("simulated outage")

    docs, warnings = load_all(registry, settings, client=Failing(), refresh=True)
    assert docs == []
    assert len(warnings) == 5
    for warning in warnings:
        assert warning.startswith("S")
        assert "SourceFetchError" in warning


def test_one_failing_source_does_not_stop_the_others(settings, registry, monkeypatch) -> None:
    no_sleep(monkeypatch)
    real_get = httpx.Client.get

    class FlakyOnS3(httpx.Client):
        def get(self, url, **kwargs):
            if "elss" in url:
                raise httpx.ConnectError("simulated outage on one source only")
            return real_get(self, url, **kwargs)

    docs, warnings = load_all(registry, settings, client=FlakyOnS3(), refresh=True)
    assert [doc.source.source_id for doc in docs] == ["S1", "S2", "S4", "S5"]
    assert len(warnings) == 1
    assert warnings[0].startswith("S3:")


def test_processed_corpus_has_no_performance_figures(corpus) -> None:
    banned = ("annualised", "annualized", "cagr", "xirr", "historic return", "nav:")
    for doc in corpus:
        lowered = doc.text.lower()
        for term in banned:
            assert term not in lowered, f"{doc.source.source_id} contains {term!r}"


def test_processed_corpus_has_no_raw_html(corpus) -> None:
    for doc in corpus:
        assert "<" not in doc.text
        assert ">" not in doc.text


def test_table_rows_survive_into_the_processed_file(corpus) -> None:
    for doc in corpus:
        rows = [line for line in doc.text.splitlines() if line.startswith("|")]
        assert len(rows) > 5, f"{doc.source.source_id} lost its tables"


def test_processed_corpus_still_states_every_in_scope_fact(corpus) -> None:
    for doc in corpus:
        lowered = doc.text.lower()
        assert "expense ratio" in lowered
        assert "exit load" in lowered
        assert "min. for sip" in lowered
        assert "fund benchmark" in lowered
