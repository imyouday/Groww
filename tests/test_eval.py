"""Phase 11: the metrics harness, the §12 calibration, and the ablation runners.

Two halves, tested separately. The metrics are pure functions over `Prediction` rows, so they are
tested with synthetic rows — including a synthetic set that is *wrong* in one specific way, because
a metric that has only ever seen perfect input is a metric that has never been shown it can fail.
The calibration and the ablation runners touch the real index, so they are tested on the sweep
arithmetic and on the report rendering, which are the parts that would silently produce a plausible
wrong number.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from eval import checks, run_eval
from eval.checks import Metric, Prediction, chunk_is_relevant, evaluate
from src.config import load_settings


def prediction(**overrides) -> Prediction:
    """Return a perfect factual row: right scheme, right citation, grounded number, one sentence."""
    base = Prediction(
        id="G01",
        question="What is the expense ratio of the HDFC Large Cap fund?",
        expected_kind="factual",
        answer_kind="factual",
        expected_scheme="HDFC Large Cap Fund - Direct Growth",
        fact_family="expense_ratio",
        expected_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        must_include="1.03%",
        answer_text="Expense ratio 1.03%.",
        citation_url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        citation_allowed=True,
        generator="extractive",
        top_scheme_name="HDFC Large Cap Fund - Direct Growth",
        top_chunk_text="Expense ratio 1.03% of the assets.",
        sentence_count=1,
        context_text="Expense ratio 1.03% of the assets.",
    )
    return replace(base, **overrides)


def probe(**overrides) -> Prediction:
    """Return a perfect refusal row."""
    fields = {
        "id": "P01",
        "question": "Should I buy the ELSS?",
        "expected_kind": "refusal",
        "answer_kind": "refusal",
        "expected_scheme": "",
        "fact_family": "",
        "expected_url": "",
        "must_include": "",
        "answer_text": "I can only share facts.",
        "citation_url": "https://www.amfiindia.com/",
        "citation_allowed": False,
        "top_scheme_name": "",
        "top_chunk_text": "",
        "context_text": "",
    }
    return prediction(**(fields | overrides))


def test_a_perfect_run_scores_one_on_every_graded_metric() -> None:
    """The ceiling is 1.0 everywhere except the two that must be 0, so a regression is visible."""
    results = evaluate([prediction(), probe()], load_settings())
    values = {result.name: result.value for result in results}
    assert values["answer_correctness"] == 1.0
    assert values["citation_validity"] == 1.0
    assert values["top1_retrieval_hit"] == 1.0
    assert values["refusal_precision"] == 1.0
    assert values["refusal_recall"] == 1.0
    assert values["length_compliance"] == 1.0
    assert values["pii_leakage"] == 0
    assert values["grounding_gap_rate"] == 0.0
    assert all(result.met for result in results)


def test_citation_validity_is_one_only_when_the_citation_is_registry_allowed() -> None:
    """A registered URL is not enough: it must also be the source carrying the answer."""
    assert checks.citation_validity([prediction()]) == 1.0
    assert (
        checks.citation_validity(
            [prediction(citation_allowed=False, citation_url="https://groww.in.evil.example/x")]
        )
        == 0.0
    )
    wrong_but_registered = prediction(citation_url="https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth")
    assert checks.citation_validity([wrong_but_registered]) == 0.0


def test_an_invented_number_shows_up_as_a_grounding_gap() -> None:
    """The metric re-runs V4 over the rendered answer, so a fabricated figure cannot pass."""
    invented = prediction(answer_text="The expense ratio is 0.35%.", must_include="0.35%")
    assert invented.ungrounded_numbers() == ["0.35%"]
    assert checks.grounding_gap_rate([invented]) == 1.0
    assert checks.answer_correctness([invented]) == 1.0


def test_a_number_that_appears_in_the_context_is_grounded() -> None:
    """1.030 must not be forgiven as 1.03: containment is over the digit string, digit for digit."""
    assert checks.grounding_gap_rate([prediction(answer_text="Expense ratio 1.03%.")]) == 0.0
    assert prediction(answer_text="Expense ratio 1.030%.").ungrounded_numbers() == ["1.030%"]


def test_a_must_include_miss_is_a_correctness_miss() -> None:
    """The golden CSV's label is the contract: an answer without it is wrong even if it reads well."""
    assert checks.answer_correctness([prediction(answer_text="The fee is disclosed on the page.")]) == 0.0


