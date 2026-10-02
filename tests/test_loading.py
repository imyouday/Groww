"""Loading stage tests: cleaning, the allowlist gate, redaction-before-disk, and idempotence.

The allowlist test monkeypatches httpx.Client so that any network call fails the test. That is
the point of the test: constraint C1 is only meaningful if the check provably happens before a
socket is opened, not after a response comes back.
"""

from __future__ import annotations

import re
from dataclasses import replace
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
<html>
  <head>
    <title>HDFC Large Cap Fund Direct Growth - NAV, Mutual Fund Performance &amp; Portfolio</title>
  </head>
  <body>
  <header>Mutual Funds | Groww | Login</header>
  <div class="loggedOut_navContainer__h99vu"><span>Stocks</span><span>F&amp;O</span></div>
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
  <div class="fundDetails_gap4__kM__Q">
    <div class="fundDetails_gap4__kM__Q">NAV: 25 Sep '26</div>
    <div>1,189.08</div>
  </div>
  <div class="fundDetails_gap4__kM__Q">
    <div class="fundDetails_gap4__kM__Q">Min. for SIP</div>
    <div>Rs 100</div>
  </div>
  <div class="fundDetails_gap4__kM__Q">
    <div class="fundDetails_gap4__kM__Q">Fund size (AUM)</div>
    <div>Rs 39,933.37 Cr</div>
  </div>
  <div class="mfGraph_buttonsContainer__AtbZS"><span>1M</span><span>6M</span><span>5Y</span><span>All</span></div>
  <div class="compareSimilarFunds_container__VCScJ">
    <h3>Compare similar funds</h3>
    <table>
      <tr><th>Name</th><th>1Y</th><th>3Y</th></tr>
      <tr><td>Invesco India Large Cap Fund Direct Growth</td><td>+2.51%</td><td>+13.80%</td></tr>
    </table>
  </div>
  <p>Fund benchmark NIFTY 100 Total Return Index</p>
  <div class="letterLinks_mb40__RN8GD">A<br>B<br>C</div>
  <div class="footerTopSection_gridContainer__UumRK">Brokerage and charges on Groww</div>
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
def scratch_settings(settings, tmp_path_factory):
    """Settings whose raw and processed directories are temporary, for tests that really fetch.

    A test that passes refresh=True overwrites data/raw/, and data/raw/ is a committed
    artefact: letting the suite rewrite it would make the corpus depend on test order and would
    dirty the working tree on every run.
    """
    root = tmp_path_factory.mktemp("scratch")
    return replace(
        settings,
        paths=replace(
            settings.paths,
            raw_dir=str(root / "raw"),
            processed_dir=str(root / "processed"),
        ),
    )


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


