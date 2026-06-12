"""Tests for genie.solve — the assembled per-question loop."""

import pytest

from genie.solve import (
    AnalysisResult,
    CallableAnalyst,
    ManifestCache,
    OracleAnalyst,
    Question,
    grade_solution,
    run,
    solve,
)


def _analyst(value, evidence="cell[3]"):
    return CallableAnalyst(lambda q, m, d: AnalysisResult(value=value, evidence=evidence))


# --- MCQ path: approximate analysis -> nearest option -> correct ----------


def test_mcq_snaps_to_nearest_option():
    q = Question(
        question="What is the adjusted p-value?",
        options=["0.0002", "7.82E-05", "0.0003", "1.85E-05"],
        ideal="0.0002",
        eval_mode="str_verifier",
    )
    sol = solve(q, _analyst(0.00021))  # within range of the true 0.0002
    assert sol.final_answer == "0.0002"
    assert sol.is_mcq
    assert grade_solution(sol, q.ideal, q.eval_mode) is True


def test_mcq_range_option_graded_by_identity():
    q = Question(
        question="What is the odds ratio?",
        options=["(1.50,1.54)", "(0.66,0.70)", "(0.95,1.05)"],
        ideal="(1.50,1.54)",
        eval_mode="range_verifier",
    )
    sol = solve(q, _analyst(1.52))
    assert sol.final_answer == "(1.50,1.54)"
    assert grade_solution(sol, q.ideal, q.eval_mode) is True


# --- Open-ended path: representation applied -------------------------------


def test_open_pvalue_formatted_scientific():
    q = Question(question="What is the adjusted p-value for the term?")
    sol = solve(q, _analyst(1.9e-05))
    assert "e" in sol.final_answer.lower()  # scientific representation
    assert sol.final_answer == "1.90e-05"


def test_open_range_scalar_in_interval():
    q = Question(question="What is the odds ratio?", ideal="(1.50,1.54)", eval_mode="range_verifier")
    sol = solve(q, _analyst(1.52))
    assert not sol.is_mcq
    assert grade_solution(sol, q.ideal, q.eval_mode) is True


def test_open_count_is_integer():
    q = Question(question="How many DE genes are there?")
    sol = solve(q, _analyst(42.0))
    assert sol.final_answer == "42"


# --- Verifier + never-refuse ----------------------------------------------


def test_verify_requires_grounding():
    grounded = solve(Question(question="p-value?"), _analyst(0.01, evidence="cell[2]"))
    ungrounded = solve(Question(question="p-value?"), _analyst(0.01, evidence=None))
    assert grounded.grounded and not ungrounded.grounded


def test_mcq_refuses_only_without_value():
    q = Question(
        question="Which group?",
        options=["A", "B", "C"],
        refusal_option="Insufficient information",
    )
    sol = solve(q, _analyst(None, evidence=None))
    assert sol.final_answer == "Insufficient information"


def test_open_answer_never_emits_refusal_phrase():
    sol = solve(Question(question="What is the value?"), _analyst(None, evidence=None))
    assert sol.final_answer == ""  # empty = needs more analysis, not a refusal


# --- Capsule manifest is built once and reused ----------------------------


def test_manifest_cached_per_capsule(tmp_path):
    (tmp_path / "coldata.csv").write_text("sample,condition\nS1,a\nS2,b\n")
    cache = ManifestCache()
    m1 = cache.get(str(tmp_path))
    m2 = cache.get(str(tmp_path))
    assert m1 is m2  # same object -> built once
    # And both questions in a batch sharing data_dir reuse it.
    qs = [
        Question(question="q1?", data_dir=str(tmp_path), capsule_uuid="c"),
        Question(question="q2?", data_dir=str(tmp_path), capsule_uuid="c"),
    ]
    sols = run(qs, _analyst(1.0))
    assert sols[0].manifest is sols[1].manifest


# --- Oracle drives the full loop ------------------------------------------


def test_oracle_exact_is_correct():
    q = Question(
        question="p-value?",
        options=["0.0002", "0.0003", "0.0004"],
        ideal="0.0002",
        eval_mode="str_verifier",
    )
    sol = solve(q, OracleAnalyst(noise=0.0))
    assert grade_solution(sol, q.ideal, q.eval_mode) is True
