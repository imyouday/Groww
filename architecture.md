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
| Vector store | `src/store.py` | Create/upsert collection; `query(vector, n, where) -> list[ScoredChunk]`; `vectors_for(ids) -> dict[str, np.ndarray]` (Phase 6, for MMR); `stats()` | ChromaDB |
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
    matched_terms: list[str] = field(default_factory=list)   # added Phase 6, §11.4

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

The bound is then clamped to **what the model will actually accept**, which is not what the tokenizer advertises. `all-MiniLM-L6-v2` ships a 512-token tokenizer but its `sentence_bert_config.json` sets `max_seq_length` to **256**, and `sentence-transformers` truncates there. `model_token_ceiling()` reads that one JSON file (no model load) and the effective text bound is `min(config.max_tokens, 256 - 2) = 254`. The two special tokens are subtracted because the encoder adds them inside the limit.

This is not a detail: clamping to the tokenizer's 512 produced 510-token chunks whose second half the encoder discarded, so the stored text was not the embedded text. The corpus first chunked to 68 pieces that way and to 106 with the correct ceiling. `chunking.max_tokens=600` stays in `config.yaml` as the requested value; the clamp is logged as a warning so the discrepancy is visible rather than silent.

### 8.3 Algorithm

```
sections = parse_sections(doc.text)        # split on '^#{1,6} ' boundaries
out = []
for sec in sections:
    sec_type = classify_section(sec.heading, sec.body)   # → SectionType
    if drop_boilerplate(sec): continue

    units = split_into_units(sec)          # §8.4
    body_units = []
    for unit in units:
        if tokens(body_units) + tokens(unit) > max_tokens:
            out.append(make_chunk(body_units, sec, len(out)))       # flush
            body_units = seed_overlap(body_units, unit)             # only if sec_type is prose
        body_units.append(unit)
    if body_units: out.append(make_chunk(body_units, sec, len(out)))
    # a section that produced more than one chunk drops any draft below MIN_CHUNK_TOKENS;
    # a section that produced exactly one keeps it, however short (§8.7)

if merge_small_sections:
    out = merge_small_sections(out)        # absorb into a same-type sibling; keep both headings
return dedupe_by_hash(out)
```

`seed_overlap` is `tail_for_overlap` **plus a trim**: after a flush the tail is dropped from its oldest unit until the incoming unit fits inside `max_tokens`. Without the trim, seeding a 254-token unit with a 41-token tail and then appending the unit produced a 295-token chunk followed by a 361-token chunk — the bound was tested before the flush and never tested again. The trim sacrifices the older half of the tail, because the tail exists to carry the sentence immediately before the boundary.

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

A label line is at most `LABEL_MAX_WORDS = 5` words. A label is a noun phrase, not a clause: at eight words "Mr. Dhruv has done B.Com, CA and CFA" was read as the label of the line below it, which left "Education" as a chunk holding one word and pushed the qualification away from the label it belongs to.

Prose is split **losslessly at sentence boundaries, with abbreviations respected**. `Mr.` and `B.` are not sentence ends, so "Mr. Dhruv has done B.Com, CA and CFA" stays one piece; the naive split rewrote the manager's qualification as two fragments in the stored text. If even one sentence exceeds the whole budget, word splitting is the last resort. An earlier version stopped packing once the budget was reached and silently discarded the rest of the paragraph — six of thirteen sentences of S1's mandate were lost while every count and median still looked healthy. `test_no_kept_section_body_is_lost_on_the_real_corpus` is the regression test: every word of every kept section body must appear in some chunk.

`classify_section` maps heading text → `SectionType` via keyword sets:
- `FEES`: expense, ratio, fee, charge, load, ter, aum, minimum, sip, amount
- `LOCK_IN`: lock, 80c, tax saver, elss, holding period
- `RISK`: riskometer, benchmark, risk, objective, horizon, category, portfolio
- `TAX`: statement, tax, capital gain, report, download, how to
- `GENERAL`: everything else

Keywords are checked heading-first and the family order is `TAX` → `LOCK_IN` → `RISK` → `FEES` → `GENERAL`, so an ELSS tax section is not read as a fee section. `nav` is deliberately absent: the loader already removes NAV, and a heading that says "nav" is chrome, not a fact.