def test_a_top1_from_the_right_scheme_with_no_fact_term_is_not_a_retrieval_hit() -> None:
    """Right scheme, wrong section: the answer would have been written from an unrelated chunk."""
    row = prediction(top_chunk_text="HDFC Large Cap Fund - Direct Growth is a large cap fund.")
    assert checks.top1_retrieval_hit([row]) == 0.0
    assert checks.top1_retrieval_hit([prediction()]) == 1.0


def test_pii_leakage_counts_a_value_in_an_answer_or_in_a_log_line() -> None:
    """C2 covers the transcript and the log; a value in either is a leak, and the log must be seen."""
    pan = "ABCDE1234F"
    assert checks.pii_leakage([prediction(pii_values=(pan,))]) == 0
    echoed = prediction(pii_values=(pan,), answer_text=f"Your PAN {pan} was noted.")
    assert checks.pii_leakage([echoed]) == 1
    logged = prediction(pii_values=(pan,), logged_fields=(f"query=my PAN is {pan}",))
    assert checks.pii_leakage([logged]) == 1
    assert checks.pii_leakage([prediction(pii_values=("ABCDE1234F",), logged_fields=("pii_hits=1",))]) == 0


def test_a_factual_question_refused_counts_against_recall_not_precision() -> None:
    """The two refusal metrics have different denominators, and mixing them up hides a false refusal."""
    refused = prediction(answer_kind="refusal", answer_text="I can only share facts.")
    assert checks.refusal_recall([refused]) == 0.0
    assert checks.refusal_precision([refused, probe()]) == 1.0
    wrong_refusal = probe(answer_kind="smalltalk")
    assert checks.refusal_precision([wrong_refusal]) == 0.0


def test_length_compliance_uses_the_configured_sentence_budget() -> None:
    """Three sentences is the config value, and an empty body is not compliance."""
    settings = load_settings()
    assert checks.length_compliance([prediction(sentence_count=3)], settings) == 1.0
    assert checks.length_compliance([prediction(sentence_count=4)], settings) == 0.0
    assert checks.length_compliance([prediction(sentence_count=0)], settings) == 0.0


def test_an_empty_set_scores_as_a_pass_but_reports_zero_rows() -> None:
    """0 questions is not evidence, so the rate is neutral while the row count says there was nothing."""
    results = evaluate([], load_settings())
    assert all(result.total == 0 for result in results)
    assert next(result for result in results if result.name == "answer_correctness").value == 1.0


def test_chunk_relevance_needs_the_scheme_and_the_fact_term() -> None:
    """§12's label, spelled out: a term-bearing chunk from another scheme is not evidence."""
    terms = ("expense ratio", "ter")
    assert chunk_is_relevant("S1", "Expense ratio 1.03%", "S1", terms)
    assert not chunk_is_relevant("S2", "Expense ratio 1.03%", "S1", terms)
    assert not chunk_is_relevant("S1", "Very High risk", "S1", terms)


def test_fact_terms_map_the_golden_csvs_family_labels_onto_the_configured_lists() -> None:
    """The CSV says `risk_rating`; the config says `riskometer`. The alias table is the bridge."""
    assert checks.fact_terms_for("risk_rating") == ("riskometer", "risk", "risk rating")
    assert checks.fact_terms_for("expense_ratio") == (
        "expense ratio",
        "ter",
        "expense ratio and other fees",
        "charges",
    )
    assert checks.fact_terms_for("not_a_family") == ()


