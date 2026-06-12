"""Tests for genie.answer.

These pin the behavior that matters for recovering points: faithful grader
replicas, representation matching, and the nearest-option / decisiveness policy.
Cases use the real answer formats seen in the BixBench dataset.
"""

import math

import pytest

from genie.answer import (
    choose_mcq,
    commit_open_answer,
    extract_number,
    format_like_options,
    grade_range_verifier,
    grade_str_verifier,
    parse_option,
    snap_to_nearest_option,
)

# --- extract_number --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.0002", 0.0002),
        ("1.9E-05", 1.9e-05),
        ("p-adj < 1.9e-4", 1.9e-4),
        ("approximately 1,234.5 genes", 1234.5),
        ("<answer> 0.66 </answer>", 0.66),
        ("Group 2", 2.0),
        ("no number here", None),
    ],
)
def test_extract_number(text, expected):
    assert extract_number(text) == expected


# --- grader replicas (must match Future-House/BixBench graders.py) ----------


def test_str_verifier_exact_match():
    assert grade_str_verifier("0.0002", "0.0002")
    assert grade_str_verifier("1.9E-05", "1.9e-05")  # case-insensitive after clean


def test_str_verifier_is_brittle_to_representation():
    # The core finding: numerically-correct, string-wrong -> graded wrong.
    assert not grade_str_verifier("0.0002", "0.00020")  # trailing zero
    assert not grade_str_verifier("0.0002", "2e-4")  # scientific
    assert not grade_str_verifier("0.0002", "0.00019")  # rounding


def test_range_verifier():
    assert grade_range_verifier("(1.50,1.54)", "1.52")
    assert grade_range_verifier("(1.50,1.54)", "the odds ratio was 1.503")
    assert not grade_range_verifier("(1.50,1.54)", "1.6")


# --- option parsing --------------------------------------------------------


def test_parse_option_point_range_text():
    assert parse_option("7.820659E-05").point == pytest.approx(7.820659e-05)
    rng = parse_option("(1.24,1.28)")
    assert (rng.low, rng.high) == (1.24, 1.28)
    assert parse_option("1-50").point is None  # categorical, not a point value
    assert parse_option("Group 2").point is None


# --- representation matching ----------------------------------------------


def test_format_like_options_matches_scientific_style():
    # Options are scientific with ~3 sig figs -> output should be too.
    opts = ["7.82E-05", "0.0003", "1.85E-05"]
    out = format_like_options(1.9e-05, opts)
    assert "e" in out.lower()
    assert extract_number(out) == pytest.approx(1.9e-05, rel=1e-3)


def test_format_like_options_matches_decimal_style():
    opts = ["1.52", "0.68", "1.01"]
    out = format_like_options(1.5234, opts)
    assert "e" not in out.lower()
    assert extract_number(out) == pytest.approx(1.52, abs=0.01)


# --- nearest-option snapping (the MCQ win) ---------------------------------


def test_snap_picks_closest_point():
    opts = ["7.820659E-05", "0.0003", "1.847038E-05", "0.0002"]
    # An analysis that computed ~0.00021 should snap to 0.0002, not exact-match.
    assert snap_to_nearest_option(0.00021, opts) == "0.0002"


def test_snap_prefers_range_containment():
    opts = ["(0.66,0.70)", "(0.95,1.05)", "(1.24,1.28)", "(1.50,1.54)"]
    assert snap_to_nearest_option(1.52, opts) == "(1.50,1.54)"
    assert snap_to_nearest_option(0.99, opts) == "(0.95,1.05)"


def test_snap_returns_none_without_numeric_options():
    assert snap_to_nearest_option(1.0, ["yes", "no", "maybe"]) is None


# --- decisiveness policy ---------------------------------------------------


def test_choose_mcq_commits_when_value_present():
    opts = ["0.0002", "0.0003", "0.0004"]
    # Even with a refusal option available, a computed value -> commit.
    assert choose_mcq(0.00025, opts, refusal_option="Insufficient information") == "0.0003"


def test_choose_mcq_refuses_only_without_value():
    opts = ["0.0002", "0.0003"]
    assert choose_mcq(None, opts, refusal_option="Insufficient") == "Insufficient"


def test_commit_open_answer_never_refuses_with_value():
    assert commit_open_answer(1.5234, ["1.52", "0.68"]) != ""
    assert commit_open_answer(None) == ""