`SectionType` drives (a) overlap suppression, (b) the `+0.03` metadata match boost at retrieval (§11.4), (c) the UI's section label. On the current corpus the mix is 79 `FEES` / 17 `TAX` / 5 `RISK` / 5 `GENERAL`, so the §11.4 boost discriminates weakly and is kept only because it costs nothing; it is a candidate for removal if the eval shows no gain.

### 8.5 Boilerplate filter

Drop a section when it matches any of:
- a curated stop-phrase list (`"disclaimer"`, `"mutual fund investments are subject to market risks"`, `"read more"`, `"know more"`, `"download app"`, `"log in"`, `"sign up"`, cookie/SEO text),
- a chrome heading (`disclosures`, `terms and conditions`, `privacy policy`, `about us`, `contact us`, `our offices`, `references`, `appendix`, …) — a substring test on wording misses reformatted promo text, a heading is stable,
- < 25 tokens **and** no digits **and** no `FEES`/`LOCK_IN` classification. Both conditions are required: "Rs 100" is a fact in three words.

Link density is **not** a chunking rule. It is enforced in the loader (§7.4), where the surrounding markup is still available; by the time a section is split into units, a line of text that was 80% links has already been separated from its links. Recorded here because §8.5 in earlier drafts claimed it.

### 8.6 Emitted statistics (feeds PRD §11.3 ablation A1)

`chunk_stats(chunks) = {count, median_tokens, p10_tokens, p90_tokens, max_tokens, by_section_type: {…}}`; `chunk_document_with_stats` adds `{dropped_sections, merged_chunks, deduped_chunks, dropped_fragments}` for the same document. All are printed by `python -m src.chunking` and folded into `BuildReport` for the UI sidebar. The per-document counters exist because a chunk count alone hides a misfire: a corpus reaches a healthy median while the chunker has deleted the tax sections as boilerplate or shredded every fee table.

Measured on the committed corpus, `max_tokens=254`, `overlap_tokens=60`:

| Variant | Requested | Effective | Chunks | Median | p10 | p90 | Max | `by_section_type` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| configured `semantic_section` | 600 | 254 | 106 | 229 | 38 | 252 | 254 | 79 fees / 17 tax / 5 risk / 5 general |
| `--variant semantic_150` | 150 | 150 | 162 | 137 | 41 | 149 | 150 | 125 fees / 22 tax / 9 risk / 6 general |
| `--variant semantic_350` | 350 | 254 | 106 | 229 | 38 | 252 | 254 | identical to configured |
| `--variant semantic_600` | 600 | 254 | 106 | 229 | 38 | 252 | 254 | identical to configured |
| `--variant fixed_512` | 512 | 254 | 94 | 244 | 224 | 253 | 254 | 94 general, no section labels |

`semantic_350` and `semantic_600` are the same run, because both exceed the model's 256 ceiling. implementation.md names those three variants, so the names are kept; `semantic_150` was added because a sweep whose arms are all clamped to the same value measures nothing. The configured strategy stays at `max_tokens=600` clamped to 254, which lands inside the PRD's 80–400 chunk / 150–450 median target — 68 chunks, just under the floor, was the *clamped-to-512* artefact, not a real operating point.

`fixed_512` is the rejected baseline kept for the ablation: 12 fewer chunks, a median of 244 against 229, every chunk labelled `general` with the heading `Overview`, and holdings tables cut without a repeated header, so a fee question retrieves a chunk of stock names.

### 8.7 Merge and fragment rules

`merge_small_sections` absorbs a draft below `min_tokens` into a **previous sibling of the same `SectionType`**, keeping both headings joined with `" / "`, and refuses three ways:

1. **Different `SectionType`.** On S1 a 12-token "Exit load" chunk, a 4-token "Stamp duty" chunk and a 48-token "Tax implication" chunk sit next to a 94-token fund-manager bio; a positional merge packs the manager into a tax chunk.
2. **A table on exactly one side.** Appending "Min. for SIP: Rs 100" to the tail of a 350-token holdings table produces a chunk that spends its budget on 50 stock names and answers the fee question from its last line.
3. **Over `max_tokens`.** A truncated fee block looks complete and is not.

A small draft that survives all three is left alone: it is still retrievable, and a wrongly themed chunk is worse. Both headings are kept on a merge so the chunk's own name still says what it covers.

