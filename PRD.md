# PRD — Mutual Fund FAQ Assistant (Facts-Only RAG Chatbot)

| Field | Value |
| --- | --- |
| Document | Product Requirements Document (PRD) |
| Version | v1.0 |
| Status | Draft for review |
| Date | 2026-09-27 |
| Source brief | Client milestone brief (client-owned, deliberately not committed; see .gitignore) |
| Deliverable type | Class demo — working prototype (facts-only RAG chatbot) |
| Owner | Team (2–3 contributors) |

---

## 1. Executive summary

Build a **facts-only RAG chatbot** that answers mutual fund scheme questions — expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, and how to download statements — strictly from a small, curated set of public web pages.

Every answer must carry **one verified source link**, must be **≤ 3 sentences**, and must include a `Last updated from sources: <date>` stamp. The assistant must refuse opinionated, portfolio, and performance-comparison questions with a polite facts-only message plus an educational link.

The system is a textbook RAG pipeline: **Loading → Chunking → Embedding → Vector Store → Retrieval → Grounded Answer Generation**, implemented in Python with `sentence-transformers/all-MiniLM-L6-v2`, ChromaDB, and a Streamlit UI.

**Success = a stranger asks 10 questions on a demo day and every answer is correct, cited, and advice-free.**

---

## 2. Problem statement

Retail users and support/content teams repeatedly ask the same factual mutual fund questions. The answers live scattered across AMC pages, SEBI/AMFI documents, factsheets, KIM/SID documents, fee pages, and statement guides. Today that means: manual searching, PDF hunting, and answers that drift out of date.

An LLM without grounding invents expense ratios and gives investment advice it should never give. A RAG system grounded in a curated, public-only corpus gives fast, cited, auditable answers — and can be constrained to refuse anything that is not a fact.

### 2.1 Why RAG and not a fine-tune / parametric answer

| Approach | Verdict | Reason |
| --- | --- | --- |
| Prompt a raw LLM | Rejected | Hallucinates fees; no citations; gives advice. |
| Fine-tune on MF data | Rejected | Facts change per factsheet cycle; no source link; untraceable. |
| **RAG over curated public pages** | **Chosen** | Facts stay fresh in the corpus, every sentence is attributable to a URL, refusals are enforceable, and the pipeline is gradeable stage-by-stage — which is what this demo needs to show. |

---

## 3. Goals and non-goals

### 3.1 Goals

- **G1 — Grounded facts:** Answer the seven core fact families for the in-scope schemes using only retrieved corpus content.
- **G2 — One citation per answer:** Every factual answer links to exactly one public source page from the registry.
- **G3 — Safe by construction:** Zero advice, zero performance claims, zero PII handling.
- **G4 — Short answers:** ≤ 3 sentences per answer, plus the `Last updated from sources:` stamp.
- **G5 — Transparent RAG:** Each pipeline stage (load → chunk → embed → store → retrieve → generate) is separately runnable, inspectable, and logged — the core teaching goal of the demo.
- **G6 — Demo reliability:** Runs end-to-end on a fresh machine with one setup command; degrades gracefully (no LLM key → extractive answers, still cited).

### 3.2 Non-goals (explicitly out of scope)

- Real-time NAV, live prices, or return/performance figures of any kind.
- Buy/sell/hold/switch recommendations, portfolio allocation, tax planning, goal planning.
- Multiple AMCs, direct-vs-regular comparison, SIP calculators, portfolio trackers.
- Login-gated or personalised data (Groww account pages, statements behind auth).
- Production hardening: auth, rate limiting, horizontal scaling, observability stack.
- Voice/multilingual UI. (UI language: English only.)

---

## 4. Users and personas

| Persona | Need | What "good" looks like |
| --- | --- | --- |
| **P1 — Retail investor (primary)** comparing HDFC schemes before investing | "What is the exit load on the flexi cap direct plan?" | One-sentence fact + link to the official fee page, in under 15 seconds. |
| **P2 — Support/content team member** answering repetitive tickets | "How do I download my capital-gains statement?" | Exact steps, cited to the official guide, copy-pasteable. |
| **P3 — Demo evaluator / professor** | "Is this actually RAG, and is it safe?" | Can see the retrieved chunks, the score, the citation, the refusal behaviour, and the stage logs. |

P3 drives the UI decisions (retrieved-context viewer) even though P1/P2 are the product users.

---

## 5. Scope: corpus definition

### 5.1 AMC and schemes in scope

AMC: **HDFC Asset Management Company**. Five schemes (satisfies the "3–5 schemes" rule and the "5 URLs" deliverable).

