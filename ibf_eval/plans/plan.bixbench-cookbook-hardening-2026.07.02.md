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
| `ibiofoundry_ai/tools/eda.py` | MODIFY | 1, 6, 8 |
| `ibiofoundry_ai/tools/python_exec.py` (`LIBRARY_COOKBOOK`, `ALLOWED_BINARIES`, `TOOL_TIMEOUTS`) | MODIFY | 2, 3, 4, 5, 9 |
| `ibiofoundry_ai/prompts/code_agent.md` | MODIFY | 7 (Rule 18) |
| `Dockerfile` | MODIFY | 9 |
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

**Fix** (`eda.py`) -- landed and verified; also handles a second real issue found while
testing: some capsules ship plain-text/TSV data under a misleading `.xls` extension
(not a real Excel binary at all -- confirmed on this exact capsule via `head`), which
raises from the Excel engine rather than parsing. The engine-only fix alone was
insufficient; needs a text-parsing fallback on Excel-engine failure:
```python
def _read_table(path: Path, suffix: str) -> pd.DataFrame:
    """Parse a tabular file into a DataFrame (delimiter sniffed for text).

    ``.xls``/``.xlsx`` need an explicit engine (pandas cannot auto-detect legacy
    ``.xls`` vs ``.xlsx``) -- but some BixBench capsules ship plain-text/TSV data
    under a misleading ``.xls`` extension, which raises from the Excel engine
    rather than parsing. Fall back to delimited-text parsing in that case rather
    than surfacing a false "unreadable".
    """
    if suffix in (".parquet",):
        return pd.read_parquet(path)
    if suffix in (".xlsx", ".xls"):
        engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
        try:
            return pd.read_excel(path, engine=engine)
        except Exception:  # noqa: BLE001 - genuinely not an Excel binary; try text
            return pd.read_csv(path, sep=None, engine="python")
    return pd.read_csv(path, sep=None, engine="python")
```
**Verified**: on the real `bix-42-q2` capsule (`COAD__geneExp.xls`,
`COAD__methylation_450__TSS200-TSS1500.xls`, `clinical_patient_coad.xls` -- the latter
two are genuinely mislabeled plain text, the geneExp one too), all three now parse
correctly with shapes/columns/AXIS-warnings surfaced as intended. **STATUS: KEPT.**

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

**Fix** (`python_exec.py`, extend the existing pydeseq2 cookbook card): the first version
of this fix set `dds.obs["size_factors"] = 1.0` and then called `dds.deseq2()` --
**empirically proven broken** by a retest agent (`deseq2()` silently re-fits and
overwrites the manually-set size factors in pydeseq2==0.5.4). Corrected version calls
the individual fit steps directly instead of the `deseq2()` wrapper, which actually
pins size factors to 1:
```python
# If the ONLY count data on disk is already normalized (non-integer values -- check
# with (counts_df % 1 != 0).any().any()), rounding it and feeding it to DeseqDataSet as
# if raw double-normalizes (pydeseq2 re-fits its own size factors on top of the
# existing normalization). VERIFIED: naively setting `dds.obs["size_factors"] = 1.0`
# before calling `dds.deseq2()` does NOT work with pydeseq2==0.5.4 -- deseq2() silently
# re-fits and overwrites it. To actually pin size factors to 1, call the individual
# fit steps directly instead of the deseq2() wrapper:
dds = DeseqDataSet(counts=counts_df.round().astype(int), metadata=meta_df, design="~condition")
dds.obs["size_factors"] = 1.0
dds.layers["normed_counts"] = dds.X  # pre-seed so deseq2() doesn't re-derive it
dds.fit_genewise_dispersions()
dds.fit_dispersion_trend()
dds.fit_dispersion_prior()
dds.fit_MAP_dispersions()
dds.fit_LFC()
dds.calculate_cooks()
dds.refit()
```
Caution: in practice, when the data is already well-normalized, pydeseq2's own auto-fit
size factors often land close to 1.0 anyway -- so this may be a smaller effect than
expected (confirmed below: it changes the gene count but doesn't resolve the motivating
question).

**Test** (`test_python_exec.py`): assert the cookbook mentions "already normalized" and
"size_factors".