A section that yields exactly one draft keeps it whatever its length, because it *is* the section — S3's exit load is the two words "Exit load" and "Nil", and a floor applied to every chunk deleted that fact. `MIN_CHUNK_TOKENS = 4` applies only to sections that split, where a below-floor draft is the residue of a mis-paired label and costs a slot in the top-k budget to say nothing.

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
| Corpus size | ~120–400 chunks → full-corpus embed ≈ 15 s for 106 chunks on CPU (measured, Phase 5); **never on the request path** |
| Query embed | ~15–40 ms (one string, warm) |

**Pre-warm:** `app.py` calls `get_encoder()`, one `embed()` call and `store.connect()` in a `@st.cache_resource` block, so the model load happens before the first question (NFR-2). The `embed()` call is not optional: measured in Phase 5, `get_encoder()` costs ~6 s and the *first* `embed()` still costs ~4.7 s more on its own (tokenizer and torch thread-pool initialisation), while every later single-string embed is 20–35 ms. A pre-warm that only loads the model leaves ~4.7 s sitting on the first user question.

**Missing weights (measured, Phase 5 fix):** `data/models/` is git-ignored, so a machine that has never run this phase has no encoder. The underlying failure is a bare `OSError` from inside huggingface_hub, which is a stack trace from a transitive dependency and not an acceptable answer to a demo machine. `get_encoder()` converts it to `ModelNotCachedError`, naming the cache directory and both remedies (build once while online, or copy `data/models/` across). Verified by moving `data/models/` aside with `HF_HUB_OFFLINE=1`: the typed error is raised with no traceback, where previously the raw `OSError` surfaced.

**Singleton and injected settings:** the encoder is an `lru_cache` singleton, so its model is fixed for the life of the process and `embed(texts, settings)` cannot change it. Rather than let a caller pass a `model_id` that is silently ignored — and get vectors from one model recorded under another's id — `embed()` compares the requested `model_id` with the loaded one and raises `PipelineError` on a mismatch. The same rule is why the fresh-machine test must move the directory rather than inject a temporary `model_cache_dir`.

---

## 10. Stage 4 — Vector store (ChromaDB)

### 10.1 Collection definition

```python
# src/store.py
client = chromadb.PersistentClient(
    path=str(settings.paths.chroma_dir),
    settings=chromadb.config.Settings(
        anonymized_telemetry=False,
        chroma_product_telemetry_impl="src.store.NoTelemetry",
    ),
)
collection = client.get_or_create_collection(
    name=settings.chroma.collection_name,            # "mf_faq_hdfc_v1"
    configuration=CollectionConfigurationInternal(
        parameters=[ConfigurationParameter(name="hnsw_configuration", value=HNSWConfigurationInternal(
            parameters=[ConfigurationParameter(name="space", value=settings.chroma.space)]))]
    ),
    metadata={"description": settings.chroma.description},   # "HDFC AMC MF FAQ facts v1"
)
```

> **Compatibility note (measured, Phase 5):** the pinned `chromadb==0.5.23` does **not** accept the `configuration={"hnsw": {"space": "cosine"}}` mapping written here before the phase, nor the public `CollectionConfiguration` interface — the mapping has no `to_json`, and the interface serialises as `CollectionConfigurationInterface`, which 0.5.23's own `from_json` then refuses. Only the *internal* typed classes work. chromadb < 0.5 uses `metadata={"hnsw:space": "cosine"}` instead of `configuration=…`. `store.collection_configuration()` branches on the parsed version and the probe result (R10).

> **Telemetry note (measured, Phase 5):** `anonymized_telemetry=False` alone is *not* sufficient on 0.5.23. `Posthog` is still constructed, its `capture(user_id, name, properties)` call does not match the installed posthog signature, and `_direct_capture` logs `Failed to send telemetry event …` on **every** client creation. Because a demo whose start-up prints telemetry failures fails NFR-7 outright, `src/store.py` defines `NoTelemetry(chromadb.config.Component)` — a component whose `capture()` returns `None` — and names it in `chroma_product_telemetry_impl`. It extends `Component` rather than `ProductTelemetryClient` because the latter is enforced by the `overrides` package, which would make this module import a package that is only chromadb's own dependency.

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
| `token_count` | int | context-budget arithmetic in retrieval (D4) |

