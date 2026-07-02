---
title: BixBench cookbook hardening -- 5 small, evidence-backed fixes from a 30-question baseline
desc: 'Five small code_agent prompt/cookbook fixes, each backed by a specific failing question from a fresh 30-question BixBench baseline run (13/30 = 43.3%) | provenance: proxy-harness eval, session claude/wonderful-goldberg-llfldp'
updated: 2026.07.02
created: 2026.07.02
---

# BixBench cookbook hardening -- 5 small fixes from a 30-question baseline

## Context

A fresh 30-question non-R BixBench baseline (proxy harness: Claude subagents faithfully
replaying the real `code_agent.md` contract, since no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`
was available to invoke the real `pydantic-ai` stack directly -- see
`https://claude.ai/code/session_01N52cWueejbZQ2cZBsVLJjf`) scored **13/30 = 43.3%**
(`llm_verifier` 9/16, `range_verifier` 2/7, `str_verifier` 2/7; full per-question results
in `ibf_eval/BASELINE_REPORT.md` and `ibf_eval/results_baseline_30q/*.json` of that
session's `Genie_BixBench` repo). Of 17 failures, most are the structural
undocumented-analyst-methodology problem BixBench is known for and not something a
prompt change fixes. Five, however, trace to concrete, fixable gaps -- each with a
specific failing question as evidence. This plan is exactly those five, no more.

Deliberately excluded from this plan (lower confidence, not directly evidenced this
run): the `code_agent` model-tier bump (`model_config.py`, OpenAI tier still
`gpt-5.4-mini`) and an MCQ answer-representation/nearest-option policy -- both remain
reasonable next steps but weren't isolated by this specific baseline (it ran open-answer
only, and used a constant proxy model throughout, not their real per-tier models). Also
investigated and **dropped**: a suspected `<solution>`-floor gap for
`UsageLimitExceeded`/`UnexpectedModelBehavior`. Re-reading `genie_api/chat_handler.py`
directly (the actual BixBench surface) shows its generic `except Exception` arm
(`chat_handler.py:633`) already routes through `solution_floor_message` at line 672 --
both exception types are `Exception` subclasses and are already covered. The gap, if
real, is in `api.py`'s separate `/chat` SSE loop (chat-api, the interactive-user
surface, lines 2023/2068) -- but that surface isn't `<solution>`-graded, so it isn't a
BixBench-scoring issue and is out of scope here.

## Relevant Files

| Path | Action | Fix # |
|---|---|---|
| `ibiofoundry_ai/tools/eda.py` | MODIFY | 1 |
| `ibiofoundry_ai/tools/python_exec.py` (`LIBRARY_COOKBOOK`) | MODIFY | 2, 3, 4, 5 |
| `tests/ibiofoundry_ai/tools/test_eda.py` | MODIFY | 1 |
| `tests/ibiofoundry_ai/tools/test_python_exec.py` | MODIFY | 2, 3, 4, 5 |

## Fix 1 -- `.xls` files come back "UNREADABLE" in the auto-inventory

**Evidence**: `bix-42-q2` (TCGA COAD capsule ships `COAD__geneExp.xls`,
`COAD__methylation_450__TSS200-TSS1500.xls`, `clinical_patient_coad.xls`). The
mandatory RULE 12 inventory reported all three as
`UNREADABLE: ValueError: Excel file format cannot be determined, you must specify an
engine manually` -- before any analysis started. Root cause: `_read_table`
(`eda.py:256-262`) calls `pd.read_excel(path)` with no `engine=`. Modern pandas cannot
auto-detect legacy `.xls` (needs `xlrd`) vs `.xlsx` (needs `openpyxl`), and `xlrd` is
already in `env/requirements.txt`. This silently breaks RULE 12's mandatory inventory
for every capsule shipping a legacy `.xls` file -- a real, recurring BixBench file
format (also seen in other TCGA-style capsules in the dataset).

**Fix** (`eda.py`):
```python
def _read_table(path: Path, suffix: str) -> pd.DataFrame:
    """Parse a tabular file into a DataFrame (delimiter sniffed for text)."""
    if suffix in (".parquet",):
        return pd.read_parquet(path)
    if suffix == ".xlsx":
        return pd.read_excel(path, engine="openpyxl")
    if suffix == ".xls":
        return pd.read_excel(path, engine="xlrd")
    return pd.read_csv(path, sep=None, engine="python")
```

