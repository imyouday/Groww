# Evaluation report

Append-only. Every run adds a dated section and nothing is edited out, because the
history of the numbers is part of the evidence.

## Run 2026-09-28T07:55:43+00:00

### Metrics — provider `extractive`, config_hash `75ee0d1fb6b6`

| Metric | Value | Target | n | Met |
| --- | --- | --- | --- | --- |
| answer_correctness | 1 | >= 0.9 | 24 | yes |
| citation_validity | 1 | >= 1 | 24 | yes |
| top1_retrieval_hit | 1 | >= 0.85 | 24 | yes |
| refusal_precision | 1 | >= 1 | 8 | yes |
| refusal_recall | 1 | >= 1 | 24 | yes |
| length_compliance | 1 | >= 1 | 32 | yes |
| pii_leakage | 0 | <= 0 | 32 | yes |
| grounding_gap_rate | 0 | <= 0 | 24 | yes |

32 rows (24 golden, 8 probes). Median latency 38 ms, p95 50 ms.

No row missed its label.

## Run 2026-09-28T07:56:17+00:00

### Metrics — provider `llm`, config_hash `75ee0d1fb6b6`

| Metric | Value | Target | n | Met |
| --- | --- | --- | --- | --- |
| answer_correctness | 1 | >= 0.9 | 24 | yes |
| citation_validity | 1 | >= 1 | 24 | yes |
| top1_retrieval_hit | 1 | >= 0.85 | 24 | yes |
| refusal_precision | 1 | >= 1 | 8 | yes |
| refusal_recall | 1 | >= 1 | 24 | yes |
| length_compliance | 1 | >= 1 | 32 | yes |
| pii_leakage | 0 | <= 0 | 32 | yes |
| grounding_gap_rate | 0 | <= 0 | 24 | yes |

32 rows (24 golden, 8 probes). Median latency 805 ms, p95 1804 ms.

No row missed its label.

## Run 2026-09-28T07:56:30+00:00

### Calibration (architecture.md §12) — configured tau 0.35

**no admissible tau** — the fix is the corpus or the chunk boundaries, not the threshold.

Separating band: (0.8165, 0.8402] — entirely above the swept grid {0.2..0.6}. Every s_i is at least 0.8402 and every t_i at most 0.8165, so a threshold in that interval keeps all 24 hits and drops all 24 false gates.

| tau | hit_rate | false_gate | admissible |
| --- | --- | --- | --- |
| 0.2 | 1.0 | 1.0 | no |
| 0.25 | 1.0 | 1.0 | no |
| 0.3 | 1.0 | 1.0 | no |
| 0.35 | 1.0 | 1.0 | no |
| 0.4 | 1.0 | 1.0 | no |
| 0.45 | 1.0 | 1.0 | no |
| 0.5 | 1.0 | 1.0 | no |
| 0.55 | 1.0 | 1.0 | no |
| 0.6 | 1.0 | 1.0 | no |

| id | s_i (best relevant) | t_i (best irrelevant) |
| --- | --- | --- |
| G01 | 0.8642 | 0.736 |
| G02 | 0.8836 | 0.8059 |
| G03 | 0.8635 | 0.7766 |
| G04 | 0.867 | 0.7546 |
| G05 | 0.8748 | 0.7844 |
| G06 | 0.8402 | 0.7035 |
| G07 | 0.8875 | 0.78 |
| G08 | 0.8586 | 0.7256 |
| G09 | 0.9129 | 0.7521 |
| G10 | 0.967 | 0.668 |
| G11 | 1.0054 | 0.8023 |
| G12 | 0.9687 | 0.7189 |
| G13 | 0.9752 | 0.6909 |
| G14 | 0.9963 | 0.6741 |
| G15 | 0.9096 | 0.7497 |
| G16 | 0.9078 | 0.8165 |
| G17 | 0.8583 | 0.8007 |
| G18 | 0.9264 | 0.7556 |
| G19 | 0.8989 | 0.7664 |
| G20 | 0.9543 | 0.715 |
| G21 | 0.9445 | 0.8073 |
| G22 | 0.9086 | 0.7851 |
| G23 | 0.9686 | 0.744 |
| G24 | 0.9508 | 0.7569 |

