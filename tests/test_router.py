"""Tests for genie.router — pinned on real BixBench question phrasings."""

import pytest

from genie.router import AnswerShape, route


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is the adjusted p-value for regulation of T cell activation?", AnswerShape.P_VALUE),
        ("What is the adjusted p-val threshold for neutrophil activation?", AnswerShape.P_VALUE),
        ("What is the odds ratio for COVID-19 severity associated with BCG?", AnswerShape.ESTIMATE),
        ("What is the log2 fold change of the top gene?", AnswerShape.ESTIMATE),
        ("How many GO terms show p-adj < 0.05?", AnswerShape.COUNT),  # count beats p-value
        ("Which patient volume group shows significant differences?", AnswerShape.CATEGORICAL),
        ("Is the ASXL1 hypothesis supported by the expression data?", AnswerShape.BOOLEAN),
        ("Describe what the enrichment results suggest about immune function.", AnswerShape.DESCRIPTIVE),
    ],
)
def test_shape_from_text(question, expected):
    assert route(question).shape == expected


def test_p_value_is_scientific():
    d = route("What is the adjusted p-value for the term?")
    assert d.representation == "scientific"
    assert d.is_numeric


def test_count_is_integer():
    assert route("How many differentially expressed genes are there?").representation == "integer"


def test_mcq_numeric_options_set_representation():
    d = route("p-adj?", options=["7.82E-05", "1.85E-05", "3.23E-08", "2.15E-06"])
    assert d.is_mcq and d.is_numeric and d.representation == "scientific"


def test_mcq_range_options_are_numeric():
    d = route("Odds ratio?", options=["(0.66,0.70)", "(0.95,1.05)", "(1.50,1.54)"])
    assert d.is_numeric  # ranges are numeric estimates


def test_mcq_text_options_are_categorical():
    d = route("Which group?", options=["1-50", "51-100", ">100", "1-100"])
    # mixed text/number options -> not all numeric -> categorical
    assert d.shape == AnswerShape.CATEGORICAL
    assert d.representation == "text"


def test_every_shape_has_a_strategy():
    for q in [
        "What is the p-value?", "What is the odds ratio?", "How many genes?",
        "Which pathway?", "Is it supported?", "Explain the result.",
    ]:
        assert route(q).strategy
