"""Tests for the typed configuration loader (implementation.md Phase 1)."""

from __future__ import annotations

import dataclasses
import io
import json
import sys
from pathlib import Path

import pytest
import yaml

from src.config import (
    DEFAULT_CONFIG_PATH,
    REPO_ROOT,
    Settings,
    config_hash,
    configure_console,
    load_settings,
)
from src.models import PipelineError

EXPECTED_SECTIONS = (
    "paths",
    "embedding",
    "chunking",
    "chroma",
    "retrieval",
    "generation",
    "loading",
    "guardrails",
    "registry",
    "copy",
)

EXPECTED_COPY_KEYS = (
    "ui_disclaimer",
    "refusal_message",
    "performance_redirect",
    "pii_refusal",
    "out_of_corpus_message",
    "not_in_corpus_message",
    "smalltalk_message",
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return load_settings()


def _raw_config() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def _write_config(tmp_path: Path, mutate) -> Path:
    raw = _raw_config()
    mutate(raw)
    target = tmp_path / "config.yaml"
    target.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return target


def test_loads_repo_config_with_every_section_present(settings: Settings) -> None:
    for name in EXPECTED_SECTIONS:
        assert hasattr(settings, name), f"missing settings section: {name}"


def test_representative_values_match_architecture_section_20(settings: Settings) -> None:
    assert settings.embedding.model_id == "sentence-transformers/all-MiniLM-L6-v2"
    assert settings.chunking.strategy == "semantic_section"
    assert settings.chunking.max_tokens == 600
    assert settings.chunking.min_tokens == 80
    assert settings.chunking.overlap_tokens == 60
    assert settings.chroma.collection_name == "mf_faq_hdfc_v1"
    assert settings.chroma.space == "cosine"
    assert settings.retrieval.dense_k == 12
    assert settings.retrieval.top_n == 5
    assert settings.retrieval.gate_threshold == 0.35
    assert settings.retrieval.mmr_lambda == 0.3
    assert settings.retrieval.context_token_budget == 1800
    assert settings.retrieval.boosts.fact_term == 0.05
    assert settings.retrieval.boosts.section_type_match == 0.03
    assert settings.guardrails.max_sentences == 3
    assert settings.guardrails.enforce_numeric_grounding is True
    assert settings.generation.provider == "extractive"

    assert "groww.in" in settings.loading.allowed_hosts


def test_paths_resolve_against_repo_root_not_cwd(settings: Settings) -> None:
    assert settings.paths.resolve("raw_dir") == REPO_ROOT / "data/raw"
    assert settings.paths.resolve("sources_csv") == REPO_ROOT / "data/sources.csv"
    assert settings.paths.resolve("raw_dir").is_absolute()


def test_fact_terms_are_exposed_per_fact_family(settings: Settings) -> None:
    fact_terms = settings.retrieval.fact_terms
    for family in (
        "expense_ratio",
        "exit_load",
        "min_sip",
        "lock_in",
        "riskometer",
        "benchmark",
        "statements",
    ):
        assert family in fact_terms
        assert fact_terms[family], f"no surface forms for {family}"
    assert "exit load" in fact_terms["exit_load"]


def test_settings_are_frozen_and_hold_no_mutable_sequences(settings: Settings) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.chunking.max_tokens = 1  # type: ignore[misc]
    for section in EXPECTED_SECTIONS:
        nested = getattr(settings, section)
        for f in dataclasses.fields(nested):
            value = getattr(nested, f.name)
            assert not isinstance(value, list), f"{section}.{f.name} is a mutable list"
            if isinstance(value, tuple):
                assert not any(isinstance(item, list) for item in value)


def test_config_hash_is_stable_across_loads(settings: Settings) -> None:
    first = config_hash(settings)
    second = config_hash(load_settings())
    third = config_hash(load_settings(DEFAULT_CONFIG_PATH))
    assert first == second == third
    assert len(first) == 64


def test_config_hash_ignores_the_checkout_directory(settings: Settings) -> None:
    """The hash must identify the config's content, not the folder it was cloned into.

    Rehearsal A caught this: a fresh clone at a different path produced a different hash from an
    identical config.yaml, which made the published `config_hash` unreproducible for anyone else.
    """
    elsewhere = dataclasses.replace(settings, source_path=r"C:\somewhere\else\config.yaml")
    assert config_hash(elsewhere) == config_hash(settings)
    assert dataclasses.replace(settings, source_path="").source_path == ""


def test_config_hash_changes_when_a_value_changes(settings: Settings) -> None:
    mutated = dataclasses.replace(
        settings, retrieval=dataclasses.replace(settings.retrieval, gate_threshold=0.5)
    )
    assert config_hash(mutated) != config_hash(settings)


def test_config_hash_is_json_serialisable_canonically(settings: Settings) -> None:
    from src.config import _canonical

    tree = _canonical(dataclasses.asdict(settings))
    payload = json.dumps(tree, sort_keys=True, separators=(",", ":"))
    assert json.loads(payload)["retrieval"]["gate_threshold"] == 0.35
    assert json.loads(payload)["retrieval"]["fact_terms"]["exit_load"] == [
        "exit load",
        "exit charges",
        "load",
    ]


def test_load_settings_is_cached(settings: Settings) -> None:
    assert load_settings() is settings
    assert load_settings() is load_settings(DEFAULT_CONFIG_PATH)


def test_copy_section_carries_all_seven_strings(settings: Settings) -> None:
    for key in EXPECTED_COPY_KEYS:
        value = getattr(settings.copy, key)
        assert isinstance(value, str) and value.strip(), f"copy.{key} is empty"
    assert "Facts-only. No investment advice." in settings.copy.ui_disclaimer


def test_every_ui_list_field_loads_as_a_tuple_of_strings(settings: Settings) -> None:
    """A `tuple[str, ...]` field must arrive as a tuple, not as its own repr as a string.

    The `ui` section used to decide this by field name, so a list added later was stringified and
    the UI iterated `"['a', 'b']"` one character at a time. Driving the coercion from the
    annotation is what makes that impossible; this asserts the result for every list field.
    """
    from dataclasses import fields as dataclass_fields

    from src.config import UiSettings

    list_fields = [
        field
        for field in dataclass_fields(UiSettings)
        if field.type in ("tuple[str, ...]", tuple[str, ...])
    ]
    assert {field.name for field in list_fields} == {
        "nav_links",
        "breadcrumb",
        "footer_links",
        "example_questions",
        "example_questions_after_refusal",
    }
    for field in list_fields:
        value = getattr(settings.ui, field.name)
        assert isinstance(value, tuple), f"ui.{field.name} is {type(value).__name__}"
        assert value and all(isinstance(item, str) and item for item in value), field.name


def test_missing_file_raises_precise_error(tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="config file not found"):
        load_settings(tmp_path / "absent.yaml")


def test_missing_key_error_lists_the_key_path(tmp_path: Path) -> None:
    def drop_gate_threshold(raw: dict) -> None:
        del raw["retrieval"]["gate_threshold"]

    path = _write_config(tmp_path, drop_gate_threshold)
    with pytest.raises(PipelineError) as excinfo:
        load_settings(path)
    assert "retrieval.gate_threshold" in str(excinfo.value)
    assert "missing required key" in str(excinfo.value)


def test_missing_boost_key_error_lists_the_key_path(tmp_path: Path) -> None:
    def drop_boost(raw: dict) -> None:
        del raw["retrieval"]["boosts"]["scheme_match"]

    path = _write_config(tmp_path, drop_boost)
    with pytest.raises(PipelineError) as excinfo:
        load_settings(path)
    assert "retrieval.boosts.scheme_match" in str(excinfo.value)


def test_missing_section_error_lists_the_section(tmp_path: Path) -> None:
    def drop_section(raw: dict) -> None:
        del raw["guardrails"]

    path = _write_config(tmp_path, drop_section)
    with pytest.raises(PipelineError) as excinfo:
        load_settings(path)
    assert "guardrails" in str(excinfo.value)


def test_invalid_gate_threshold_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, lambda raw: raw["retrieval"].update(gate_threshold=1.4))
    with pytest.raises(PipelineError, match=r"retrieval\.gate_threshold: 1.4 is outside \[0.0, 1.0\]"):
        load_settings(path)


