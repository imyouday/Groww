# Architecture — Mutual Fund FAQ Assistant (Facts-Only RAG Chatbot)

| Field | Value |
| --- | --- |
| Document | Software Architecture Document (SAD) |
| Version | v1.0 |
| Status | Draft for review |
| Date | 2026-09-27 |
| Derived from | `PRD.md` v1.0 (requirements IDs `FR-*`, `NFR-*`, `C1–C7`, `R1–R10`) |
| Audience | Engineering team, demo evaluator |
| Scope | Technical design only. Product intent, requirements, and acceptance criteria live in `PRD.md`. |

---

## 1. Purpose and how to read this document

`PRD.md` states *what* to build and *why*. This document states *how* it is built: component boundaries, data contracts between stages, algorithms, persistence schemas, failure behaviour, and the engineering rationale behind each structural choice.

**Design principle for the whole system — "cite, don't compute":** every number the user sees must be copied from a stored corpus chunk and attributed to a registered URL. No stage is permitted to derive a figure that is not literally present in its input. This single rule explains most of the design: the answer template owns the citation, the validator cross-checks digits, and refusals short-circuit before the LLM is ever called.

### 1.1 Traceability — PRD requirement → architecture section

| PRD requirement | Implemented in |
| --- | --- |
| FR-1…FR-7 (ingestion) | §7 Loading, §9.1, §15.1 |
| FR-8…FR-13 (chunking) | §8 Chunking, §15.2 |
| FR-14…FR-18 (embedding + store) | §9 Embedding, §10 Vector store |
| FR-19…FR-28 (retrieval) | §11 Retrieval, §12 Grounding gate, §15.3 |
| FR-29…FR-34 (guardrails) | §13 Guardrails, §14 PII design |
| FR-35…FR-42 (UI) | §6 Component view, §16 UI architecture |
| NFR-1…NFR-3 (setup, startup, latency) | §17 Performance budget, §18 Deployment |
| NFR-4…NFR-6 (offline, reproducible, cost) | §15.7, §18.3 |
| NFR-7…NFR-10 (privacy, transparency, modularity, portability) | §5 Module map, §14, §19 Observability |
| C1…C7 (hard constraints) | §13.3 enforcement matrix |
| R1…R10 (risks) | §21 Spike plan, §14, §18 |

---

## 2. Architecture drivers

Quality attributes, in priority order. These drive trade-offs elsewhere in this document.

| # | Driver | Architectural consequence |
| --- | --- | --- |
| D1 | **Groundedness** — no claim without corpus support | LLM is a *phrasing* layer only; citation is rendered by the system; numeric validator + extractive fallback |
| D2 | **Stage visibility** — the pipeline is the demo | Every stage is an independent module with an explicit dataclass in/out contract and a CLI entry point |
| D3 | **Determinism & reproducibility** — same corpus ⇒ same answers | Pinned versions, recorded `content_hash`, deterministic `chunk_id`, temperature ≤ 0.2, snapshot-based corpus |
| D4 | **Zero-key resilience** — the demo must not die on a missing API key | LLM client is an optional adapter behind a `Generator` protocol; extractive path is a first-class implementation, not an error branch |
| D5 | **Latency (< 6 s p95)** | Corpus is tiny (< 500 chunks); single in-process embedding model; index loaded once; only one LLM round-trip |
| D6 | **Privacy** — no PII retention | Redaction at ingest *and* runtime detection; query text never persisted by default |
| D7 | **Extensibility** — multi-AMC / rerankers later | Registry-driven, not hardcoded: schemes, sources, fact families, and refusal links all come from config/CSV |

---

## 3. System context

```
                    ┌────────────────────────────────────────────┐
                    │        Mutual Fund FAQ Assistant           │
                    │   (facts-only RAG chatbot, Streamlit UI)   │
                    └────────────────────────────────────────────┘
   actors / systems  │            ▲            │            ▲
   ─────────────────┼────────────┼────────────┼────────────┼──────────────
   Retail investor ──┘            │            │            │
   Support agent   ───────────────┤            │            │
   Demo evaluator  ───────────────┘            │            │
                                                │            │
                            (1) fetch public   │            │ (4) optional
                                pages offline  │            │     phrasing
                                                │            │     call
                                     ┌──────────▼──────┐   ┌─┴──────────────┐
                                     │  Public web     │   │ LLM API        │
                                     │  (AMC / AMFI /  │   │ (optional,     │
                                     │   SEBI pages)   │   │  free tier)    │
                                     └─────────────────┘   └────────────────┘

   local disk: data/raw (snapshots) · data/processed · data/chroma · data/*.csv
   local model: sentence-transformers/all-MiniLM-L6-v2  (cached, offline-capable)
```

**Trust boundaries**

| Boundary | Rule |
| --- | --- |
| Internet → ingestion | Only URLs present in `data/sources.csv` may be fetched (C1). Fetch is a build-time, manual step — never triggered by a user query. |
| Corpus → LLM | Retrieved chunk text is *untrusted input* to the LLM, delimited and labelled; the LLM has no tool access and no network. |
| LLM → user | LLM output is *never* trusted: it is untrusted text until it passes the validation chain (§13.2). The citation is not taken from the LLM. |
| User → system | User query is untrusted: PII-detected, length-capped, and never persisted. |

---

## 4. Architecture style

A **linear staged pipeline** (offline build) feeding a **gated request pipeline** (online query), wrapped in a **thin presentation layer**.

Rejected alternatives:

| Style | Why not |
| --- | --- |
| Microservices | One demo laptop, 5 documents. Network hops only add latency and failure modes. |
| Agentic / multi-step reasoning loop | Non-deterministic, uncitable, and directly at odds with C3/C4. A single grounded generation call is the correct granularity. |
| Full agentic ReAct with tools for "compute" | Explicitly banned by C3 (no performance claims). |
| Framework-first (LangChain default graph) | Hides the stages that are the point of the demo, and its defaults (recursive char splitter) are the chunking strategy we rejected. The pipeline is hand-written per stage; only stable leaf libraries (Chroma, sentence-transformers) are used. |
| Event-driven / async ingestion | No streaming corpus; build takes seconds. |

---

## 5. Module map

### 5.1 Repository layout

```
Groww/
├── PRD.md
├── architecture.md                    # this document
├── README.md
├── requirements.txt                   # pinned (§18.3)
├── config.yaml                        # single source of runtime config (§20)
├── app.py                             # Streamlit entry point
├── data/
│   ├── sources.csv                    # SOURCE REGISTRY (allowlist, editable by hand)
│   ├── raw/{source_id}.html|.md       # immutable page snapshots
│   ├── processed/{source_id}.txt      # cleaned text with '#' heading markers
│   ├── chunks.jsonl                   # chunk dump for inspection/debugging
│   └── chroma/                        # ChromaDB PersistentClient directory
├── src/
│   ├── config.py                      # typed config loader + path resolution
│   ├── models.py                      # ALL dataclasses / enums (§6.2)
│   ├── registry.py                    # sources.csv + scheme + fact-family loading
│   ├── pii.py                         # detection + redaction (no deps beyond re)
│   ├── loading.py                     # STAGE 1  fetch + clean
│   ├── chunking.py                    # STAGE 2  semantic section chunker
│   ├── embedding.py                   # STAGE 3  MiniLM encoder (singleton)
│   ├── store.py                       # STAGE 4  Chroma writer/query layer
│   ├── intents.py                     # intent + PII + scheme/fact resolution
│   ├── retrieval.py                   # STAGE 5  hybrid + MMR + gate
│   ├── generation.py                  # STAGE 6  LLM adapter + extractive composer
│   ├── guardrails.py                  # STAGE 7  validators + refusal routing
│   ├── templates.py                   # answer/refusal/disclaimer strings
│   ├── prompts.py                     # system + user prompt construction
│   └── pipeline.py                    # orchestration: build() and answer()
├── eval/
│   ├── golden_questions.csv
│   ├── out_of_scope_probes.csv
│   ├── run_eval.py
│   └── report.md
├── docs/
│   ├── sources.md                     # deliverable: source list (MD twin of CSV)
│   └── sample_qa.md                   # deliverable: 5–10 Q&A with links
└── scripts/
    ├── build_index.ps1 / build_index.sh
    └── run_eval.ps1
```

