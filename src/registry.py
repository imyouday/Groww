"""Source registry: the allowlist that makes "public official sources only" enforceable.

Every citation the system can render must come from a row of data/sources.csv
(architecture.md §6.1, §11.3). Nothing here fetches, embeds, or ranks anything; this module
only answers "which schemes are in scope", "may I cite this URL", and "what is the official
link for this message". The layering test proves it depends on nothing from later stages.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from src.config import Settings, load_settings
from src.models import PipelineError, SourceRecord, SourceType

REQUIRED_HEADER: tuple[str, ...] = (
    "source_id",
    "scheme_id",
    "scheme_name",
    "source_type",
    "title",
    "url",
    "publisher",
    "allowed_for_citation",
    "fetched_at",
    "notes",
)

_TRUTHY = frozenset({"true", "yes", "1"})
_WORDISH = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class SchemeInfo:
    """One in-scope HDFC AMC scheme, resolved from the scheme-page rows of the registry."""

    scheme_id: str
    scheme_name: str
    aliases: tuple[str, ...]
    page_url: str


@dataclass(frozen=True)
class Registry:
    """Immutable view over data/sources.csv plus the alias map from config.yaml."""

    sources: tuple[SourceRecord, ...]
    schemes: tuple[SchemeInfo, ...]
    csv_path: Path
    known_other_amcs: tuple[str, ...]
    education_url: str
    help_url: str
    factsheet_index_url: str

    def _sources_by_id(self) -> dict[str, SourceRecord]:
        return {source.source_id: source for source in self.sources}

    def _allowed_urls(self) -> frozenset[str]:
        return frozenset(
            source.url for source in self.sources if source.allowed_for_citation
        )

    def _alias_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs = [
            (alias, scheme.scheme_id)
            for scheme in self.schemes
            for alias in scheme.aliases
        ]
        return tuple(sorted(pairs, key=lambda pair: (-len(pair[0]), pair[0])))

    def source_by_id(self, source_id: str) -> SourceRecord | None:
        """Return the registered source with this id, or None."""
        return self._sources_by_id().get(source_id)

    def source_by_url(self, url: str) -> SourceRecord | None:
        """Return the registered source whose URL is exactly this string, or None."""
        return next((source for source in self.sources if source.url == url), None)

    def is_citation_allowed(self, url: str) -> bool:
        """Return True only for an exact, full-string match against a citation-allowed URL.

        This is a full-string comparison on purpose (architecture.md §11.3). A prefix or
        host-suffix check would accept "https://groww.in/anything" or
        "https://groww.in.evil.example/x", which is how a citation allowlist turns into no
        allowlist at all.
        """
        return url in self._allowed_urls()

    def citation_url_for(self, source_id: str) -> str:
        """Return the citation URL for a source id, or the empty string if it may not be cited."""
        source = self.source_by_id(source_id)
        if source is None or not source.allowed_for_citation:
            return ""
        return source.url

    def scheme(self, scheme_id: str) -> SchemeInfo | None:
        """Return the in-scope scheme with this id, or None."""
        return next((item for item in self.schemes if item.scheme_id == scheme_id), None)

    def resolve_scheme(self, text: str) -> str | None:
        """Return the scheme_id whose alias appears in text, or None.

        Aliases are matched longest-first so that a specific alias such as "tax saver" is
        never shadowed by a shorter one, and matching is on word boundaries so that
        "capital" cannot resolve a "cap" alias out of an unrelated word.
        """
        haystack = " ".join(_WORDISH.findall(text.lower()))
        padded = f" {haystack} "
        for alias, scheme_id in self._alias_pairs():
            if f" {alias} " in padded:
                return scheme_id
        return None

    def mentions_other_amc(self, text: str) -> bool:
        """Return True when text names a mutual fund house outside this corpus."""
        haystack = " ".join(_WORDISH.findall(text.lower()))
        padded = f" {haystack} "
        return any(f" {amc} " in padded for amc in self.known_other_amcs)

    def scheme_names(self) -> tuple[str, ...]:
        """Return every in-scope scheme name, in registry order, for out-of-corpus copy."""
        return tuple(scheme.scheme_name for scheme in self.schemes)


def _parse_bool(value: str, source_id: str, field: str) -> bool:
    normalised = value.strip().lower()
    if normalised in _TRUTHY:
        return True
    if normalised in {"false", "no", "0"}:
        return False
    raise PipelineError(
        f"data/sources.csv: source {source_id} has {field}={value!r}, expected true or false"
    )


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the CSV and validate the header, which must match REQUIRED_HEADER exactly."""
    if not csv_path.is_file():
        raise PipelineError(
            f"source registry not found: {csv_path}. Run `python -m src.pipeline build` or "
            "restore data/sources.csv (architecture.md §6.1)."
        )
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        if header != REQUIRED_HEADER:
            raise PipelineError(
                f"{csv_path}: header must be exactly {','.join(REQUIRED_HEADER)}, got "
                f"{','.join(header) if header else '<empty file>'}"
            )
        return [row for row in reader if any((value or "").strip() for value in row.values())]