| # | Category | Scheme | Source URL |
| --- | --- | --- | --- |
| S1 | Large Cap | HDFC Large Cap Fund — Direct Growth | `https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth` |
| S2 | Flexi Cap | HDFC Equity Fund — Direct Growth | `https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth` |
| S3 | ELSS | HDFC ELSS Tax Saver Fund — Direct Plan — Growth | `https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth` |
| S4 | Small Cap | HDFC Small Cap Fund — Direct Growth | `https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth` |
| S5 | Balanced Advantage (Hybrid) | HDFC Balanced Advantage Fund — Direct Growth | `https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth` |

Also permitted as supporting sources (same public-only rule): official HDFC AMC scheme pages/factsheets, AMFI scheme documents, SEBI investor-education pages. Any added page must be registered in `data/sources.csv` and listed in the final source list.

> **Phase 0 correction (2026-09-27).** The S3 URL in the brief ended `-direct-growth` and returns
> **HTTP 404**; the live slug is `-direct-plan-growth` (see `docs/corpus_matrix.md` §1). The
> corrected URL is what the registry uses.

### 5.2 In-scope fact families (the answerable question set)

1. Expense ratio / TER and other fee components
2. Exit load (and entry load, if stated)
3. Minimum SIP / minimum lump sum / purchase amount
4. ELSS lock-in period and 80C relevance
5. Riskometer category and benchmark
6. Minimum investment horizon, and scheme objective/category in one line
7. How to download statements and tax documents (capital-gains statement, account statement, tax report)

> **Phase 0 finding (2026-09-27, `docs/corpus_matrix.md` §2).** Families 1–3 and the benchmark
> half of 5 are present as retrievable text on all five pages. Families 4, the riskometer half of
> 5, and 7 are **not obtainable from any fetchable official page**: HDFC's own domain returns
> HTTP 403 to a scripted client, and the official statement channels (CAMS, `investor.hdfcfund.com`)
> are login-gated, which is a stated non-goal. The system still recognises those question types
> and answers "not in my sources" with a link rather than guessing. They are not in the demo's
> answerable set; see `docs/corpus_matrix.md` §3 for the rejected candidates.

### 5.3 Out-of-scope query classes (must be refused or redirected)

| Class | Example | Required behaviour |
| --- | --- | --- |
| Advice | "Should I buy the ELSS?" | Refuse politely + facts-only notice + one educational link (SEBI/AMFI investor-education page) |
| Portfolio | "I hold X and Y, rebalance for me?" | Refuse politely + educational link |
| Performance | "Which of these gave the best 1-year return?" / "What is the return?" | Do **not** compute or compare. State that returns are not covered and link the official factsheet. |
| Other AMC/scheme | "What is the expense ratio of Parag Parflex?" | State the corpus covers HDFC AMC only; offer closest in-scope scheme or AMC page link. |
| PII | PAN, Aadhaar, account number, OTP, email, phone | Do not store, do not echo back, refuse and point to official support/help page. |
| Legal/financial-advice framing | "Is HDFC ELSS a good ELSS for me?" | Treated as advice → refuse. |

---

## 6. Key constraints (hard, from the brief)

| ID | Constraint | Enforcement mechanism |
| --- | --- | --- |
| **C1** | Public sources only. No third-party blogs, no screenshots of app back-ends. | Source registry allowlist; ingestion refuses any URL not registered. |
| **C2** | No PII accepted or stored. | Pre-ingestion PII redaction; runtime PII detector; logging filter. |
| **C3** | No performance claims or return computation. | Intent classifier routes performance queries to a redirect response; LLM system prompt forbids returns; post-generation validator. |
| **C4** | Answers ≤ 3 sentences. | Post-generation sentence-count check + truncation. |
| **C5** | Exactly one citation link per factual answer. | Answer template renders a single link from registry, never from LLM output. |
| **C6** | `Last updated from sources: <date>` on every answer. | Template field sourced from the chunk's `retrieved_at`/source registry date. |
| **C7** | Educational link on refusals. | Refusal template maps to a fixed SEBI/AMFI education URL from the registry. |

---

## 7. Functional requirements

### 7.1 Ingestion (offline pipeline)

| ID | Requirement |
| --- | --- |
| FR-1 | The system loads each registered public page (HTML) at a pinned, recorded fetch date. |
| FR-2 | Raw HTML → cleaned text: strip nav, footer, cookie banners, ads, and script/style; preserve headings and table structure as text. |
| FR-3 | Store a raw snapshot per source under `data/raw/<source_id>.html` (or `.md` when JS-rendered) so every answer is reproducible. |
| FR-4 | Each document carries metadata: `source_id`, `scheme_id`, `source_type` (scheme_page / factsheet / kim / sid / fee_page / education), `url`, `title`, `fetched_at`, `content_hash`. |
| FR-5 | PII redaction runs on extracted text before chunking; redaction is logged by count and pattern type, never by value. |
| FR-6 | Ingestion is idempotent: re-running with unchanged `content_hash` skips re-embedding. |
| FR-7 | Pipeline emits a run report: pages fetched, chars extracted, chunks created, tokens, embedding time, duplicates dropped. |

