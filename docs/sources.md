# Sources

The markdown twin of `data/sources.csv`. The CSV is the machine-readable registry and the only
thing the code reads; this file is for a human deciding whether to trust an answer.

Regenerate the fact-family column rather than trusting a previous version of this table: the honest
question is not "does the page mention exit load anywhere" but "can the assistant actually answer
this from this source", and that is what the grounding gate decides.

## Ingested sources (5)

All five are HDFC AMC scheme pages fetched once and snapshotted. The snapshot in `data/processed/` is
the source of truth for the demo; nothing is re-crawled at query time, so every answer is only as
current as its `fetched_at` date.

| ID | Scheme | Type | Publisher | URL | Fetched | Fact families backed |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | HDFC Large Cap Fund - Direct Growth | scheme page | HDFC AMC (via Groww) | [groww.in](https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth) | 2026-09-27 | expense ratio, exit load, min SIP, risk rating, benchmark |
| S2 | HDFC Equity Fund (Flexi Cap) - Direct Growth | scheme page | HDFC AMC (via Groww) | [groww.in](https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth) | 2026-09-27 | expense ratio, exit load, min SIP, risk rating, benchmark |
| S3 | HDFC ELSS Tax Saver Fund - Direct Plan Growth | scheme page | HDFC AMC (via Groww) | [groww.in](https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth) | 2026-09-27 | expense ratio, min SIP, risk rating, benchmark — **no exit-load text** |
| S4 | HDFC Small Cap Fund - Direct Growth | scheme page | HDFC AMC (via Groww) | [groww.in](https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth) | 2026-09-27 | expense ratio, exit load, min SIP, risk rating, benchmark |
| S5 | HDFC Balanced Advantage Fund - Direct Growth | scheme page | HDFC AMC (via Groww) | [groww.in](https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth) | 2026-09-27 | expense ratio, exit load, min SIP, risk rating, benchmark |

**24 of 25 scheme/family pairs are answerable.** The single gap is S3's exit load, and it is a
genuine absence in the page rather than a retrieval failure: no S3 chunk contains the word *exit* or
the word *load* at all. The gate refuses the question and points at the scheme page rather than
borrowing another scheme's answer.

## Education sources (2) — links only, never ingested

These are registered so a refusal can offer somewhere useful to go. They are never fetched, chunked,
embedded, or cited as a fact source, and `allowed_for_citation` is `false` for both, which the
citation validator enforces.

| ID | Title | URL | Used for |
| --- | --- | --- | --- |
| E1 | AMFI — Association of Mutual Funds in India | [amfiindia.com](https://www.amfiindia.com/) | Advice and performance refusals |
| E2 | Groww Help Centre | [groww.in/help](https://groww.in/help) | PII refusal ("contact support") |

## Why Groww and not hdfcfund.com

`hdfcfund.com` is HDFC's own domain, but it returns **HTTP 403** to a scripted client, so it is not
fetchable and cannot be snapshotted. The five pages above are distributor-hosted (Groww) pages that
carry the scheme facts. That is a real limitation of the corpus and it is stated in the app's scope
line rather than hidden: the pages are Groww's rendering of HDFC's funds, not HDFC's own documents.
`hdfcmutualfund.com` is not HDFC's domain at all and was removed during the Phase 0 spike. Full
findings, including every candidate URL tried, are in `docs/corpus_matrix.md`.

## Host allowlist

Only `groww.in`, `amfiindia.com`, and `sebi.gov.in` may be fetched. A URL outside that list is
rejected at load time, so a redirect to an unexpected host cannot widen the corpus.
