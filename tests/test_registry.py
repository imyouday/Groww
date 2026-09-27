"""Registry tests: the allowlist, alias resolution, and strict CSV validation.

These are the tests that make constraint C1 ("public official sources only") and the
architecture §11.3 citation rule provable rather than aspirational.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from src.config import load_settings
from src.models import PipelineError
from src.registry import REQUIRED_HEADER, load_registry, urlparse_host

HEADER_LINE = ",".join(REQUIRED_HEADER)

ROW_S1 = [
    "S1",
    "S1",
    "HDFC Large Cap Fund",
    "scheme_page",
    "Large Cap",
    "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    "HDFC AMC (via Groww)",
    "true",
    "2026-09-27",
    "spike verified",
]
ROW_S3 = [
    "S3",
    "S3",
    "HDFC ELSS Tax Saver Fund",
    "scheme_page",
    "ELSS",
    "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
    "HDFC AMC (via Groww)",
    "true",
    "2026-09-27",
    "url corrected",
]
ROW_E1 = [
    "E1",
    "",
    "",
    "education",
    "AMFI",
    "https://www.amfiindia.com/",
    "AMFI",
    "false",
    "2026-09-27",
    "refusal link only",
]


def with_field(row: list[str], **changes: str) -> list[str]:
    """Return a copy of a row with the named columns replaced."""
    updated = list(row)
    for column, value in changes.items():
        updated[REQUIRED_HEADER.index(column)] = value
    return updated


def write_csv(
    tmp_path: Path, rows: list[list[str]], header: tuple[str, ...] = REQUIRED_HEADER
) -> Path:
    """Write a sources CSV with the given header and rows, then return its path."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path = tmp_path / "sources.csv"
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def registry():
    """The real registry built from the repository's data/sources.csv."""
    return load_registry()


def test_real_registry_loads_five_schemes_and_two_education_sources(registry) -> None:
    assert len(registry.sources) == 7
    assert [scheme.scheme_id for scheme in registry.schemes] == ["S1", "S2", "S3", "S4", "S5"]


def test_every_citation_allowed_url_is_https_and_on_the_fetch_allowlist(registry) -> None:
    allowed = set(load_settings().loading.allowed_hosts)
    for source in registry.sources:
        assert source.url.startswith("https://"), source.url
    for source in registry.sources:
        if source.allowed_for_citation:
            assert urlparse_host(source.url) in allowed, source.url


def test_education_sources_are_never_citationable(registry) -> None:
    education = [source for source in registry.sources if source.source_type.value == "education"]
    assert education
    for source in education:
        assert source.allowed_for_citation is False
        assert registry.citation_url_for(source.source_id) == ""
        assert registry.is_citation_allowed(source.url) is False


def test_rejects_wrong_header(tmp_path) -> None:
    path = write_csv(tmp_path, [ROW_S1], header=("source_id", "scheme_id", "url"))
    with pytest.raises(PipelineError, match="header must be exactly"):
        load_registry(csv_path=path)


def test_rejects_missing_header_column(tmp_path) -> None:
    path = write_csv(
        tmp_path, [ROW_S1], header=tuple(name for name in REQUIRED_HEADER if name != "notes")
    )
    with pytest.raises(PipelineError, match="header must be exactly"):
        load_registry(csv_path=path)


def test_rejects_reordered_header(tmp_path) -> None:
    path = write_csv(tmp_path, [ROW_S1], header=tuple(reversed(REQUIRED_HEADER)))
    with pytest.raises(PipelineError, match="header must be exactly"):
        load_registry(csv_path=path)


def test_rejects_duplicate_source_id(tmp_path) -> None:
    path = write_csv(tmp_path, [ROW_S1, ROW_S1, ROW_S3])
    with pytest.raises(PipelineError, match="duplicate source_id 'S1'"):
        load_registry(csv_path=path)


def test_rejects_two_scheme_pages_for_one_scheme(tmp_path) -> None:
    path = write_csv(
        tmp_path,
        [ROW_S1, with_field(ROW_S1, source_id="S1b", url="https://groww.in/other")],
    )
    with pytest.raises(PipelineError, match="more than one scheme_page row"):
        load_registry(csv_path=path)


def test_rejects_non_https_url(tmp_path) -> None:
    path = write_csv(tmp_path, [with_field(ROW_S1, url="http://groww.in/mutual-funds/x")])
    with pytest.raises(PipelineError, match="not an https"):
        load_registry(csv_path=path)


def test_rejects_citation_source_off_the_allowlist(tmp_path) -> None:
    path = write_csv(
        tmp_path, [with_field(ROW_S1, url="https://funds.example.in/hdfc-large-cap")]
    )
    with pytest.raises(PipelineError, match="not in loading.allowed_hosts"):
        load_registry(csv_path=path)