### 7.2 Chunking

| ID | Requirement |
| --- | --- |
| FR-8 | Documents are split on **semantic section boundaries** (headings, labelled blocks, and table rows), with a token-size ceiling as a hard cap. |
| FR-9 | Each chunk is prefixed with a context header (`<scheme name> — <section heading>`) so the embedded text is self-describing. |
| FR-10 | Overlap is applied at the chunk level for prose; **no overlap across section boundaries** for fee/charge tables. |
| FR-11 | Every chunk retains its parent `section`, `url`, and `scheme_id` metadata for citation and filtering. |
| FR-12 | Chunks that contain no factual content (boilerplate, marketing copy, nav residue) are dropped by a heuristic stop-list filter. |
| FR-13 | Chunk statistics (count, median tokens, min/max) are printed and stored for evaluation. |

### 7.3 Embedding + vector store

| ID | Requirement |
| --- | --- |
| FR-14 | Embeddings are produced by `sentence-transformers/all-MiniLM-L6-v2` (384-dim, mean pooling, L2-normalised, cosine). |
| FR-15 | Vectors are persisted in **ChromaDB** (persistent client, local disk) in a versioned collection. |
| FR-16 | Chroma metadata holds `source_id`, `scheme_id`, `section`, `url`, `section_type` so retrieval can filter and citations can be built without extra joins. |
| FR-17 | The collection can be wiped and rebuilt from raw snapshots in one command. |
| FR-18 | The model is downloaded once and cached locally so the demo works offline. |

### 7.4 Retrieval

| ID | Requirement |
| --- | --- |
| FR-19 | **Step 1 — Intent classification:** the user query is classified into one of: `factual_fact`, `advice_request`, `performance_request`, `pii_request`, `out_of_corpus`, `greeting/help`. |
| FR-20 | Non-factual intents short-circuit to a templated response (no LLM, no retrieval) — this is the safety fast path. |
| FR-21 | **Step 2 — Query normalisation:** resolve scheme aliases ("large cap", "elss", "tax saver") to `scheme_id`; detect fact family keywords. |
| FR-22 | **Step 3 — Hybrid candidate retrieval:** dense top-k (k=12) with cosine similarity, plus a lexical/keyword boost for exact fact terms (`expense ratio`, `exit load`, `minimum sip`, `lock-in`, `riskometer`, `benchmark`). |
| FR-23 | **Step 4 — Rerank/diversify:** score = dense similarity + keyword-boost + metadata match; apply MMR (λ=0.3) to avoid five near-duplicate fee-table chunks; keep top-n = 4–5. |
| FR-24 | **Step 5 — Grounding gate:** if the best chunk's score is below a tuned threshold (default 0.35 cosine, to be calibrated on the eval set), the system answers "not in my sources" and points to the scheme page — it never guesses. |
| FR-25 | **Step 6 — Context assembly:** top chunks are ordered, deduped, and rendered with their `scheme` + `section` + `url` labels before being handed to the LLM. |
| FR-26 | **Step 7 — Answer generation:** the LLM receives only the assembled context and a strict instruction to use it exclusively. |
| FR-27 | **Step 8 — Post-generation validation:** sentence count, citation presence, citation-in-registry, and forbidden-content checks; failures fall back to the extractive path. |
| FR-28 | The UI exposes the retrieved chunks, their scores, and the chosen citation (demo/debug value for P3). |

### 7.5 Guardrails

| ID | Requirement |
| --- | --- |
| FR-29 | **PII:** regex + keyword detection for PAN, Aadhaar, account numbers, OTPs, emails, phone numbers in both query and source text. PII in a query is never logged raw and never stored. |
| FR-30 | **Advice refusal:** template response stating the assistant shares facts, not recommendations, plus one educational link. |
| FR-31 | **Performance redirect:** any return/NAV/ranking question is answered with "returns are not covered here" + the official factsheet link. No arithmetic on returns, ever. |
| FR-32 | **Out-of-corpus:** explicit statement of corpus scope (HDFC AMC, 5 schemes) + nearest in-scope alternative. |
| FR-33 | **Hallucination guard:** if the LLM output contains a URL, token, or scheme name not present in the retrieved context/registry, the answer is replaced by the extractive fallback. |
| FR-34 | **Extractive fallback:** when no LLM key is configured or generation fails, compose the answer directly from the top chunk's sentences (still ≤3 sentences, still cited). |

### 7.6 UI (Streamlit)