The labels are §12's own rule applied in code rather than by hand: a chunk is relevant when it
is the expected scheme *and* its text carries a configured surface form of the expected fact
family. That is a weaker claim than a hand-labelled set, and the same term lists also feed the
retrieval boost, so this sweep measures whether the threshold separates term-bearing
right-scheme chunks from the rest — not whether a human agreed. Stated rather than hidden.

## Run 2026-09-28T07:57:21+00:00

### Ablations — A1

A1 — chunking strategy (index rebuilt per variant into a scratch store)

| variant | chunks | median_tokens | max_tokens | answer_correctness | citation_validity | top1_retrieval_hit | refusal_recall | median_latency_ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| semantic_600 | 106 | 229.0 | 254 | 1.0 | 1.0 | 1.0 | 1.0 | 36.3 |
| semantic_350 | 106 | 229.0 | 254 | 1.0 | 1.0 | 1.0 | 1.0 | 38.1 |
| fixed_512 | 94 | 244.5 | 254 | 0.7917 | 1.0 | 0.7917 | 0.7917 | 37.6 |

A1's numbers come from a scratch store per variant (`data/eval_scratch/`), reset before each
rebuild, so the demo index in `data/chroma` is never left holding an ablation's chunks. A3's MMR
row is the shipped configuration (lambda = 0.3).

## Run 2026-09-28T07:57:37+00:00

### Ablations — A2

A2 — grounding-gate threshold

| tau | answer_correctness | refusal_recall | citation_validity | answered | is_calibrated |
| --- | --- | --- | --- | --- | --- |
| 0.25 | 1.0 | 1.0 | 1.0 | 24 | False |
| 0.35 | 1.0 | 1.0 | 1.0 | 24 | False |
| 0.45 | 1.0 | 1.0 | 1.0 | 24 | False |

A2 calibration sweep (architecture.md §12)

no admissible tau; separating band (0.8165, 0.8402] (outside the swept grid)

| tau | hit_rate | false_gate |
| --- | --- | --- |
| 0.2 | 1.0 | 1.0 |
| 0.25 | 1.0 | 1.0 |
| 0.3 | 1.0 | 1.0 |
| 0.35 | 1.0 | 1.0 |
| 0.4 | 1.0 | 1.0 |
| 0.45 | 1.0 | 1.0 |
| 0.5 | 1.0 | 1.0 |
| 0.55 | 1.0 | 1.0 |
| 0.6 | 1.0 | 1.0 |

A1's numbers come from a scratch store per variant (`data/eval_scratch/`), reset before each
rebuild, so the demo index in `data/chroma` is never left holding an ablation's chunks. A3's MMR
row is the shipped configuration (lambda = 0.3).

## Run 2026-09-28T07:57:55+00:00

### Ablations — A3

A3 — dense only vs +boost vs +MMR

| configuration | top1_retrieval_hit | answer_correctness | citation_validity | mean_schemes_in_context | mean_context_tokens |
| --- | --- | --- | --- | --- | --- |
| dense_only | 0.9583 | 0.875 | 1.0 | 1 | 365.4 |
| dense_boost | 1.0 | 1.0 | 1.0 | 1 | 382.2 |
| dense_boost_mmr | 1.0 | 1.0 | 1.0 | 1 | 638.4 |

A1's numbers come from a scratch store per variant (`data/eval_scratch/`), reset before each
rebuild, so the demo index in `data/chroma` is never left holding an ablation's chunks. A3's MMR
row is the shipped configuration (lambda = 0.3).

## Run 2026-09-28T07:58:26+00:00

### Ablations — A4

A4 — generator: LLM vs extractive

| provider | answer_correctness | citation_validity | grounding_gap_rate | length_compliance | fallbacks | rows_degraded | why | median_latency_ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extractive | 1.0 | 1.0 | 0.0 | 1.0 | 0 | 0 | - | 40.7 |
| llm | 1.0 | 1.0 | 0.0 | 1.0 | 51 | 17 | extractive_retry_ms x17, generator_error x17, guardrail x17 | 814.9 |

A1's numbers come from a scratch store per variant (`data/eval_scratch/`), reset before each
rebuild, so the demo index in `data/chroma` is never left holding an ablation's chunks. A3's MMR
row is the shipped configuration (lambda = 0.3).
