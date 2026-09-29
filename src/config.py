"""Typed, validated configuration for every pipeline stage.

Settings mirror config.yaml one-to-one as frozen dataclasses, so a stage can only read values
that were declared and checked at load time (architecture.md §5.2, §18.2, §20).

All sequences are tuples rather than lists so a Settings instance cannot be mutated after
validation. The `copy` section is the single source of truth for user-visible strings
(architecture.md §20 closing note), which src/templates.py re-exports as module constants.

This module is infrastructure: it imports no other module in the project.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import Field, asdict, dataclass, fields
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from src.models import PipelineError

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ChunkingStrategy(str, Enum):
    SEMANTIC_SECTION = "semantic_section"
    SEMANTIC_350 = "semantic_350"
    SEMANTIC_600 = "semantic_600"
    FIXED_512 = "fixed_512"


class Provider(str, Enum):
    AUTO = "auto"
    LLM = "llm"
    EXTRACTIVE = "extractive"


@dataclass(frozen=True)
class PathsSettings:
    raw_dir: str
    processed_dir: str
    chroma_dir: str
    chunks_dump: str
    vectors_dump: str
    sources_csv: str
    model_cache_dir: str

    def resolve(self, key: str) -> Path:
        """Return a config-declared path resolved against the repository root, never the CWD."""
        return REPO_ROOT / getattr(self, key)


@dataclass(frozen=True)
class EmbeddingSettings:
    model_id: str
    batch_size: int
    device: str
    normalize: bool


@dataclass(frozen=True)
class ChunkingSettings:
    strategy: str
    max_tokens: int
    min_tokens: int
    overlap_tokens: int
    preserve_tables: bool
    include_context_header: bool
    drop_boilerplate: bool
    merge_small_sections: bool


@dataclass(frozen=True)
class ChromaSettings:
    collection_name: str
    space: str
    description: str


@dataclass(frozen=True)
class BoostSettings:
    fact_term: float
    additional_fact_term: float
    section_type_match: float
    scheme_match: float


@dataclass(frozen=True)
class RetrievalSettings:
    dense_k: int
    top_n: int
    mmr_lambda: float
    context_token_budget: int
    gate_threshold: float
    unclassified_gate_margin: float
    require_term_coverage: bool
    scheme_filter: bool
    boosts: BoostSettings
    fact_terms: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class GenerationSettings:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    timeout_s: float
    retries: int


@dataclass(frozen=True)
class LoadingSettings:
    offline_cache_first: bool
    request_delay_s: float
    timeout_s: int
    retries: int
    min_extracted_chars: int
    allowed_hosts: tuple[str, ...]
    drop_class_substrings: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardrailsSettings:
    max_sentences: int
    enforce_numeric_grounding: bool
    banned_terms: tuple[str, ...]


@dataclass(frozen=True)
class RegistrySettings:
    known_other_amcs: tuple[str, ...]
    scheme_aliases: dict[str, tuple[str, ...]]
    education_url: str
    help_url: str
    factsheet_index_url: str


@dataclass(frozen=True)
class CopySettings:
    ui_disclaimer: str
    refusal_message: str
    performance_redirect: str
    pii_refusal: str
    out_of_corpus_message: str
    not_in_corpus_message: str
    smalltalk_message: str
    greeting_templates: tuple[str, ...]
    unclear_replies: tuple[str, ...]
    unclear_examples: tuple[str, ...]


@dataclass(frozen=True)
class UiSettings:
    title: str
    scope_line: str
    hero_title: str
    hero_body: str
    badge_verified: str
    badge_no_advice: str
    placeholder: str
    link_label: str
    sources_label: str
    index_missing_title: str
    index_missing_message: str
    nav_links: tuple[str, ...]
    nav_active_link: str
    breadcrumb: tuple[str, ...]
    footer_disclaimer: str
    footer_links: tuple[str, ...]
    privacy_note: str
    chip_row_label: str
    example_questions: tuple[str, ...]
    example_questions_after_refusal: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    paths: PathsSettings
    embedding: EmbeddingSettings
    chunking: ChunkingSettings
    chroma: ChromaSettings
    retrieval: RetrievalSettings
    generation: GenerationSettings
    loading: LoadingSettings
    guardrails: GuardrailsSettings
    registry: RegistrySettings
    copy: CopySettings
    ui: UiSettings
    source_path: str = ""


_SECTIONS: dict[str, type] = {
    "paths": PathsSettings,
    "embedding": EmbeddingSettings,
    "chunking": ChunkingSettings,
    "chroma": ChromaSettings,
    "retrieval": RetrievalSettings,
    "generation": GenerationSettings,
    "loading": LoadingSettings,
    "guardrails": GuardrailsSettings,
    "registry": RegistrySettings,
    "copy": CopySettings,
    "ui": UiSettings,
}

_ALLOWED_CHUNKING_STRATEGIES = frozenset(strategy.value for strategy in ChunkingStrategy)
_ALLOWED_PROVIDERS = frozenset(provider.value for provider in Provider)
_UNIT_INTERVAL_KEYS = (
    ("retrieval", "gate_threshold"),
    ("retrieval", "mmr_lambda"),
    ("retrieval", "unclassified_gate_margin"),
    ("retrieval", "boosts", "fact_term"),
    ("retrieval", "boosts", "additional_fact_term"),
    ("retrieval", "boosts", "section_type_match"),
    ("retrieval", "boosts", "scheme_match"),
)


def _as_str_tuple(value: Any, key_path: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise PipelineError(f"config key {key_path}: expected a list of strings, got {value!r}")
    return tuple(str(item) for item in value)


def _as_alias_map(value: Any) -> dict[str, tuple[str, ...]]:
    """Coerce registry.scheme_aliases into scheme_id -> ordered alias tuple."""
    key_path = "registry.scheme_aliases"
    if not isinstance(value, Mapping):
        raise PipelineError(f"config key {key_path}: expected a mapping of scheme_id to aliases")
    aliases: dict[str, tuple[str, ...]] = {}
    for scheme_id, terms in value.items():
        lowered = _as_str_tuple(terms, f"{key_path}.{scheme_id}")
        if any(not term.strip() for term in lowered):
            raise PipelineError(f"config key {key_path}.{scheme_id}: aliases must not be blank")
        aliases[str(scheme_id)] = tuple(term.strip().lower() for term in lowered)
    return aliases


def _required_keys(raw: Mapping[str, Any]) -> list[str]:
    """Return every required key path absent from the loaded YAML mapping."""
    missing: list[str] = []
    for section_name, section_cls in _SECTIONS.items():
        section = raw.get(section_name)
        if section is None:
            missing.append(section_name)
            continue
        if not isinstance(section, Mapping):
            missing.append(f"{section_name}: expected a mapping, got {type(section).__name__}")
            continue
        for f in fields(section_cls):
            if f.name not in section:
                missing.append(f"{section_name}.{f.name}")
        if section_name == "retrieval" and isinstance(section.get("boosts"), Mapping):
            for f in fields(BoostSettings):
                if f.name not in section["boosts"]:
                    missing.append(f"retrieval.boosts.{f.name}")
    return missing

def _build_sections(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Construct every nested settings section, raising a precise error on any bad value."""
    retrieval_raw = raw["retrieval"]
    fact_terms_raw = retrieval_raw["fact_terms"]
    if not isinstance(fact_terms_raw, Mapping):
        raise PipelineError("config key retrieval.fact_terms: expected a mapping of FactFamily to terms")
    boosts_raw = retrieval_raw["boosts"]

    return {
        "paths": PathsSettings(**{f.name: raw["paths"][f.name] for f in fields(PathsSettings)}),
        "embedding": EmbeddingSettings(
            **{f.name: raw["embedding"][f.name] for f in fields(EmbeddingSettings)}
        ),
        "chunking": ChunkingSettings(
            **{f.name: raw["chunking"][f.name] for f in fields(ChunkingSettings)}
        ),
        "chroma": ChromaSettings(**{f.name: raw["chroma"][f.name] for f in fields(ChromaSettings)}),
        "retrieval": RetrievalSettings(
            **{
                f.name: retrieval_raw[f.name]
                for f in fields(RetrievalSettings)
                if f.name not in {"boosts", "fact_terms"}
            },
            boosts=BoostSettings(**{f.name: boosts_raw[f.name] for f in fields(BoostSettings)}),
            fact_terms={
                str(family): _as_str_tuple(terms, f"retrieval.fact_terms.{family}")
                for family, terms in fact_terms_raw.items()
            },
        ),
        "generation": GenerationSettings(
            **{f.name: raw["generation"][f.name] for f in fields(GenerationSettings)}
        ),
        "loading": LoadingSettings(
            **{
                f.name: (
                    _as_str_tuple(raw["loading"][f.name], f"loading.{f.name}")
                    if f.name in {"allowed_hosts", "drop_class_substrings"}
                    else raw["loading"][f.name]
                )
                for f in fields(LoadingSettings)
            }
        ),
        "guardrails": GuardrailsSettings(
            **{
                f.name: (
                    _as_str_tuple(raw["guardrails"][f.name], f"guardrails.{f.name}")
                    if f.name == "banned_terms"
                    else raw["guardrails"][f.name]
                )
                for f in fields(GuardrailsSettings)
            }
        ),
        "registry": RegistrySettings(
            known_other_amcs=_as_str_tuple(
                raw["registry"]["known_other_amcs"], "registry.known_other_amcs"
            ),
            scheme_aliases=_as_alias_map(raw["registry"]["scheme_aliases"]),
            education_url=str(raw["registry"]["education_url"]),
            help_url=str(raw["registry"]["help_url"]),
            factsheet_index_url=str(raw["registry"]["factsheet_index_url"]),
        ),
        "copy": CopySettings(
            **{
                f.name: (
                    _as_str_tuple(raw["copy"][f.name], f"copy.{f.name}")
                    if f.name in {"greeting_templates", "unclear_replies", "unclear_examples"}
                    else str(raw["copy"][f.name])
                )
                for f in fields(CopySettings)
            },
        ),
        "ui": UiSettings(**{f.name: _ui_value(f, raw["ui"]) for f in fields(UiSettings)}),
    }