| ID | Requirement |
| --- | --- |
| FR-35 | Welcome line naming the scope (HDFC AMC, 5 schemes, facts-only). |
| FR-36 | Exactly 3 example questions as clickable chips. |
| FR-37 | Persistent note: **"Facts-only. No investment advice."** |
| FR-38 | Chat interface with streamed answer text, the single citation link rendered as a clickable button, and the `Last updated from sources:` stamp. |
| FR-39 | A collapsible "Sources used" panel showing the retrieved chunk text and similarity score. |
| FR-40 | A prominent disclaimer (banner + footer) with the exact snippet in §12. |
| FR-41 | Suggested-question row that changes based on the last intent (e.g., after a refusal, offer fact questions). |
| FR-42 | No chat-history persistence beyond the session; a visible "Clear chat" control. |

---

## 8. Non-functional requirements

| ID | Category | Requirement |
| --- | --- | --- |
| NFR-1 | Setup | One-command setup on a clean Python 3.11 environment: `pip install -r requirements.txt` then `python -m src.ingest && streamlit run app.py`. Documented in README. |
| NFR-2 | Startup | App cold start < 10 s with a warm ChromaDB and cached model; the vector store is never rebuilt on app start. |
| NFR-3 | Latency | End-to-end answer < 6 s typical (retrieval < 1 s, generation < 5 s). |
| NFR-4 | Offline | With model + corpus cached and no network, the app still answers (extractive fallback path). |
| NFR-5 | Reproducibility | Pinned dependencies; recorded model id, chunker config, and collection name; `content_hash` per source. |
| NFR-6 | Cost | Free-tier LLM or local fallback. No paid API required to demo. |
| NFR-7 | Privacy | No telemetry. Logs contain no query text by default and never contain PII. |
| NFR-8 | Transparency | Every answer displays its source and the retrieval score; stage logs available in the UI. |
| NFR-9 | Maintainability | Stage modules are independent (`ingest`, `chunk`, `embed`, `store`, `retrieve`, `generate`, `guardrails`), each runnable from CLI. |
| NFR-10 | Portability | Runs on Windows/macOS/Linux; paths from config, not hardcoded. |

---

## 9. System architecture

### 9.1 High-level flow

```
                      OFFLINE (build once, cached to disk)
 ┌──────────────┐   ┌───────────┐   ┌────────────┐   ┌──────────────┐   ┌───────────┐
 │ 1. LOADING   │──▶│2. CHUNKING│──▶│3. EMBEDDING│──▶│4. VECTOR     │──▶│ ChromaDB   │
 │ fetch HTML   │   │ section-  │   │ all-MiniLM │   │ STORE        │   │ persistent │
 │ from         │   │ aware     │   │ L6-v2      │   │ write + meta │   │ (384-d)    │
 │ sources.csv  │   │ + tables  │   │ 384-d      │   │              │   │            │
 └──────┬───────┘   └───────────┘   └────────────┘   └──────────────┘   └─────┬─────┘
        │                                                                    │
        └── raw/ snapshots + content_hash                                   │
                                                                             │
                      ONLINE (per question)                                   ▼
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

### 9.2 Stage specifications

#### Stage 1 — Loading

- **Input:** `data/sources.csv` (registry).
- **Fetcher:** `httpx`/`requests` with a browser-like User-Agent, retries with backoff, and a polite delay between requests.
- **Parser:** `beautifulsoup4` + `lxml` to remove non-content nodes; `trafilatura` (optional) as fallback for boilerplate-heavy pages; JS-rendered content handled via a saved `.md` snapshot if the static HTML lacks the fact table.
- **Output:** `data/raw/{source_id}.{html,md}` + `data/processed/{source_id}.txt` with heading markers preserved as `#`-prefixed lines (this is what the chunker consumes).

#### Stage 2 — Chunking (decision below in §9.3)

#### Stage 3 — Embedding

- **Model:** `sentence-transformers/all-MiniLM-L6-v2` — 384 dimensions, ~22M params, ~80 MB, fast on CPU, strong on English factual QA. This is the brief's mandated model.
- **Pooling:** mean pooling over token embeddings, then L2 normalisation → cosine == inner product.
- **Prefix trick:** the chunk header (`<scheme> — <section>`) is prepended *before* embedding so the vector carries scheme context. The same header is stripped from the text shown to the LLM's citation formatting.
- **Batching:** batch size 32, `normalize_embeddings=True`.

#### Stage 4 — Vector store

- **Engine:** ChromaDB, `chromadb.PersistentClient(path="data/chroma")`, collection `mf_faq_hdfc_v1`, metric `cosine`.
- **Why Chroma:** zero-ops, local, metadata filtering in-query, and it makes the "store" stage visually obvious in a demo.
- **Written metadata per chunk:** `chunk_id`, `source_id`, `scheme_id`, `scheme_name`, `section`, `section_type`, `url`, `title`, `fetched_at`, `token_count`, `char_start`, `char_end`.
- **Indexing:** upsert by deterministic `chunk_id = sha1(source_id + section + ordinal)` so rebuilds don't duplicate.