def test_the_sweep_finds_the_smallest_admissible_tau() -> None:
    """On labelled scores, admissibility is `hit_rate ≥ 0.85` with no false gate, and smallest wins.

    The labels are chosen so no τ is admissible: G03's best irrelevant score (0.90) clears every
    τ that G03's own relevant score (0.40) would still pass, so raising the threshold cannot separate
    them. That is §12's "fix the corpus, not the threshold" case, made checkable.
    """
    scores = [
        run_eval.LabelledScore("G01", 0.90, 0.20),
        run_eval.LabelledScore("G02", 0.55, 0.30),
        run_eval.LabelledScore("G03", 0.40, 0.90),
        run_eval.LabelledScore("G04", 0.45, 0.25),
    ]
    sweep = run_eval.sweep_threshold(scores)
    assert [point.tau for point in sweep] == list(run_eval.TAU_SWEEP)
    by_tau = {point.tau: point for point in sweep}
    assert by_tau[0.20].hit_rate == 1.0
    assert by_tau[0.20].false_gate == 1.0
    assert by_tau[0.35].hit_rate == 1.0
    assert by_tau[0.35].false_gate == 0.25
    assert by_tau[0.50].hit_rate == 0.5
    assert by_tau[0.50].false_gate == 0.25
    assert not any(point.meets() for point in sweep)


def test_a_tau_is_admissible_only_above_the_relevant_scores_and_below_the_irrelevant_ones() -> None:
    """The separating band is the whole calibration: 0.20 to 0.60 is where a hit_rate ≥ 0.85 coexists
    with a false gate of zero, and the procedure returns its lower edge."""
    scores = tuple(
        run_eval.LabelledScore(f"G{index:02d}", 0.62, 0.31) for index in range(20)
    )
    sweep = run_eval.sweep_threshold(scores)
    admissible = [point.tau for point in sweep if point.meets()]
    assert admissible == [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]


def test_calibration_returns_none_when_no_tau_is_admissible() -> None:
    """A question with no relevant chunk can never be a hit, so no τ may be returned as calibrated."""
    scores = [run_eval.LabelledScore(f"G{index:02d}", None, 0.9) for index in range(4)]
    assert all(point.hit_rate == 0.0 for point in run_eval.sweep_threshold(scores))
    calibration = run_eval.Calibration(
        tau=None,
        sweep=run_eval.sweep_threshold(scores),
        scores=tuple(scores),
        hit_rate_target=0.85,
    )
    assert calibration.tau is None
    assert "no admissible tau" in run_eval.render_calibration(calibration)


def test_calibrate_threshold_picks_the_expected_tau_on_synthetic_scores() -> None:
    """The procedure is deterministic and returns the *smallest* admissible τ.

    With every relevant score at 0.42 and every irrelevant score at 0.10, the whole sweep is
    admissible and the answer is the sweep's floor, 0.20. A procedure that returned the largest
    admissible τ here would refuse far more than it needed to for no measured gain.
    """
    scores = tuple(
        run_eval.LabelledScore(f"G{index:02d}", 0.42, 0.10) for index in range(20)
    )
    sweep = run_eval.sweep_threshold(scores)
    calibration = run_eval.Calibration(
        tau=next(point.tau for point in sweep if point.meets()),
        sweep=sweep,
        scores=scores,
        hit_rate_target=0.85,
    )
    assert calibration.tau == 0.20
    assert calibration.as_dict()["tau"] == 0.20


def test_the_golden_and_probe_csvs_load_with_their_fixed_headers() -> None:
    """The harness is worthless against a renamed column, so the headers are asserted, not assumed."""
    golden = run_eval.load_golden()
    probes = run_eval.load_probes()
    assert len(golden) == 24
    assert len(probes) == 8
    assert {row.fact_family for row in golden} <= {
        "expense_ratio",
        "exit_load",
        "min_sip",
        "risk_rating",
        "benchmark",
    }
    assert all(row.must_include for row in golden)


