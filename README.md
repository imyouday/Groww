# Mutual Fund FAQ Assistant — facts-only RAG

A Streamlit chatbot that answers factual questions about **five HDFC AMC mutual fund schemes** from
public scheme pages, and refuses everything else. Every answer is assembled from retrieved chunks,
carries exactly one citation from a fixed source registry, and is at most three sentences long. It
never states a return, a NAV, or a performance figure, because the system has no way to compute one.

**Scope: HDFC AMC · 5 schemes · 5 of the 7 in-scope fact families · English only · snapshot-based.**

The demo runs entirely offline after the first build. There is no cloud service, no API key required,
and no telemetry.

---

## Setup

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # fully pinned, incl. transitive deps
.venv\Scripts\python -m src.pipeline build                # ~16 s -> 106 chunks (~50 s the first time, while the model downloads)
.venv\Scripts\python -m streamlit run app.py              # http://localhost:8501
```

`requirements.txt` is a complete `pip freeze`, not a loose list. Two constraints are load-bearing
and are documented in the file itself: **chromadb 0.5.x** needs `tokenizers <=0.20.3`, and
**sentence-transformers 5.7.0** needs `transformers <5`. Upgrading either without re-checking the
pair breaks the install; `pip check` is the test.

### The LLM is optional

The demo needs **no `.env` and no API key**. Generation has two paths:

- **extractive** (default when no key is present) — lifts the answer sentence out of a retrieved
  chunk. Every number it emits is grounded by construction rather than by validation.
- **llm** (only if `LLM_API_KEY` is set) — one hand-written `POST` to an OpenAI-compatible
  `/chat/completions` endpoint. No SDK, no agent framework.

```bash
# .env  (optional)
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=...
LLM_MODEL=qwen/qwen3.8-27b
```

`LLM_BASE_URL` and `LLM_API_KEY` are read at call time; `config.yaml` is the fallback. **Known limit:**
that free-tier endpoint returns HTTP 429 after roughly six consecutive questions, because a
~700-token prompt exhausts its token-per-minute quota. The pipeline retries once, then falls back to
the extractive composer, so answers stay correct — but the demo should be rehearsed on extractive.
Measured in ablation A4: 17 of 24 LLM rows degraded, and correctness was 1.0 either way.

`config.yaml` `generation.provider` accepts `auto` (default), `llm`, or `extractive`; the CLI takes
`--provider`. `auto` degrades to extractive when no key is present, so a missing key is never a crash.

---

## What it will and will not answer

| Ask | Response |
| --- | --- |
| "What is the expense ratio of the HDFC Large Cap Fund - Direct Growth?" | Answered, with one source link |
| "What is the exit load on the HDFC ELSS Tax Saver Fund - Direct Plan Growth?" | **Refused** — that page carries no exit-load text |
| "Which fund should I put my money into for a five year goal?" | **Refused** — that is advice; AMFI link |
| "Which of these gave the best 1-year return?" | **Redirected** — the system never compares returns |
| "My PAN is ABCDE1234F, which folio holds my units?" | **Refused** — PII never stored, echoed, or logged |
| "What is the expense ratio of Parag Parflex?" | **Refused** — outside the five registered schemes |

**24 of 25 scheme/family pairs are answerable.** The one gap, S3's exit load, is a real absence in the
page: no S3 chunk contains the word *exit* or the word *load*. Ten worked examples with verbatim
answers are in [`docs/sample_qa.md`](docs/sample_qa.md); the per-source breakdown is in
[`docs/sources.md`](docs/sources.md).

---

## Architecture

```
                       OFFLINE (build once, cached to disk)
 ┌──────────────┐   ┌───────────┐   ┌────────────┐   ┌──────────────┐   ┌───────────┐
 │ 1. LOADING   │──▶│2. CHUNKING│──▶│3. EMBEDDING│──▶│4. VECTOR     │──▶│ ChromaDB   │
 │ fetch HTML   │   │ section-  │   │ all-MiniLM │   │ STORE        │   │ persistent │
 │ from         │   │ aware     │   │ L6-v2      │   │ write + meta │   │ (384-d)    │
 │ sources.csv  │   │ + tables  │   │ 384-d      │   │              │   │            │
 └──────┬───────┘   └───────────┘   └────────────┘   └──────────────┘   └─────┬─────┘
        └── raw/ snapshots + content_hash                                   │
                       ONLINE (per question)                                  ▼
 ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  ┌───────────────┐  ┌────────┐
 │ 0. INPUT     │─▶│ 1. INTENT +  │─▶│ 2. RETRIEVAL  │─▶│ 3. GROUNDED   │─▶│ 4. POST│
 │ + PII check  │  │ PII / SCHEME │  │ dense+keyword │  │ GENERATION    │  │ VALID. │
 │              │  │ normalise    │  │ MMR → top 5   │  │ ≤3 sentences  │  │ + cite │
 └──────────────┘  └──────────────┘  └───────────────┘  └───────────────┘  └────────┘
                                         │                      │                │
                                         └──── grounding gate ──┴── refusal ─────┘
                                                                          │
                                                             ┌────────────▼────────────┐
                                                             │ 5. ANSWER + 1 CITATION   │
                                                             │ + Last updated stamp     │
                                                             └─────────────────────────┘