def test_unknown_chunking_strategy_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, lambda raw: raw["chunking"].update(strategy="magic"))
    with pytest.raises(PipelineError, match="chunking.strategy"):
        load_settings(path)


def test_top_n_above_dense_k_is_rejected(tmp_path: Path) -> None:
    def raise_top_n(raw: dict) -> None:
        raw["retrieval"]["top_n"] = raw["retrieval"]["dense_k"] + 1

    path = _write_config(tmp_path, raise_top_n)
    with pytest.raises(PipelineError, match="top_n"):
        load_settings(path)


def test_overlap_not_smaller_than_max_tokens_is_rejected(tmp_path: Path) -> None:
    def widen_overlap(raw: dict) -> None:
        raw["chunking"]["overlap_tokens"] = raw["chunking"]["max_tokens"]

    path = _write_config(tmp_path, widen_overlap)
    with pytest.raises(PipelineError, match="overlap_tokens"):
        load_settings(path)


def test_empty_allowed_hosts_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, lambda raw: raw["loading"].update(allowed_hosts=[]))
    with pytest.raises(PipelineError, match="allowed_hosts"):
        load_settings(path)


def test_non_cosine_space_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, lambda raw: raw["chroma"].update(space="l2"))
    with pytest.raises(PipelineError, match="chroma.space"):
        load_settings(path)