#### Stage 5 — Retrieval

1. **Intent gate** (rule + small keyword classifier, no LLM needed) → advice / performance / PII / out-of-corpus handled immediately.
2. **Query rewrite:** append the resolved `scheme_name` and the fact-family label to the embedding text (contextualisation for short queries like "exit load?").
3. **Dense search:** `collection.query(query_embeddings=[...], n_results=12, where={scheme_id: ...})` — scheme filter applied when confidently resolved, else unfiltered.
4. **Lexical boost:** +0.05 per matched exact fact term in the chunk text; +0.03 if the chunk's `section` matches the detected fact family (e.g., `section_type == "fee"` for expense-ratio questions).
5. **MMR (λ=0.3):** greedily select 4–5 chunks balancing relevance and novelty.
6. **Grounding gate:** `top_score < 0.35` → "not in my sources" + link to the scheme page.
7. **Context assembly:** numbered blocks `[1] <scheme> — <section> (<url>)` with the source text; capped at ~1,800 tokens total, highest-scoring chunks first, no truncation of the top-1 chunk mid-number.

#### Stage 6 — Grounded generation

**System prompt contract** (abridged; full text in `src/prompts.py`):

> You are a mutual fund *facts* assistant for HDFC AMC schemes. Answer ONLY from the provided context. Never use prior knowledge. Never estimate, calculate, compare, or rank. Never recommend buying, selling, holding, or switching. Never mention returns, NAV, or performance. Maximum 3 sentences. No URLs in the text — the citation is added by the system. If the context does not contain the answer, reply exactly: `NOT_IN_CORPUS`. If the question asks for advice or performance, reply exactly: `REFUSE`.

- **LLM:** free-tier hosted model (e.g. Gemini Flash / OpenRouter free model) via `OPENAI`-style env config; **optional**.
- **Fallback:** if `LLM_API_KEY` is absent or the call fails, the extractive composer builds the answer from the top chunk (first 3 sentences containing the fact-term keywords) — same template, same citation.
- **Temperature:** 0.0–0.2. **Max tokens:** 220.

#### Stage 7 — Post-generation validation & answer template

```
{answer_text}                     # ≤3 sentences, validated
Source: {citation_url}            # exactly one, from registry, rendered as a button
Last updated from sources: {fetched_at_date}
```

Validation rules (any failure → extractive fallback, and a logged `validation_error`):
1. Sentence count ≤ 3.
2. Contains no digits-with-`%`-pattern not present in context (blocks invented fee figures).
3. Contains no URL (LLM output is never trusted to supply links).
4. Contains no banned advice/performance terms (`should`, `recommend`, `best`, `outperform`, `return`, `NAV`, `buy`, `sell`, `suitable`).
5. If the model returned `NOT_IN_CORPUS` or `REFUSE`, route to the corresponding template.
6. The rendered citation URL exists in the registry (allowlist).

### 9.3 Chunking strategy — decision & rationale

> Brief instruction: *"Chunking Strategy → Ask Cursor to decide the chunking strategy based on the data."*

**Decision: semantic section-aware chunking with table-row preservation, 350–600 tokens, 60-token prose overlap, explicit context headers, plus a keyword question-boost at query time.**

Rationale grounded in the actual corpus shape:

| Observation about the data | Consequence for chunking |
| --- | --- |
| MF fact pages are **short labelled blocks** ("Expense ratio", "Exit load", "Minimum SIP amount", "Lock-in period"), not long prose. | Fixed 512-token windows over-split blocks and merge unrelated ones. Use **section boundaries as the primary split point**. |
| Fee and load data live in **compact tables / definition lists** (`Direct Growth 0.xx%`, `0–1 yr: 1%`). | Tables must stay intact and must **not** be given overlap — an overlapped table row yields a chunk with a half-row that reads as a different fee. Split oversized tables by row groups, never mid-row. |
| Each page is about **one scheme**, but the same fact family repeats across 5 schemes. | Embedding a section header (scheme + section) disambiguates near-identical fee vectors; retrieval can then filter by `scheme_id`. |
| Answers are 1–3 sentences about a **single fact**. | Small chunks (≈400 tokens) beat large ones: less dilution, higher top-1 precision, and cheaper context. |
| Question wording differs from page wording ("minimum SIP" vs "minimum investment amount"). | Pure lexical overlap fails → this is why retrieval uses **dense + keyword boost + MMR**, not chunking tricks. |

Rejected alternatives:

| Alternative | Why rejected |
| --- | --- |
| Fixed-size 512/50 overlap | Splits labelled fee blocks, merges unrelated sections, dilutes embeddings. |
| Recursive character splitting (LangChain default) | Same problem, plus it shreds table rows. |
| One chunk per page | Page ≈ 1,500–4,000 tokens; a question about exit load gets swamped by NAV history and fund-manager text; precision collapses. |
| Parent-child / small-to-big retrieval | Excellent, but adds a second indexing layer and complexity that a class demo does not need. Noted as a stretch improvement. |
| Per-sentence chunks | Loses the label↔value binding ("Exit load" heading with its table). |
| Summarise-then-chunk | LLM preprocessing adds cost, non-determinism, and a hallucination surface. |

Parameters (tunable via `config.yaml`):

```yaml
chunking:
  strategy: semantic_section
  max_tokens: 600
  min_tokens: 80
  overlap_tokens: 60          # prose only; suppressed across table/section boundaries
  merge_small_sections: true  # sections < min_tokens merge into the next sibling, header preserved
  preserve_tables: true       # rows kept intact; split by row groups if over max_tokens
  include_context_header: true
  drop_boilerplate: true
```

**Measurement plan:** build 3 chunking variants (semantic-600, semantic-350, fixed-512) and score each on the eval set (§11) for citation accuracy and top-1 hit rate. Ship the winner; report the comparison — this is the evidence behind the decision, and it is demo material.

---

## 10. UI specification (Streamlit)

```
┌───────────────────────────────────────────────────────────────┐
│  🏦 Mutual Fund FAQ Assistant — Facts Only                    │
│  Scope: HDFC AMC · 5 schemes · Public sources only            │
├───────────────────────────────────────────────────────────────┤
│  ⚠️ Facts-only. No investment advice. We never recommend      │
│     buying or selling. Returns/NAV are not covered.           │
├───────────────────────────────────────────────────────────────┤
│  Welcome! Ask me factual questions about HDFC mutual funds.   │
│  Every answer links its official source.                      │
│                                                               │
│  [What is the expense ratio of the HDFC Large Cap fund?]      │
│  [Is there a lock-in period on the ELSS tax saver fund?]      │
│  [How do I download my capital-gains statement?]              │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐    │
│  │ You: exit load on flexi cap direct growth?            │    │
│  │ Bot: <≤3 sentence answer>                             │    │
│  │      [ View source ]  ← single citation button        │    │
│  │      Last updated from sources: 2026-09-27            │    │
│  │      ▾ Sources used (3 chunks · top score 0.71)        │    │
│  └───────────────────────────────────────────────────────┘    │
│  [ Ask a question…                                  ] [Send]  │
│                                    [Clear chat]               │
└───────────────────────────────────────────────────────────────┘
```

- Sidebar (collapsed): pipeline stage logs, collection stats (documents, chunks, model id, collection name), and a "Rebuild index" hint (no button that destroys state mid-demo).
- Refusal state renders a neutral info panel + educational link, never a lecture.

---

## 11. Evaluation plan

### 11.1 Golden set

- **20 factual questions** (from the 7 fact families × 5 schemes), each with the expected source URL and a keyword the answer must contain.
- **8 non-factual probes** (4 advice, 2 performance, 1 PII, 1 out-of-corpus) with expected behaviour codes.

### 11.2 Metrics

| Metric | Definition | Target |
| --- | --- | --- |
| Answer correctness | Human check: answer contains the expected fact keyword/number | ≥ 90% |
| Citation validity | Rendered URL ∈ registry **and** supports the answer | 100% |
| Top-1 retrieval hit | Best chunk is from the correct scheme + correct fact section | ≥ 85% |
| Refusal precision | Non-factual probes correctly refused/redirected | 100% |
| Refusal recall | Factual probes not wrongly refused | 100% (0 false refusals) |
| Length compliance | Answers ≤ 3 sentences | 100% |
| PII leakage | PII values present in state, logs, or output | 0 |
| Grounding gap rate | Answers containing claims absent from context | 0 |

### 11.3 Ablations (demo material, proves the pipeline matters)

1. Chunking: semantic-600 vs semantic-350 vs fixed-512.
2. Threshold sweep: 0.25 / 0.35 / 0.45 grounding gate.
3. Dense-only vs dense + keyword boost vs dense + boost + MMR.
4. LLM generation vs extractive fallback (shows why grounding is what makes it safe).

### 11.4 Test asset layout

```
eval/golden_questions.csv   # id, question, expected_scheme, fact_family, expected_url, must_include
eval/out_of_scope_probes.csv
eval/run_eval.py            # prints the metrics table
eval/report.md              # filled after each run
```

---

## 12. Disclaimer and copy

**UI disclaimer (banner + footer + answer footer):**

> **Facts-only. No investment advice.** This assistant shares publicly available factual information about HDFC AMC schemes and is not a SEBI-registered investment adviser. It does not recommend buying, selling, or holding any fund, and it does not provide return, NAV, or performance figures. Information may change — always verify on the linked official source page.