Full body text lives in Chroma's document field; `data/chunks.jsonl` is the human-readable mirror for debugging and for the eval harness (avoids a DB read in offline evaluation).

`section_type` is stored as the enum's *string value*, never its `repr`, so a rename cannot silently change what is on disk. `embed_text` is **not** stored: it is derived on read as `f"[{scheme_name}] {section}\n{document}"`, which is exactly §8.3's construction, so the store holds the body once and the header once rather than twice. Every value is a `str` or `int` — Chroma rejects `None`, and a single `None` fails the whole upsert, which is why the coercion lives in one function, `store.metadata_for()`.

### 10.3 Write path

```
chunk_id = sha1(source_id|section|ordinal)            # deterministic ⇒ idempotent (FR-17)
collection.upsert(ids=[...], embeddings=vecs.tolist(), documents=texts, metadatas=[...])
collection.delete(ids=[ids no longer produced by the corpus])
```

Rebuild command `python -m src.pipeline build --rebuild` deletes the collection and recreates it from `data/processed/` — no refetch (snapshot is authoritative), which is what makes M8's fresh-clone rehearsal fast and reliable.

The delete step is not a nicety. A deterministic `chunk_id` makes a *repeat* build idempotent, but it makes a *changed* build cumulative: if a source is dropped or a section splits differently, the ids that no longer exist stay in the collection, `count()` outruns the real chunk count, and retrieval serves text that is not in `data/chunks.jsonl`. `store.delete_missing()` closes that gap, and `build()` calls it after every upsert so a plain `build` is always exact.

### 10.4 Query path

```python
res = collection.query(
    query_embeddings=[qvec.tolist()],
    n_results=settings.retrieval.dense_k,          # 12
    where={"scheme_id": scheme_id} if scheme_id else None,
    include=["documents", "metadatas", "embeddings"],
)
similarity = min(1.0, max(0.0, float(stored_embedding @ qvec)))
```

> **Similarity note (measured, Phase 5):** this section previously converted Chroma's distance as `similarity = 1 - distance`, and that is **wrong for `chromadb==0.5.23`**. Its `cosine` space returns `2 - 2·cos` — the squared L2 distance of the two unit vectors — so a self-match is `0.0`, an orthogonal pair is `2.0`, and a typical matched pair around `cos = 0.45` reports `1.03`. `1 - distance` therefore clamps to `0.0` for essentially every hit, which is exactly what the first Phase 5 build did: a correct ranking behind a column of zeros. The score is now recomputed as the dot product of the query with each returned candidate's stored vector and clamped to `[0, 1]`. §9 already guarantees both operands are L2-normalised unit vectors, so the dot product *is* the cosine similarity, exactly, and the conversion no longer depends on which of chroma's two cosine conventions the installed version uses. HNSW still selects the candidate set; only a candidate's score is recomputed, which is what makes the percentage shown in the UI trustworthy. `tests/test_store.py` pins this with a self-match of 1.0 *and* an orthogonal pair of 0.0 — a self-match alone would also pass under `1 - distance`.

Calibration for the §12 gate, measured on the Phase 5 index (106 chunks, MiniLM-L6-v2, CPU): strong matches (`what is the minimum amount for a monthly SIP` → *Minimum investments / Exit load*) score **≈ 0.48**, weak ones (`is there an exit load` → same chunk) **≈ 0.16**, and unrelated ones **≈ 0.03**. Short FAQ chunks against short questions produce low absolute cosines, so `retrieval.gate_threshold` must be set from this distribution rather than from intuition about cosine similarity. The configured `0.35` does fall between the strong and weak bands, but it sits only 0.13 below a strong match, so Phase 6 should re-measure it over the eval set (M-series) before treating it as settled.

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

**Rule 1 and 2 measured gaps (Phase 6).** The table's regex column is a *family* of surface forms, not a literal string list, and two PRD §5.3 rows were not covered by the forms as first written. Both were found by testing the PRD's own example queries, not by reading the regex:

- Rule 1 missed a pasted Aadhaar. `pii.detect` recognises the digit patterns, but `"1234 5678 9012"` is spaced, and the literal list only matched `"my aadhaar"` — so `aadhaar 1234 5678 9012` fell through to rule 7 and was routed as an ordinary factual question with `needs_evidence=True`. The literal list now matches the identifier *words* themselves (`pan`, `aadhaar`, `folio`, `account number`, `ifsc`, `upi id`, `phone number`, …) rather than only a possessive phrase. The cost is over-refusal: "what is a PAN used for?" now routes to the PII refusal instead of being answered. For a facts-only assistant, refusing is the correct side of that trade (C2).
- Rule 2 missed two advice framings the PRD requires: the table's `should i` does not match *"the fund **I should** buy"*, and `is it (a )?good` does not match *"**is HDFC ELSS a good** ELSS for me?"* — the PRD's "legal/financial-advice framing" row. Both now route to `ADVICE_REQUEST`, confirmed by the two PRD examples.

The lesson worth keeping: a rule table written as prose is not a test suite. Every PRD §5.3 example is now an assertion in `tests/test_intents.py`.

### 11.3 5.2 — Query contextualisation

Short queries embed poorly ("exit load?"). The embedded string is therefore:

```
f"[{scheme_name or 'HDFC mutual fund'}] {fact_family_label} — {original_query}"
```

where `fact_family_label` ∈ {`expense ratio and fees`, `exit load`, `minimum SIP amount`, `lock-in period`, `riskometer and benchmark`, `tax statements and reports`}. This injects the corpus's vocabulary at query time, compensating for vocabulary mismatch *without* touching the chunker (PRD §9.3).

Scheme alias resolution: ordered longest-suffix match against `registry.scheme_aliases` (`"large cap"`, `"largecap"`, `"hdfc large cap"`, `"elss"`, `"tax saver"`, `"flexi cap"`, `"equity fund"`, `"small cap"`, `"balanced advantage"`). Unresolved ⇒ no `where` filter (recall preserved).

**`retrieval.scheme_filter` — an ambiguity in this section, resolved and measured (Phase 6).** §11.1 calls the scheme filter "optional" and §11.3's "unresolved ⇒ no filter (recall preserved)" implies a resolved scheme *is* filtered — but §11.4's `+0.02` scheme-match boost and §11.5's MMR rationale ("without MMR the top-5 collapses onto one scheme") both presuppose that *other* schemes are in the candidate pool. Under a hard filter the scheme boost is dead code and MMR has no schemes to diversify across. Both readings were measured on the same queries:

| Query | Filtered pool | Global pool |
| --- | --- | --- |
| expense ratio, HDFC Large Cap | top-1 `S1 Overview` (0.864) | top-1 `S1 Overview` (0.864) — same |
| lock-in, ELSS tax saver | top-1 `S3 About` (0.729) | top-1 `S3 About` (0.729) — same |
| exit load, flexi cap | top-1 `S2 Minimum investment` | top-1 `S2 Minimum investment` — same |
| MMR picks (first query) | `S1 S1 S1 S1 S1` | `S1 S4 S1 S2 S1` |

Top-1 is identical either way, so the choice is not about accuracy at rank 1. `scheme_filter: true` is the default because a hard in-scheme constraint is a stronger guarantee for the single citation (C5): a `+0.02` nudge losing a dense-score fight means answering about HDFC Flexi Cap with the Small Cap's fee. Set it to `false` for the Phase 11 ablation, where the global pool is the configuration in which §11.5's diversification claim can actually be tested.

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

**"Distinct" is doing real work in that formula (Phase 6).** The additional-term increment is per *distinct* term, and two over-counts showed up in the first live trace:

- **Substring containment.** `"exit load"` contains the word `"load"`, which is itself a configured `exit_load` synonym. A plain `in` test pays `0.05 + 0.05` for one mention. Matching is span-based and a term whose characters are already claimed by a longer term is discarded.
- **Repetition.** The S3 `About HDFC ELSS` chunk says `"tax saver"` three times. Counting occurrences paid it `0.05 + 2 × 0.05 = 0.15` for a family it barely evidences, and it won rank 1 on that borrowed boost (`final` 0.829 against 0.681 for the runner-up). A term now counts once per chunk regardless of how often it appears; the chunk's boost fell to a correct `0.07` (`0.05` term + `0.02` scheme) and its rank 1 became earned rather than purchased.