def _ui_value(field: Field, raw_section: dict[str, Any]) -> Any:
    """Coerce one `ui` value by its declared type, so a new list field cannot become a string.

    The `ui` section is a mix of scalars and string lists, and this was a list of names checked by
    hand. A name-based rule reads as harmless and then quietly hands the UI the literal text
    "['a', 'b']" to iterate character by character, which is how the nav ended up rendering one
    link per letter of its own config. Driving the coercion from the annotation means the next list
    field works the moment it is declared.
    """
    value = raw_section[field.name]
    if field.type in ("tuple[str, ...]", tuple[str, ...]):
        return _as_str_tuple(value, f"ui.{field.name}")
    return str(value)


def _lookup(settings: Settings, key_path: tuple[str, ...]) -> Any:
    value: Any = settings
    for part in key_path:
        value = getattr(value, part)
    return value


def _validate(settings: Settings) -> None:
    """Range and enum checks that a key-presence walk cannot express."""
    chunking = settings.chunking
    if chunking.strategy not in _ALLOWED_CHUNKING_STRATEGIES:
        raise PipelineError(
            f"config key chunking.strategy: {chunking.strategy!r} is not one of "
            f"{sorted(_ALLOWED_CHUNKING_STRATEGIES)}"
        )
    if chunking.min_tokens > chunking.max_tokens:
        raise PipelineError(
            f"config key chunking.min_tokens ({chunking.min_tokens}) must not exceed "
            f"chunking.max_tokens ({chunking.max_tokens})"
        )
    if chunking.overlap_tokens >= chunking.max_tokens:
        raise PipelineError(
            f"config key chunking.overlap_tokens ({chunking.overlap_tokens}) must be smaller than "
            f"chunking.max_tokens ({chunking.max_tokens})"
        )
    if chunking.overlap_tokens < 0:
        raise PipelineError("config key chunking.overlap_tokens must not be negative")

    for key_path in _UNIT_INTERVAL_KEYS:
        value = _lookup(settings, key_path)
        dotted = ".".join(key_path)
        if not 0.0 <= float(value) <= 1.0:
            raise PipelineError(f"config key {dotted}: {value} is outside [0.0, 1.0]")

    retrieval = settings.retrieval
    if retrieval.top_n > retrieval.dense_k:
        raise PipelineError(
            f"config key retrieval.top_n ({retrieval.top_n}) must not exceed "
            f"retrieval.dense_k ({retrieval.dense_k})"
        )
    if retrieval.dense_k < 1 or retrieval.top_n < 1:
        raise PipelineError("config keys retrieval.dense_k and retrieval.top_n must both be at least 1")
    if retrieval.context_token_budget < 1:
        raise PipelineError("config key retrieval.context_token_budget must be at least 1")
    if retrieval.gate_threshold + retrieval.unclassified_gate_margin > 1.0:
        raise PipelineError(
            "config key retrieval: gate_threshold + unclassified_gate_margin must not exceed 1.0"
        )

    if settings.generation.provider not in _ALLOWED_PROVIDERS:
        raise PipelineError(
            f"config key generation.provider: {settings.generation.provider!r} is not one of "
            f"{sorted(_ALLOWED_PROVIDERS)}"
        )
    if not 0.0 <= settings.generation.temperature <= 2.0:
        raise PipelineError("config key generation.temperature: must be between 0.0 and 2.0")
    if settings.generation.max_tokens < 1:
        raise PipelineError("config key generation.max_tokens must be at least 1")

    if settings.chroma.space != "cosine":
        raise PipelineError(
            f"config key chroma.space: only 'cosine' is supported (got {settings.chroma.space!r}); "
            "embeddings are L2-normalised so cosine similarity equals dot product"
        )
    if settings.guardrails.max_sentences < 1:
        raise PipelineError("config key guardrails.max_sentences must be at least 1")
    if not settings.loading.allowed_hosts:
        raise PipelineError("config key loading.allowed_hosts must list at least one host (constraint C1)")
    if settings.embedding.batch_size < 1:
        raise PipelineError("config key embedding.batch_size must be at least 1")
    if not settings.embedding.model_id:
        raise PipelineError("config key embedding.model_id must not be empty")

    registry = settings.registry
    if not registry.scheme_aliases:
        raise PipelineError(
            "config key registry.scheme_aliases must map at least one scheme_id to its aliases; "
            "without it no question can be resolved to a scheme (architecture.md §11.3)"
        )
    for url_key, url_value in (
        ("registry.education_url", registry.education_url),
        ("registry.help_url", registry.help_url),
    ):
        if not url_value:
            raise PipelineError(
                f"config key {url_key} must not be empty: it is rendered to users in a refusal "
                "or redirect message, and an invented URL is a visible defect (PRD §12)"
            )
    for url_key, url_value in (
        ("registry.education_url", registry.education_url),
        ("registry.help_url", registry.help_url),
        ("registry.factsheet_index_url", registry.factsheet_index_url),
    ):
        if url_value and not url_value.startswith("https://"):
            raise PipelineError(f"config key {url_key}: must be an https:// URL, got {url_value!r}")