### 5.2 Layering and allowed dependencies

```
   ┌──────────────────────────────────────────────────────┐
   │  presentation      app.py (Streamlit)              │
   └───────────────────────────┬──────────────────────────┘
                               │ calls
   ┌───────────────────────────▼──────────────────────────┐
   │  orchestration     pipeline.py                      │
   │  answer(query) -> Answer      build() -> BuildReport│
   └──────┬────────────┬─────────────┬─────────────┬─────┘
          │            │             │             │
   ┌──────▼─────┐ ┌────▼──────┐ ┌────▼──────┐ ┌────▼───────┐
   │ intents    │ │ retrieval │ │generation │ │guardrails  │  policy layer
   └──────┬─────┘ └────┬──────┘ └────┬──────┘ └────┬───────┘
          │            │             │             │
          └────────────┴──────┬──────┴─────────────┘
                              │ reads
   ┌──────────────────────────▼──────────────────────────┐
   │  corpus/stores   loading · chunking · embedding     │
   │                  store · registry · pii             │
   └──────────────────────────┬──────────────────────────┘
                              │ reads/writes
   ┌──────────────────────────▼──────────────────────────┐
   │  infrastructure  models · config · templates        │
   │                   prompts · ChromaDB · disk          │
   └─────────────────────────────────────────────────────┘
```

**Dependency rule (enforced by a unit test, §19.2):** `infrastructure` imports nothing from higher layers; `corpus` imports only `infrastructure`; `policy` may import `corpus` and `infrastructure`; `orchestration` may import all; `presentation` imports only `orchestration` + `models`. `retrieval` must never import `generation` — the gate decision must be independent of the generator (D1).

---

## 6. Component view

### 6.1 Components

| Component | File | Responsibility | Key collaborator |
| --- | --- | --- | --- |
| Config loader | `src/config.py` | Load/validate `config.yaml`, resolve paths, expose typed settings | — |
| Registry | `src/registry.py` | Load `sources.csv`; provide scheme lookup, alias→`scheme_id` resolution, citation allowlist, refusal link map | `sources.csv` |
| PII guard | `src/pii.py` | `detect(text) -> PIIHit[]`, `redact(text) -> (text, n_hits)` | `re` |
| Loader | `src/loading.py` | Fetch registered URL, snapshot to `data/raw`, clean to `data/processed`, emit `LoadedDoc` | registry, pii |
| Chunker | `src/chunking.py` | `LoadedDoc` → `list[ChunkRecord]` (semantic sections, tables intact) | config |
| Encoder | `src/embedding.py` | Singleton `SentenceTransformer`; `embed(list[str]) -> np.ndarray[384]` | model cache |
| Vector store | `src/store.py` | Create/upsert collection; `query(vector, n, where) -> list[ScoredChunk]`; `stats()` | ChromaDB |
| Intent resolver | `src/intents.py` | `classify(query) -> IntentResult{intent, scheme_id?, fact_family?, pii_hits}` | registry, pii |
| Retriever | `src/retrieval.py` | Query rewrite → dense search → lexical boost → MMR → grounding gate → `AssembledContext` | store, registry |
| Generator | `src/generation.py` | `LLMGenerator` (optional) and `ExtractiveGenerator` behind one protocol; returns `DraftAnswer` | prompts, templates |
| Guardrails | `src/guardrails.py` | Validate `DraftAnswer`; route refusals; build final `Answer` | templates, registry |
| Pipeline | `src/pipeline.py` | `build()` and `answer(query)`; wiring only, no logic | all |
| UI | `app.py` | Chat UI, chips, citation button, sources panel, disclaimers, stage stats | pipeline |

### 6.2 Data contracts (all in `src/models.py`)

Every stage boundary is a frozen dataclass. Stages never return raw tuples/dicts, and never mutate their input.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

class SourceType(str, Enum):
    SCHEME_PAGE = "scheme_page"
    FACTSHEET  = "factsheet"
    KIM        = "kim"
    SID        = "sid"
    FEE_PAGE   = "fee_page"
    EDUCATION  = "education"   # refusal-link sources only

class SectionType(str, Enum):
    FEES = "fees"            # expense ratio, TER, exit load, minimums
    LOCK_IN = "lock_in"      # ELSS lock-in, 80C
    RISK = "risk"            # riskometer, benchmark, horizon, objective
    TAX = "tax"              # statement / tax-doc guides
    GENERAL = "general"      # overview, scheme narrative

class FactFamily(str, Enum):
    EXPENSE_RATIO = "expense_ratio"
    EXIT_LOAD     = "exit_load"
    MIN_SIP       = "min_sip"
    LOCK_IN       = "lock_in"
    RISKOMETER    = "riskometer"
    BENCHMARK     = "benchmark"
    STATEMENTS    = "statements"
    OTHER         = "other"

class Intent(str, Enum):
    FACTUAL_FACT        = "factual_fact"
    ADVICE_REQUEST      = "advice_request"
    PERFORMANCE_REQUEST = "performance_request"
    PII_REQUEST         = "pii_request"
    OUT_OF_CORPUS       = "out_of_corpus"
    SMALLTALK           = "smalltalk"

@dataclass(frozen=True)
class SourceRecord:
    source_id: str; scheme_id: str; scheme_name: str; source_type: SourceType
    title: str; url: str; publisher: str
    allowed_for_citation: bool; fetched_at: str   # ISO-8601 date
    content_hash: str | None = None; notes: str = ""

@dataclass(frozen=True)
class LoadedDoc:
    source: SourceRecord
    raw_path: str            # data/raw/{source_id}.html|.md
    text_path: str           # data/processed/{source_id}.txt
    text: str                # cleaned, '#'-marked headings
    char_count: int
    redaction_hits: int      # count only, never values (D6)

@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str            # sha1(source_id|section|ordinal)
    source_id: str; scheme_id: str; scheme_name: str
    section: str; section_type: SectionType
    text: str                # body WITHOUT the context header
    embed_text: str          # header + body  (what gets embedded)
    url: str; title: str
    fetched_at: str
    ordinal: int
    token_count: int

@dataclass(frozen=True)
class ScoredChunk:
    chunk: ChunkRecord
    dense: float             # cosine, 0..1
    keyword_boost: float
    final: float             # dense + boosts
    mmr_selected: bool

@dataclass(frozen=True)
class AssembledContext:
    query: str
    scheme_id: str | None
    fact_family: FactFamily
    chunks: list[ScoredChunk]         # ordered, deduped, MMR-selected
    total_tokens: int
    top_score: float

@dataclass(frozen=True)
class DraftAnswer:
    text: str
    generator: Literal["llm", "extractive"]
    sentinels: list[str] = field(default_factory=list)   # NOT_IN_CORPUS / REFUSE
    raw_model_output: str | None = None                   # debug only, never rendered

@dataclass(frozen=True)
class Answer:
    text: str
    kind: Literal["factual", "not_in_corpus", "refusal", "performance_redirect",
                  "out_of_corpus", "pii_refusal", "smalltalk"]
    citation_url: str | None
    citation_source_id: str | None
    last_updated: str | None
    generator: str
    retrieved: list[ScoredChunk]         # for the UI "Sources used" panel
    trace: dict                          # intent, top_score, guardrail results