**Refusal message (advice / portfolio):**

> I can only share facts — I can't recommend funds or suggest what to do with your money. Here's an official investor-education page instead: `[link]`. Ask me anything factual (expense ratio, exit load, minimum SIP, lock-in, benchmark, statements) and I'll answer with a source.

**Performance redirect:**

> I don't provide or compare returns, NAVs, or performance figures. The official factsheet for this scheme has the published figures: `[factsheet link]`.

**PII refusal:**

> Please don't share personal identifiers like PAN, Aadhaar, account numbers, or OTPs — I won't store them. For account-specific help, use the official support channel: `[link]`.

---

## 13. Repository layout

```
Groww/
├── PRD.md
├── README.md                     # setup, scope, known limits
├── requirements.txt
├── config.yaml                   # chunking, retrieval, thresholds, model ids
├── app.py                        # Streamlit UI
├── data/
│   ├── sources.csv               # source registry (deliverable)
│   ├── raw/                      # page snapshots
│   ├── processed/                # cleaned text
│   └── chroma/                   # persisted vector store
├── src/
│   ├── config.py
│   ├── loading.py                # stage 1
│   ├── chunking.py               # stage 2
│   ├── embedding.py              # stage 3
│   ├── store.py                  # stage 4 (Chroma)
│   ├── retrieval.py              # stage 5 (intent, hybrid, MMR, gate)
│   ├── generation.py             # stage 6 (LLM + extractive fallback)
│   ├── guardrails.py             # PII, advice, perf, validation
│   ├── prompts.py
│   └── pipeline.py               # end-to-end orchestration
├── eval/
│   ├── golden_questions.csv
│   ├── out_of_scope_probes.csv
│   └── run_eval.py
├── docs/
│   └── sample_qa.md              # deliverable: 5–10 Q&A with links
└── scripts/
    ├── build_index.sh / .ps1
    └── run_eval.ps1
```

`data/sources.csv` schema:

```csv
source_id,scheme_id,scheme_name,source_type,title,url,publisher,allowed_for_citation,fetched_at,notes
```

---

## 14. Milestones

Assumes 4 working days for a small team; scale linearly.

| # | Milestone | Tasks | Exit criteria |
| --- | --- | --- | --- |
| **M0** | Setup & scaffold (half day) | Repo, `requirements.txt`, `config.yaml`, stub CLI per stage | App boots; all modules importable |
| **M1** | Source registry & corpus | Write `sources.csv`, fetch + snapshot 5 pages, cleaner | 5/5 pages yield cleaned text containing all 7 fact families |
| **M2** | Chunking + embedding + store | Chunker (3 variants), embedder, Chroma writer | Index built; stats printed; rebuild is idempotent |
| **M3** | Retrieval | Intent classifier, query normalisation, dense + keyword + MMR, threshold tuning | ≥85% top-1 hit on the 20-question golden set |
| **M4** | Generation + guardrails | Prompt, extractive fallback, validators, PII/advice/performance routes | 100% refusal precision, 0 false refusals, 0 PII leakage |
| **M5** | UI | Streamlit chat, chips, citation button, sources panel, disclaimers | Matches §10; cold start < 10 s |
| **M6** | Eval & ablations | Run metrics table, chunking/threshold/retrieval ablations | `eval/report.md` complete |
| **M7** | Deliverables pack | README, `sample_qa.md`, sources list, 3-min demo script, demo recording | All 5 brief deliverables submitted |
| **M8** | Rehearsal | Two full dry runs, one with network off, one fresh-machine clone | Zero live failures; ≤3-min demo script timed |

**Demo script outline (≤3 min):** 1) problem + scope (20 s) → 2) pipeline architecture slide (30 s) → 3) two factual questions with the sources panel open (60 s) → 4) one advice refusal and one performance redirect (30 s) → 5) PII probe refused (15 s) → 6) eval table + chunking ablation result (25 s) → 7) limits and future work (20 s).

---

## 15. Risks and mitigations