def _canonical(value: Any) -> Any:
    """Recursively normalise a settings tree into JSON-serialisable form."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _ensure_directories(paths: PathsSettings) -> None:
    for key in ("raw_dir", "processed_dir", "chroma_dir", "model_cache_dir"):
        paths.resolve(key).mkdir(parents=True, exist_ok=True)
    paths.resolve("chunks_dump").parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=4)
def _load_settings(resolved_path: str) -> Settings:
    config_path = Path(resolved_path)
    if not config_path.is_file():
        raise PipelineError(f"config file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PipelineError(f"config file {config_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise PipelineError(f"config file {config_path} must contain a mapping at the top level")

    missing = _required_keys(raw)
    if missing:
        raise PipelineError(
            f"config file {config_path} is missing required key(s): {sorted(missing)}"
        )

    settings = Settings(source_path=resolved_path, **_build_sections(raw))
    _validate(settings)
    _ensure_directories(settings.paths)
    return settings


def load_settings(path: Path | str | None = None) -> Settings:
    """Load, validate and freeze config.yaml. Cached per resolved path."""
    return _load_settings(str(Path(path).resolve()) if path is not None else str(DEFAULT_CONFIG_PATH))


def configure_console() -> None:
    """Make stdout and stderr able to print the corpus' own currency symbols.

    The corpus contains U+20B9, and a Windows console defaults to cp1252, where printing it raises
    UnicodeEncodeError and destroys output that was otherwise correct. Every CLI entrypoint calls
    this before it prints. Unencodable characters are replaced rather than fatal, so a trace stays
    readable on a terminal that cannot represent the text at all.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


