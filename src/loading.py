"""Stage 1: fetch each registered source, strip its boilerplate, and redact it before disk.

Order matters here. The host allowlist is checked before a socket is opened (constraint C1).
Redaction happens before the text is written anywhere, so an unredacted identifier never
reaches a snapshot, a log, or the vector store (architecture.md §14.2). A snapshot already on
disk is never overwritten unless the caller explicitly asks, which is what makes a second
build identical to the first (architecture.md §7.4).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from src.config import Settings, load_settings
from src.models import (
    FactFamily,
    LoadedDoc,
    ParseEmptyError,
    SourceFetchError,
    SourceNotAllowed,
    SourceRecord,
    SourceType,
)
from src.pii import redact
from src.registry import Registry, load_registry

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

STRIP_SELECTORS: tuple[str, ...] = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "header",
    "footer",
    "nav",
    "title",
    "[role='navigation']",
    "[class*='cookie' i]",
    "[id*='consent' i]",
    "[class*='modal' i]",
    "[class*='popup' i]",
    "[class*='newsletter' i]",
    "[class*='breadcrumb' i]",
)

HEADING_TAGS: tuple[str, ...] = ("h1", "h2", "h3", "h4", "h5", "h6")
BLOCK_TAGS: tuple[str, ...] = ("p", "div", "section", "article", "main", "tr", "ul", "ol")

HEADING_LEVELS = {tag: index + 1 for index, tag in enumerate(HEADING_TAGS)}

FACT_TERMS: dict[FactFamily, tuple[str, ...]] = {
    FactFamily.EXPENSE_RATIO: ("expense ratio", "ter", "expense"),
    FactFamily.EXIT_LOAD: ("exit load", "exit charge", "exit load applicable"),
    FactFamily.MIN_SIP: (
        "minimum sip",
        "min. for sip",
        "sip investment",
        "minimum lumpsum",
        "lump sum",
    ),
    FactFamily.LOCK_IN: ("lock-in", "lock in", "80c"),
    FactFamily.RISKOMETER: ("riskometer", "rated very high", "risk rating", "high risk"),
    FactFamily.BENCHMARK: ("fund benchmark", "benchmark", "total return index"),
    FactFamily.STATEMENTS: ("statement", "tax report", "capital gains"),
}

_MANY_NEWLINES = re.compile(r"\n{3,}")

# A labelled NAV line is a performance figure (PRD C3) and is dropped at ingest rather than
# refused at query time. Only the label-and-value pair goes: neighbouring stat lines such as
# "Min. for SIP" are in scope and must survive.
PERFORMANCE_LINE = re.compile(r"^\s*NAV\s*:", re.IGNORECASE)
# The amount sits in a sibling node, so removing the label node alone orphans the figure and
# leaves a bare rupee value in the corpus with nothing to mark it as a NAV. The pair wrapper is
# the label node's grandparent, and it is short because it holds exactly a label and a value.
PERFORMANCE_PAIR_MAX_CHARS = 40
# Each page's own summary sentence repeats the NAV as prose ("... and the Latest NAV as of
# 25 Sep 2026 is 1,189.08."), which no selector can reach because it is page copy rather than a
# widget. The clause is removed whole so the sentence keeps its AUM fact and stays grammatical.
# Anchoring on "Latest NAV as of <date> is <amount>" is what makes this safe: a bare "NAV" token
# also appears in ordinary prose that carries no figure, and stripping on the token alone would
# silently delete sentences.
NAV_CLAUSE = re.compile(
    r",?\s*(?:and\s+)?the\s+Latest\s+NAV\s+as\s+of\s+[^.]*?\s+is\s+[₹\d][\d,.]*",
    re.IGNORECASE,
)
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_INLINE_SPACE = re.compile(r"[ \t]{2,}")
_NON_BREAKING = {
    0x00A0: " ",
    0x200B: "",
    0x200C: "",
    0x200D: "",
    0x202F: " ",
    0xFEFF: "",
}


def assert_url_allowed(url: str, settings: Settings) -> str:
    """Return the host of url, or raise SourceNotAllowed when it is not on the allowlist."""
    if not url.startswith("https://"):
        raise SourceNotAllowed(f"refusing non-https source URL: {url!r}")
    host = (urlsplit(url).hostname or "").lower()
    allowed = {item.lower() for item in settings.loading.allowed_hosts}
    if host not in allowed:
        raise SourceNotAllowed(
            f"host {host!r} is not in loading.allowed_hosts {sorted(allowed)} (constraint C1)"
        )
    return host


def fetch(url: str, settings: Settings, client: httpx.Client | None = None) -> str:
    """Return the raw HTML of url, retrying with backoff. Raises SourceFetchError on failure."""
    assert_url_allowed(url, settings)
    session = client or _new_client(settings)
    owned = client is None
    last_error = "no attempt was made"
    try:
        for attempt in range(settings.loading.retries):
            if attempt:
                time.sleep(min(2.0**attempt, 8.0))
            try:
                response = session.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}"
                continue
            return response.text
    finally:
        if owned:
            session.close()
    raise SourceFetchError(
        f"could not fetch {url} after {settings.loading.retries} attempts ({last_error})"
    )


def normalise(text: str) -> str:
    """Return text with non-breaking spaces, runs of spaces, and blank-line runs collapsed."""
    cleaned = soup_free_text(text)
    cleaned = _INLINE_SPACE.sub(" ", cleaned)
    cleaned = _TRAILING_SPACE.sub("\n", cleaned)
    cleaned = "\n".join(line.strip() for line in cleaned.split("\n"))
    return _MANY_NEWLINES.sub("\n\n", cleaned).strip()


def soup_free_text(text: str) -> str:
    """Return text with invisible Unicode formatting characters removed."""
    return text.translate(_NON_BREAKING)


def strip_performance_pairs(soup: BeautifulSoup) -> None:
    """Remove each labelled NAV figure together with its value, in place.

    The page renders every stat tile as a wrapper holding a label div and a value div, so a
    label-only removal deletes the words "NAV: 25 Sep '26" and leaves "1,189.08" behind. The
    wrapper is the label's grandparent; the length guard keeps the rule from reaching further up
    and deleting a whole tile group when a page ever inlines the label differently.
    """
    for node in list(soup.find_all(string=PERFORMANCE_LINE)):
        label_wrapper = node.parent
        pair = label_wrapper.parent if label_wrapper is not None else None
        if pair is None or label_wrapper.name not in {"p", "div", "span", "td"}:
            continue
        if len(pair.get_text(" ", strip=True)) > PERFORMANCE_PAIR_MAX_CHARS:
            continue
        pair.decompose()


def strip_nav_clauses(text: str) -> str:
    """Return text with each "Latest NAV as of <date> is <amount>" clause removed."""
    return NAV_CLAUSE.sub("", text)


def clean(html: str, settings: Settings, min_chars: int | None = None) -> str:
    """Return the readable text of html, or raise ParseEmptyError when too little survives.

    Headings, list items and table rows become markdown-ish lines because Phase 4 chunking
    depends on the `| a | b |` shape surviving into data/processed/. min_chars overrides the
    configured floor so a small synthetic fixture can be exercised without a giant page.
    """
    soup = BeautifulSoup(html, "lxml")
    for selector in STRIP_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    for needle in settings.loading.drop_class_substrings:
        for node in soup.find_all(class_=re.compile(re.escape(needle), re.IGNORECASE)):
            node.decompose()
    for tag in HEADING_TAGS:
        for node in soup.find_all(tag):
            heading = node.get_text(" ", strip=True)
            marker = f"\n\n{'#' * HEADING_LEVELS[tag]} {heading}\n\n" if heading else "\n\n"
            node.replace_with(marker)
    for node in soup.find_all("li"):
        item = node.get_text(" ", strip=True)
        node.replace_with(f"\n- {item}\n" if item else "\n")
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        row.replace_with("\n| " + " | ".join(cells) + " |\n" if cells else "\n")
    for tag in BLOCK_TAGS:
        for node in soup.find_all(tag):
            node.insert_before("\n")
    strip_performance_pairs(soup)
    for node in soup.find_all(string=PERFORMANCE_LINE):
        node.parent.decompose()
    for node in soup.find_all("br"):
        node.replace_with("\n")

    text = normalise(soup.get_text("\n"))
    text = strip_nav_clauses(text)

    threshold = settings.loading.min_extracted_chars if min_chars is None else min_chars
    if len(text) < threshold:
        raise ParseEmptyError(
            f"extracted only {len(text)} chars, below the configured minimum of {threshold}. "
            "The page is probably JS-rendered, blocked, or an error page; fix the source rather "
            "than lowering the threshold (implementation.md Phase 3)."
        )
    return text


def raw_snapshot_path(settings: Settings, source: SourceRecord) -> Path:
    """Return the snapshot path for a source, preferring .html and accepting a .md render."""
    if source.source_type is SourceType.SCHEME_PAGE:
        markdown = settings.paths.resolve("raw_dir") / f"{source.source_id}.md"
        if markdown.is_file():
            return markdown
    return settings.paths.resolve("raw_dir") / f"{source.source_id}.html"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _new_client(settings: Settings) -> httpx.Client:
    """Return an HTTP client with the browser user agent and the configured timeout."""
    return httpx.Client(
        timeout=settings.loading.timeout_s,
        follow_redirects=True,
        headers={"User-Agent": BROWSER_USER_AGENT},
    )


def _has_snapshot(settings: Settings, source: SourceRecord) -> bool:
    """Return True when a usable snapshot for this source is already on disk."""
    return settings.loading.offline_cache_first and raw_snapshot_path(settings, source).is_file()


def load_source(
    source: SourceRecord,
    settings: Settings,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> LoadedDoc:
    """Return one source fetched or read from snapshot, cleaned and redacted.

    The snapshot on disk wins over the network unless refresh is set, so an offline second run
    produces byte-identical output and a transient upstream outage cannot silently change the
    corpus that the evaluation numbers were measured against.
    """
    if source.source_type is SourceType.EDUCATION:
        raise SourceNotAllowed(
            f"source {source.source_id} is an education link: refusal links are rendered to the "
            "user and are never ingested (architecture.md §13.4)"
        )
    snapshot = raw_snapshot_path(settings, source)
    text_path = settings.paths.resolve("processed_dir") / f"{source.source_id}.txt"

    if not refresh and _has_snapshot(settings, source):
        raw = snapshot.read_text(encoding="utf-8", errors="replace")
    else:
        raw = fetch(source.url, settings, client or _new_client(settings))
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(raw, encoding="utf-8")

    extracted = normalise(raw) if snapshot.suffix == ".md" else clean(raw, settings)
    redacted, hits = redact(extracted)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(redacted, encoding="utf-8")

    return LoadedDoc(
        source=source,
        raw_path=str(snapshot),
        text_path=str(text_path),
        text=redacted,
        char_count=len(redacted),
        redaction_hits=hits,
    )


def load_all(
    registry: Registry,
    settings: Settings,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> tuple[list[LoadedDoc], list[str]]:
    """Return every ingestible source loaded, plus one warning per source that failed.

    A single dead source must not abort the build: the phase-3 DoD requires a processed file
    for all five schemes, and a later-stage warning is the honest way to report a shortfall.
    """
    docs: list[LoadedDoc] = []
    warnings: list[str] = []
    ingestible = [
        source for source in registry.sources if source.source_type is not SourceType.EDUCATION
    ]
    session = client
    created: list[httpx.Client] = []
    try:
        for index, source in enumerate(ingestible):
            needs_fetch = refresh or not _has_snapshot(settings, source)
            if index and needs_fetch:
                time.sleep(settings.loading.request_delay_s)
            if session is None and needs_fetch:
                session = _new_client(settings)
                created.append(session)
            try:
                docs.append(load_source(source, settings, session, refresh))
            except (SourceFetchError, SourceNotAllowed, ParseEmptyError) as exc:
                warnings.append(f"{source.source_id}: {type(exc).__name__}: {exc}")
    finally:
        for made in created:
            made.close()
    return docs, warnings


def assert_fact_coverage(
    docs: list[LoadedDoc], required: tuple[FactFamily, ...]
) -> list[str]:
    """Return a warning for each required fact family that no loaded document states.

    A family missing from every document is a corpus problem, not a runtime error, so this
    reports rather than raises. FactFamily.OTHER is skipped: it is the "no family matched"
    bucket used by intent classification, not a fact any document is expected to contain. The
    spike found lock-in and statements absent from all five pages, so those two are expected to
    be reported; the risk rating itself is present, which is why RISKOMETER is not.
    """
    corpus = "\n".join(doc.text.lower() for doc in docs)
    missing: list[str] = []
    for family in required:
        if family is FactFamily.OTHER:
            continue
        terms = FACT_TERMS.get(family, ())
        if not any(term in corpus for term in terms):
            missing.append(f"fact family {family.value} is absent from every loaded source")
    return missing


def main(argv: list[str] | None = None) -> int:
    """Load every registered scheme source and print a per-source status table."""
    parser = argparse.ArgumentParser(
        prog="python -m src.loading", description="Stage 1: fetch, clean and redact the corpus."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch even when a snapshot already exists (overwrites data/raw/)",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    registry = load_registry(settings)
    docs, warnings = load_all(registry, settings, refresh=args.refresh)
    by_id = {warning.split(":", 1)[0]: warning for warning in warnings}

    print(f"{'source_id':<10} {'status':<10} {'chars':>7} {'redactions':>11}  warnings")
    for doc in sorted(docs, key=lambda item: item.source.source_id):
        source_id = doc.source.source_id
        warning = by_id.get(source_id)
        status = "ok"
        note = ""
        if warning:
            status = "failed"
            note = warning.split(": ", 1)[-1]
        elif doc.redaction_hits:
            note = "pii_redacted"
        print(
            f"{source_id:<10} {status:<10} {doc.char_count:>7} {doc.redaction_hits:>11}  {note or '-'}"
        )
    for warning in warnings:
        if warning.split(":", 1)[0] not in {doc.source.source_id for doc in docs}:
            print(f"{warning.split(':', 1)[0]:<10} {'failed':<10} {'-':>7} {'-':>11}  {warning}")

    coverage = assert_fact_coverage(docs, tuple(FactFamily))
    for missing in coverage:
        print(f"coverage: {missing}")
    print(f"\n{len(docs)} source(s) processed, {len(warnings)} failure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