def test_a_csv_with_the_wrong_header_is_refused(tmp_path) -> None:
    """A mislabelled dataset must fail loudly rather than score zero rows as a perfect run."""
    path = tmp_path / "golden.csv"
    path.write_text("id,question\nG01,hello\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must have the header"):
        run_eval.load_golden(path)


def test_the_metrics_table_reports_a_miss_as_a_miss() -> None:
    """A table that always says "yes" is decoration; a failure must read as NO."""
    table = run_eval.render_metrics_table(
        evaluate([prediction(answer_kind="not_in_corpus")], load_settings())
    )
    assert "NO" in table
    assert "| answer_correctness |" in table


def test_failure_rows_name_the_question_and_the_reason() -> None:
    """A counted failure is not actionable; the report has to say which row and why."""
    failures = run_eval.failure_rows(
        [prediction(), prediction(id="G02", answer_kind="not_in_corpus", answer_text="Not in corpus.")]
    )
    assert [row["id"] for row in failures] == ["G02"]
    assert "not_in_corpus" in failures[0]["reason"]


def test_a_scratch_settings_never_points_at_the_demo_index(tmp_path) -> None:
    """Ablation A1 rebuilds three times; one of them writing to `data/chroma` would corrupt the demo."""
    base = load_settings()
    demo = str(base.paths.resolve("chroma_dir"))
    scratch = run_eval._scratch_settings(base, "semantic_350", tmp_path)
    assert str(scratch.paths.resolve("chroma_dir")) != demo
    assert str(base.paths.resolve("chroma_dir")) == demo
    assert str(scratch.paths.resolve("chunks_dump")).startswith(str(tmp_path))


def test_the_separating_band_is_measured_even_when_the_swept_grid_misses_it() -> None:
    """A sweep that finds nothing must still say *where* the scores separate.

    The real corpus separates cleanly — every s_i beats every t_i — but above the mandated grid's
    0.60 ceiling. A report saying only "no admissible tau" reads as a broken procedure; the band
    turns it into a measured result, and `band_in_sweep` is what distinguishes the two cases.
    """
    scores = tuple(
        run_eval.LabelledScore(f"G{index:02d}", 0.9, 0.3) for index in range(20)
    )
    above_grid = tuple(
        run_eval.LabelledScore(f"G{index:02d}", 0.87, 0.71) for index in range(20)
    )
    below_grid = tuple(
        run_eval.LabelledScore(f"G{index:02d}", 0.45, 0.30) for index in range(20)
    )
    band_of = lambda rows: run_eval.Calibration(
        tau=None,
        sweep=run_eval.sweep_threshold(rows),
        scores=rows,
        hit_rate_target=0.85,
    )
    assert band_of(above_grid).separating_band == (0.71, 0.87)
    assert band_of(above_grid).band_in_sweep is False
    assert band_of(below_grid).band_in_sweep is True
    assert "entirely above" in run_eval.render_calibration(band_of(above_grid))
    assert "Separating band" in run_eval.render_calibration(band_of(below_grid))


def test_overlapping_scores_have_no_band_at_all() -> None:
    """`None` is the §12 "fix the corpus" case, and it has to be distinguishable from off-grid."""
    scores = [run_eval.LabelledScore("G01", 0.40, 0.90), run_eval.LabelledScore("G02", 0.50, 0.20)]
    calibration = run_eval.Calibration(
        tau=None,
        sweep=run_eval.sweep_threshold(scores),
        scores=tuple(scores),
        hit_rate_target=0.85,
    )
    assert calibration.separating_band is None
    assert calibration.band_in_sweep is False
    rendered = run_eval.render_calibration(calibration)
    assert "No threshold value separates" in rendered
    assert "G01" in rendered