def test_clean_drops_the_nav_value_with_its_label(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "1,189.08" not in text


def test_clean_keeps_the_stat_tiles_beside_the_nav(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "Min. for SIP" in text
    assert "Fund size (AUM)" in text


def test_clean_drops_the_cross_fund_return_comparison(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "Compare similar funds" not in text
    assert "+13.80%" not in text


def test_clean_drops_the_nav_chart_range_buttons(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    for button in ("1M", "6M", "5Y", "All"):
        assert button not in text.split("\n")


def test_clean_drops_the_page_title(settings) -> None:
    text = clean(FIXTURE_HTML, settings, min_chars=50)
    assert "Mutual Fund Performance" not in text


def test_clean_removes_a_nav_clause_from_prose(settings) -> None:
    html = (
        "<html><body><p>The fund currently has an Asset Under Management(AUM) of "
        "&#8377;9,86,237 Cr and the Latest NAV as of 25 Sep 2026 is &#8377;1,189.08."
        "</p><p>Minimum SIP Investment is set to &#8377;100.</p></body></html>"
    )
    text = clean(html, settings, min_chars=50)
    assert "Latest NAV" not in text
    assert "1,189.08" not in text
    assert "Asset Under Management(AUM) of" in text
    assert "Minimum SIP Investment is set to" in text


def test_clean_keeps_prose_that_mentions_nav_without_a_figure(settings) -> None:
    html = (
        "<html><body><p>" + ("The NAV is disclosed daily on the AMC website. " * 12) + "</p></body></html>"
    )
    text = clean(html, settings, min_chars=50)
    assert "NAV is disclosed daily" in text


def test_clean_drops_site_chrome_that_is_not_markup_chrome(settings) -> None:
    html = (
        "<html><body>"
        "<div class='loggedOut_navContainer__h99vu'><span>Stocks</span><span>F&amp;O</span></div>"
        "<div class='letterLinks_mb40__RN8GD'>A<br>B<br>C<br>D</div>"
        "<div class='footerTopSection_gridContainer__UumRK'>Brokerage and charges</div>"
        "<p>Expense ratio is 1.03%</p>"
        "</body></html>"
    )
    text = clean(html, settings, min_chars=20)
    assert "Stocks" not in text
    assert "Brokerage and charges" not in text
    assert "Expense ratio is 1.03%" in text


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
    # Citationable now means "the 5 scheme pages plus the 19 education summaries", each of which is
    # ingested from a file:// path or a scheme page and cited at its own public URL.
    citable = [doc.source.source_id for doc in docs if doc.source.allowed_for_citation]
    assert citable == [f"EDU{index:02d}" for index in range(1, 20)] + ["S1", "S2", "S3", "S4", "S5"]


def test_every_citation_source_resolves_to_an_https_citation(settings, registry) -> None:
    docs, _ = load_all(registry, settings)
    for doc in docs:
        if not doc.source.allowed_for_citation:
            continue
        citation = registry.citation_url_for(doc.source.source_id)
        assert citation.startswith("https://"), doc.source.source_id
        assert registry.is_citation_allowed(citation) is True



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
    # LOCK_IN and STATEMENTS are now covered by education content
    missing = assert_fact_coverage(corpus, (FactFamily.LOCK_IN, FactFamily.STATEMENTS))
    assert missing == []


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
    # 5 scheme sources + 19 education content sources = 24
    assert len(docs) == 24


def test_education_content_sources_are_ingested(settings, registry, corpus) -> None:
    """Education content sources (EDU01-EDU19) ARE ingested for definitions."""
    edu_content = [source for source in registry.sources if source.source_id.startswith("EDU")]
    assert len(edu_content) == 19
    ingested_ids = {doc.source.source_id for doc in corpus}
    for source in edu_content:
        assert source.source_id in ingested_ids, f"Education content source {source.source_id} should be ingested"


def test_refusal_link_education_sources_are_never_ingested(settings, registry, corpus) -> None:
    """Refusal link education sources (E1, E2) are NOT ingested - only used for refusal links."""
    refusal_links = [source for source in registry.sources if source.source_id in {"E1", "E2"}]
    assert len(refusal_links) == 2
    ingested_ids = {doc.source.source_id for doc in corpus}
    for source in refusal_links:
        assert source.source_id not in ingested_ids, f"Refusal link source {source.source_id} should NOT be ingested"


def test_loading_a_refusal_link_education_source_directly_is_refused(settings, registry) -> None:
    refusal_link = next(
        source for source in registry.sources if source.source_id in {"E1", "E2"}
    )
    with pytest.raises(SourceNotAllowed, match="refusal links are rendered to the user and are never ingested"):
        load_source(refusal_link, settings)


def test_a_failing_source_becomes_a_warning_not_an_exception(settings, registry, monkeypatch) -> None:
    no_sleep(monkeypatch)

    class Failing(httpx.Client):
        def get(self, url, **kwargs):
            raise httpx.ConnectError("simulated outage")

    docs, warnings = load_all(registry, settings, client=Failing(), refresh=True)
    # Only HTTP scheme sources (S1-S5) fail; E1/E2 are not ingested; local education files (19) still load
    http_sources = {"S1", "S2", "S3", "S4", "S5"}
    failed_ids = {w.split(":")[0] for w in warnings}
    assert failed_ids == http_sources
    loaded_ids = {doc.source.source_id for doc in docs}
    # Load all sources normally to get expected EDU sources
    reg = load_registry(load_settings())
    all_docs, _ = load_all(reg, load_settings())
    expected_edu = {doc.source.source_id for doc in all_docs if doc.source.source_id.startswith("EDU")}
    assert loaded_ids == expected_edu


def test_one_failing_source_does_not_stop_the_others(
    scratch_settings, registry, monkeypatch
) -> None:
    no_sleep(monkeypatch)
    real_get = httpx.Client.get

    class FlakyOnS3(httpx.Client):
        def get(self, url, **kwargs):
            if "elss" in url:
                raise httpx.ConnectError("simulated outage on one source only")
            return real_get(self, url, **kwargs)

    committed = {path: path.stat().st_mtime_ns for path in sorted(Path("data/raw").glob("S*.html"))}
    docs, warnings = load_all(registry, scratch_settings, client=FlakyOnS3(), refresh=True)
    # Should load all sources except S3 (which fails)
    loaded_ids = {doc.source.source_id for doc in docs}
    assert "S3" not in loaded_ids
    assert len(loaded_ids) == 23  # 24 total - 1 failed
    assert len(warnings) == 1
    assert warnings[0].startswith("S3:")
    assert {path: path.stat().st_mtime_ns for path in committed} == committed


def test_a_refresh_run_does_not_touch_the_committed_snapshots(
    scratch_settings, registry, settings
) -> None:
    before = {
        path: path.stat().st_mtime_ns
        for path in sorted(settings.paths.resolve("raw_dir").glob("S*.html"))
    }
    load_all(registry, scratch_settings, refresh=True)
    after = {
        path: path.stat().st_mtime_ns
        for path in sorted(settings.paths.resolve("raw_dir").glob("S*.html"))
    }
    assert before == after


def test_processed_corpus_has_no_performance_figures(corpus) -> None:
    banned = (
        "annualised",
        "annualized",
        "cagr",
        "xirr",
        "historic return",
        "nav:",
        "latest nav",
        "nav history",
        "since inception",
        "compare similar funds",
        "return calculator",
        "returns and rankings",
    )
    for doc in corpus:
        # Only check scheme documents; education content may define terms
        if doc.source.source_id.startswith("S"):
            lowered = doc.text.lower()
            for term in banned:
                assert term not in lowered, f"{doc.source.source_id} contains {term!r}"


def test_processed_corpus_carries_no_bare_nav_figure(corpus) -> None:
    amount = re.compile(r"₹\d[\d,]*\.\d\d")
    label = re.compile(r"nav|aum|expense|exit load|min\.|fund size|rating", re.IGNORECASE)
    for doc in corpus:
        # Only check scheme documents
        if doc.source.source_id.startswith("S"):
            previous = ""
            for line in doc.text.splitlines():
                stripped = line.strip()
                if amount.search(stripped) and not label.search(stripped) and not label.search(previous):
                    assert stripped.startswith("|"), (
                        f"{doc.source.source_id} has an unlabelled amount line: {stripped!r}"
                    )
                if stripped:
                    previous = stripped


def test_processed_corpus_has_no_raw_html(corpus) -> None:
    for doc in corpus:
        # Only check scheme documents; education content may have markdown syntax
        if doc.source.source_id.startswith("S"):
            assert "<" not in doc.text
            assert ">" not in doc.text


def test_table_rows_survive_into_the_processed_file(corpus) -> None:
    for doc in corpus:
        if doc.source.source_id.startswith("S"):
            rows = [line for line in doc.text.splitlines() if line.startswith("|")]
            assert len(rows) > 5, f"{doc.source.source_id} lost its tables"


def test_processed_corpus_still_states_every_in_scope_fact(corpus) -> None:
    for doc in corpus:
        if doc.source.source_id.startswith("S"):
            lowered = doc.text.lower()
            assert "expense ratio" in lowered
            assert "exit load" in lowered
            assert "min. for sip" in lowered
            assert "fund benchmark" in lowered