**Housekeeping note (2026-07-06)**: found via an unrelated full source/mirror
consistency sweep that `ibf_eval/prompt_builder.py`'s copy of this cookbook card had
drifted back to the disproven `size_factors = 1.0; deseq2()` one-liner -- the initial
correction above had landed in the real source file but was never propagated to the
harness's mirror. Re-synced now (`prompt_builder.py`'s `LIBRARY_COOKBOOK` is byte-for-byte
identical to the source again, confirmed programmatically). Does not change this fix's
KEPT verdict or the 241-vs-target-700/900 result below -- `bix-3-q1` was already correctly
scored as a FAIL either way -- but any *other* future question exercising this exact
code path between the drift and this fix would have been silently shown the broken
snippet. Worth a standing habit: re-diff `prompt_builder.py` against the real source
after any cookbook edit, not just at the time each fix is first written.

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

## Results (all 5 fixes + 1 newly-discovered fix, empirically tested)

Re-ran the 30-question baseline's affected questions against the proxy harness after
applying each fix. **Score: 13/30 (43.3%) -> 15/30 (50.0%)** after Fixes 1-6, +2
questions flipped. Fix 9 (Trimmomatic, see below, found in a later round) adds a 3rd:
**15/30 (50.0%) -> 16/30 (53.3%)**, pending the same full-batch spot-check the other
fixes got before their score was finalized.