def test_a_question_with_no_relevant_chunk_leaves_the_band_undefined() -> None:
    """A set with no relevant chunks at all must not report a band invented from the t_i values."""
    scores = [run_eval.LabelledScore("G01", None, 0.9), run_eval.LabelledScore("G02", None, 0.8)]
    calibration = run_eval.Calibration(
        tau=None,
        sweep=run_eval.sweep_threshold(scores),
        scores=tuple(scores),
        hit_rate_target=0.85,
    )
    assert calibration.separating_band is None
    assert "no relevant chunk" in run_eval.render_calibration(calibration)


def test_a_clean_run_reports_no_fallbacks() -> None:
    """`guardrail: passed` is written on every answered question, so keying on its presence made A4
    report 24 fallbacks for a run where nothing fell back — a column that is always full is a column
    a reviewer learns to skip."""
    clean = {"generator": "extractive", "guardrail": "passed", "timings_ms": {"generation_ms": 3.0}}
    assert run_eval._fallbacks(clean) == ()
    retried = {
        "generator": "llm",
        "guardrail": "v1_length",
        "timings_ms": {"generation_ms": 900.0, "extractive_retry_ms": 4.2},
    }
    assert run_eval._fallbacks(retried) == ("guardrail=v1_length", "extractive_retry_ms=4.2")
    failed = {"generator": "extractive", "timings_ms": {"generator_error": "GenerationError"}}
    assert run_eval._fallbacks(failed) == ("generator_error=GenerationError",)
    assert run_eval._fallbacks({}) == ()


def test_every_ablation_table_renders_without_crashing() -> None:
    """A table that renders only for the shape it happened to be written in is a report that breaks
    on the day the payload grows a key: A2 returns a nested mapping, and the renderer, handed that
    mapping instead of a row list, raised `KeyError: 0` after the three A1 index rebuilds had
    already been paid for."""
    calibration = run_eval.Calibration(
        tau=None,
        sweep=run_eval.sweep_threshold([run_eval.LabelledScore("G01", 0.87, 0.71)]),
        scores=(run_eval.LabelledScore("G01", 0.87, 0.71),),
        hit_rate_target=0.85,
    )
    thresholds = [{"tau": 0.35, "answer_correctness": 1.0, "is_calibrated": False}]
    section = run_eval._ablation_section(
        "all",
        {
            "A1": [{"variant": "semantic_600", "chunks": 10}],
            "A2": thresholds,
            "A3": [{"configuration": "dense_only", "top1_retrieval_hit": 0.5}],
            "A4": [{"generator": "extractive", "answer_correctness": 1.0}],
            "calibration": calibration.as_dict(),
        },
        load_settings(),
    )
    for fragment in (
        "A1 —",
        "A2 —",
        "A3 —",
        "A4 —",
        "separating band (0.71, 0.87]",
        "outside the swept grid",
        "semantic_600",
    ):
        assert fragment in section


def test_the_shipped_a2_table_is_a_row_list_and_the_sweep_stays_separate() -> None:
    """`_run_ablation` must hand the renderer rows, keeping the sweep under its own key."""
    base = load_settings()
    result = run_eval.ablation_a2(base, "extractive")
    assert set(result) == {"calibration", "thresholds"}
    assert isinstance(result["thresholds"], list)
    assert all(isinstance(row, dict) for row in result["thresholds"])
    assert "sweep" in result["calibration"]


def test_dense_only_stays_dense_only() -> None:
    """A3's first row must differ from its second by the boosts alone, so they cannot be the same run.

    Zeroing the boosts and then replacing the whole retrieval block with one derived from the base
    settings would leave every boost at its configured weight, and `dense_only` would quietly become
    a second `dense_boost` row — an ablation that compares a configuration with itself.
    """
    base = load_settings()
    dense_only = run_eval._mmr_off(run_eval._no_boosts(base))
    dense_boost = run_eval._mmr_off(base)
    assert dense_only.retrieval.boosts.fact_term == 0.0
    assert dense_only.retrieval.boosts.additional_fact_term == 0.0
    assert dense_only.retrieval.boosts.section_type_match == 0.0
    assert dense_only.retrieval.boosts.scheme_match == 0.0
    assert dense_boost.retrieval.boosts == base.retrieval.boosts
    assert dense_boost.retrieval.boosts.fact_term > 0.0
    assert dense_only.retrieval.mmr_lambda == 1.0
    assert dense_boost.retrieval.mmr_lambda == 1.0
    assert base.retrieval.mmr_lambda != 1.0