```

One module per stage, communicating only through frozen dataclasses in `src/models.py`.
`src/pipeline.py` is the only module that wires them together, and `tests/test_layering.py` fails
the build if a stage imports another stage's internals or if `src/retrieval.py` ever imports
`src/generation.py`.

### The chunking decision, and what it was worth

**Section-aware semantic chunking**, not fixed-size. A fee table stays one unit, a label and its
value stay together, prose sections get 60 tokens of overlap, and tax/fee sections get none
(overlapping a table corrupts it).

Measured by ablation A1, rebuilding a real index per variant into a scratch store:

| Variant | Chunks | Median tokens | Answer correctness | Top-1 hit |
| --- | --- | --- | --- | --- |
| `semantic_600` (shipped) | 106 | 229 | **1.0** | **1.0** |
| `semantic_350` | 106 | 229 | 1.0 | 1.0 |
| `fixed_512` | 94 | 244.5 | **0.7917** | **0.7917** |

Section awareness is worth about **21 points** of answer correctness. The two semantic rows are
identical because both request more than the encoder's 254-token ceiling, so both are clamped to it
— recorded rather than hidden, since "two variants" that are one variant is exactly the kind of
thing a demo should not imply otherwise.

### Retrieval settings

| Setting | Value | Where it comes from |
| --- | --- | --- |
| `gate_threshold` (τ) | **0.35** | `ARCH` §12 calibration — see below |
| `unclassified_gate_margin` | 0.10 | questions with no fact family are held to τ + 0.10 |
| `dense_k` | 12 | candidates pulled from the vector store before reranking |
| `top_n` | 5 | chunks in the assembled context |
| `mmr_lambda` | 0.3 | relevance/diversity trade-off in MMR selection |
| `context_token_budget` | 1800 | ceiling on assembled context |
| `scheme_filter` | true | restrict the candidate pool to the resolved scheme |
| boosts | 0.05 / 0.05 / 0.03 / 0.02 | fact term, additional fact term, section type, scheme |

**τ = 0.35 is a measured decision to leave the number alone, not a guess.** The §12 procedure sweeps
τ ∈ {0.20 … 0.60} and finds *no* admissible value: the best irrelevant score (`t_i`) sits at
0.67–0.82, so every threshold in the grid false-gates. The scores *are* cleanly separable, just not
in that range — the separating band is **(0.8165, 0.8402]**, entirely above the grid. So the
conclusion is that at 0.35 the score term never fires on its own, and what actually refuses an
out-of-corpus question is **term coverage**: a retrieved chunk must actually contain the words for
the fact family being asked. That is a stronger guarantee than a threshold, and it is why the
calibration produced no number to apply.

Two other ablation results, reported as measured:

- **The keyword boosts are load-bearing.** Dense-only scores 0.875 correctness; dense + boosts
  scores 1.0. +12.5 points.
- **MMR shows no measurable gain here.** 1.0 either way, and it raises mean context tokens from 382
  to 638 while `mean_schemes_in_context` stays at 1. With five schemes and 106 chunks there is no
  diversity for it to find. It is kept because it is the right mechanism at corpus scale, not
  because the current corpus proves it.

---

## Evaluation

24 golden questions (five schemes × five fact families, minus the one real gap) and 8 adversarial
probes. Run it yourself:

```bash
.venv\Scripts\python eval\run_eval.py --mode metrics --provider extractive
.venv\Scripts\python eval\run_eval.py --mode metrics --provider llm
.venv\Scripts\python eval\run_eval.py --mode calibration    # the §12 sweep
.venv\Scripts\python eval\run_eval.py --mode ablation --ablation all
.venv\Scripts\python eval\run_eval.py --mode metrics --provider extractive --json
```

**Pass `--provider` explicitly.** Omitting it evaluates only the *active* provider — `llm` when
`LLM_API_KEY` is set, `extractive` otherwise — so one bare run gives you half the table with no
warning. Each run appends a dated block to `eval/report.md`; the file is never rewritten.

Latest run, `config_hash c6fae467b326`. The eight metrics come out identical on both providers:

| Metric | Value | Target | n | Met |
| --- | --- | --- | --- | --- |
| answer_correctness | 1.0 | ≥ 0.9 | 24 | yes |
| citation_validity | 1.0 | ≥ 1 | 24 | yes |
| top1_retrieval_hit | 1.0 | ≥ 0.85 | 24 | yes |
| refusal_precision | 1.0 | ≥ 1 | 8 | yes |
| refusal_recall | 1.0 | ≥ 1 | 24 | yes |
| length_compliance | 1.0 | ≥ 1 | 32 | yes |
| pii_leakage | 0 | ≤ 0 | 32 | yes |
| grounding_gap_rate | 0 | ≤ 0 | 24 | yes |

Median latency **42 ms** extractive (p95 57 ms), **812 ms** with the LLM (p95 1326 ms, which includes
the rate-limited rows that fell back to extractive). Every number, plus the labelled `s_i`/`t_i`
scores behind the sweep and all four ablation tables, is in [`eval/report.md`](eval/report.md). That
file is append-only and dated: runs are added, never edited out. An earlier run minutes before this
one measured 783 ms / 1308 ms with the same eight metrics, which is the spread you should expect from
a shared endpoint.

The eight metrics are pure functions over prediction rows in `eval/checks.py`, and they are tested
with synthetic rows that are *wrong* in specific ways — a metric that has only ever seen perfect
input has not been shown it can fail.

---

## Reproducibility

| | |
| --- | --- |
| Python | 3.11 |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` (384-d, cosine, CPU) |
| Vector store | ChromaDB 0.5.23, collection `mf_faq_hdfc_v1` |
| `config_hash` | `c6fae467b32678c7373aac09ffe95898ed9eeec4dc0dadc052384d6119121193` |
| `corpus_hash` | `924af25cea195b3a94f797eed707ddacb3a7a77422dc2d4115b9a099a6c54291` |
| Chunks | 106 — fees 79, tax 17, risk 5, general 5; median 229 tokens, max 254 |
| Sources fetched | 2026-09-27 (all five), from the committed snapshots in `data/processed/` |
| Index build, model already cached | 16.4 s |
| Index build, first run on a fresh clone | ~50 s (includes the 384-d model download) |

