# Project conventions — MF FAQ Assistant (RAG)

These are the rules that keep `architecture.md` enforceable rather than aspirational.

## Stack (do not introduce alternatives)
- Python 3.11, Streamlit (UI), sentence-transformers (embeddings), chromadb (vector store),
  beautifulsoup4 + lxml (parsing), httpx (fetch), pyyaml (config), pytest (tests).
  No LangChain / LlamaIndex / OpenAI SDK (one HTTP call for the optional LLM is hand-written).
- All tunable constants live in `config.yaml`. No magic numbers in stage code.
- No cloud services, no telemetry, no API keys required to run the demo.

## Code style
- Type hints on every function signature. Full annotations, no bare `dict`/`list` without params.
- Dataclasses are `@dataclass(frozen=True)`; enums subclass `str, Enum`.
- One module per pipeline stage (`architecture.md` §5.1). Stages communicate ONLY via the
  dataclasses in `src/models.py`. A stage never imports another stage's internals.
- Public functions get a one-line docstring. NO inline comments. No commented-out code.
  Comments explaining "why" belong in `architecture.md`, not in `.py` files.
- No bare `except:`. Catch specific exceptions; on failure either raise a typed error from
  `src/models.py` or degrade per `architecture.md` §15.3, and log which happened.
- No new third-party dependency without an explicit instruction.

## Architecture rules (a test enforces these)
- `src/retrieval.py` must NOT import `src/generation.py`.
- `src/loading.py`, `src/chunking.py`, `src/embedding.py`, `src/store.py` must NOT import
  `src/intents.py`, `src/retrieval.py`, `src/generation.py`, `src/guardrails.py`.
- `src/pipeline.py` is the only module that wires stages together.
- `app.py` may import only `src.pipeline`, `src.config`, `src.models`, `src.templates`, `src.theme`.

## Safety invariants (never weaken; add a test when you touch these)
- The system never returns a citation URL that is not in `data/sources.csv`.
- The system never emits more than 3 sentences in an answer body.
- The system never computes or compares returns/NAV/performance.
- The system never stores, echoes, or logs PAN/Aadhaar/account numbers/OTP/email/phone.
- The LLM's output is never trusted for: URLs, numbers, sentence count, advice language.

## Definition of Done (every phase)
1. Code runs: the phase's Verify commands pass, output pasted.
2. Tests: `python -m pytest -q` green, with at least one new test for the phase's core logic.
3. No layer violations: `python -m pytest -q tests/test_layering.py` green.
4. `python -m src.pipeline --help` still works (CLI not broken).
5. Commit made.

## Commands
```bash
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest -q tests/test_layering.py
.venv\Scripts\python -c "from src.config import load_settings, config_hash; print(config_hash())"
```

## Comment/doc policy for the repo
- The only prose documents are `PRD.md`, `architecture.md`, `implementation.md`, `README.md`,
  `AGENTS.md`, `docs/*`.
- Do not add new `.md` files unless instructed.

## Phase order
Follow `implementation.md`. One phase per session; never start a phase before the previous
phase's Definition of Done is met. Phase 0 (corpus spike) gates everything.