def test_rejects_unknown_source_type(tmp_path) -> None:
    path = write_csv(tmp_path, [with_field(ROW_S1, source_type="pdf")])
    with pytest.raises(PipelineError, match="source_type='pdf'"):
        load_registry(csv_path=path)


def test_rejects_non_boolean_citation_flag(tmp_path) -> None:
    path = write_csv(tmp_path, [with_field(ROW_S1, allowed_for_citation="maybe")])
    with pytest.raises(PipelineError, match="allowed_for_citation='maybe'"):
        load_registry(csv_path=path)


def test_rejects_alias_for_scheme_with_no_page(tmp_path) -> None:
    path = write_csv(tmp_path, [ROW_S1])
    with pytest.raises(PipelineError, match="scheme_aliases names scheme_id"):
        load_registry(csv_path=path)


def test_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(PipelineError, match="source registry not found"):
        load_registry(csv_path=tmp_path / "absent.csv")


def test_rejects_header_only_csv(tmp_path) -> None:
    with pytest.raises(PipelineError, match="no source rows"):
        load_registry(csv_path=write_csv(tmp_path, []))


def test_is_citation_allowed_accepts_only_exact_registry_urls(registry) -> None:
    assert registry.is_citation_allowed(registry.citation_url_for("S1")) is True
    assert registry.is_citation_allowed(registry.citation_url_for("S5")) is True


def test_is_citation_allowed_rejects_lookalike_host(registry) -> None:
    assert registry.is_citation_allowed("https://groww.in.evil.example/x") is False


def test_is_citation_allowed_rejects_same_host_other_path(registry) -> None:
    assert registry.is_citation_allowed("https://groww.in/mutual-funds/some-other-fund") is False


def test_is_citation_allowed_rejects_plain_http_variant(registry) -> None:
    secure = registry.citation_url_for("S1")
    assert registry.is_citation_allowed(secure.replace("https://", "http://")) is False


def test_is_citation_allowed_rejects_empty_and_none_like_input(registry) -> None:
    assert registry.is_citation_allowed("") is False
    assert registry.is_citation_allowed(registry.citation_url_for("S1") + "/") is False


def test_citation_url_for_unknown_source_is_empty(registry) -> None:
    assert registry.citation_url_for("S99") == ""
    assert registry.source_by_id("S99") is None


def test_source_by_url_is_exact(registry) -> None:
    assert registry.source_by_url(registry.citation_url_for("S2")).source_id == "S2"
    assert registry.source_by_url("https://groww.in/") is None
    assert registry.source_by_url("") is None


def test_resolve_scheme_maps_elss_aliases_to_s3(registry) -> None:
    assert registry.resolve_scheme("elss") == "S3"
    assert registry.resolve_scheme("tax saver fund") == "S3"
    assert registry.resolve_scheme("what is the exit load on the HDFC ELSS Tax Saver Fund?") == "S3"
    assert registry.resolve_scheme("Tax Saving Plan") == "S3"


def test_resolve_scheme_resolves_every_in_scope_scheme(registry) -> None:
    assert registry.resolve_scheme("expense ratio of the large cap fund") == "S1"
    assert registry.resolve_scheme("flexi cap") == "S2"
    assert registry.resolve_scheme("hdfc equity fund") == "S2"
    assert registry.resolve_scheme("small cap") == "S4"
    assert registry.resolve_scheme("balanced advantage") == "S5"


def test_resolve_scheme_prefers_the_longest_alias(registry) -> None:
    assert registry.resolve_scheme("balanced advantage fund") == "S5"
    assert registry.resolve_scheme("taxsaver") == "S3"
    assert registry.resolve_scheme("hdfc large cap fund") == "S1"


def test_resolve_scheme_does_not_match_inside_unrelated_words(registry) -> None:
    assert registry.resolve_scheme("what is the capital gain tax") is None
    assert registry.resolve_scheme("small businesses") is None
    assert registry.resolve_scheme("a balanced meal") is None


def test_resolve_scheme_returns_none_for_unknown_text(registry) -> None:
    assert registry.resolve_scheme("parag parflex") is None
    assert registry.resolve_scheme("") is None
    assert registry.resolve_scheme("who is the fund manager?") is None


def test_out_of_corpus_amc_is_detected_not_resolved(registry) -> None:
    assert registry.mentions_other_amc("tell me about parag parflex") is True
    assert registry.mentions_other_amc("compare hdfc large cap with icici funds") is True
    assert registry.mentions_other_amc("tell me about the large cap fund") is False


def test_refusal_links_are_present_and_distinct(registry) -> None:
    assert registry.education_url.startswith("https://")
    assert registry.help_url.startswith("https://")
    assert registry.education_url != registry.help_url
    assert registry.scheme_names()[0].startswith("HDFC Large Cap")