def test_unknown_generation_provider_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, lambda raw: raw["generation"].update(provider="guess"))
    with pytest.raises(PipelineError, match="generation.provider"):
        load_settings(path)


def test_invalid_yaml_is_reported_as_config_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("retrieval: [unclosed\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="not valid YAML"):
        load_settings(path)


def test_data_directories_are_ensured(settings: Settings) -> None:
    for key in ("raw_dir", "processed_dir", "chroma_dir", "model_cache_dir"):
        assert settings.paths.resolve(key).is_dir(), f"{key} was not created"


def test_configure_console_lets_a_cp1252_console_print_the_rupee_sign() -> None:
    """A Windows console defaults to cp1252, where printing U+20B9 raises UnicodeEncodeError."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        configure_console()
        print("minimum investment is \u20b9 500", file=stream)
        stream.flush()
        printed = stream.buffer.getvalue().decode("utf-8")
    assert "\u20b9 500" in printed


def test_configure_console_is_harmless_without_a_reconfigurable_stream() -> None:
    """pytest replaces stdout with a capture object that has no reconfigure method."""

    class _NoReconfigure:
        pass

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", _NoReconfigure())
        patch.setattr(sys, "stderr", _NoReconfigure())
        configure_console()


def test_state_dir_relocates_writable_paths_only(tmp_path, monkeypatch) -> None:
    """GROWW_STATE_DIR must move everything the app writes, and nothing it reads.

    A hosted deploy mounts the repository read-only and offers scratch space instead, so the
    Chroma store, model cache, processed corpus and dumps all have to move while `raw_dir` and
    `sources_csv` stay put, since those are the committed inputs the build reads.
    """
    settings = load_settings()
    monkeypatch.setenv("GROWW_STATE_DIR", str(tmp_path))

    for key in ("chroma_dir", "model_cache_dir", "processed_dir", "chunks_dump", "vectors_dump"):
        resolved = settings.paths.resolve(key)
        assert str(resolved).startswith(str(tmp_path)), f"{key} must live in the state dir"

    assert settings.paths.resolve("raw_dir") == REPO_ROOT / "data" / "raw"
    assert settings.paths.resolve("sources_csv") == REPO_ROOT / "data" / "sources.csv"


def test_paths_default_to_the_repo_when_no_state_dir_is_set(monkeypatch) -> None:
    monkeypatch.delenv("GROWW_STATE_DIR", raising=False)
    settings = load_settings()
    assert settings.paths.resolve("chroma_dir") == REPO_ROOT / settings.paths.chroma_dir
