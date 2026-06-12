# Task types in BixBench

Reproduce every count here with `python analysis/profile_task_types.py`
(add `--scan-ecosystem 22` for the language split).

## The headline: it's *one* benchmark, not several

BixBench is a single benchmark type — **agentic data analysis**. Every task gives
the agent real data files plus a notebook sandbox, and the agent must **run code
to compute the answer**. There is no separate "Q&A benchmark" and "code-running
benchmark." Instead, the *same* 205 questions vary along six axes, and are
*evaluated* in different regimes. Here are all six, with live counts.

---

## Axis 1 — Evaluation regime  ← this is the "Q&A vs code-running" distinction

The closest thing to "Q&A vs code-running" is **how the same question is run**:

| Regime | What the model gets | Measures |
|---|---|---|
| **Agentic / code-execution** (the real benchmark) | data files + notebook; must write & run code | data-analysis ability |
| **Zero-shot Q&A baseline** | the question only — no data, no code | parametric recall |

Same 205 questions, two regimes. The **gap** between them is the value of actual
analysis (see `docs/FINDINGS.md` §3: open-ended recall ≈ 11.5%, so most of the
score must be *earned* by code-running, not Q&A recall).

## Axis 2 — Delivery format (the paper's named "benchmarks")

**All 205 questions ship distractors**, so each can be posed three ways, each ×
with/without images. These are what the paper reports as separate result bars:

- **Open-answer** (free text)
- **MCQ with refusal option**
- **MCQ without refusal option**

## Axis 3 — Grading type (`eval_mode`)

| mode | count | how it grades |
|---|---|---|
| `llm_verifier` | 83 (40%) | LLM judge (correct/incorrect/refused) |
| `str_verifier` | 61 (30%) | exact string match (brittle) |
| `range_verifier` | 61 (30%) | numeric interval (forgiving) |

## Axis 4 — Answer/question type (what the answer *is*)

Classified by `genie/router.py` over all 205 (MCQ framing, where the options
reveal the expected type):

| type | count | example |
|---|---|---|
| **estimate** (continuous statistic) | 97 (47%) | odds ratio, log2 fold-change, mean, correlation |
| **categorical** (name/label/group) | 49 (24%) | which pathway / gene / sample group |
| **count** (integer) | 42 (20%) | how many DE genes / GO terms |
| **p-value** (significance) | 17 (8%) | adjusted p-value of a term |

Cross-tabbed with grading (the actionable view — *what kind of answer, graded
how*):

```
shape          str   range    llm
estimate        12      44     41     <- mostly forgiving (range/llm)
categorical     15       0     34
count           28       8      6     <- mostly exact-string integers
p_value          6       9      2
```

(Posed as *open-ended* text instead, ~39 questions read as "descriptive" — they
sound free-form but still have a single concrete answer.)

## Axis 5 — Scientific domain (multi-label)

Genomics 74 · RNA-seq 69 · Transcriptomics 69 · Differential Expression 67 ·
WGS 48 · Phylogenetics 47 · Sequence Analysis 30 · Imaging 21 ·
Genomic Variant Analysis 16 · Epigenomics 12 · Functional Genomics 10.

## Axis 6 — Execution ecosystem (what *kind* of code-running)

Language of the canonical analysis, sampled over the 22 smallest capsules
(`--scan-ecosystem 22`):

| ecosystem | count | note |
|---|---|---|
| **R / Bioconductor** | 13 (59%) | DESeq2, clusterProfiler, edgeR, limma |
| **Python** | 6 (27%) | pandas / scikit-style |
| **command-line (no notebook)** | 3 (14%) | alignment, variant calling, tree-building |

The reference agent runs **Python** → an ecosystem mismatch on the majority of
tasks (FINDINGS §4). Larger genomics/WGS capsules (not in this sample) skew even
more command-line, so 14% is a floor.

---

## Putting it together: the task archetypes you actually face

Combining the axes, BixBench tasks fall into a handful of recognizable
archetypes (see `docs/FINDINGS.md` §6 for the per-archetype failure/winnability
analysis):

| Archetype | Typical answer type | Typical grading | Typical ecosystem |
|---|---|---|---|
| Multi-step pipeline → one number (RNA-seq DE→GO) | p-value / estimate | str / range | R |
| Statistical model estimate (regression, OR) | estimate | range | R / Python |
| Count after filtering | count | str | R / Python |
| Identify a gene / term / group | categorical | str / llm | R / Python |
| Variant calling / phylogenetics | estimate / count | range / str | command-line |
| Image-derived quantity | estimate / count | str / range | Python (image libs) |

**Bottom line for design:** "Q&A vs code-running" is really *zero-shot vs
agentic*; within the agentic benchmark the meaningful type axes are **answer
type × grading mode × ecosystem**, and those are what `genie/router.py` and the
DESIGN target.