```

**Boundary invariants (asserted in code):**
- `AssembledContext.chunks` is non-empty whenever `kind == "factual"`.
- `Answer.citation_url` is `None` **iff** `kind` is a refusal/smalltalk variant *or* the refusal template supplies its own fixed link.
- No `Answer` field is ever populated from LLM output except `Answer.text` (and only after validation).

---

## 7. Stage 1 — Loading

### 7.1 Contract

**In:** `list[SourceRecord]` from `registry.load_sources()`
**Out:** `list[LoadedDoc]`, written to `data/raw/` and `data/processed/`
**Side effects:** disk writes only. No network during `answer()`.

### 7.2 Algorithm

```
for source in registry.sources():
    if source.source_type is EDUCATION: continue          # E1/E2 are refusal-only, never ingested
    assert_url_host_allowed(source.url)                   # C1 enforcement, before any socket opens
    client = new_http_client() if this source needs a fetch else None

    cached = data/raw/{source_id}.html|.md
    if cached.exists() and config.loading.offline_cache_first and not refresh:
        raw = read(cached)                                # snapshot is the source of truth (D3)
    else:
        raw = fetch_with_retry(source.url, timeout=20, retries=3, backoff=2**n)
        write(cached, raw)                                # never overwrite an existing snapshot by default

    text = clean(raw)                                     # §7.3
    text, n_redacted = pii.redact(text)                   # D6 — before chunking, before disk
    write(data/processed/{source_id}.txt, text)
    docs.append(LoadedDoc(..., text=text, redaction_hits=n_redacted))

assert_fact_coverage(docs, required=[...])
return docs
```

**No socket opens on a fully cached run.** The HTTP client is constructed *lazily*, on the first
source that actually needs a fetch, and closed in a `finally`. Eagerly building one client would
make an offline demo build look like a network dependency and would be defeated by a single
"no network in tests" tripwire even though zero requests are issued.

**Request pacing** is per *fetch*, not per source: the delay is applied only when the next source
is going to hit the network, so a cached re-run does not sleep five times for nothing.

### 7.3 Cleaning rules (`loading.clean`)

1. Parse with `BeautifulSoup(html, "lxml")`.
2. **Remove by selector**: `script, style, noscript, iframe, svg, header, footer, nav, title`, elements with `role="navigation"`, cookie/consent banners (`[class*="cookie" i]`, `[id*="consent" i]`), and any node whose text matches the boilerplate stop-list (§8.5).
3. **Remove performance widgets**: drop any node whose class attribute contains a substring listed in `config.loading.drop_class_substrings` (`returnCalculator`, `returnStats`, `returnsAndRankings`, `compareSimilarFunds`, `mfGraph`, …) and any text node matching `NAV: <date>`. This is a *structural* C3 control, not a cosmetic one: the live Groww pages embed a return calculator, a historic-return panel, a cross-fund 1Y/3Y comparison table, and a NAV chart, so a selector-and-boilerplate filter alone leaves returns and NAV in the corpus. Removing the widget's DOM node cannot leave a stray figure behind, whereas banning the word `return` in `guardrails` would only catch it in the answer.
4. **Remove the NAV's value with its label**: each stat tile renders as a wrapper holding a label `div` and a value `div`, so deleting the label alone orphans the figure and leaves a bare `1,189.08` with nothing to mark it as a NAV. `strip_performance_pairs` decomposes the label node's **grandparent**, guarded by a length check so the rule can never reach further up and delete a whole tile group.
5. **Remove the NAV clause from prose**: each page's summary sentence repeats it in copy no selector can reach ("… and the Latest NAV as of 25 Sep 2026 is ₹1,189.08."). `strip_nav_clauses` removes that clause whole, anchored on `Latest NAV as of <date> is <amount>`, so the sentence keeps its AUM fact and stays grammatical. Anchoring on the full phrase is what makes this safe: stripping on a bare `NAV` token would delete ordinary sentences that merely mention it.
6. **Remove site chrome**: the same config list carries `loggedOut_hover`, `loggedOut_navContainer`, `letterLinks`, `footerTopSection`, `footer_other`. Groww's mega-menu, its A–Z product index, and its footer product grid live in plain `div`s — the pages contain no `<nav>` or `<footer>` element at all — so ~150 lines of noise per page survive selector-based removal. This matters beyond tidiness: lines like `1M 6M 1Y 3Y 5Y All` contain digits and so pass §8.5's "short and digitless" boilerplate test.
7. **Normalise structure**: convert `h1..h6` → `#`…`######` lines, `<li>` → `- `, `<tr>`/`<td>` → `| cell | cell |` (one row per line), `<br>`/block ends → newlines.
8. **Collapse whitespace**: 3+ newlines → 2; trailing spaces stripped; non-breaking spaces normalised.
9. **Assert non-empty**: a page yielding < 400 chars is treated as a fetch/parse failure (this is how R1/JS-rendered pages surface loudly) and reported in `BuildReport.warnings`.

> **Status after Phase 3 (2026-09-27):** rules 3–6 were not in the original spec, and all four were added *after* reading `data/processed/` rather than the spike snapshots. The spike pages looked clean; the committed snapshots did not, and the first pass still left a cross-fund return table and an orphaned NAV. The lesson is recorded because it generalises: for this corpus, class-name and label-driven removal is the only C3 control that can be verified by reading the output, and a keyword blacklist verified against the wrong sample proves nothing. The processed corpus is now 7.4k–36k chars per page, down from 17k–46k, with every in-scope fact family intact.



**JS-rendered content (mitigation for R1):** if a registered source is annotated `render: md` in `sources.csv` (e.g. a manually saved rendered snapshot), the loader reads that `.md` and skips HTML parsing beyond light normalisation. This is why `data/raw/` is committed to the repo for a class demo — the corpus becomes deterministic and reviewable.

> **Status after spike S1 (2026-09-27):** the `render` column is **not** in the shipped `sources.csv`. The spike proved all five pages are server-rendered and yield 17k–46k chars, so no source needs the `.md` path. The loader still honours a `render` value if one is ever added — the branch costs one `if` and is what makes a future JS-only source survivable without a redesign.

### 7.4 Failure behaviour

| Condition | Behaviour |
| --- | --- |
| Network error + no snapshot | `BuildReport.failed_sources`, pipeline continues, warning surfaced in UI sidebar |
| HTTP 4xx/5xx | Same, with status recorded |
| 200 but < 400 chars extracted | Same + `PARSE_EMPTY` warning (blocks the "corpus complete" badge) |
| Duplicate `content_hash` across sources | Keep first, record `DUPLICATE_SOURCE` warning |
| PII found in page text | Redact, count, warn (`C2`) |

---

## 8. Stage 2 — Chunking

Design rationale and rejected alternatives are in `PRD.md` §9.3; this section is the implementation contract.

### 8.1 Contract

**In:** `LoadedDoc`
**Out:** `list[ChunkRecord]` (also dumped to `data/chunks.jsonl` for inspection)
**Config:** `config.chunking` (`max_tokens=600`, `min_tokens=80`, `overlap_tokens=60`, `merge_small_sections`, `preserve_tables`, `include_context_header`, `drop_boilerplate`)

### 8.2 Token accounting

Token counts use **the embedding model's own tokenizer** (`AutoTokenizer.from_pretrained(MODEL_ID)`), not `tiktoken`. Reason: `min_tokens`/`max_tokens` bounds and the embedding input must be measured in the same unit the encoder uses; using an unrelated tokenizer makes the bounds advisory rather than real (D3).

### 8.3 Algorithm

```
sections = parse_sections(doc.text)        # split on '^#{1,6} ' boundaries
out = []
for sec in sections:
    sec_type = classify_section(sec.heading, sec.body)   # → SectionType
    if drop_boilerplate(sec): continue

    units = split_into_units(sec)          # §8.4
    body_units, overlap_tail = [], []
    for unit in units:
        if est_tokens(body_units + [unit]) > max_tokens:
            out.append(make_chunk(body_units, sec, len(out)))   # flush
            body_units = tail_for_overlap(body_units)            # only if unit is prose
        body_units.append(unit)
    if body_units: out.append(make_chunk(body_units, sec, len(out)))

if merge_small_sections:
    out = merge_under_min_tokens(out)      # absorb into next sibling in the same doc; keep both headers
return dedupe_by_hash(out)
```

`make_chunk` composes:

```
header = f"[{scheme_name}] {section_heading}"          # e.g. "[HDFC Large Cap Fund - Direct Growth] Expense ratio and other fees"
embed_text = f"{header}\n{body}"                        # embedded (contextualised vector)
text       = body                                        # shown to LLM / UI, header excluded
chunk_id   = sha1(f"{source_id}|{section_heading}|{ordinal}")[:16]
```