Matched terms are returned in reading order and carried on `ScoredChunk.matched_terms`, which is what the sources panel renders.

### 11.5 5.5 — MMR (implemented in-repo, no extra dependency)

```
selected, pool = [], candidates sorted desc by final
while pool and len(selected) < top_n:
    best = argmax over c in pool of
             λ * final(c) - (1-λ) * max_over(s in selected) cos(emb(c), emb(s))     # λ = 0.3
    selected.append(best); pool.remove(best)
```

Rationale: the five scheme pages contain near-identical fee tables; without MMR the top-5 collapses onto one scheme. Embeddings are already in hand from the store query, so MMR costs ~5×12 dot products — negligible.

Two implementation notes. MMR needs *candidate-to-candidate* similarity, which `store.query()` cannot supply — it scores each candidate against the query, not against its neighbours — so `store.vectors_for()` fetches the handful of stored vectors MMR actually compares (one `get`, ~1 ms; `query()`'s contract and Phase 5's tests are untouched). And when no vectors are supplied, the redundancy term falls back to token overlap, so `mmr()` is testable in isolation; `retrieve()` always passes the real vectors, so the deployed path measures redundancy with the same cosine geometry the dense stage used.

### 11.6 5.6 — Grounding gate (D1's enforcement point)

```
τ = 0.35                                   # calibrated in §12
if top_score < τ  ->  NOT_IN_CORPUS answer (fixed template + scheme page link)
if needs_evidence and top_score < τ + 0.10 -> NOT_IN_CORPUS   # stricter for unclassified queries
```

Additionally, the gate requires **term coverage**: the assembled context must contain the detected fact term (or its `FactFamily` synonym set). Dense similarity alone can be high for a topically-similar-but-different fact ("direct growth" vs "regular"), so coverage is a hard AND-condition. This is what prevents the classic failure of a fluent, confident, wrong fee.

**Measured: τ alone does not reject out-of-corpus questions, and this is the finding Phase 11 must start from.** Raw MiniLM cosine over this corpus is compressed into a narrow high band, so in-corpus and off-topic questions overlap heavily:

| Query | family | top `final` | gate |
| --- | --- | --- | --- |
| expense ratio, HDFC Large Cap | `expense_ratio` | 0.864 | pass |
| lock-in, ELSS tax saver | `lock_in` | 0.729 | pass |
| exit load, flexi cap | `exit_load` | 0.712 | pass |
| **ticker symbol of the fund** | `other` | **0.708** | **pass** |
| **recipe for chocolate cake** | `other` | **0.596** | **pass** |
| **bake sourdough bread at high altitude** | `other` | **0.479** | **pass** |

The last three are unanswerable from this corpus and none of them should pass. The reason is structural, not a mistuned constant: a `FactFamily.OTHER` question has no synonym set, so the coverage condition is vacuous and the raised threshold (`0.35 + 0.10 = 0.45`) sits *below* the off-topic band. Worse, §11.3's contextualisation makes this worse on purpose — it injects `"[HDFC mutual fund] scheme facts — "` in front of an off-topic question, raising the score of a cake recipe by design. So the sweep in this section is not a formality: at every τ in `{0.20 … 0.60}` the false-gate rate on `other`-family questions will be non-zero, and the sweep's own rule (no τ separates them) points at the real conclusion — **term coverage is the load-bearing part of this gate, and τ is a secondary guard.** The coverage condition *does* work where a family exists: an "expense ratio" question passes only if a retrieved chunk literally contains expense-ratio vocabulary, and a high-scoring "direct growth" chunk is rejected for a regular-plan question.

**Corpus vocabulary audit (Phase 6).** Term coverage is only as good as the agreement between `config.retrieval.fact_terms` and the words the corpus actually uses, so every family's synonym list was checked against all 106 chunks:

| Family | Surface forms present in the corpus | Verdict |
| --- | --- | --- |
| `expense_ratio` | "expense ratio" (5) | covered |
| `exit_load` | "exit load" (8) | covered |
| `min_sip` | **"Min. for SIP" (10)**, "minimum sip" (5) | **was not covered** — `"minimum sip"` never appears in a "Min. for SIP" chunk, so every min-SIP question hard-failed coverage despite the answer being present. `"min. for sip"` added. |
| `riskometer` | "Very High Risk" (5) | covered via "risk" |
| `benchmark` | "benchmark" (5), "Nifty" (41), "index" (47) | covered |
| `lock_in` | **nothing** — 0 of 106 chunks | **unanswerable from this corpus** |
| `statements` | **nothing** — 0 of 106 chunks | **unanswerable from this corpus** |

