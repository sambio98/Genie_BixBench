"""Tests for genie.eda — synthetic capsule files (no network)."""

import pytest

pytest.importorskip("pandas")

from genie.eda import build_manifest  # noqa: E402


def _write_capsule(tmp_path):
    # Counts matrix: Geneid + 4 sample columns, whitespace-aligned (like featureCounts).
    counts = tmp_path / "gene_counts.txt"
    rows = ["Geneid   S1   S2   S3   S4"]
    rows += [f"g{i}   {i}   {i * 2}   {i * 3}   {i * 4}" for i in range(1, 21)]
    counts.write_text("\n".join(rows) + "\n")

    # Sample metadata: only S1-S3 (S4 omitted -> a "dropped" sample).
    coldata = tmp_path / "coldata.csv"
    coldata.write_text("sample,condition\nS1,control\nS2,control\nS3,disease\n")
    return tmp_path


def test_roles_and_shapes(tmp_path):
    m = build_manifest(_write_capsule(tmp_path))
    by_name = {f.name: f for f in m.files}

    counts = by_name["gene_counts.txt"]
    assert counts.role == "expression_matrix"
    assert counts.n_cols == 5  # Geneid + S1..S4 (delimiter handled)
    assert counts.n_rows == 20  # true row count, not preview cap
    assert counts.numeric_cols == 4

    coldata = by_name["coldata.csv"]
    assert coldata.role == "sample_metadata"


def test_group_counts_surface_the_design(tmp_path):
    m = build_manifest(_write_capsule(tmp_path))
    coldata = next(f for f in m.files if f.name == "coldata.csv")
    notes = " ".join(coldata.notes)
    assert "condition" in notes and "control=2" in notes and "disease=1" in notes


def test_join_detects_dropped_sample(tmp_path):
    m = build_manifest(_write_capsule(tmp_path))
    joins = " ".join(m.joins)
    assert "coldata.csv" in joins
    assert "S4" in joins  # the sample present in counts but missing from metadata


def test_large_table_skips_partial_group_counts(tmp_path):
    # A big categorical column should NOT emit (misleading) preview-based counts.
    big = tmp_path / "annotation.csv"
    lines = ["gene,kind"] + [f"g{i},{'A' if i % 2 else 'B'}" for i in range(6000)]
    big.write_text("\n".join(lines) + "\n")
    m = build_manifest(tmp_path)
    info = next(f for f in m.files if f.name == "annotation.csv")
    assert info.n_rows == 6000
    assert not any("groups in 'kind'" in n for n in info.notes)