def urlparse_host(url: str) -> str | None:
    """Return the hostname of an https URL, or None when url is not a usable https URL."""
    if not url.startswith("https://"):
        return None
    host = urlsplit(url).hostname
    return host.lower() if host else None


def _parse_source(row: dict[str, str], csv_path: Path) -> SourceRecord:
    source_id = (row["source_id"] or "").strip()
    raw_type = (row["source_type"] or "").strip()
    try:
        source_type = SourceType(raw_type)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceType)
        raise PipelineError(
            f"{csv_path}: source {source_id or '<blank>'} has source_type={raw_type!r}; "
            f"expected one of {allowed}"
        ) from exc
    url = (row["url"] or "").strip()
    if urlparse_host(url) is None:
        raise PipelineError(
            f"{csv_path}: source {source_id} has url={url!r}, which is not an https:// URL "
            "(constraint C1: public https sources only)"
        )
    return SourceRecord(
        source_id=source_id,
        scheme_id=(row["scheme_id"] or "").strip(),
        scheme_name=(row["scheme_name"] or "").strip(),
        source_type=source_type,
        title=(row["title"] or "").strip(),
        url=url,
        publisher=(row["publisher"] or "").strip(),
        allowed_for_citation=_parse_bool(
            row["allowed_for_citation"] or "", source_id, "allowed_for_citation"
        ),
        fetched_at=(row["fetched_at"] or "").strip(),
        notes=(row["notes"] or "").strip(),
    )


def _build_registry(csv_path: Path, settings: Settings) -> Registry:
    """Validate every row, reject duplicate ids, and assemble the frozen registry."""
    rows = _read_rows(csv_path)
    if not rows:
        raise PipelineError(f"{csv_path}: contains a header but no source rows")

    sources: list[SourceRecord] = []
    seen_ids: set[str] = set()
    for row in rows:
        source = _parse_source(row, csv_path)
        if not source.source_id:
            raise PipelineError(f"{csv_path}: a row has an empty source_id")
        if source.source_id in seen_ids:
            raise PipelineError(
                f"{csv_path}: duplicate source_id {source.source_id!r}. A second row for the same "
                "source would make the citation allowlist ambiguous."
            )
        seen_ids.add(source.source_id)
        sources.append(source)

    sources.sort(key=lambda item: item.source_id)
    allowed_hosts = set(settings.loading.allowed_hosts)
    for source in sources:
        host = urlparse_host(source.url)
        if source.allowed_for_citation and host not in allowed_hosts:
            raise PipelineError(
                f"{csv_path}: citation-allowed source {source.source_id} points at host {host!r}, "
                f"which is not in loading.allowed_hosts {sorted(allowed_hosts)} (constraint C1)"
            )

    page_by_scheme: dict[str, SourceRecord] = {}
    for source in sources:
        if source.source_type is SourceType.SCHEME_PAGE:
            if source.scheme_id in page_by_scheme:
                raise PipelineError(
                    f"{csv_path}: scheme {source.scheme_id} has more than one scheme_page row "
                    f"({page_by_scheme[source.scheme_id].source_id} and {source.source_id}); "
                    "a scheme needs exactly one citable page"
                )
            page_by_scheme[source.scheme_id] = source

    aliases = settings.registry.scheme_aliases
    unknown = sorted(set(aliases) - set(page_by_scheme))
    if unknown:
        raise PipelineError(
            f"config key registry.scheme_aliases names scheme_id(s) {unknown} that have no "
            "scheme_page row in data/sources.csv"
        )

    schemes = tuple(
        SchemeInfo(
            scheme_id=source.scheme_id,
            scheme_name=source.scheme_name,
            aliases=aliases[source.scheme_id],
            page_url=source.url,
        )
        for source in sorted(page_by_scheme.values(), key=lambda item: item.scheme_id)
    )
    return Registry(
        sources=tuple(sources),
        schemes=schemes,
        csv_path=csv_path,
        known_other_amcs=tuple(settings.registry.known_other_amcs),
        education_url=settings.registry.education_url,
        help_url=settings.registry.help_url,
        factsheet_index_url=settings.registry.factsheet_index_url,
    )


@lru_cache(maxsize=4)
def _load_registry(csv_path: str, config_path: str) -> Registry:
    settings = load_settings(config_path)
    return _build_registry(Path(csv_path), settings)


def load_registry(
    settings: Settings | None = None, csv_path: Path | str | None = None
) -> Registry:
    """Return the validated registry for a sources CSV, loading and caching it on first use."""
    resolved_settings = settings or load_settings()
    resolved_csv = Path(csv_path) if csv_path is not None else resolved_settings.paths.resolve(
        "sources_csv"
    )
    return _load_registry(str(resolved_csv.resolve()), resolved_settings.source_path)