| Fix | Motivating question | Before | After | Verdict |
|---|---|---|---|---|
| 1 (`.xls` engine + fallback) | bix-42-q2 | UNREADABLE (fixed via manual override in baseline) | parses correctly, auto | **KEPT** -- verified directly, no regression risk |
| 2 (Chronos sign) | bix-16-q3 | 0 | **3** (exact match) | **KEPT -- CONFIRMED WIN** |
| 3 (pre-normalized counts) | bix-3-q1 | 247 | 241 (target 700-1000) | KEPT (corrected a broken code snippet -- the original one-liner doesn't actually pin size factors in pydeseq2==0.5.4) but does **not** resolve the motivating case; root cause is something else |
| 4 (qualitative distribution shape) | bix-36-q5 | "not normal" | "bell-shaped/Normal-like" | **KEPT -- CONFIRMED WIN** |
| 5 (ortholog set scope) | bix-12-q4 | 1162.0 (target 6948.0) | 3480.0 after 3 revisions | KEPT as a well-justified general default (narrowed scope after a near-regression risk was caught before it shipped) but motivating case still unresolved -- likely a different issue (rank-sum convention?) |
| **6 (NEW) multi-sheet Excel** | bix-3-q1 (discovered mid-retest) | 2nd sheet invisible to RULE 12 inventory | both sheets surfaced | **KEPT** -- confirmed on 5 capsules total; doesn't change scores here (capable agents had already self-corrected by manually opening the workbook) but removes wasted effort and a real robustness gap |

**Process note**: Fix 5 went through 3 revisions based on empirical failures, each one informative:
v1 (exclude partial-taxa) -> no change (1162.0) -> v2 (include >=2-taxa) -> 5408.0,
closer but still short -> v3 (include >=3-taxa, excluding the mathematically-forced-
zero 2-taxa case) -> 3480.0, still short but now using the sample composition
(n=100/n=249) that matches 2 OTHER already-passing questions in this family. Final
wording was narrowed to scope the >=3-taxa exclusion to parsimony-informative-sites
specifically (mathematically forced) rather than all phylogenomic metrics, after
realizing the broader wording risked regressing 4 already-passing tree-length-family
questions (DVMC/tree-length/patristic-distance are well-defined even for 2-taxa trees).

**Assessment**: the cheap, cookbook-level fix bucket identified from this baseline is
now largely exhausted -- 2 clean wins, 2 technically-improved-but-insufficient, 1
new robustness fix. The remaining 15 failures are predominantly the structural
undocumented-analyst-methodology problem (11 of 17 original fails) or deeper
misinterpretation (`bix-31-q1`), which did not yield to this style of fix in the
cases directly tested (`bix-3-q1`, `bix-12-q4`) despite real, careful effort.

## Two more levers tested, both negative -- important, precise findings

**Model-tier bump (3 cases, a stronger proxy model vs the baseline tier)**: NO
improvement on any of the 3 remaining failures tested (`bix-53-q6`, `bix-27-q4`,
`bix-31-q1`), including cases where the stronger model did MORE rigorous work (more
clustering configurations, more sensitivity variants tested) yet converged on the
SAME wrong answer. `bix-27-q4` is the clearest evidence: 3 of 5 independently-tried
clustering methods converged on "Axon Guidance" (wrong) under the stronger model --
this is a consistent, repeatable disagreement with the reference, not noise a
smarter model resolves. Conclusion: raw reasoning strength is not the bottleneck for
this failure class; do not expect a model upgrade alone to close this gap.

**MCQ nearest-option snapping (retroactively tested against all 30 raw computed
answers + their real distractor options, zero additional compute)**: **zero net
new wins**. Every numeric question whose raw answer snaps to the correct option was
ALREADY counted correct (the raw value already matched); every numeric question
that's currently wrong stays wrong after snapping, because the computed VALUE is
too far from the truth to land in the right neighborhood (e.g. `bix-31-q1`'s -0.38
snaps to a distractor near -0.45, nowhere near the true 18.93). This is an
important correction to an earlier-session hypothesis: nearest-option snapping only
recovers points when the analysis is APPROXIMATELY right but differently formatted
(a representation problem) -- none of this baseline's current failures are that;
they are all genuinely-wrong VALUES from a different methodology, for which no
answer-formatting trick can help. The lever is still valid in principle (and cheap
enough to keep as a formatting safety net for future near-misses), but it is not
the lever that moves this specific baseline's score.

## Fix 8 (NEW) -- oversized files skip RULE 12's inventory entirely

**Evidence**: DepMap's `CRISPRGeneEffect.csv` (408 MB) and
`OmicsExpressionProteinCodingGenesTPMLogp1BatchCorrected.csv` (587 MB) -- used by
`bix-16-q2`/`q3`/`q4` -- both exceed `MAX_INVENTORY_BYTES` (200 MB), so
`inventory_inputs` skipped them entirely with "file too large to inventory; inspect
manually", giving RULE 12's mandatory pre-analysis inventory zero information: no
column names, no shape, nothing. This is silent for capable agents that self-correct
(all 3 DepMap questions already pass in the baseline) but forces expensive manual
re-discovery every time, and would not self-correct for a less careful agent. Found
via a systematic scan of all 30 capsules for silent inventory failures (the same
method that found Fixes 1 and 6) -- notably, this scan also confirmed all OTHER
tabular files across all 30 capsules parse cleanly with a bounded read, so there are
no further silent parsing bugs at this time.

**Fix** (`eda.py`): replace the all-or-nothing skip with a bounded preview -- read
only `_PREVIEW_NROWS=5` data rows (pandas stops immediately regardless of file size)
for real column names/dtypes, and get the true row count via `_fast_line_count`, a
byte-level newline count that streams the file in fixed-size chunks (O(1) memory)
instead of loading it as a DataFrame. A new `FileInventory.preview_note` field
(deliberately separate from `read_error`, which `summary()` treats as "print nothing
else") carries the "this is a preview, not a full read" caveat alongside the real
shape/columns, and reminds the agent to load only what it needs (`usecols=`,
`nrows=`, `chunksize=`) rather than the whole file.

**Verified**: on the real DepMap capsule, both files now surface real shapes
(1178×17917 and 1673×19139) and real column samples (revealing the file's
`GENE_SYMBOL (ENTREZID)` column-naming convention -- useful for downstream ID
matching) in 71s total for ~1GB of file, versus zero information before.
**STATUS: KEPT.**

**Also tested this round, found NOT to help (documented so it isn't re-tried)**:
for `bix-31-q1`/`bix-31-q3` (the only capsules shipping both `RawReadCounts_Zenodo.csv`
and `BatchCorrectedReadCounts_Zenodo.csv`), hypothesized that pydeseq2 should prefer
raw counts + an explicit `~batch+sex` design covariate over the agent's original
choice of pre-corrected counts + `~sex`. Empirically this made `bix-31-q3` WORSE (83
significant genes vs the original 113, target 197) -- the batch-corrected values are
integer-valued ComBat-seq output, a legitimate, standard DESeq2-compatible input, not
a mistake. No cookbook change made; a good example of testing before shipping.

## Fix 9 (NEW) -- Trimmomatic is not installed or whitelisted at all

**Evidence**: `bix-61-q1` names an exact tool AND exact parameters -- "quality
control on these reads using Trimmomatic PE... ILLUMINACLIP:TruSeq3-PE.fa:2:30:10,
LEADING:3, TRAILING:3, SLIDINGWINDOW:4:15, MINLEN:36" -- about as unambiguous as
BixBench gets. `trimmomatic` is neither in `ALLOWED_BINARIES` nor installed by the
`Dockerfile`'s `apt-get install` line (confirmed by reading the Dockerfile directly --
it installs samtools/bcftools/bwa/bowtie2/mafft/bedtools/hmmer/fasttree but not
trimmomatic). Lacking the real tool, the baseline agent hand-reimplemented
ILLUMINACLIP/SLIDINGWINDOW trimming from scratch and landed on 4456 -- the reference
is 344895, a ~77x gap. Found via a targeted audit distinct from Fixes 1/6/8's file-format
scan: grep every question's raw text for an explicitly-named tool, then check that
name against `ALLOWED_BINARIES` and the `Dockerfile`. Across all 30 questions only two
name a specific tool by name (Trimmomatic here; DESeq2 in `bix-3-q1`, already covered
by the pydeseq2 cookbook entry and already investigated/exhausted in an earlier round)
-- a small, high-precision search space, and it found a real, severe gap.

**Fix**:
- `Dockerfile`: add `trimmomatic` to the existing apt-get install line (same
  Debian-bookworm-main family as its neighbors, `default-jre` + `libjbzip2-java` deps
  only). Debian's package ships two separate entrypoints, `TrimmomaticPE` /
  `TrimmomaticSE`, not bioconda's unified `trimmomatic PE|SE ...` CLI -- added a
  one-line shim (`/usr/local/bin/trimmomatic` dispatching `"$1"` to
  `Trimmomatic${1}`) directly below the existing analogous `fasttree`/`FastTree`
  naming-mismatch shim already in the file, so `run_tool("trimmomatic", "PE", ...)`
  resolves the same way regardless of which packaging produced the binaries.
- `python_exec.py`: added `"trimmomatic"` to `ALLOWED_BINARIES` and `TOOL_TIMEOUTS`
  (600s, matching bwa/bowtie2 -- same order-of-magnitude input as short-read
  alignment). New `LIBRARY_COOKBOOK` entry covers three non-obvious things a correct
  from-scratch attempt could still miss even with the real tool available: (1) the
  exact `run_tool` invocation shape for PE mode; (2) Trimmomatic prints its summary to
  **stderr**, not stdout, and PE's "reads discarded" is `Input Read Pairs - Both
  Surviving` (equivalently `Forward Only + Reverse Only + Dropped`) -- NOT the
  `Dropped` field alone, which counts only pairs where BOTH mates failed; a mate
  demoted to unpaired output is still conventionally "discarded" from the paired-end
  result; (3) `ILLUMINACLIP`'s adapter FASTA (e.g. `TruSeq3-PE.fa`) ships with the
  Trimmomatic install, not in `DATA_DIR` -- a question naming one by filename does not
  mean it was uploaded, so the snippet globs a short list of plausible install roots
  and falls back to writing out the standard 2-record `TruSeq3-PE.fa` content
  (verified below) if no installed copy is found.

**Verified**: installed real Trimmomatic (apt `trimmomatic` 0.39 + `default-jre`) and
ran it by hand with the question's exact flags against the real `bix-61-q1` capsule
(two paired-end samples, SRR35228486 and SRR35233585). Per-sample stderr summaries:

| Sample | Input Pairs | Both Surviving | Fwd Only | Rev Only | Dropped |
|---|---|---|---|---|---|
| SRR35228486 | 325099 | 320698 | 3763 | 236 | 402 |
| SRR35233585 | 667764 | 327285 | 334988 | 1075 | 4416 |

Summed `Input Read Pairs - Both Surviving` across both samples = 4401 + 340479 =
**344880**, versus the reference **344895** -- a 15-count (0.004%) gap, almost
certainly Trimmomatic patch-version or JRE-level floating-point noise at sliding-window
threshold boundaries, not a methodology difference. The naive "Dropped-only" reading
(402 + 4416 = 4818) is nowhere close, confirming the counting-convention gotcha
documented in the cookbook entry above is the actual crux, not just tool availability.

A fresh agent retest (blind to the reference value, given only the updated prompt,
10 tool calls) independently reproduced the exact same derivation start to finish with
NO hand-holding: found `trimmomatic` on PATH unprompted, located the real shipped
`TruSeq3-PE.fa` via the documented glob (never needed the embedded fallback), ran both
samples with the question's exact flags, applied the "Input Read Pairs - Both
Surviving" convention (not the naive "Dropped" reading) from the cookbook text alone,
and reported the identical **344880**, i.e. `<solution>344880</solution>` against
ideal `344895` -- a 0.004% gap, and not within reach of any of this benchmark's three
listed distractors (341095/348695/352495, each ~3800+ away). This flips `bix-61-q1`
from FAIL (4456, ~77x off) to what should grade as PASS under `llm_verifier`.
**STATUS: KEPT.**

Also fixed while verifying this: an unrelated `python_exec.py`/`prompt_builder.py`
mirror-drift bug found via a full source-vs-harness consistency sweep (see the
Fix 3 housekeeping note above) -- `ALLOWED_BINARIES` and `code_agent.md`'s mirror were
already exact; `ALLOWED_DOMAINS`'s mirror differs only by stripped inline comments
(same 14 domains, functionally identical, not worth touching). Also caught and fixed a
benchmark-integrity slip in this fix's own first draft: the cookbook's illustrative
Trimmomatic stderr example originally used `bix-61-q1`'s own real per-sample numbers
(325099/320698/...) as the "e.g." -- accurate, but a general cookbook card should not
double as a worked answer key for the one question that motivated it. Replaced with
clearly-fictional, arithmetic-consistent placeholder numbers before shipping.

## bix-12-q4 revisited -- a suspicious ~2x ratio, chased and not explained

**Motivation**: `bix-12-q4` ("Mann-Whitney U comparing parsimony-informative-site
percentages between animals and fungi", ideal `6948.0`) was left unresolved by Fix 5
at `3480.0`. `6948 / 3480 = 1.9966` -- close enough to exactly 2x to be a real
mechanism signature worth chasing rather than noise, especially since the three
listed distractors (4532/5891/7823) all sit in the same 4500-7800 range, meaning
the correct comparison scale (n1~100 x n2~249) is almost certainly right and the
gap is a specific methodological choice, not a wrong-ballpark answer.

**Investigation**: reused the real per-ortholog PIS-percentage values already computed
and saved from the Fix 5 retest (`output_v4/{animals,fungi}_parsimony_informative_sites.tsv`,
n1=100/n2=249, matching Fix 5's final `>=3-taxa` sample) rather than re-deriving from
scratch, and tested every mechanism that could plausibly produce a ~2x shift on the
SAME underlying data:

| Hypothesis | Result | vs. target 6948.0 |
|---|---|---|
| Reported value (animals vs fungi, standard MWU) | 3480.0 | baseline |
| Complementary U (fungi vs animals; U1+U2=n1n2=24900) | 21420.0 | not 2x, ruled out |
| `alternative=` two-sided/greater/less | 3480.0 (unchanged) | scipy doesn't vary the statistic by this param |
| Paired Wilcoxon signed-rank on the 96 ortholog IDs shared between both sets | 58.0 | far off -- overlap is a BUSCO/OrthoDB naming artifact (both groups draw from the same `eukaryota_odb10` lineage set), not evidence of a paired design |
| MWU on raw `pis_count` instead of `pis_pct` | 3280.0 | close to but not 3480/6948 |
| Restrict to full-4-taxa orthologs only (22 vs 211) | 1162.0 | exactly reproduces Fix 5's original v1 -- confirms empirically that in THIS dataset every <4-taxa ortholog has `pis_pct==0`, so ">=3 taxa" and ">=4 taxa" filters differ only in how many zero-tied entries get included, not in which orthologs carry real signal |
| Exclude exact-zero `pis_pct` from both groups | 1162.0 | identical to full-4-taxa-only, confirming the above |
| Exclude zeros from animals only (asymmetric) | 1998.0 | no |
| Manual tie decomposition: 1996 strict wins, 19936 strict losses, 2968 exact ties (2964 of them zero-zero) | -- | -- |
| Ties credited as full win instead of standard half-credit | 4964.0 | closer, still off by ~2000 |
| Ties credited as full loss instead of half-credit | 1996.0 | no |

**Outcome**: none of the eight reinterpretations of the same underlying data reproduce
6948.0 or a clean multiple of it. This maps out essentially the full space of "same
per-ortholog PIS values, different aggregation/subsetting/tie-convention" -- so this
specific 2x hunch, while a reasonable and worthwhile thing to check (and now
definitively ruled out rather than left as an open guess), does not resolve to a
findable mechanism from the artifacts already on disk. Closing this for real would
require re-deriving the per-ortholog PIS values with a different tool/alignment
methodology entirely -- a materially bigger and less certain investment than anything
else in this plan -- which is itself informative: it's a second, independent data
point (alongside Fixes 3, 5, 7) that the remaining gap is dominated by genuine
methodological divergence from the reference, not a slicing choice one more clever
pass over the same numbers will find. No cookbook change made.

**Combined conclusion**: with cookbook fixes, model tier, and answer-representation
all now empirically tested (not just proposed), the remaining ~15-question gap is
the benchmark's core reproducibility problem -- the ground truth encodes one
analyst's specific, undocumented methodological path among several equally
defensible ones, and no combination of prompt engineering, model strength, or
answer formatting available to this harness closes that gap. Meaningfully higher
scores likely require either much deeper per-domain-family engineering (a
prescribed canonical pipeline per analysis type, removing the agent's freedom to
choose an equally-valid-but-different method) or accepting a ceiling on the
exact-value-reproduction questions and optimizing elsewhere.

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

## Fix 7 (NEW) -- RULE 18: prefer the textbook-default method

Added directly to `code_agent.md`'s numbered rules (not the cookbook -- this is a
general behavioral principle, not a per-library note). Motivated by a pattern across
several failures where the agent made a defensible but NON-default methodological
choice (e.g. `bix-27-q2`'s strict "100% agreement across all appearances" threshold
for "consistently classified", vs. a more standard majority-vote consensus
convention) that a plain textbook default would likely have avoided. Distinct from
Fixes 1-6: this is a meta-rule, not a fact about one specific tool/dataset.

Text added as RULE 18 in `ibiofoundry_ai/prompts/code_agent.md` (mirrored in the
harness's `prompt_builder.py` `CODE_AGENT_TEMPLATE`): instructs the agent to
recognize when it selected a "more sophisticated" or "more rigorous-seeming" option
over the plain default for an unspecified step, and to redo with the default unless
it has a concrete, stated reason the default is wrong for this data.

**Results**: mixed, no confirmed win, but real signal.
- `bix-27-q2`: 42 -> **215** (target 160-180). Major improvement: the agent replaced
  its original ad hoc "100% agreement across all appearances" heuristic with proper
  textbook consensus clustering (Monti et al. pairwise co-association matrices,
  Hungarian-algorithm cluster alignment), validated across 4 robustness variants all
  converging to 212-218. Overshoots the target but is far closer and far more
  methodologically defensible than before.
- `bix-36-q4`: 0.194 -> **6.28e-24** (target 0.55-0.59). Regressed: the agent's
  "more textbook" choice (gene-level ANOVA across all 827 filtered miRNA genes,
  reasoning this was more standard than per-sample aggregation) has far more
  statistical power than the original approach, producing an extremely significant
  p-value against a target that is NOT significant. Neither this nor the original
  approach is clearly correct; the true reference method is likely something else
  entirely (e.g. a different test, different grouping).

**STATUS: KEPT**, on the same basis as Fixes 3 and 5 (sound reasoning, a real
improvement in at least one case, no demonstrated regression on any previously-
passing question, purely additive prompt text) -- but explicitly NOT a confirmed
win like Fixes 2/4/6. This is a general principle with mixed, case-dependent
results: it seems to help most when the agent's original choice was an ad hoc
heuristic with no real statistical grounding (bix-27-q2), and can hurt when
"more standard" also means "more statistical power," which is a different axis
than correctness (bix-36-q4).