`tail_for_overlap` returns the trailing units covering `overlap_tokens`, **but only when the section is prose** (`SectionType.GENERAL`/`RISK` narrative). For `FEES` and `TAX` sections `overlap = 0`, because overlapping a definition list or a table row range produces chunks that look like a different fee (PRD §9.3).

### 8.4 Unit splitting

| Unit content | Splitting rule |
| --- | --- |
| Table block (consecutive `\|` lines) | Keep header row with **every** group; group rows until `max_tokens`; never split a row |
| Definition list (`Term: value` lines) | Group consecutive `Term: value` lines up to `max_tokens` |
| Numbered/bulleted list | Group consecutive items |
| Prose paragraph | Paragraph is the atomic unit; split at sentence boundary only if a single paragraph alone exceeds `max_tokens` |

`classify_section` maps heading text → `SectionType` via keyword sets:
- `FEES`: expense, ratio, fee, charge, load, ter, aum, minimum, sip, amount, nav
- `LOCK_IN`: lock, 80c, tax saver, elss, holding period
- `RISK`: riskometer, benchmark, risk, objective, horizon, category, portfolio
- `TAX`: statement, tax, capital gain, report, download, how to
- `GENERAL`: everything else

`SectionType` drives (a) overlap suppression, (b) the `+0.03` metadata match boost at retrieval (§11.4), (c) the UI's section label.

### 8.5 Boilerplate filter

Drop a section (or a paragraph) when it matches any of:
- a curated stop-phrase list (`"disclaimer"`, `"mutual fund investments are subject to market risks"`, `"read more"`, `"know more"`, `"download app"`, `"log in"`, `"sign up"`, cookie/SEO text),
- < 25 tokens **and** no digits **and** no `SectionType.FEES/LOCK_IN` classification,
- link-density > 0.6 in a block of 5 lines.

### 8.6 Emitted statistics (feeds PRD §11.3 ablation A1)

`BuildReport.chunk_stats = {count, median_tokens, p10, p90, by_section_type: {…}, dropped_sections, merged_chunks}` — printed to console and exposed in the UI sidebar.

---

## 9. Stage 3 — Embedding

**In:** `list[str]` of `embed_text`
**Out:** `np.ndarray` shape `(n, 384)`, dtype `float32`, L2-normalised rows

```python
# src/embedding.py  — process-wide singleton (D5, NFR-2)
@lru_cache(maxsize=1)
def get_encoder() -> SentenceTransformer:
    return SentenceTransformer(
        MODEL_ID,                      # "sentence-transformers/all-MiniLM-L6-v2"
        cache_folder=str(settings.model_cache_dir),   # offline-capable (NFR-4)
        device="cpu",                  # demo laptops: CPU is fast enough at this corpus size
    )

def embed(texts: list[str]) -> np.ndarray:
    vecs = get_encoder().encode(
        texts, batch_size=settings.embedding.batch_size,   # 32
        normalize_embeddings=True,                          # cosine == dot product
        convert_to_numpy=True, show_progress_bar=False,
    )
    return vecs.astype("float32")
```

| Property | Value |
| --- | --- |
| Model | `sentence-transformers/all-MiniLM-L6-v2` (mandated by the brief) |
| Dimensions | 384 |
| Pooling | mean pooling (library default for this checkpoint) + L2 norm |
| Similarity | cosine |
| Corpus size | ~120–400 chunks → full-corpus embed ≈ 2–6 s on CPU; **never on the request path** |
| Query embed | ~15–40 ms (one string) |

**Pre-warm:** `app.py` calls `get_encoder()` and `store.connect()` in a `@st.cache_resource` block, so the ~1–3 s model load happens before the first question (NFR-2).

---

## 10. Stage 4 — Vector store (ChromaDB)

### 10.1 Collection definition

```python
# src/store.py
client = chromadb.PersistentClient(path=str(settings.chroma_dir))
collection = client.get_or_create_collection(
    name=settings.chroma.collection_name,            # "mf_faq_hdfc_v1"
    configuration={"hnsw": {"space": "cosine"}},     # chromadb >= 0.5
    metadata={"hnsw:space": "cosine", "description": "HDFC AMC MF FAQ facts v1"},
)
```

> **Compatibility note:** chromadb < 0.5 uses `metadata={"hnsw:space": "cosine"}` instead of `configuration=…`. The loader detects the installed version and passes the right kwarg — pinned in `requirements.txt` (`chromadb==0.5.x`) so this is belt-and-braces (R10).

### 10.2 Stored metadata per chunk

| Key | Type | Used by |
| --- | --- | --- |
| `chunk_id` | str | idempotent upsert key, dedup |
| `source_id` | str | citation lookup, rebuild diffing |
| `scheme_id` | str | metadata-filtered retrieval |
| `scheme_name` | str | UI label |
| `section` | str | citation label ("Expense ratio and other fees") |
| `section_type` | str | `SectionType` → metadata-match boost |
| `url` | str | citation rendering |
| `title` | str | citation label |
| `fetched_at` | str | `Last updated from sources:` stamp (C6) |
| `ordinal` | int | stable chunk identity across rebuilds |

Full body text lives in Chroma's document field; `data/chunks.jsonl` is the human-readable mirror for debugging and for the eval harness (avoids a DB read in offline evaluation).

### 10.3 Write path

```
chunk_id = sha1(source_id|section|ordinal)            # deterministic ⇒ idempotent (FR-17)
collection.upsert(ids=[...], embeddings=vecs.tolist(), documents=texts, metadatas=[...])
```

Rebuild command `python -m src.pipeline build --rebuild` deletes the collection and recreates it from `data/processed/` — no refetch (snapshot is authoritative), which is what makes M8's fresh-clone rehearsal fast and reliable.

### 10.4 Query path

```python
res = collection.query(
    query_embeddings=[qvec.tolist()],
    n_results=settings.retrieval.dense_k,          # 12
    where={"scheme_id": scheme_id} if scheme_id else None,
    include=["documents", "metadatas", "distances"],
)
```

Chroma returns cosine **distance**; converted to similarity as `similarity = 1 - distance` and clamped to `[0, 1]`. This is the value carried in `ScoredChunk.dense` and displayed in the UI.

### 10.5 Why Chroma (D7 rationale)

Zero-ops local persistence, in-query metadata filtering (needed for the scheme filter), and a visible on-disk store — a plus when the demo walks through "the vector data" stage. Trade-offs accepted: no distributed scale, rebuild is a directory copy, and HNSW parameters are not tuned (irrelevant below ~10⁴ chunks).

---

## 11. Stage 5 — Retrieval

### 11.1 Sub-stages

```
query ──▶ 5.1 intent + PII ──▶ [non-factual ⇒ REFUSE FAST PATH, no retrieval, no LLM]
      │                              (C3, C7, FR-20)
      ▼
      5.2 query contextualisation
      ▼
      5.3 dense candidate search (k=12, cosine, optional scheme filter)
      ▼
      5.4 lexical / metadata boost
      ▼
      5.5 MMR diversification (λ=0.3) → top 4–5
      ▼
      5.6 grounding gate (top_score < τ ⇒ NOT_IN_CORPUS)
      ▼
      5.7 context assembly (≤ ~1,800 tokens, labelled, numbered)
```

### 11.2 5.1 — Intent resolution (`intents.classify`)

Rule-first, ordered, and **exhaustive**; the first match wins, so ordering encodes precedence (safety before helpfulness).

| # | Rule (regex, case-insensitive) | Intent |
| --- | --- | --- |
| 1 | PAN / Aadhaar / account-number / OTP / email / phone patterns, or `"my pan"`, `"my folio"`, `"otp"` | `PII_REQUEST` |
| 2 | `should i\|should we\|would you\|do you think\|is it (a )?good\|recommend\|opinion\|best (fund\|option)\|worth (buying\|investing)\|allocate\|portfolio\|rebalance\|tax saving tips\|which one should` | `ADVICE_REQUEST` |
| 3 | `return\|performance\|nav\|cagr\|xirr\|profit\|loss\|gained\|growth of\|yield\|rank\|vs\b.*return\|best performing\|top performer` | `PERFORMANCE_REQUEST` |
| 4 | Explicit non-HDFC AMC/scheme name (from a `known_other_amcs` list in config) or `"parag\|axis\|icici\|sbi\|kotak\|tata\|nippon"` | `OUT_OF_CORPUS` |
| 5 | `hi\|hello\|thanks\|who are you\|what can you do` | `SMALLTALK` |
| 6 | A recognised fact-family term or scheme alias present | `FACTUAL_FACT` |
| 7 | Anything else | `FACTUAL_FACT` **with `needs_evidence=True`** → stricter gate, and the UI nudges toward supported questions |