| ID | Risk | Impact | Likelihood | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Live pages are JS-rendered; fee tables absent from static HTML | Corpus unusable | High | Save rendered `.md` snapshots at build time; treat snapshots as the source of truth; validate fact coverage in M1 and switch source URLs if needed |
| R2 | Page structure changes between runs | Silent degradation | Medium | `content_hash` + fact-coverage assertion at ingest; warnings surfaced in the UI |
| R3 | Hallucinated numbers slip through | High (defeats the demo's purpose) | Medium | Numeric-claim validator against context + extractive fallback + 0% grounding-gap target in eval |
| R4 | No LLM API key at demo time | Demo stalls | Medium | Extractive fallback is a first-class path, not an error path; rehearse offline (NFR-4) |
| R5 | Rate-limited or blocked page fetch | Incomplete corpus | Medium | Polite fetch with backoff, caching, and manual `.md` snapshot fallback |
| R6 | Embedding model cold start is slow on the demo laptop | Long first answer | Medium | Pre-warm at app start with a background thread; cache the model dir |
| R7 | Advice/performance queries answered anyway | High (compliance) | Low | Intent gate runs **before** retrieval and never calls the LLM for those classes; tested with 8 probes |
| R8 | PII leaks into logs or Chroma | High (privacy) | Low | Redaction at ingest + runtime detection + no raw query logging; asserted in eval |
| R9 | Scope creep into a full MF platform | Schedule | Medium | Non-goals list is binding; new features go to a "future work" slide |
| R10 | Chroma version drift breaks rebuilds | Build failure | Low | Pin `chromadb` version; test full rebuild in M8 rehearsal |

---

## 16. Acceptance criteria (definition of done)

- [ ] All 7 fact families answerable for all 5 in-scope schemes from the corpus alone.
- [ ] 100% of factual answers include exactly one citation URL that exists in the source registry.
- [ ] 100% of answers are ≤ 3 sentences and carry `Last updated from sources: <date>`.
- [ ] 100% of advice, portfolio, and performance queries are refused or redirected with an educational/factsheet link; zero return figures are ever produced.
- [ ] 0 PII values accepted, stored, or logged; verified by an automated probe.
- [ ] Top-1 retrieval hit ≥ 85% on the golden set; grounding-gap rate 0%.
- [ ] UI shows welcome line, exactly 3 example questions, the "Facts-only. No investment advice." note, and the disclaimer.
- [ ] `README.md` documents setup, scope (AMC + 5 schemes), and known limits.
- [ ] `docs/sample_qa.md` contains 5–10 real Q&A pairs with links.
- [ ] Source list exported as CSV **and** MD.
- [ ] Full rebuild from raw snapshots works with one command; offline demo works.
- [ ] ≤3-minute demo rehearsed twice, including an offline run.

---

## 17. Deliverables mapping (to the brief)

| Brief deliverable | Where it lives | Status |
| --- | --- | --- |
| Working prototype link (app or notebook) | Streamlit app in this repo + deployed demo link | M5 |
| Source list (CSV/MD) of the 5 URLs | `data/sources.csv` + `docs/sources.md` | M1 |
| README with setup, scope, known limits | `README.md` | M7 |
| Sample Q&A (5–10 queries + answers + links) | `docs/sample_qa.md` | M7 |
| Disclaimer snippet used in the UI | `src/prompts.py` + §12, rendered in the app banner | M5 |
| (Added) End output: RAG chatbot with all stages | Stages 1–7 in §9, each independently runnable | M2–M4 |

---

## 18. Known limits (to state in the README and the demo)

1. **Corpus is 5 schemes of one AMC.** No other AMC, no other plan variants, no other schemes.
2. **Snapshot-based.** Facts reflect the fetch date shown in the answer; the system does not re-crawl during a demo, and a live scheme update will not appear until the index is rebuilt.
3. **Facts only — no returns, no NAV, no portfolio logic, no tax computation.**
4. **English only.**
5. **Answer quality is bounded by the page text.** If a page omits a fact, the assistant says so instead of filling the gap.
6. **No auth, no personalisation, no multi-user state, no production security hardening.**
7. **Retrieval is hybrid lexical + dense with MMR**, not a learned reranker; gains from a cross-encoder reranker are a known future improvement.
8. **Vector store is local ChromaDB**; scaling beyond tens of thousands of chunks needs a real vector DB.

---

## 19. Future work (post-demo, out of scope now)

- Parent-child (small-to-big) chunk retrieval for long KIM/SID PDFs.
- Cross-encoder reranking of the top-20 candidates.
- Ingestion of AMFI/SEBI education corpus for a richer refusal library.
- Multilingual queries over the same corpus.
- Scheduled re-crawl with diff detection and changelog on answers.
- Optional structured extraction (fees as JSON) verified against the factsheet.

---

## 20. Open questions

| # | Question | Owner | Needed by |
| --- | --- | --- | --- |
| Q1 | Which hosted LLM (if any) will be permitted on demo day — and is there a key available? | Team | M3 |
| Q2 | Are rendered `.md` snapshots acceptable as the "public page" artifact, given static HTML may omit fee tables? | Team + instructor | M1 |
| Q3 | Will the demo be graded on the RAG stage walkthrough, the safety guardrails, or both equally? | Instructor | M0 |
| Q4 | Do we need the extra AMFI/SEBI education sources in the citation registry, or only as refusal links? | Team | M4 |
| Q5 | Hosting target for the demo link (Streamlit Community Cloud vs local screen share)? | Team | M5 |

---

*End of PRD v1.0. Supersedes nothing. Next revision after M3 retrieval metrics are available.*