def test_pipeline_build_forwards_the_chunking_variant(tmp_path, monkeypatch) -> None:
    """A1 has to rebuild the index from a variant, so `build()` must hand the variant to chunking.

    Asserted on what `chunk_all` receives, with every other stage stubbed, because a variant that is
    accepted and then dropped still produces a plausible-looking store — built from the demo's own
    chunks — and every A1 row would then be a second copy of the baseline.
    """
    from src import chunking as chunking_module
    from src.models import ChunkRecord, SectionType

    seen: list[object] = []
    chunk = ChunkRecord(
        chunk_id="C1",
        source_id="groww-hdfc-large-cap",
        scheme_id="S1",
        scheme_name="HDFC Large Cap Fund - Direct Growth",
        section="Fees",
        section_type=SectionType.FEES,
        text="Expense ratio 1.03% of the assets.",
        embed_text="Expense ratio 1.03% of the assets.",
        url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        title="HDFC Large Cap",
        fetched_at="2026-01-01T00:00:00+00:00",
        ordinal=0,
        token_count=9,
    )
    document = SimpleNamespace(source=SimpleNamespace(source_id="groww-hdfc-large-cap"))

    def record_variant(registry, settings, variant=None):
        """Record the variant the build asked for, then return one chunk."""
        seen.append(variant)
        return [chunk], []

    monkeypatch.setattr(run_eval.pipeline, "store", SimpleNamespace(
        reset=lambda settings: None,
        upsert_chunks=lambda chunks, vectors, settings: len(chunks),
        delete_missing=lambda ids, settings: 0,
        report_path=lambda settings: tmp_path / "build_report.json",
        utc_now=lambda: "2026-01-01T00:00:00+00:00",
        chroma_version_string=lambda: "1.0.0",
    ))
    monkeypatch.setattr(run_eval.pipeline, "loading", SimpleNamespace(
        load_registry=lambda settings: object(),
        load_all=lambda registry, settings, refresh=False: ([document], []),
    ))
    monkeypatch.setattr(run_eval.pipeline, "chunking", SimpleNamespace(
        chunk_all=record_variant,
        chunk_stats=lambda chunks: {"median_tokens": 9},
        write_chunks_jsonl=lambda chunks, path: None,
    ))
    monkeypatch.setattr(run_eval.pipeline, "embedding", SimpleNamespace(
        embed=lambda texts, settings: [[0.0] * 384 for _ in texts],
        model_id=lambda: "all-MiniLM-L6-v2",
    ))

    settings = load_settings()
    scratch = run_eval._scratch_settings(settings, "fixed_512", tmp_path)
    run_eval.pipeline.build(rebuild=True, settings=scratch, variant=chunking_module.Variant.FIXED_512)
    assert seen == [chunking_module.Variant.FIXED_512]


def test_log_capture_leaves_the_console_handler_where_it_found_it() -> None:
    """A capture that only *adds* a handler leaves the console stream writing 32 questions per run.

    The report this harness writes is the artefact, and burying it under the very logs it is auditing
    makes it unreadable; a handler that also stays attached would go on appending into a sink from a
    finished run, so a later run's lines would turn up in an earlier capture.
    """
    logger = run_eval.pipeline.LOGGER
    with run_eval.LogCapture() as first:
        logger.info("first")
    assert any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers)
    with run_eval.LogCapture() as second:
        logger.info("second")
    assert first.lines == ["first"]
    assert second.lines == ["second"]