The last two are corpus gaps, not configuration gaps, and §12's rule is explicit that the answer is a better source, not a friendlier threshold. The five Groww scheme pages simply do not carry ELSS lock-in terms or tax-statement instructions; the only tax content in the entire corpus is the stamp-duty paragraph. Consequently:

- Every lock-in and statement question *should* fail the gate until a source that carries those facts is added (an HDFC AMC ELSS page or tax-statement page, registered in `data/sources.csv` per C1). `"how do I download the capital gains statement?"` already fails correctly at `top = 0.612`, which is the gate working.
- The lock-in question currently *passes* on `"tax saver"` alone — a scheme-name synonym doing the work of a fact term. That is the one place where the gate is rubber-stamping a question the corpus cannot answer, and the recommended fix is to delete `"tax saver"` from `fact_terms.lock_in` so the refusal is honest. Left in place here because it trades a demo answer for a refusal, which is a product decision, not a technical one.

Consequence for later phases: generation (Phase 7) must treat the top-1 chunk's `section_type` and matched terms as part of the answer contract, not just its text — a passed gate on an `other`-family question is weak evidence, and the guardrails (Phase 8) inherit that weakness rather than fixing it.

**After the `"min. for sip"` fix, the min-SIP query not only passes but ranks the right chunk first** (`final` 0.974, section `Minimum investments / Exit`) — the same query hard-failed coverage before it, and in Phase 5's raw-cosine check it missed S3 entirely. Vocabulary alignment moved it from "wrong or refused" to correct, which is the §12 argument in miniature: the lever was the corpus's words, not τ.

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

The budget is accounted in the stored `chunk.token_count` of each chunk, the same measure the chunker recorded, so the accounting needs no tokenizer at query time and cannot drift from the build. The short numbered labels sit outside that count; measured assembled contexts run 616–678 chunk tokens against the 1,800 budget, so the labels' ~20 tokens each are far inside the headroom. A chunk is appended whole or not at all, and a top-1 chunk larger than the whole budget is still included whole — never truncated, never dropped.

### 11.8 Retrieval cost

| Operation | Typical |
| --- | --- |
| Intent classification | < 1 ms |
| Query embed | 15–40 ms |
| Chroma query (k=12) | 5–30 ms |
| Boost + MMR | < 5 ms |
| **Total retrieval** | **< 100 ms** |

Measured in Phase 6, warm process, three queries end to end: **35–46 ms** total (embed 12–16 ms, dense 11–17 ms, boost + MMR + assembly the rest). The table holds.

Cold, it does not, and the pre-warm in §9 is not optional. The first `retrieve()` in a fresh process measured **11.9 s** — ~8.1 s of which is the encoder load and the remainder the first HNSW index read. That is the same cost `app.py`'s `@st.cache_resource` block exists to absorb (§16), and it is the reason the pre-warm calls `embed()` once rather than only loading the model.

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
   ├─▶ embedding.embed()      ──▶ np.ndarray (n, 384)
   ├─▶ store.upsert_chunks()  ──▶ data/chroma (collection mf_faq_hdfc_v1)
   └─▶ BuildReport { sources_ok, sources_failed, chunk_count, chunk_stats, warnings,
                     duration_s, config_hash, corpus_hash }
            └─▶ data/build_report.json; prints the summary; warnings surfaced in UI sidebar