`corpus_hash` is a sha256 over every chunk's id and content in sorted id order. Rebuilding from the
committed snapshots reproduced it exactly in a fresh clone at a different path, which is the
reproducibility claim: the same corpus produces the same index, byte for byte in content.
`data/raw/` and `data/processed/` are committed precisely so the build does not depend on the
network.

`config_hash` is a sha256 over the *resolved* settings. It deliberately excludes the path the
config file was read from, so a clone at any location hashes identically — a fresh-clone rehearsal
caught it being included, which had made the published value unreproducible for anyone else.

### Frozen configuration

`config.lock.json` is the freeze record for tag `v1.0-class-demo`: the fully resolved settings, all
125 pinned package versions, both hashes, the embedding model, and each source's URL, `fetched_at`
and sha256 over its extracted text. It is a record, not an input — nothing reads it at runtime. The
API key is deliberately absent.

```json
{
  "lock_version": 1,
  "frozen_for": "v1.0-class-demo",
  "python": "3.11.9",
  "config_hash": "c6fae467b32678c7373aac09ffe95898ed9eeec4dc0dadc052384d6119121193",
  "corpus_hash": "924af25cea195b3a94f797eed707ddacb3a7a77422dc2d4115b9a099a6c54291",
  "chunk_count": 106
}
```

