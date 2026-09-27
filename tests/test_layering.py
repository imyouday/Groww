"""Architecture layering rules (architecture.md §5.2), enforced by test rather than convention.

A stage may only import from the layers below it. The online stages in particular must stay
independent of the generator, so the grounding-gate decision is provable without a model
(architecture.md §11.6, driver D1). ALLOWED is the whole rule set; the tests below only assert
its consequences that are not already covered by the per-module parametrisation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

INFRASTRUCTURE = {"config", "models", "templates", "prompts", "theme"}
# The corpus pipeline runs in one direction: the registry lists the sources, pii scrubs them,
# and each later stage consumes the artefacts of the previous one. Naming the permitted
# predecessor per module is stricter than a blanket "any corpus module" rule, which would let
# chunking import store and hide a real inversion.
CORPUS = {"registry", "pii", "loading", "chunking", "embedding", "store"}
POLICY = {"intents", "retrieval", "generation", "guardrails"}

ALLOWED: dict[str, set[str]] = {
    **{name: {"models"} for name in INFRASTRUCTURE if name not in {"models", "templates"}},
    "models": set(),
    "templates": {"models", "config"},
    "registry": INFRASTRUCTURE,
    "pii": INFRASTRUCTURE,
    "loading": INFRASTRUCTURE | {"registry", "pii"},
    "chunking": INFRASTRUCTURE | {"registry", "pii", "loading"},
    "embedding": INFRASTRUCTURE | {"registry", "pii", "loading", "chunking"},
    "store": INFRASTRUCTURE | {"registry", "pii", "loading", "chunking", "embedding"},
    **{name: INFRASTRUCTURE | CORPUS for name in POLICY - {"retrieval"}},
    # 5.1 intent resolution is a sub-stage of retrieval, not a peer of it, so retrieval is the one
    # policy module allowed to read intents. Every other policy-to-policy edge stays forbidden:
    # generation must not import retrieval, and guardrails must not import either.
    "retrieval": INFRASTRUCTURE | CORPUS | {"intents"},
    "pipeline": INFRASTRUCTURE | CORPUS | POLICY,
    # theme is design-token data and CSS generation: no retrieval, no prompting, no model.
    "app": {"pipeline", "config", "models", "templates", "theme"},
}

MANDATORY_RULES: list[tuple[str, str, str]] = [
    ("retrieval", "generation", "the grounding gate must be independent of the model (D1)"),
    ("loading", "retrieval", "loading runs offline, before retrieval exists"),
    ("loading", "generation", "loading runs offline, before generation exists"),
    ("chunking", "embedding", "chunking produces text, not vectors"),
    ("embedding", "store", "embedding produces vectors, not a collection"),
    ("store", "retrieval", "retrieval consumes the store, never the reverse"),
]


def _local_imports(path: Path) -> set[str]:
    """Return the names of sibling `src` modules imported by the file at path."""
    if not path.is_file():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
            found.add(node.module.split(".", 1)[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src."):
                    found.add(alias.name.split(".", 1)[1])
    return found


def _module_path(module: str) -> Path:
    candidate = SRC / f"{module}.py"
    return candidate if candidate.is_file() else SRC / module / "__init__.py"


def test_every_existing_module_has_a_declared_layer() -> None:
    declared = set(ALLOWED) - {"app"}
    present = {p.stem for p in SRC.glob("*.py")} - {"__init__"}
    assert present <= declared, f"undeclared module(s): {sorted(present - declared)}"


@pytest.mark.parametrize("module", sorted(set(ALLOWED) - {"app", "pipeline"}))
def test_module_imports_only_from_lower_layers(module: str) -> None:
    imported = _local_imports(_module_path(module))
    violations = imported - ALLOWED[module]
    assert not violations, f"src/{module}.py imports {sorted(violations)}, which is not allowed"


@pytest.mark.parametrize(
    ("first", "second", "why"),
    MANDATORY_RULES,
    ids=[f"{a}-must-not-import-{b}" for a, b, _ in MANDATORY_RULES],
)
def test_mandatory_independence_rules(first: str, second: str, why: str) -> None:
    imported = _local_imports(_module_path(first))
    assert second not in imported, f"src/{first}.py must not import src/{second}.py: {why}"


def test_models_is_fully_self_contained() -> None:
    imported = _local_imports(SRC / "models.py")
    assert not imported, f"src/models.py imports {sorted(imported)}; it must depend on nothing"


def test_prompts_depends_only_on_models() -> None:
    imported = _local_imports(_module_path("prompts"))
    assert imported <= {"models"}, (
        f"src/prompts.py imports {sorted(imported)}; the prompt contract may depend only on models"
    )


def test_templates_reads_copy_from_config_not_the_other_way_round() -> None:
    imported = _local_imports(_module_path("templates"))
    assert imported <= {"models", "config"}, (
        f"src/templates.py imports {sorted(imported)}; copy is configuration, so templates reads "
        "config.yaml, and config must never import templates"
    )
    assert "templates" not in _local_imports(_module_path("config"))


def test_config_module_is_self_contained() -> None:
    imported = _local_imports(SRC / "config.py")
    assert imported == {"models"}, (
        "config.py may only depend on models.py for PipelineError; "
        f"found {sorted(imported)}"
    )