```

Measured on the Phase 5 corpus (5 pages, 106 chunks, CPU): **39 s** of build work, 61 s of wall clock including ~21 s of `torch`/`chromadb` import. A build without `--refresh` opens no socket at all — verified by running it with `socket.create_connection` and `httpx.Client` replaced by raising stubs — because `loading.load_all` reuses `data/raw/` and never constructs a client. `--rebuild` deletes and recreates the collection and reproduces an identical `chunk_count` and `corpus_hash` (`924af25c…` for this corpus), which is the DoD check for FR-17 rather than a claim about it.

`corpus_hash` is sha256 over `f"{chunk_id}:{sha256(embed_text)}"` lines sorted by that string, so it moves when either the chunking output or the source text moves, and is independent of the order the sources happened to load in.

**Inspection command.** `python -m src.pipeline dump` writes `data/chunks_and_vectors.txt`: every chunk with its scheme, section, token count, URL, `fetched_at`, full `embed_text`, body text and all 384 vector components, under a header carrying `config_hash`, `corpus_hash` and `built_at`. The vectors are read back **out of** Chroma rather than recomputed, and the dump aborts if a stored document differs from `data/chunks.jsonl` — so the file is evidence of what the store holds, and a drifted index is a loud failure rather than a dump that quietly agrees with itself. This is the artefact to open when a citation looks wrong (P3, §19.1).

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
| Encoder weights absent (`data/models/` empty) | `embedding.get_encoder()` | `ModelNotCachedError` naming the directory and both remedies | Actionable start-up message, not a stack trace (NFR-4) |
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
| Theme control | `st.sidebar.toggle` switching `src.theme` light ⇄ dark (§16.1) | Default light; label names the theme it switches to |

**Separation of concerns:** the UI holds no retrieval or prompt logic. It calls `pipeline.answer()` and renders an `Answer`. This keeps the demo narrative honest — what you see in the UI is exactly what the library returns, and the same call path is exercised by `eval/run_eval.py`.

### 16.1 Theme: light and dark, switchable at runtime

Two Stitch design directions were supplied for this phase — **Groww FinTech Clean** (light, primary `#006c4f`) and **Obsidian Teal Wealth** (dark, primary `#44edb7`). They share an identical token schema (47 colour tokens, 11 type roles, 5 radii under the same names), so a theme is a value substitution rather than a second design. Both are carried; light is the default because the demo presents itself as a Groww page, and Groww's own site is light.

**A live toggle cannot use `.streamlit/config.toml`.** Streamlit reads that theme once at process start, so changing it requires a restart, which is not a toggle. The switch therefore works by CSS: `src/theme.py` holds the two palettes as frozen data and `stylesheet()` emits them as custom properties that `app.py` injects with `st.markdown(..., unsafe_allow_html=True)` on every rerun, keyed off a `st.session_state` value. Both themes are emitted inside `prefers-color-scheme` blocks *and* the active one is applied unconditionally — the media query keeps the page honest when the OS flips while the app is open, and the unconditional rule is what makes the in-app control authoritative rather than advisory.

`src/theme.py` exists as a module rather than living in `app.py` because of the import rule in this section: `app.py` may import `src.pipeline`, `src.config`, `src.models` and `src.templates`, so a palette defined in the UI would be unreachable to the tests that need to assert it. `theme` is therefore declared as infrastructure — it holds design tokens and CSS strings, and imports no stage, no store and no model.

Three deliberate constraints on the token set, each a bug found while building it:

- **Only colour switches.** Type scale, radii and spacing are shared, so the toggle cannot reflow the layout and there is no second responsive surface to test. Two Stitch spacings differ between the directions (`space-md` 0.75 → 1rem, `space-lg` 1 → 1.5rem); the tighter light values are used for both.
- **Both palettes define every token.** `test_both_palettes_define_every_token_exactly_once` exists because a missing key in one palette would silently drop a colour rather than fail.
- **A junk preference resolves instead of raising.** `resolve_theme()` coerces `None`, stray strings and wrong types to the default, since the value comes back from `st.session_state` and a stale entry must not take the app down on rerun.

`.streamlit/config.toml` is still worth writing, from `base_config()`, for one reason only: it sets the background before any script runs, so the first paint does not flash the wrong colour on a projector. The toggle does not read it.

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
| Stage CLI | `python -m src.chunking --doc S1`, `python -m src.retrieval --query "exit load?"`, `python -m src.pipeline ask --query "..."` — each stage runnable in isolation (NFR-9, D2). Generation's CLI is the pipeline's `ask` subcommand rather than a module of its own, because answering a query requires classifying and retrieving it, and §5.2 forbids `generation` from importing either |

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
  vectors_dump: data/chunks_and_vectors.txt   # `pipeline dump` output; git-ignored, regenerable
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
  description: HDFC AMC MF FAQ facts v1   # collection metadata, so a human opening the store knows what it is

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
