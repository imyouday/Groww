# Corpus matrix — Phase 0 spike

**Run date:** 2026-09-27 · **Script:** `scripts/spike_fetch.py` · **Snapshots:** `data/raw/spike/`

Purpose: decide whether the 5 public scheme pages in `PRD.md` §5.1 actually contain the 7
in-scope fact families as *retrievable text*, before any pipeline code is written (risk R1).
Answer: **partly.** Four of the five URLs work; the ELSS URL in the PRD is dead; and three
fact families are not obtainable from any fetchable official page. Details below.

## 1. Scheme pages

| id | Scheme | URL status | Extractable text | Verdict |
| --- | --- | --- | --- | --- |
| S1 | HDFC Large Cap Fund — Direct Growth | 200 | 17,678 chars | usable |
| S2 | HDFC Equity Fund (Flexi Cap) — Direct Growth | 200 | 19,536 chars | usable |
| S3 | HDFC ELSS Tax Saver Fund — Direct Plan Growth | 200 | 18,913 chars | usable, **URL corrected** |
| S4 | HDFC Small Cap Fund — Direct Growth | 200 | 19,841 chars | usable |
| S5 | HDFC Balanced Advantage Fund — Direct Growth | 200 | 43,485 chars | usable |

**URL correction (S3).** The brief's URL
`https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-growth`
returns **HTTP 404**. The live page is
`https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth`
(note `direct-plan`, not `direct`). The registry uses the corrected URL; the original 404
evidence is in the spike history. This is the single highest-value outcome of the spike: had
we not run it, the ELSS would have been silently empty at build time and the ELSS lock-in
question would have failed on demo day.

## 2. Fact family coverage (5 usable scheme pages)

`yes` = label and value appear in the extractable text. `false positive` = the regex matched,
but the surrounding text does not state the fact.

| Fact family | S1 | S2 | S3 | S4 | S5 | Genuinely covered? |
| --- | --- | --- | --- | --- | --- | --- |
| Expense ratio | yes | yes | yes | yes | yes | **yes** (1.03% / 0.77% / 1.21% / 0.78% / 0.78%) |
| Exit load | yes | yes | yes | yes | yes | **yes** |
| Minimum SIP | yes | yes | yes | yes | yes | **yes** (₹100, except ELSS ₹500) |
| Minimum lump sum | yes | yes | yes | yes | yes | **yes** |
| Benchmark | yes | yes | yes | yes | yes | **yes** (NIFTY 100 / 500 TRI, BSE 250 SmallCap TRI, NIFTY 50 Hybrid) |
| Risk rating | n/a | n/a | n/a | n/a | n/a | **partial** — pages say "rated Very High risk", never the word "riskometer" |
| ELSS lock-in | false positive | false positive | false positive | false positive | false positive | **no** — matches came from a related-funds nav list naming "HDFC ELSS Tax Saver Fund", not from a lock-in statement |
| Statement download | false positive | false positive | false positive | false positive | false positive | **no** — matched the tax glossary line "Tax: a percentage of your capital gains payable…", not download steps |

## 3. Sources rejected, and why

| Candidate | Result | Decision |
| --- | --- | --- |
| `hdfcmutualfund.com` (listed in `config.yaml` `allowed_hosts`) | wrong domain | remove from allowlist |
| `hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct` | **HTTP 403** | bot-blocked; not fetchable |
| `hdfcfund.com/statutory-disclosure/riskometers` | **HTTP 403** | bot-blocked; the only "riskometer" hit was inside the 403 error page itself |
| `investor.hdfcfund.com` (statement download) | login-gated | non-goal (`PRD.md` §3.2) |
| `sebi.gov.in` | connection reset | not fetchable from this environment |
| `investor.gov.in` | DNS does not resolve | not fetchable |
| `amfiindia.com/investor-education` | 404 (guessed path) | not a valid path |
| Economic Times, ClearTax, Value Research, Morningstar, Scripbox, News18, Coverfox | 200 | **rejected**: third-party publishers, forbidden by constraint C1 and by the brief ("no third-party blogs as sources") |
| CAMS `camsonline.com` (capital-gains statement) | login/PAN-gated | non-goal, and PII-adjacent |

Note: refusal links (`education_url`, `help_url`) do **not** need to be fetchable — they are
never ingested or chunked, only rendered as links. That is why a 403 on `hdfcfund.com` blocks
using it as a *corpus* source but does not block the refusal feature.

## 4. Decisions taken

1. **Corpus = the 5 Groww scheme pages**, with S3's URL corrected to `-direct-plan-growth`.
   All 5 are server-rendered; no `.md` snapshot fallback is needed, so `data/sources.csv`
   needs no `render` column.
2. **In-scope fact families reduce from 7 to 5** for the demo: expense ratio, exit load,
   minimum SIP / lump sum, benchmark, risk rating. `RISKOMETER` and `LOCK_IN` are kept in
   `config.retrieval.fact_terms` so the system recognises the question and answers
   "not in my sources" with a link, rather than hallucinating. `STATEMENTS` is reduced to a
   refusal/redirect: the system points the user to the official help page.
3. **`factsheet_index_url` remains unverified.** The performance redirect (`PRD.md` §12) wants
   an official factsheet link; HDFC's own factsheet host is 403. Must be resolved before
   Phase 8, or the performance redirect degrades to the help page.
4. **PII hint regions exist** on the scheme pages (1 hint type each — a phone/email pattern in
   broker contact boilerplate). Redaction at ingest is mandatory, not optional.

## 5. Consequences for the plan

- `PRD.md` §5.1 needs the S3 URL correction. Flagged, not yet edited.
- `PRD.md` §5.2 fact-family list and the 3 example questions in `PRD.md` §10 should use the
  5 verified families. Flagged, not yet edited.
- `config.yaml` `loading.allowed_hosts` must drop `hdfcmutualfund.com`. Done in this phase.
- The chunking decision in `PRD.md` §9.3 is **unaffected**: the spike confirms the pages are
  short labelled blocks (expense ratio, exit load, minimum SIP, benchmark), which is exactly
  the structure the semantic section chunker is designed for. `data/raw/spike/S1.txt` is the
  reference sample for Phase 4.