def config_hash(settings: Settings | None = None) -> str:
    """Return a stable sha256 over the resolved settings, for reproducibility records (§18.3).

    `source_path` is excluded: it records where the file was read from, not what it said, so
    including it would make the hash a function of the checkout directory. Two clones of the same
    commit must produce the same value, which is the whole point of publishing it.
    """
    resolved = settings if settings is not None else load_settings()
    payload = {key: value for key, value in asdict(resolved).items() if key != "source_path"}
    canonical = json.dumps(
        _canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LlmEnv:
    """Secrets and machine-specific values read from the environment, then .env (architecture.md §18.2).

    The key is never logged, echoed, or included in any trace. `log_queries` is a local debugging
    switch and defaults to False, so raw query text stays out of logs by default.
    """

    api_key: str
    base_url: str
    model: str
    log_queries: bool


def _parse_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE pairs from a .env file, ignoring comments and blank lines."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_llm_env(path: Path | str | None = None) -> LlmEnv:
    """Load LLM settings from the process environment, falling back to the git-ignored .env file.

    The process environment wins so a deployed host can inject secrets without writing a file.
    """
    file_values = _parse_env_file(Path(path) if path is not None else REPO_ROOT / ".env")

    def resolve(key: str) -> str:
        from_env = os.environ.get(key)
        if from_env is not None and from_env.strip():
            return from_env.strip()
        return file_values.get(key, "").strip()

    log_queries = resolve("LOG_QUERIES").lower() in {"1", "true", "yes", "on"}
    return LlmEnv(
        api_key=resolve("LLM_API_KEY"),
        base_url=resolve("LLM_BASE_URL"),
        model=resolve("LLM_MODEL"),
        log_queries=log_queries,
    )