**Design note (D1):** classification is deterministic and does not use the LLM. The safety fast path must be provably independent of model behaviour — this is what makes "100% refusal precision" in PRD §11.2 an engineering guarantee rather than a prompt hope.

Ambiguity handling: if rules 2 and 6 both match (e.g., *"What is the exit load on the fund I should buy?"*), rule 2 wins for routing, but the answer body still delivers the fact and closes with the facts-only notice — helpful **and** safe.

### 11.3 5.2 — Query contextualisation

Short queries embed poorly ("exit load?"). The embedded string is therefore:

```
f"[{scheme_name or 'HDFC mutual fund'}] {fact_family_label} — {original_query}"
```

where `fact_family_label` ∈ {`expense ratio and fees`, `exit load`, `minimum SIP amount`, `lock-in period`, `riskometer and benchmark`, `tax statements and reports`}. This injects the corpus's vocabulary at query time, compensating for vocabulary mismatch *without* touching the chunker (PRD §9.3).

Scheme alias resolution: ordered longest-suffix match against `registry.scheme_aliases` (`"large cap"`, `"largecap"`, `"hdfc large cap"`, `"elss"`, `"tax saver"`, `"flexi cap"`, `"equity fund"`, `"small cap"`, `"balanced advantage"`). Unresolved ⇒ no `where` filter (recall preserved).

### 11.4 5.4 — Boost scoring

```
final = dense + 0.05 * (exact fact-term match in chunk text)
             + 0.05 * (additional distinct fact-term match)
             + 0.03 * (chunk.section_type == expected_section_type)
             + 0.02 * (chunk.scheme_id == resolved scheme_id)
```

- `expected_section_type` mapping: `EXPENSE_RATIO|EXIT_LOAD|MIN_SIP → FEES`, `LOCK_IN → LOCK_IN`, `RISKOMETER|BENCHMARK → RISK`, `STATEMENTS → TAX`.
- Fact terms are matched on the **chunk text** (not just metadata), so the boost is evidence-based, and the matched term is shown in the UI's sources panel so the boost is explainable.
- Weights live in `config.retrieval.boosts` and are part of ablation A3 (PRD §11.3).

### 11.5 5.5 — MMR (implemented in-repo, no extra dependency)

```
selected, pool = [], candidates sorted desc by final
while pool and len(selected) < top_n:
    best = argmax over c in pool of
             λ * final(c) - (1-λ) * max_over(s in selected) cos(emb(c), emb(s))     # λ = 0.3
    selected.append(best); pool.remove(best)
```

Rationale: the five scheme pages contain near-identical fee tables; without MMR the top-5 collapses onto one scheme. Embeddings are already in hand from the store query, so MMR costs ~5×12 dot products — negligible.

### 11.6 5.6 — Grounding gate (D1's enforcement point)

```
τ = 0.35                                   # calibrated in §12
if top_score < τ  ->  NOT_IN_CORPUS answer (fixed template + scheme page link)
if needs_evidence and top_score < τ + 0.10 -> NOT_IN_CORPUS   # stricter for unclassified queries
```

Additionally, the gate requires **term coverage**: the assembled context must contain the detected fact term (or its `FactFamily` synonym set). Dense similarity alone can be high for a topically-similar-but-different fact ("direct growth" vs "regular"), so coverage is a hard AND-condition. This is what prevents the classic failure of a fluent, confident, wrong fee.

### 11.7 5.7 — Context assembly

```
[1] HDFC Equity Fund - Direct Growth | Expense ratio and other fees | source: <url>
    <chunk text, verbatim>
[2] ...
```

- Ordering: descending `final`.
- Dedupe: identical normalised text dropped.
- Budget: `retrieval.context_token_budget = 1800`. Chunks are appended whole until the budget would be exceeded; the **top-1 chunk is never truncated mid-number** (a half number is how invented digits appear).
- Total context for a typical query: 2–4 chunks, 400–900 tokens.

### 11.8 Retrieval cost

| Operation | Typical |
| --- | --- |
| Intent classification | < 1 ms |
| Query embed | 15–40 ms |
| Chroma query (k=12) | 5–30 ms |
| Boost + MMR | < 5 ms |
| **Total retrieval** | **< 100 ms** |

---

## 12. Threshold calibration procedure (τ = 0.35)

The gate is a tuned constant, so the tuning must be reproducible and re-runnable — it is also demo material (ablation A2).

```
1. For each golden question q_i, retrieve with τ = 0 (gate disabled).
2. Label chunk relevance by hand: correct iff  chunk.scheme_id == expected_scheme
                                  AND fact term of expected family present in chunk text
3. Record s_i = score of the best *relevant* chunk, t_i = score of the best *irrelevant* chunk.
4. Sweep τ ∈ {0.20 … 0.60} step 0.05; for each τ compute
       hit_rate(τ)   = |{ i : s_i ≥ τ }| / N                    (want ≥ 0.85)
       false_gate(τ) = |{ i : t_i ≥ τ }| / N                    (want 0)
5. Pick the smallest τ meeting both; expect ≈ 0.30–0.40 → default 0.35.
6. Persist the sweep table to eval/report.md; the chosen τ and its justification go in README.
```

If no τ satisfies both conditions, the fix is **not** to relax τ — it is to add a source page or improve the chunker's section boundaries. The gate is a correctness mechanism, not a tuning knob.

---

## 13. Guardrails and answer composition

### 13.1 Layered policy (defence in depth)

| Layer | Mechanism | Blocks |
| --- | --- | --- |
| L1 Intent gate (pre-retrieval) | Rule classifier, no LLM | advice, performance, PII, out-of-corpus |
| L2 Corpus boundary | `sources.csv` allowlist + `allowed_for_citation` | non-public, third-party, invented sources (C1) |
| L3 Grounding gate | similarity + term coverage | unanswerable questions |
| L4 Prompt contract | strict system prompt, context-only, sentinels | invention, verbosity, advice, returns |
| L5 Post-generation validation | 6 checks (§13.2) | bad output that slips past L1–L4 |
| L6 Answer template | system renders the citation, not the LLM | fabricated/duplicate/missing links (C5, C6) |
| L7 Extractive fallback | deterministic sentence selection | any LLM misbehaviour (D4) |

### 13.2 Post-generation validation (`guardrails.validate`)

Applied in order; the **first failure** triggers fallback to the extractive path (or to the appropriate template) and is recorded in `Answer.trace['guardrail']`.

