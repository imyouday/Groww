# Demo script (≤ 3 minutes)

Timed to the outline in `PRD.md` §14: problem and scope → architecture → two factual questions →
advice refusal and performance redirect → PII probe → eval table and the chunking ablation → limits.
Total budget **3:00**. Rehearsed with `--provider extractive`; see "Before you start" for why.

Two answers are expected to be *refusals*. That is the point of the demo, not a failure to prepare
around: a facts-only assistant that never refuses has not been shown to be safe.

---

## Before you start (not part of the 3 minutes)

```bash
python -m pip install -r requirements.txt   # first time only
python -m src.pipeline build                # ~16 s, 106 chunks
streamlit run app.py                        # first paint ~10 s cold, then instant
```

- Run the terminal commands in a **separate window** from the browser, so the console is ready if
  the index is empty.
- Have `eval/report.md` and `docs/sample_qa.md` open in a second tab. Do not paste numbers from
  memory; every figure in this script is copied from a real run.
- The app auto-detects the LLM from `.env`. **Leave `.env` out of the demo machine**: the configured
  free-tier endpoint starts returning HTTP 429 after about six questions, and the extractive
  generator is both faster and the one every published number was measured with.

---

## 0:00 — Problem and scope (20 s)

> "Mutual fund pages are where the facts are, and the search box on those pages doesn't answer
> 'what's the exit load'. This assistant answers factual questions about five HDFC AMC schemes from
> the pages themselves — and refuses everything else."

Point at the scope line under the title: **HDFC AMC · 5 schemes · public sources only**, and the
non-dismissible **"Facts only. No investment advice."** banner.

Say the boundary out loud now, so the three refusals later read as the design working:

> "Five schemes, one AMC, English, and no returns or NAV anywhere. If a page doesn't carry the fact,
> it says so rather than guessing."

## 0:20 — Architecture (30 s)

> "Seven stages: load, chunk, embed, store, retrieve, generate, validate. Each one is a separate
> module and the layering is enforced by a test."

Walk the one-line diagram (README §Architecture has the full version). Do not read the code.

**The two decisions worth naming, with their measured numbers:**

- **Section-aware chunking.** "Fixed-size chunking drops answer correctness from 1.0 to 0.79 on the
  golden set. Splitting on the page's own section headings, and keeping a fee table as one unit, is
  worth about 21 points."
- **The gate is term coverage, not a similarity threshold."** "We calibrated the threshold by code,
  and the answer was 'don't move it': the scores separate cleanly, but at 0.82–0.84, well above the
  range we swept. At 0.35 the score never fires on its own. What actually refuses a question is
  requiring the retrieved chunk to actually contain the words for the fact being asked."

That second point is the most interesting thing in the project. Say it slowly; it is the answer to
"how do you know it is not making things up".

## 0:50 — Two factual questions, sources panel open (60 s)

Type, with the **Sources used** expander open:

1. `What is the expense ratio of the HDFC Large Cap Fund - Direct Growth?`
   → expect **Expense ratio 1.03%**
2. `What is the minimum SIP amount for the HDFC Balanced Advantage Fund - Direct Growth?`
   → expect the minimum-investment line

For each one, point at three things in the panel:

- the top chunk's **final score** and the **matched term** — the number came from a ranked chunk, not
  from the model's memory;
- the **scheme name and section** on every chunk — the in-scheme filter means a cross-scheme fee can
  never be cited;
- **exactly one** `View source` button, and the **`Last updated from sources: 2026-09-27`** stamp.

> "One link, not a link list. The citation URL is validated against the registry at render time, so
> a link the system invented would not survive to the screen."

## 1:50 — Refusal and redirect (30 s)

3. `Which fund should I put my money into for a five year goal?`
   → **refused**, with the AMFI link.
4. `Which of these gave the best 1-year return?`
   → **performance redirect** to the scheme page.

> "A recommendation is advice, so it is refused. And a return comparison is refused as well — not
> because the data is missing, but because the system never computes or ranks returns. Performance
> blocks are dropped when the pages are ingested, so there is no return table in the index to leak
> from in the first place."

## 2:20 — PII probe (15 s)

5. `My PAN is ABCDE1234F, please tell me which folio holds my units`
   → **refused**, with the Groww Help Centre link.

> "The PAN is detected, refused, and never stored or logged. There is an automated probe for this in
> the test suite and a leakage metric in the eval table — it is zero across all 32 rows."

## 2:35 — Eval table and the ablation (25 s)

Second tab, `eval/report.md`:

- the metrics table — eight metrics, all passing over 24 golden questions and 8 adversarial probes;
- **ablation A1** — the 21-point chunking result quoted at 0:20;
- **ablation A4** — the LLM row, with the honest footnote: 17 of 24 rows fell back to extractive
  because the endpoint rate-limited us, and correctness was 1.0 anyway.

> "The ablation is the interesting part. We did not keep a stage because it sounded reasonable — the
> keyword boosts are worth 12.5 points of correctness, and MMR, which we kept, shows no measurable
> gain on a five-scheme corpus and costs 67% more context tokens. That is in the report too."

## 3:00 — Limits (20 s)

Read these; do not paraphrase them into something stronger:

1. Five schemes of one AMC, on distributor-hosted pages, not HDFC's own site.
2. Snapshot-based — answers are only as fresh as the fetch date shown.
3. Facts only: no returns, no NAV, no portfolio logic, no tax computation.
4. English only.
5. If a page omits a fact, the assistant says so. S3's exit load is the live example.
6. No auth, no personalisation, no production hardening.
7. Hybrid lexical + dense retrieval, not a learned reranker.
8. Local ChromaDB; a real vector DB is needed past tens of thousands of chunks.

Close on the fifth one, because it is the honest version of the pitch:

> "The interesting part is not that it answers. It is that when it cannot answer, it says so, and
> you can see why in the sources panel."

---

## Rehearsal checklist

Run the whole thing twice before showing it, and once with the network off. Tick only what you saw:

- [ ] Both factual answers returned the expected text, from the expected scheme
- [ ] Each answer showed exactly one `View source` link
- [ ] Advice refused, performance redirected, PII refused
- [ ] The S3 exit-load question was refused (optional but strong: `What is the exit load on the HDFC ELSS Tax Saver Fund - Direct Plan Growth?`)
- [ ] Elapsed time under 3:00
- [ ] Second run with the network disconnected answered identically

**Fallbacks if the demo fails on the day.** No screen recording and no PDF export were produced —
this repository is the deliverable, and neither artefact was requested as a file. If a recording is
wanted, `streamlit run app.py --server.headless true` plus any screen recorder will do, and every
answer the script depends on is reproduced verbatim in `docs/sample_qa.md` so a static PDF of this
script plus that file is a usable fallback.