**Test** (`test_eda.py`): write a real `.xls` (via `pandas.DataFrame.to_excel(...,
engine="xlrd" is write-incompatible -- use `openpyxl` to write `.xlsx` and separately
assert `.xls` parsing doesn't raise via a small fixture, or monkeypatch `pd.read_excel`
call args to assert `engine="xlrd"` is passed for a `.xls` suffix and `engine="openpyxl"`
for `.xlsx`). Assert `inventory_inputs` on a directory containing a real `.xls` produces
`read_error=None`, not the format-detection message.

## Fix 2 -- DepMap Chronos essentiality sign convention

**Evidence**: `bix-16-q3` asked for genes with strong *positive* correlation
(essentiality vs. expression) at |ρ| ≥ 0.6. The agent's own per-gene table shows the 3
strongest-magnitude correlations sit exactly at the threshold but are **negative**
(CCND1 -0.629, FERMT2 -0.612, KLF5 -0.602) -- reported answer 0, ideal 3. Root cause:
DepMap's `CRISPRGeneEffect.csv` Chronos score is *more negative* for *more essential*
genes (an unintuitive convention); a sign-flipped "essentiality" (higher = more
essential) turns these exact 3 genes into strong *positive* correlations, matching
ideal=3 precisely. Nothing in the current cookbook mentions this.

**Fix** (`python_exec.py`, new `LIBRARY_COOKBOOK` card, placed near the existing pysam
card since both are DepMap/genomics-adjacent):
```python
# DepMap CRISPRGeneEffect.csv -- Chronos essentiality score SIGN CONVENTION.
# A MORE NEGATIVE Chronos score means a gene is MORE essential (knocking it out is
# more harmful to cell viability) -- this is the opposite of the intuitive "higher =
# more essential" reading. If a question frames "essentiality" as a positive-scaled
# quantity (e.g. "strong POSITIVE correlation between expression and essentiality"),
# check whether you should negate the raw Chronos score before correlating -- the
# question's intended sign convention isn't always the raw file's sign convention.
```

**Test** (`test_python_exec.py`): extend `test_cookbook_has_canonical_apis` to assert
the cookbook mentions "Chronos" and "sign" / "negative".

## Fix 3 -- pydeseq2 on pre-normalized (not raw) counts

**Evidence**: `bix-3-q1` capsule (`Data_deposition_RNAseq_Paroxetine_2017.xlsx`,
`NormCount` sheet) ships only size-factor-normalized, non-integer counts -- no raw
counts anywhere in the capsule. The agent rounded the normalized values to fake integer
"counts" and fed them to `pydeseq2` as if raw, getting 247 significant genes against a
`(700,1000)` target. `pydeseq2`'s own internal size-factor normalization assumes true
raw counts; feeding it already-normalized values double-normalizes and very plausibly
under-powers the model (fewer genes cross the significance threshold than the correctly
un-normalized comparison would yield).

**Fix** (`python_exec.py`, extend the existing pydeseq2 cookbook card):
```python
# If the ONLY count data on disk is already normalized (non-integer values -- check
# with (counts_df % 1 != 0).any().any()), do NOT round and feed it to DeseqDataSet as
# if raw: pydeseq2 re-normalizes internally, so feeding it pre-normalized data
# double-normalizes and distorts the fitted dispersion, changing which genes pass
# significance. Instead pass explicit unit size factors so pydeseq2 skips its own
# normalization step:
dds = DeseqDataSet(counts=counts_df.round().astype(int), metadata=meta_df, design="~condition")
dds.obs["size_factors"] = 1.0  # counts are already normalized -- skip re-normalizing
dds.deseq2()
```

**Test** (`test_python_exec.py`): assert the cookbook mentions "already normalized" and
"size_factors".

## Fix 4 -- "describe the distribution shape" wants a qualitative read, not a formal test

**Evidence**: `bix-36-q5` asked to characterize a pooled log2-fold-change distribution
(n=242,881 values) as e.g. "Normal" or skewed. The agent computed real descriptive
stats consistent with "approximately normal" (skewness=0.10, unimodal, symmetric) but
then ran Shapiro-Wilk / D'Agostino-Pearson formal normality tests, which rejected
normality at p<1e-50 -- and reported "not normal" against an ideal of "Normal". At
n≈240K, formal normality tests reject almost any real-world distribution regardless of
practical shape (a well-known large-sample statistics artifact: the test's power grows
with n while its sensitivity threshold doesn't, so it detects arbitrarily small,
practically-irrelevant deviations). The same failure pattern is plausible on any
"characterize the shape" question over a large dataset.

**Fix** (`python_exec.py`, new `LIBRARY_COOKBOOK` card near the existing sanity-check
guidance):
```python
# "Describe/characterize the SHAPE of a distribution" (e.g. skewed vs Normal vs
# bimodal) wants a QUALITATIVE/visual read from descriptive statistics (skewness,
# kurtosis, a histogram), NOT a formal normality hypothesis test. At large n (tens of
# thousands+), Shapiro-Wilk / D'Agostino-Pearson / Kolmogorov-Smirnov reject normality
# for almost ANY real dataset, however normal-looking, because their power grows with
# sample size while detecting arbitrarily small deviations -- a formally-significant
# rejection at n=100,000+ is not evidence the shape looks non-normal. Judge shape from
# skewness (|skew| < ~0.5 reads as approximately symmetric/Normal-like), kurtosis, and
# a plotted histogram -- not from a p-value.
```

**Test** (`test_python_exec.py`): assert the cookbook mentions "large n" / "formal
normality" guidance is present.

## Fix 5 -- ortholog set scoping is inconsistent (partial- vs. full-taxa inclusion)

**Evidence**: within this single baseline run, three DIFFERENT questions against the
SAME underlying `scogs_fungi.zip`/`scogs_animals.zip` dataset made three inconsistent
scoping choices: `bix-4-q3` and `bix-28-q5` used ALL available per-ortholog trees
regardless of taxa count (100 animal + 249 fungi orthologs) and both landed on
exact/near-exact matches; `bix-12-q4` restricted to only FULL 4-taxa orthologs (22 + 211)
reasoning that partial-taxa alignments can't have parsimony-informative sites -- a
locally valid argument for THAT metric, but it produced a Mann-Whitney U of 1162.0
against an ideal of 6948.0, a ~6x gap consistent with using a much smaller, differently
composed sample. The cookbook's existing phylogenomics card doesn't say which scoping is
the default.

**Fix** (`python_exec.py`, extend the existing phylogenomics cookbook card):
```python
# Ortholog SET SCOPE for cross-group statistics (Mann-Whitney U, medians, ratios, etc.
# across all orthologs in a group): use EVERY ortholog that has the artifact the metric
# needs (a .treefile for tree-length/treeness/DVMC stats; an alignment for RCV/gap%
# stats) -- do not restrict to orthologs present in ALL 4 taxa unless the SPECIFIC
# metric you are computing is undefined/degenerate for a partial-taxa ortholog (e.g.
# parsimony-informative-sites truly needs >=4 taxa to be non-trivial; tree-length/
# treeness/DVMC are well-defined for a 3-taxa tree too and should include it). Restricting
# to full-taxa orthologs when the metric doesn't require it silently shrinks and
# reweights the sample.
```

**Test** (`test_python_exec.py`): assert the cookbook mentions "ortholog SET SCOPE" /
"do not restrict to orthologs present in ALL".

## Approach (all 5 fixes)

1. Land Fix 1 first and in isolation -- it's a pure bug fix (no judgment calls), lowest
   risk, and unblocks correct auto-inventory for every future `.xls`-bearing capsule
   (including re-tests of Fixes 2-5, some of which may touch `.xls`-bearing capsules).
2. Fixes 2-5 are additive `LIBRARY_COOKBOOK` cards/extensions only -- no exec/timeout/
   gate logic touched, consistent with how Blocks E/H/I landed their science steers
   (prompt + cookbook only, per the derivation-gate plan's established pattern).
3. Escape literal `{`/`}` in any inserted text -- `LIBRARY_COOKBOOK` is a plain string
   constant (not `.format()`-templated, unlike `code_agent.md`), so no escaping is
   actually needed here, but verify `_build_system_prompt` still renders after the edit
   (no accidental brace introduced elsewhere).

## Verification

- `pre-commit run` (ruff, mypy, markdownlint, testmon) clean.
- New/updated unit tests pass; existing cookbook/whitelist tests still pass.
- Re-run the 30-question BixBench baseline (or at minimum the 5 specific
  motivating questions: bix-42-q2, bix-16-q3, bix-3-q1, bix-36-q5, bix-12-q4) after each
  fix lands, keep the fix only if it flips its motivating question (or a materially
  overlapping one) from FAIL to PASS without regressing anything else in a quick spot
  check. Delete/revert a fix that doesn't measurably help.

## Out of scope

- `code_agent` model-tier bump (`model_config.py`) -- not evidenced by this baseline
  (proxy used a constant model throughout); a good next experiment, not this plan.
- MCQ nearest-option / answer-representation policy -- this baseline ran open-answer
  only; needs a harness that presents distractor options to test.
- The `<solution>`-floor investigation for `UsageLimitExceeded`/`UnexpectedModelBehavior`
  -- re-examined and appears already handled on the genie-api/BixBench surface (see
  Context); not pursued further here.
- The 11 method-sensitivity failures (undocumented analyst methodology) and the 1
  fundamental-misinterpretation failure (`bix-31-q1`) -- these are the structural
  BixBench ground-truth-reproduction problem, not a code_agent prompt gap; no fix
  proposed.

## Files

- `ibiofoundry_ai/tools/eda.py` -- Fix 1 (`_read_table` engine selection)
- `ibiofoundry_ai/tools/python_exec.py` -- Fixes 2-5 (`LIBRARY_COOKBOOK` additions)
- `tests/ibiofoundry_ai/tools/test_eda.py` -- Fix 1 test
- `tests/ibiofoundry_ai/tools/test_python_exec.py` -- Fixes 2-5 tests
- `notes/ibiofoundry_ai.tools.eda.md` -- dated section (Fix 1)
- `notes/ibiofoundry_ai.tools.python_exec.md` -- dated section (Fixes 2-5)