The load-bearing pins, and what breaks without them:

| Package | Version | Constraint |
| --- | --- | --- |
| `chromadb` | 0.5.23 | must stay 0.5.x; 0.6 changes the client constructor |
| `tokenizers` | 0.20.3 | `chromadb` 0.5.x fails above this |
| `sentence-transformers` | 5.7.0 | — |
| `transformers` | 4.46.3 | must stay <5 for `sentence-transformers` 5.7.0 |
| `streamlit` | 1.64.0 | — |

## Deploying

The app is a single Streamlit process with no API key required, so it deploys as-is to
**[Streamlit Community Cloud](https://share.streamlit.io)** (free, GitHub login). That host is the
right target for this app specifically: it runs a long-lived WebSocket server, has a persistent disk
for the index, and supports `torch`. Serverless hosts such as Vercel do not — see
[Known limits](#known-limits).

```bash
# 1. push, then create the app at share.streamlit.io -> "Deploy" -> pick imyouday/Groww -> main
# 2. click "Advanced settings" and set Python to 3.11
# 3. deploy (no secrets needed - see below)
```

**Python 3.11 is a manual step, and it is not optional.** Community Cloud ignores `runtime.txt`,
`.python-version` and `config.toml` when it picks an interpreter, and it defaults to 3.12, where the
pinned `torch`/`tokenizers`/`transformers` trio does not resolve. You choose the version in the
deploy dialog, and after the first deploy the only way to change it is to delete the app and
redeploy it. Select 3.11 the first time.

Two things happen on the hosted host that do not happen locally, both by design:

- **The index builds itself on first boot.** `data/chroma/` is derived state and is not committed
  (architecture.md §18.1), so a fresh clone starts with an empty collection. `warm_index()` builds it
  from the committed `data/raw` snapshots instead of raising: about 12 s, no network, and it
  reproduces `corpus_hash 924af25c…` exactly. Only the UI self-heals — `answer()` still raises
  `IndexNotBuiltError` when the index is absent, so a script that skipped the build is told rather
  than silently handed a 12-second wait.
- **The embedding model downloads on first boot** (87 MB, cached on the persistent disk afterwards).
  Every later session reuses the cache, so this is a one-time cost per redeploy.

`.streamlit/config.toml` sets `server.headless` and turns `browser.gatherUsageStats` off, since
NFR-7 rules out telemetry and Streamlit's own counter is a telemetry channel. It deliberately sets no
`[theme]` block, because `src/theme.py` owns the light/dark toggle. Save that file as UTF-8 **without**
a byte-order mark: Streamlit's TOML reader treats a BOM as part of the first key name and refuses to
start.

Run it with **no secrets at all** and it uses the extractive composer — the designed zero-key mode
(determinism driver D4). Answers still come from the corpus with a citation, and they are *faster*
(≈40 ms versus ≈800 ms for the LLM path), because the composer lifts the sentence out of the corpus
rather than asking a model to write one. To use the LLM on a hosted app, add `LLM_API_KEY`,
`LLM_BASE_URL` and `LLM_MODEL` as secrets. Be deliberate about that: a public URL means anyone can
spend your quota.

To re-freeze after an intentional change: `python -m src.pipeline build` and re-derive the lock.

Both hashes are recorded in `data/build_report.json` and reprinted by `python -m src.pipeline build`.

### Measured performance

| | |
| --- | --- |
| Index build, model already cached (5 sources → 106 chunks) | 16.4 s |
| Index build, first run, model not yet downloaded | ~50 s |
| App cold start, fresh process, warm file cache | **10.5 s** |
| — of which `warm_index()` on first call | 4.4 s |
| — repeat `warm_index()` call | 0.02 s |
| First answer after warm-up | 102 ms |
| Steady-state answer latency | 42 ms median, 57 ms p95 (32-row eval run) |
| Answer latency with the LLM | 812 ms median, 1326 ms p95 |

The 10.5 s is the honest number for a demo machine starting cold: it is dominated by importing
`sentence_transformers` and `torch`, not by the corpus — which is 106 chunks, not 106,000. Once the
process is up, warming is 4.4 s and every answer after that is tens of milliseconds. A spinner covers
the initial load.

---

## Safety invariants

These are enforced by tests, and any change that weakens one has to change a test first:

- The system never returns a citation URL that is not in `data/sources.csv`.
- The system never emits more than 3 sentences in an answer body.
- The system never computes or compares returns, NAV, or performance — and performance blocks are
  dropped **at ingest**, so there is no return table in the index to leak from.
- The system never stores, echoes, or logs PAN, Aadhaar, account numbers, OTP, email, or phone.
- The LLM's output is never trusted for URLs, numbers, sentence count, or advice language. Every
  draft goes through validators V1–V6 and is replaced by the extractive answer if any of them fails.

Log lines carry allowlisted fields only — intent, scheme, ids, scores, counts, durations. The query
text, the assembled context, the draft, and the raw model output are absent by construction
(`src/pipeline.py` `SAFE_LOG_FIELDS`).

---

## Known limits

From `PRD.md` §18, plus what the build actually taught:

1. **Five schemes of one AMC.** No other AMC, no other plan variants.
2. **Snapshot-based.** Facts are only as current as the `Last updated from sources` stamp; nothing is
   re-crawled at query time.
3. **Facts only** — no returns, no NAV, no portfolio logic, no tax computation.
4. **English only.**
5. **Answer quality is bounded by the page text.** S3's exit load is the live example.
6. **No auth, no personalisation, no multi-user state, no production hardening.**
7. **Hybrid lexical + dense with MMR**, not a learned reranker. A cross-encoder is known future work.
8. **Local ChromaDB.** A real vector DB is needed past tens of thousands of chunks.
9. **Distributor-hosted pages, not the AMC's own site.** `hdfcfund.com` returns HTTP 403 to a
   scripted client, so the corpus uses Groww's rendering of the same funds. See
   [`docs/sources.md`](docs/sources.md) and `docs/corpus_matrix.md`.
10. **Two of the seven in-scope fact families are not in the corpus.** Lock-in period and statements
    have no public source among the five pages (recorded at the Phase 0 spike). Questions about them
    are refused rather than answered, which is correct behaviour, but it means this demo covers 5 of
    the 7 families the PRD lists.
11. **MMR is unproven on this corpus** (see above). Its cost is measured; its benefit is not.
12. **Not deployable to a serverless host as-is.** The app is a long-lived WebSocket process whose
    dependencies include `torch` (~800 MB installed) and a 12 MB vector index on a writable disk, so
    Vercel-style function hosts are a poor fit on three counts at once: function size, no persistent
    process, and no writable filesystem. Streamlit Community Cloud is the supported target; see
    [Deploying](#deploying). A Dockerfile is the route if a container host is ever needed.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| App shows "index is empty" | `data/chroma/` not built | `python -m src.pipeline build`. The Streamlit UI does this for you on startup; the CLI does not. |
| `IndexNotBuiltError` / unusable index | Chroma dir written by a different version | `rm -r data/chroma`, then rebuild |
| `LLM_API_KEY is not set` | No `.env` | Expected. The demo works without it; use `--provider extractive` |
| Answers all fall back to extractive mid-session | Endpoint HTTP 429 (token-per-minute limit) | Wait a minute, or stay on extractive for the demo |
| First load of the model is slow | Downloading `all-MiniLM-L6-v2` (~90 MB) | Needs network once; afterwards `data/models/` caches it |
| `chroma` import errors after a pip install | `tokenizers`/`sentence-transformers` version clash | See the constraints block at the top of `requirements.txt`; run `pip check` |
| Build prints a `max_tokens is 600 but the encoder accepts 254` warning | Working as designed | Expected: a longer chunk is silently truncated, so it is clamped. `ARCH` §8.2 |
| Build needs the network | Snapshots missing | `data/processed/` is committed; only a `--refresh` build fetches |

---

## Acceptance

Every `PRD.md` §16 checkbox, verified:

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | All 7 fact families answerable for all 5 schemes | **Partial — 5 of 7** | 24/25 scheme×family pairs answer (`docs/sources.md`). Lock-in and statements have no public source; recorded at the Phase 0 spike. This is the one criterion not met in full, and it is a corpus limit, not a code gap |
| 2 | 100% of factual answers carry exactly one registry URL | Met | `citation_validity` 1.0 over 24; `tests/test_ui_smoke.py` asserts exactly one link |
| 3 | 100% of answers ≤ 3 sentences with the stamp | Met | `length_compliance` 1.0 over 32 rows |
| 4 | Advice/portfolio/performance refused or redirected, zero return figures | Met | `refusal_precision` and `refusal_recall` 1.0; performance blocks dropped at ingest |
| 5 | 0 PII accepted, stored, or logged, verified by probe | Met | `pii_leakage` 0 over 32; `tests/test_pii.py`, probe P07 |
| 6 | Top-1 hit ≥ 85%, grounding-gap rate 0% | Met | `top1_retrieval_hit` 1.0; `grounding_gap_rate` 0 |
| 7 | UI shows welcome, 3 examples, facts-only note, disclaimer | Met | `tests/test_ui_smoke.py`; verified in the headless UI sweep |
| 8 | README documents setup, scope, known limits | Met | this file |
| 9 | `docs/sample_qa.md` has 5–10 real Q&A pairs with links | Met | 10 pairs, verbatim output |
| 10 | Source list exported as CSV **and** MD | Met | `data/sources.csv`, `docs/sources.md` |
| 11 | Full rebuild from raw snapshots in one command; offline demo works | Met | `python -m src.pipeline build` from committed snapshots reproduced `corpus_hash` exactly in a fresh clone; offline rehearsal with `HF_HUB_OFFLINE=1` answered all 5 corpus families and refused the 2 absent ones |
| 12 | ≤3-minute demo rehearsed twice, including offline | Met | `docs/demo_script.md` retimed to 2:55 and run twice through the real render path (identical results, 0.1 s of step time); offline run in Rehearsal B. Details in `implementation.md` Phase 13 |

**Rehearsal results (Phase 13).** A — fresh `git clone`, README followed verbatim, `corpus_hash`
reproduced exactly at a different path, all six documented behaviours correct with no API key. B —
`HF_HUB_OFFLINE=1`, no key: 5 families answered, 2 absent families and 1 advice probe refused,
extractive engaged, no crash, model cache sufficient. C — the script run twice: identical output,
no stutter. D — 6/6 adversarial probes refused or redirected, with zero fabricated figures, zero
advice sentences and zero prompt leakage.

The rehearsals also found a real bug: `config_hash` included the absolute path of the `config.yaml`
it was read from, so the published fingerprint changed with the checkout directory and was
unreproducible for anyone who cloned the repo. Fixed, with a regression test.

---

## Repository layout

```
app.py                  Streamlit UI (imports only pipeline, config, models, templates, theme)
config.yaml             every tunable constant; nothing hard-coded in stage modules
data/sources.csv        the source registry — the citation allowlist
data/raw, data/processed  committed HTML + cleaned snapshots (offline reproducibility)
data/chroma/            the vector store (rebuildable, not committed)
eval/checks.py          the eight metrics as pure functions
eval/run_eval.py        metrics / calibration / ablation harness
eval/report.md          append-only, dated evidence
src/                    one module per pipeline stage
tests/                  570 tests, including the layering enforcement
config.lock.json        the v1.0 freeze record: resolved config, 125 pins, hashes, fetch dates
docs/                   sources.md, sample_qa.md, demo_script.md, corpus_matrix.md,
                        fallback_transcript.html (printable, for a failed browser or network)
```

## Development

```bash
.venv\Scripts\python -m pytest -q                          # 569 passed
.venv\Scripts\python -m pytest -q tests/test_layering.py   # 26 passed
.venv\Scripts\python -m src.pipeline --help
```

Conventions, the phase plan, and the rule that `implementation.md` is the task list are in
[`AGENTS.md`](AGENTS.md). Design decisions and their measured justifications live in
[`architecture.md`](architecture.md); requirements in [`PRD.md`](PRD.md).
