# Sample Q&A

Every answer below is **verbatim output** from `python -m src.pipeline ask`, captured with
provider `extractive` against `config_hash` `75ee0d1fb6b6`. None of it is
hand-written: regenerate it with the same commands and the text will match, because the
answer is assembled from retrieved chunks rather than composed.

The set is chosen to show the system at its best *and* at its limits: five fact families,
an advice refusal, a performance redirect, a PII refusal, a well-formed question about a fund
outside the corpus, and one fact the corpus does not carry for a scheme that carries four others.

| # | Case | Question | Kind |
| --- | --- | --- | --- |
| 1 | Expense ratio | What is the expense ratio of the HDFC Large Cap Fund - Direct Growth? | `factual` |
| 2 | Exit load | What is the exit load on the HDFC Large Cap fund? | `factual` |
| 3 | Exit load, absent from the corpus | What is the exit load on the HDFC ELSS Tax Saver Fund - Direct Plan Growth? | `not_in_corpus` |
| 4 | Minimum SIP | What is the minimum SIP amount for the HDFC Balanced Advantage Fund - Direct Growth? | `factual` |
| 5 | Risk rating | What is the risk rating of the HDFC Small Cap Fund - Direct Growth? | `factual` |
| 6 | Benchmark | What is the benchmark of the HDFC Equity Fund (Flexi Cap) - Direct Growth? | `factual` |
| 7 | Advice refusal | Which fund should I put my money into for a five year goal? | `refusal` |
| 8 | Performance redirect | Which of these gave the best 1-year return? | `performance_redirect` |
| 9 | PII refusal | My PAN is ABCDE1234F, please tell me which folio holds my units | `pii_refusal` |
| 10 | Out of corpus | What is the expense ratio of Parag Parflex? | `out_of_corpus` |

## 1. Expense ratio

**Q.** What is the expense ratio of the HDFC Large Cap Fund - Direct Growth?

**A.** Expense ratio 1.03%

- **Kind:** `factual`
- **Link:** https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
- **Last updated from sources:** 2026-09-27
- **Why this case is in the set:** One of the five supported fact families, answered from the scheme page's fee block.

## 2. Exit load

**Q.** What is the exit load on the HDFC Large Cap fund?

**A.** Exit load of 1% if redeemed within 1 year

- **Kind:** `factual`
- **Link:** https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
- **Last updated from sources:** 2026-09-27
- **Why this case is in the set:** A fact the corpus carries for four of the five schemes.

## 3. Exit load, absent from the corpus

**Q.** What is the exit load on the HDFC ELSS Tax Saver Fund - Direct Plan Growth?

**A.** I don't have that in my sources for the 5 HDFC AMC schemes I cover, so I won't guess. The official scheme page is the place to check: https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth

- **Kind:** `not_in_corpus`
- **Link:** https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth
- **Last updated from sources:** n/a
- **Why this case is in the set:** S3's page carries no exit-load text, so the same well-formed question about a different scheme is refused. Same shape as the answerable case above, opposite outcome, which is the scope boundary doing its job rather than a bug.

## 4. Minimum SIP

**Q.** What is the minimum SIP amount for the HDFC Balanced Advantage Fund - Direct Growth?

**A.** Min. for 1st investment ₹100 Min. for 2nd investment ₹100 Min. for SIP ₹100

- **Kind:** `factual`
- **Link:** https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth
- **Last updated from sources:** 2026-09-27
- **Why this case is in the set:** The family that needed a vocabulary fix in Phase 6 to rank the right chunk first.

## 5. Risk rating

**Q.** What is the risk rating of the HDFC Small Cap Fund - Direct Growth?

**A.** The HDFC Small Cap Fund Direct Growth is rated Very High risk.

- **Kind:** `factual`
- **Link:** https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth
- **Last updated from sources:** 2026-09-27
- **Why this case is in the set:** A rating quoted from the source, never derived or compared.

## 6. Benchmark

**Q.** What is the benchmark of the HDFC Equity Fund (Flexi Cap) - Direct Growth?

**A.** Fund benchmark NIFTY 500 Total Return Index

- **Kind:** `factual`
- **Link:** https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth
- **Last updated from sources:** 2026-09-27
- **Why this case is in the set:** A plain lookup with no number of our own anywhere in the answer.

## 7. Advice refusal

**Q.** Which fund should I put my money into for a five year goal?

**A.** I can only share facts — I can't recommend funds or suggest what to do with your money. Here's an official investor-education page instead: https://www.amfiindia.com/. Ask me anything factual (expense ratio, exit load, minimum SIP, lock-in, benchmark, statements) and I'll answer with a source.

- **Kind:** `refusal`
- **Link:** https://www.amfiindia.com/
- **Last updated from sources:** n/a
- **Why this case is in the set:** A recommendation request. Answering it would be advice, so it is refused and redirected.

## 8. Performance redirect

**Q.** Which of these gave the best 1-year return?

**A.** I don't provide or compare returns, NAVs, or performance figures. The official factsheet for this scheme has the published figures: https://www.amfiindia.com/.

- **Kind:** `performance_redirect`
- **Link:** https://www.amfiindia.com/
- **Last updated from sources:** n/a
- **Why this case is in the set:** A comparison. The system never computes or ranks returns; it points at the source page.

## 9. PII refusal

**Q.** My PAN is ABCDE1234F, please tell me which folio holds my units

**A.** Please don't share personal identifiers like PAN, Aadhaar, account numbers, or OTPs — I won't store them. For account-specific help, use the official support channel: https://groww.in/help.

- **Kind:** `pii_refusal`
- **Link:** https://groww.in/help
- **Last updated from sources:** n/a
- **Why this case is in the set:** Client material in the question. The value is detected, never stored, echoed, or logged.

## 10. Out of corpus

**Q.** What is the expense ratio of Parag Parflex?

**A.** My sources cover only HDFC AMC's 5 schemes (HDFC Large Cap Fund - Direct Growth, HDFC Equity Fund (Flexi Cap) - Direct Growth, HDFC ELSS Tax Saver Fund - Direct Plan Growth, HDFC Small Cap Fund - Direct Growth, HDFC Balanced Advantage Fund - Direct Growth), all facts-only. I can't answer from a page I don't have — please treat anything about other funds as unverified here. If you meant an in-scope scheme, I can share its documented facts: https://www.amfiindia.com/

- **Kind:** `out_of_corpus`
- **Link:** https://www.amfiindia.com/
- **Last updated from sources:** n/a
- **Why this case is in the set:** A well-formed question about a fund outside the five registered schemes. Refused, not guessed.