| # | Check | Rule | Failure action |
| --- | --- | --- | --- |
| V1 | Sentinel | output == `NOT_IN_CORPUS` / `REFUSE` | route to matching template |
| V2 | Length | sentence count ≤ 3 (regex split on `[.!?]` + abbreviation guard) | extractive fallback |
| V3 | Non-empty / on-topic | ≥ 5 words, ≥ 1 term from the fact family or the top chunk's vocabulary | extractive fallback |
| V4 | **Numeric grounding** | every number+unit token in the output (e.g. `0.35%`, `3 years`, `Rs 500`) must appear in the assembled context, digit-for-digit | extractive fallback (blocks invented fees — C3) |
| V5 | Banned terms | none of `should, recommend, advisable, best, outperform, guaranteed, returns?, nav, buy, sell, hold, suitable for you, advice` | replace the final sentence with the facts-only notice, or extractive fallback |
| V6 | No URLs / no external refs | output contains no `http`, no "according to" attributions to non-registered sources | strip + extractive fallback (citation is the template's job) |

V4 is the highest-value check and the strongest argument for the architecture: it is a mechanical proof that no figure reached the user without appearing verbatim in a cited chunk. It is a genuine answer to "how do you know the model didn't make up the expense ratio?"

### 13.3 Constraint → enforcement matrix (PRD C1–C7)

| Constraint | Enforcement points (code) |
| --- | --- |
| C1 public sources only | `loading.ALLOWED_HOSTS`; `registry.allowed_for_citation`; L2 in §13.1; V6 |
| C2 no PII | `pii.redact` at ingest (§7.2); `intents` rule 1; logging filter (§14.3) |
| C3 no performance claims | rules 2–3 in `intents`; performance redirect template; V5; **no arithmetic exists in the codebase** — asserted by a unit test scanning for return/NAV computation helpers |
| C4 ≤ 3 sentences | template + V2 |
| C5 exactly one citation | `guardrails.build_answer` renders one URL from the top chunk; V6 strips LLM URLs |
| C6 last-updated stamp | `Answer.last_updated` = top chunk's `fetched_at` (source registry date, never "today") |
| C7 educational link on refusals | `templates.REFUSAL` / `PERFORMANCE_REDIRECT` with a fixed URL from the registry |

### 13.4 Refusal routing (kind → template)

| `Intent` / gate result | `Answer.kind` | Template + link |
| --- | --- | --- |
| `ADVICE_REQUEST` | `refusal` | Facts-only notice + `education_url` (registry) |
| `PERFORMANCE_REQUEST` | `performance_redirect` | "returns not covered" + `factsheet_url` for the resolved scheme. **Spike caveat:** no official factsheet index is fetchable (HDFC's host 403s), so `factsheet_index_url` is empty and this falls back to the resolved scheme's own page. Never emit an empty link. |
| `PII_REQUEST` | `pii_refusal` | "don't share identifiers" + official support `help_url` |
| `OUT_OF_CORPUS` | `out_of_corpus` | Corpus-scope statement + nearest in-scope scheme page link |
| `SMALLTALK` | `smalltalk` | Capability summary + 3 example questions |
| Gate fail / `NOT_IN_CORPUS` | `not_in_corpus` | "I don't have that in my sources for these 5 schemes" + scheme page link |
| Otherwise | `factual` | ≤ 3 sentences + **one** citation + last-updated stamp |

**Out-of-corpus nuance:** the honest, maximally useful behaviour is to name the boundary *and* stay helpful — "My sources cover HDFC AMC's 5 schemes only. If you meant the HDFC Flexi Cap fund, its expense ratio is listed here: [link]."

### 13.5 Answer template (the only place an answer is rendered)

```
{text}                                    # ≤3 sentences, validated
[ View source ]                           # exactly one button, url = top chunk's url
Last updated from sources: {fetched_at}   # source-page fetch date, not today's date
```

Non-factual kinds use the same skeleton so the UI has a single renderer (`kind` selects the copy and the link).

---

## 14. Privacy design (D6)

### 14.1 Detection (`pii.detect`)

Regex set, applied to queries and to ingested text. Each pattern is named for logging **counts only**.

| Pattern | Shape |
| --- | --- |
| `PAN` | `[A-Z]{5}[0-9]{4}[A-Z]` (case-sensitive: `Aaaaa1234A` is not a PAN) |
| `AADHAAR` | 12 digits with optional `X`/`x`, in a labelled context (`aadhaar`, `aadhar`, `uidai`) |
| `ACCOUNT_NO` | 8–18 consecutive digits, or labelled (`account`, `acct`, `folio`, `client id`) |
| `OTP` | 4–6 digit code near `otp\|code\|verification`; **always** label-gated |
| `EMAIL` | RFC-ish local@domain.tld |
| `PHONE_IN` | 10 digits with optional `+91` (after stripping separators), or an Indian landline `0XX-XXXXXXX` |

Only the `secret` group of a match is claimed, so the label that made a bare number detectable
("my OTP is 482913") survives redaction and the sentence still reads like a sentence.

**Claim order** (`pii.DETECTION_ORDER`, first pattern to reach a span wins):

1. `PAN` — the only pattern that must not be eaten by the 8–18 digit account rule.
2. `AADHAAR` — a 12-digit Aadhaar is also 12 digits, so the specific form must go first.
3. `OTP` — a labelled 6-digit code is below the account rule's floor but is still PII; a *bare*
   4–6 digit run is not claimed, because on a fund page it is a quantile or a rank.
4. `EMAIL` — independent of the numeric rules.
5. `PHONE_IN` — **before** `ACCOUNT_NO`, because a bare 10-digit run is far more often an Indian
   mobile than an account number, and the account rule's 8–18 digit range would otherwise claim
   it and mislabel what was removed. Landlines are claimed here for the same reason.
6. `ACCOUNT_NO` — the widest numeric rule goes last, so it only claims spans nothing else wanted.

> **Status after Phase 3 (2026-09-27):** rule 5 was originally specified as `ACCOUNT_NO` before
> `PHONE_IN`. The corpus contains a real 10-digit contact number, and the as-specified order
> redacted it as `[REDACTED:ACCOUNT_NO]` — safe, but factually the wrong kind, which would
> misreport a corpus statistic. The order above is the corrected one and `DETECTION_ORDER` is the
> single source of truth for it.


### 14.2 Redaction

`[REDACTED:EMAIL]` style placeholders preserve sentence shape (so the corpus stays readable and the embedding does not shift) while guaranteeing the secret is never persisted. Replacement is applied *before* any text is written to `data/processed/` or to Chroma.

### 14.3 Logging policy

| Logged | Never logged |
| --- | --- |
| Intent class, stage timings, chunk ids, scores, `content_hash`, guardrail verdicts, counts of PII hits | Raw query text (unless `LOG_QUERIES=true` for local debugging), PII values, LLM prompts containing user text |

Session state is in-memory only, cleared by the "Clear chat" control; no database, no cookies, no analytics (NFR-7).

---

## 15. Runtime flows

### 15.1 Offline build (`python -m src.pipeline build`)

```
registry.load_sources()
   │
   ├─▶ loading.load_all()      ──▶ data/raw/*, data/processed/*
   │        └─ pii.redact, assert_fact_coverage
   ├─▶ chunking.chunk_all()   ──▶ data/chunks.jsonl  (+ ChunkStats)
   ├─▶ embedding.embed_batch()──▶ np.ndarray (n, 384)
   ├─▶ store.upsert_all()     ──▶ data/chroma (collection mf_faq_hdfc_v1)
   └─▶ BuildReport { sources_ok, sources_failed, chunks, stats, warnings, duration }
            └─▶ prints summary; warnings surfaced in UI sidebar
```

Total expected runtime: < 60 s on CPU for 5 pages (network fetch dominates).

### 15.2 Online query — factual path

```
Streamlit chat_input
  → pipeline.answer(query)
      1. pii.detect(query)                         ── hit? → PII_REQUEST template
      2. intents.classify(query)                   ── rule 2/3/4/5? → template, STOP (no LLM)
      3. intents.resolve_scheme(), resolve_fact_family()
      4. retrieval.retrieve(...)  → AssembledContext | GateFailure
             4.1 embed contextualised query
             4.2 store.query(k=12, where=scheme_id?)
             4.3 boost → MMR(λ=0.3) → top 4–5
             4.4 grounding gate (τ, term coverage)
             4.5 assemble context (≤1800 tok)
      5. generation.generate(context, intent)      ── LLM → DraftAnswer | REFUSE | NOT_IN_CORPUS
      6. guardrails.validate(draft, context)        ── fail → extractive regenerate
      7. guardrails.build_answer(...)              ── Answer (+ citation, stamp, trace)
  → st.chat_message render + expander("Sources used")
```

### 15.3 Online query — degradation matrix

| Failure | Detection point | Behaviour | User sees |
| --- | --- | --- | --- |
| No network at query time | not needed | Corpus and index are local; nothing degrades | Normal answer |
| No `LLM_API_KEY` | `generation.resolve_generator()` | `ExtractiveGenerator` selected at startup, logged in sidebar | Extractive but correct + cited answer |
| LLM timeout / 5xx / rate limit | client wrapper, 1 retry, 8 s timeout | Fall back to extractive | Extractive answer; `trace.generator="extractive"` |
| LLM returns sentinel | V1 | Route to template | Refusal / not-in-corpus message |
| V2–V6 validation failure | `guardrails` | Extractive fallback | Extractive answer; `trace.guardrail="v4_numeric"` |
| Corpus not built | `store.stats()` at startup | Banner in sidebar: "Index empty — run `python -m src.pipeline build`" | No chat, actionable instruction |
| Unknown scheme in query | `resolve_scheme` | No `where` filter; fact family still enforced | Best in-corpus answer or gate message |

**Invariants under every row above:** at most one citation, ≤ 3 sentences, last-updated stamp present, no advice, no PII echo.

### 15.4 Session & concurrency

`@st.cache_resource` for the encoder, Chroma client, and registry (shared, read-only, safe across Streamlit sessions). Per-session state holds only the chat transcript and the last `Answer`. The pipeline is stateless and re-entrant; the build path must never run inside a session.

---

## 16. UI architecture (`app.py`)

| Region | Content | Notes |
| --- | --- | --- |
| Title | "Mutual Fund FAQ Assistant — Facts Only" + scope line (HDFC AMC · 5 schemes · public sources) | PRD FR-35 |
| Banner | Disclaimer (§ PRD 12), `st.warning` | Persistent, non-dismissible |
| Chips | Exactly 3 example questions (PRD FR-36); replaced contextually after a refusal (FR-41) | `st.button` → fills the input |
| Transcript | `st.chat_message` per turn; bot turn = answer text + `st.link_button("View source")` + `Last updated from sources: …` | One link button per answer (C5) |
| Sources expander | Per retrieved chunk: scheme, section, similarity score, matched boost term, chunk text (`st.caption`/`code`) | P3 transparency (FR-39) |
| Sidebar | Pipeline stats: pages, chunks, median tokens, model id, collection name, generator in use, last build report + warnings | PRD NFR-8 |
| Footer | Short disclaimer repeat + "Verify on the linked official source" | |
| Control | "Clear chat" (FR-42) | |

**Separation of concerns:** the UI holds no retrieval or prompt logic. It calls `pipeline.answer()` and renders an `Answer`. This keeps the demo narrative honest — what you see in the UI is exactly what the library returns, and the same call path is exercised by `eval/run_eval.py`.

---

## 17. Performance budget (NFR-3)

| Segment | Budget (p95) | Mechanism |
| --- | --- | --- |
| Intent + PII | 5 ms | regex |
| Query embedding | 60 ms | singleton model, 1 string |
| Chroma query | 40 ms | 384-d, ~300 vectors, HNSW |
| Boost + MMR + assemble | 10 ms | numpy |
| LLM generation (optional) | 4,000 ms | temp ≤ 0.2, `max_tokens` 220, 1 retry |
| Validation + render | 10 ms | pure Python |
| **Total (LLM path)** | **< 4.2 s** | |
| **Total (extractive path)** | **< 150 ms** | |

Memory: MiniLM ≈ 90 MB RSS; Chroma client ≈ negligible at this scale; full-corpus embedding array ≈ 0.5 MB. Cold start (model + client init) ≈ 2–4 s, performed once inside `st.cache_resource` (NFR-2).

---

## 18. Deployment and reproducibility

### 18.1 Modes

| Mode | Use | Requirements |
| --- | --- | --- |
| **Local (primary demo)** | Laptop + projector | Python 3.11, deps, committed `data/raw` snapshots, built index, optional LLM key |
| **Deployed link** | Submission artefact | Streamlit Community Cloud / any host; `data/chroma` + model cache baked into the image or rebuilt on first run |
| **Offline / air-gapped** | Rehearsal proof (NFR-4) | Same as local, `loading.offline_cache_first=true`, no `LLM_API_KEY` → extractive path |

### 18.2 Runtime configuration

All knobs live in `config.yaml`; `src/config.py` validates and exposes them. Environment variables hold only secrets and machine-specific paths (`.env`, git-ignored). **No magic numbers in stage code** — every constant in this document appears in `config.yaml` and nowhere else (enforced by review + ablation reproducibility).

### 18.3 Reproducibility controls (D3)

- `requirements.txt` fully pinned, including transitive deps (`pip freeze`).
- `config.yaml` records `embedding.model_id`, `chunking.*`, `retrieval.*`, and `chroma.collection_name`.
- `sources.csv` records `fetched_at` and `content_hash` per source; the build report prints them.
- Deterministic `chunk_id` ⇒ rebuild produces byte-identical metadata.
- `config.lock.json` (written at build time) captures resolved config + package versions + corpus hash, and is pasted into the README so the demo is exactly reproducible.

---

## 19. Observability and testing

### 19.1 Debug UX (a deliverable in its own right for P3)

| Surface | Content |
| --- | --- |
| Structured stage log | `Answer.trace`: intent, scheme, fact family, candidates (id, dense, boost, final), MMR picks, gate decision + τ, generator, guardrail verdicts, timings |
| Sources panel (UI) | Retrieved chunk text + scores + boost term |
| Build report | Per-source status, chunk stats, warnings, timings |
| `data/chunks.jsonl` | Every chunk as stored — inspect any citation's origin without running the app |
| Stage CLI | `python -m src.chunking --doc S1`, `python -m src.retrieval --query "exit load?"` — each stage runnable in isolation (NFR-9, D2) |

### 19.2 Test architecture

| Layer | Scope | Examples |
| --- | --- | --- |
| Unit | pure functions, no I/O | chunker on a synthetic fee page; `pii.detect` on PAN/email/Aadhaar strings; intent rules incl. adversarial phrasings; MMR selection; sentence counter; **V4 numeric validator** (context has `0.35%` → output `0.45%` must fail) |
| Integration | one real page, no network | `clean()` on a committed snapshot; end-to-end `LoadedDoc → chunks → vectors → store` into a temp Chroma dir; `answer()` with a stubbed generator |
| Contract | stage boundaries | every stage honours its dataclass in/out; no stage returns `None` silently |
| Layering | architecture rules | `test_no_forbidden_imports` — `retrieval` must not import `generation`; `loading` must not import `retrieval`; infrastructure imports nothing upward (§5.2) |
| End-to-end | full pipeline | `build()` then 20 golden questions → `Answer`; assert citation ∈ registry, ≤ 3 sentences, stamp present |
| Guardrail suite | policy | 8 out-of-scope probes: all refused/redirected with the right `kind` and a registry link; 0 false refusals across the 20 factual questions |
| Privacy | policy | PII probe in query → `pii_refusal`, value absent from transcript, logs, and `data/` |
| Eval harness | quality | `eval/run_eval.py` computes the PRD §11.2 metric table; ablations A1–A4 re-runnable with `--variant` flags |
| Smoke (rehearsal) | operability | fresh clone → setup → build → 5 questions → clear chat; and the same with network disabled |

Test-to-requirement mapping lives in `eval/report.md` so the grader can see that every acceptance checkbox has a test behind it.

---

## 20. Configuration reference

```yaml
paths:
  raw_dir: data/raw
  processed_dir: data/processed
  chroma_dir: data/chroma
  chunks_dump: data/chunks.jsonl
  sources_csv: data/sources.csv
  model_cache_dir: data/models

embedding:
  model_id: sentence-transformers/all-MiniLM-L6-v2
  batch_size: 32
  device: cpu
  normalize: true

chunking:
  strategy: semantic_section          # | fixed_512   (A1 ablation)
  max_tokens: 600
  min_tokens: 80
  overlap_tokens: 60                 # prose only
  preserve_tables: true
  include_context_header: true
  drop_boilerplate: true
  merge_small_sections: true

chroma:
  collection_name: mf_faq_hdfc_v1
  space: cosine

retrieval:
  dense_k: 12
  top_n: 5
  mmr_lambda: 0.3
  context_token_budget: 1800
  gate_threshold: 0.35              # §12 calibration
  unclassified_gate_margin: 0.10
  require_term_coverage: true
  boosts:
    fact_term: 0.05
    additional_fact_term: 0.05
    section_type_match: 0.03
    scheme_match: 0.02
  fact_terms:                       # fact term → FactFamily synonyms for coverage checks
    expense_ratio: ["expense ratio", "ter", "expense ratio and other fees", "charges"]
    exit_load:     ["exit load", "exit charges", "load"]
    min_sip:       ["minimum sip", "min sip", "minimum investment", "minimum amount"]
    lock_in:       ["lock-in", "lock in", "3 years", "80c", "tax saver"]
    riskometer:    ["riskometer", "risk"]
    benchmark:     ["benchmark", "index"]
    statements:    ["capital gains", "statement", "tax report", "download"]

generation:
  provider: auto                    # auto | llm | extractive   (ablation A4)
  model: <free-tier model id>
  temperature: 0.1
  max_tokens: 220
  timeout_s: 8
  retries: 1

loading:
  offline_cache_first: true
  request_delay_s: 1.0
  timeout_s: 20
  retries: 3
  min_extracted_chars: 400
  allowed_hosts: [groww.in, hdfcmutualfund.com, amfiindia.com, sebi.gov.in]

guardrails:
  max_sentences: 3
  enforce_numeric_grounding: true
  banned_terms: [should, recommend, advisable, best, outperform, guaranteed, return, returns, nav, buy, sell, hold, suitable]

registry:
  known_other_amcs: [parag, axis, icici, sbi, kotak, tata nippon, nippon, quant, mahindra, icici prudential]
  education_url: <SEBI/AMFI investor-education page>
  help_url: <official support page>
  factsheet_index_url: <AMC factsheets index>
```

`config.yaml` also carries the disclaimer/refusal copy so the UI, the LLM prompt, and the README all quote **one** string (PRD deliverable "disclaimer snippet").

---

## 21. Spike plan (do these first — they de-risk the two scariest items)

| Spike | Question | Time-box | Success criterion |
| --- | --- | --- | --- |
| **S1 — corpus reality check** (kills R1) | Do the 5 Groww pages expose expense ratio, exit load, minimum SIP, lock-in, riskometer, benchmark in retrievable text — static or rendered? | 2 h | All 7 fact families found in ≥ 4 of 5 schemes. If not: switch the source set to HDFC AMC's own scheme/fee pages and update `sources.csv` + PRD §5.1 before writing any pipeline code. |
| **S2 — embedding signal** | Does MiniLM + section chunks separate schemes and fact families well enough for `top_score` to be discriminative? | 1 h | Cosine separation between correct and incorrect chunks is visible; early evidence for τ ≈ 0.35. |
| **S3 — LLM availability** (Q1) | Which model/key will exist on demo day, and does it honour the sentinel + length contract? | 1 h | Model returns `REFUSE`/`NOT_IN_CORPUS` correctly on 8 probes. Otherwise: run the demo in `provider: extractive` (a legitimate, defensible demo mode) and show the prompt contract as design. |
| **S4 — Streamlit cold start** | Does the app hit the < 10 s cold-start budget on the demo laptop? | 30 min | Measured and recorded in README. |

S1 and S2 gate M1/M2. **Do not build the pipeline before S1 returns** — the corpus is the only assumption that can invalidate the chunking decision.

### 21.1 S1 result — corpus reality check (run 2026-09-27)

Full evidence in `docs/corpus_matrix.md`. Outcome: **partially met**, and the fallback the success
criterion calls for was *not* available, so the source set stayed as briefed with two corrections.

| Question | Result |
| --- | --- |
| Are the 5 pages fetchable and server-rendered? | 4 of 5 yes. S3's briefed URL (`…-direct-growth`) is **HTTP 404**; the live slug is `…-direct-plan-growth`. All 5 yield 17k–46k chars of text, so no headless-render path is needed. |
| Are expense ratio, exit load, min SIP, min lump, benchmark retrievable? | **Yes, on all 5** pages. |
| Is the ELSS lock-in retrievable? | **No.** "lock-in" never appears; matches were a related-funds nav list. |
| Is the riskometer retrievable? | **No.** Pages say "rated Very High risk"; the word "riskometer" is absent. |
| Are statement-download steps retrievable? | **No.** Official channels (CAMS, `investor.hdfcfund.com`) are login-gated, a stated non-goal. |

The success criterion's remedy — "switch to HDFC AMC's own scheme/fee pages" — was attempted and
**failed**: `hdfcfund.com` returns **HTTP 403** to a scripted client on both the scheme page and
the statutory riskometer page, and `sebi.gov.in` resets the connection from this environment
(`investor.gov.in` does not resolve). Third-party publishers that *do* serve these facts were
rejected under constraint C1.

**Consequence for the design:** the answerable set is 5 fact families, not 7. `RISKOMETER`,
`LOCK_IN` and `STATEMENTS` remain in `retrieval.fact_terms` so the system recognises the question
and returns the "not in my sources" path with a link, instead of hallucinating. `factsheet_index_url`
stays empty because no official factsheet index is fetchable; the performance redirect degrades to
the scheme page rather than shipping an invented URL. The `renderer` field described in §7 is
**dropped** from `sources.csv` — it exists only for JS-rendered pages, and the spike proved these
are not.

---

## 22. Evolution path (designed for, not built)

| Extension | Architectural hook | Cost |
| --- | --- | --- |
| Cross-encoder reranking | Insert a `Reranker` between `store.query` and boost/MMR; `retrieval` already owns candidate ordering | small |
| Parent-child (small-to-big) retrieval | `ChunkRecord` gains `parent_id`; Chroma indexes children, assembly returns parents | medium |
| More AMCs / schemes | `sources.csv` + alias config only (D7) | config only |
| Statement/tax guides from a wider corpus | Same pipeline; `SectionType.TAX` already routes those queries | config only |
| Structured fee extraction (fees as JSON) | New stage emitting `FactRecord`, validated against chunk text by the same V4 check | medium |
| Hosted API for the same pipeline | Wrap `pipeline.answer` in FastAPI; the `Answer` dataclass is already the response model | small |
| Any new corpus (e.g. insurance FAQs) | Replace `registry` + `fact_terms` + intent rules; stages 1–7 unchanged | config |

---

## 23. Architecture decision records (summary)

| ADR | Decision | Alternatives rejected | Consequence |
| --- | --- | --- | --- |
| A-01 | Hand-written staged pipeline, not a framework | LangChain/LlamaIndex graph | More code, but stages are inspectable and independently runnable (D2) — the point of the demo |
| A-02 | Semantic section chunking, tables without overlap | fixed 512, recursive char, one-per-page, per-sentence, parent-child | Requires cleaned text with intact structure; justified in PRD §9.3 and measured by A1 |
| A-03 | MiniLM-L6-v2 + Chroma (cosine, local persistence) | OpenAI embeddings, FAISS, Pinecone, Qdrant | Free, offline-capable, mandated by the brief; no vendor key, no network at query time |
| A-04 | Citation rendered by the system, never by the LLM | letting the model emit links | Makes C5 mechanically enforceable; removes the entire fabricated-URL failure class |
| A-05 | Rule-based intent gate before retrieval | LLM classifier | Deterministic 100% refusal precision; no model call for unsafe/irrelevant queries; auditable |
| A-06 | LLM as optional adapter + extractive fallback | LLM required | Demo cannot die on a missing key (D4); also enables ablation A4 |
| A-07 | Snapshot-based corpus, committed `data/raw` | live crawl at query time | Deterministic, offline-capable, reviewable by the grader; staleness is disclosed via `Last updated from sources` (C6) |
| A-08 | Chroma metadata filtering for the scheme filter | filter in Python post-hoc | Filtering happens inside ANN search, so recall is preserved when a scheme is resolved |
| A-09 | MMR with hand-rolled numpy | top-k by score only; library MMR | Near-duplicate fee chunks otherwise collapse the context; no extra dependency |
| A-10 | Numeric grounding validator (V4) | relying on the prompt only | Mechanical proof that no figure was invented; a distinctive, demonstrable safety property |

---

*End of architecture v1.0. Companion to `PRD.md` v1.0. Next revision after spikes S1–S2 and the §12 threshold sweep.*
