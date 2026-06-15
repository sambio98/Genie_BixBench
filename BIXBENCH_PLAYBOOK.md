# BixBench Playbook — context for a coding-agent session (Python + CLI scope)

> **Purpose.** Drop this file into a Claude Code session that already has a coding
> agent, to give it everything it needs to score well on BixBench. It explains
> the benchmark, how it is graded, why agents lose points, and the concrete
> tricks to maximize score. **Scope: Python + command-line tasks only — R is
> excluded** (that subset = **124 of 205 questions**; Python 67, CLI 57).

---

## 1. What BixBench is (mental model)

- **205 questions across 59 "capsules"**, each capsule built from a real
  bioinformatics paper. A capsule = **data files + the original analyst's
  notebook (stripped out before you see it) + the paper's hypothesis**.
- The agent is given **only the data files** plus a **notebook / shell sandbox**
  and must **write and run code to compute each answer**. There is *no*
  pure-knowledge Q&A — every question is a computation on the provided data.
- **Capsule vs question is one-to-many:** one capsule has 1–7 questions
  (usually 4–5) that **share the same data files**. Explore the data once per
  capsule and reuse it.
- **This scope (non-R): 37 capsules / 124 questions** — Python 67, command-line 57.
- **Load it:** HF dataset `futurehouse/BixBench` (split `train`, 205 rows).
  Key fields per row: `question`, `ideal` (reference answer), `distractors`
  (wrong MCQ options), `eval_mode` (how it's graded), `capsule_uuid`,
  `question_id`, `data_folder` (the capsule zip on HF), `categories`.
  Capsule zip layout: `CapsuleData-<uuid>/<the data files>` (the
  `CapsuleNotebook-*` and any `.ipynb` are removed for the agent).

---

## 2. How scoring works — READ THIS, it dictates everything

Each question has an `eval_mode` that determines grading. **In this scope:**

| `eval_mode` | count (of 124) | how it grades | brittleness |
|---|---|---|---|
| `llm_verifier` | **76 (61%)** | an LLM judges your answer vs the reference: correct / incorrect / refused | **forgiving** (semantic) |
| `range_verifier` | **27 (22%)** | `lower ≤ float(your_answer) ≤ upper` | forgiving (ballpark) |
| `str_verifier` | **21 (17%)** | exact string match after stripping non-alphanumerics + lowercasing | **brutal** (exact) |

**The single most useful fact for this scope:** grading skews *semantic*.
- **All 57 command-line questions are `llm_verifier`.**
- The brittle `str_verifier` bucket (21) is **entirely inside the Python subset**.
- So representation/precision pain is confined to ~21 questions; for the other
  ~103 you mainly need the right *answer*, not the right *string*.

Exact grader mechanics (replicate them to format winning answers):

```python
# str_verifier: numerically-correct but mis-formatted -> WRONG
clean = lambda s: re.sub(r"[^a-zA-Z0-9]", "", s).lower()
correct = clean(prediction) == clean(reference)     # "0.00020" != "0.0002"; "2e-4" != "0.0002"

# range_verifier: reference looks like "(1.50,1.54)"
lo, hi = literal_eval(reference); correct = lo <= float(prediction) <= hi

# llm_verifier: a judge LLM sees (question, reference, prediction) -> correct/incorrect/refused
```

**MCQ vs open-ended.** Every question ships distractors, so it can be posed as
open-answer *or* multiple choice. In MCQ mode correctness = **you selected the
reference option**. Distractors usually cluster numerically *near* the truth.

**Recall floor.** A model answering with **no data** still scores ~**11%**
open-ended and ~**34%** on MCQ (guessing among options). Your agent's gains only
count if they beat this — so **ground every answer in a computed result.**

---

## 3. The task types you'll see (this scope)

Answer types (what the answer *is*): **estimate 60** (odds ratios, fold-changes,
means, correlations, percentages), **categorical 35** (which gene/pathway/group),
**count 20** (how many …), **p-value 9**.

Two Python and two CLI workflow families cover almost everything:

- **Python — differential expression & enrichment:** `pydeseq2` (DESeq2 in
  Python) → `gseapy` (GO/KEGG/Reactome/GSEA). Plus `scanpy`/`anndata`
  (single-cell), `statsmodels` (logistic/ordinal regression), `scikit-learn`.
- **CLI — phylogenetics / comparative genomics** (~12 caps): **BUSCO** (ortholog
  ID), **PhyKIT** (tree stats: tree length, treeness, DVMC, parsimony-informative
  sites — the most-named tool), **MAFFT/MUSCLE** (alignment), **ClipKIT**
  (trimming), **IQ-TREE/FastTree** (tree building).
- **CLI — alignment & variant calling / WGS** (~6 caps): **Trimmomatic/fastp +
  FastQC** → **BWA** (indexes provided) → **samtools** → **GATK4 / bcftools**
  (VCF) → **Picard / bedtools / vcftools**.

---

## 4. Why agents lose points (failure modes, ranked)

1. **Toolchain gaps** — a bare Python kernel can't run pydeseq2/gseapy/BUSCO/BWA;
   the task is unsolvable before reasoning even starts.
2. **Method divergence** — the reference is *one analyst's* exact pipeline
   (specific params, sample exclusions, covariates, tool versions). Different but
   valid choices → a different number. Worst for `str_verifier`.
3. **Representation / precision** — correct value, wrong string (only bites the
   21 `str_verifier` questions, but it bites hard).
4. **Recall contamination** — ungrounded answers that merely echo priors.
5. **Refusal / abstention** — bailing under uncertainty; roughly halves score.
6. **Long-horizon execution** — failed installs, dead kernels, unhandled
   exceptions, large files, undecompressed inputs.

---

## 5. The tricks to maximize score

### Trick 0 — Read the question for the exact method spec (highest leverage)
BixBench v1.5 questions are often **prescriptive**: they name the tool, gene-set,
and cutoffs — e.g. *"using `gseapy` and `GO_Biological_Process_2021`"*,
*"padj < 0.05 and |log2FC| ≥ 0.5"*, *"PhyKIT's `tree_length` function"*,
*"KEGG_2019_Mouse"*, *"LFC shrinkage"*. **These ARE the analyst's hidden choices
made explicit.** Extract every named tool, library, gene-set, parameter, and
threshold and follow them *exactly*. This is the cheapest, biggest counter to
method-divergence — do not substitute your own tool or cutoffs.

### Trick 1 — Bring the full toolchain & be able to install on the fly
Provision Python omics + CLI bioinformatics up front (see `environment.yml` /
`requirements-agent.txt`), and let the agent `pip`/`conda install` and `apt`
mid-run (the reference notebooks themselves `!pip install` and `!wget`). Allow
**outbound network** (Zenodo data, BUSCO lineage DBs, mygene/g:Profiler/ChEMBL
APIs) and **decompression** (`.gz/.zip/.tar.gz`).

### Trick 2 — Make the execution loop robust
Persistent kernel state; on an exception, **read the traceback and repair**
rather than abort; auto-install missing packages; set timeouts; handle large
FASTQ/BAM; inspect intermediate outputs. Treat "write code → run → observe real
output → iterate" as the core loop.

### Trick 3 — Ground in the data before analyzing (EDA, once per capsule)
Inventory files, schemas, and **group structure**; detect joins. This catches
the traps that sink analyses — e.g. sample IDs in the count matrix that are
**missing from the metadata** (a tell-tale of samples the analyst dropped), or a
`condition`/`sex` design you must model. Cache this manifest and reuse it across
the capsule's questions.

### Trick 4 — Use the standard pipeline with library defaults
When the question doesn't pin a parameter, use the **conventional workflow and
default settings** — the reference was produced by biologists using standard
pipelines, so defaults land closest. Do not get creative.

### Trick 5 — Answer policy (format to the grader)
- **`str_verifier` (21, Python numeric):** compute at full precision; emit the
  number in the form the question implies (sci vs decimal, sig figs). In MCQ,
  **snap to the nearest option** by numeric distance instead of exact-matching —
  this turns a ~2% exact-match hit-rate into ~90%+.
- **`range_verifier` (27):** just be in the ballpark; return a clean scalar.
- **`llm_verifier` (76, incl. all CLI):** state the answer clearly with the
  computed value and a one-line justification; exact formatting is not required —
  correctness is semantic.
- **MCQ:** always commit to the nearest option; pick the refusal option *only*
  if no computation was possible.
- **Open-ended: never refuse.** If uncertain, give your best computed estimate.

### Trick 6 — Verify and re-run
Add a verification pass that **re-derives the final value from the notebook's
actual outputs** (kills hallucinations and beats the recall floor), checks
units/sign/magnitude, and **re-runs the final analysis clean** to confirm the
number is reproducible (not an artifact of stale kernel state).

### Trick 7 — Prefer diverse-method consistency over sampling
Re-sampling the same approach at temperature gives correlated errors (didn't help
in the paper). Instead, when feasible, compute via **two different valid methods**;
agreement = high confidence, disagreement = a method-sensitive question (treat
its exact value as low-confidence and, in MCQ, lean on nearest-option).

### Trick 8 — Triage effort by question type
~83% of this scope (`llm_verifier` + `range_verifier`) rewards *correct, standard
analysis* and tolerates representation — spend your budget here and you win.
The ~17% `str_verifier` exact-number questions can be method-sensitive and
sometimes unreproducible; do the standard pipeline, format carefully, use
nearest-option on MCQ, and **don't burn a 40-step budget chasing one exact value.**

### Trick 9 — Read what's provided; you may start mid-pipeline
Inputs can be partial: a `.bam` present means skip alignment and go to variant
calling; BWA index files (`.amb/.ann/.bwt/.pac/.sa`) + `.fastq` mean you must
align first; pre-extracted `.faa` proteins mean go straight to BUSCO/PhyKIT.
Inspect the file set and enter the pipeline at the right step.

---

## 6. Per-question checklist (the loop to follow)

1. **Parse the question** → extract answer type, named tool(s), gene-set(s),
   parameters, thresholds. (Trick 0)
2. **Load the capsule manifest** (build once: files, schemas, groups, joins,
   dropped-sample/covariate flags). (Trick 3)
3. **Plan the standard pipeline** for that task family, honoring every named
   tool/param; use defaults for the rest. (Tricks 4, 0)
4. **Execute**, recovering from errors and installing as needed; keep outputs as
   evidence. (Trick 2)
5. **Verify**: re-derive the answer from a real cell output; sanity-check
   magnitude/sign/units; re-run clean. (Trick 6)
6. **Format & commit**: representation per `eval_mode`; MCQ → nearest option;
   never refuse. (Trick 5)

**Do:** follow named methods/params exactly · ground every answer in a computed
result · commit to a best estimate · snap MCQ to nearest option · use defaults.
**Don't:** invent a different tool/cutoff than the question names · refuse ·
report an exact number you didn't compute · chase an unreproducible exact value
on a low-value `str_verifier` MCQ when nearest-option already wins.

---

## 7. Quick reference

```python
# Load the benchmark
from datasets import load_dataset
ds = load_dataset("futurehouse/BixBench", split="train")   # 205 rows
# fields: question, ideal, distractors, eval_mode, capsule_uuid, question_id,
#         data_folder, categories, hypothesis, result, answer

# Download + unpack a capsule's data
from huggingface_hub import hf_hub_download
import zipfile
p = hf_hub_download("futurehouse/BixBench", row["data_folder"],
                    repo_type="dataset", local_dir="caps")
zipfile.ZipFile(p).extractall("caps")   # -> caps/CapsuleData-<uuid>/<files>
```

**Install the runtime** (covers this scope):
`conda env create -f environment.yml` (Python omics + CLI bioconda tools), or
`pip install -r requirements-agent.txt` for the Python-only pieces.

**Python libs:** pandas, numpy, scipy, statsmodels, scikit-learn, matplotlib,
seaborn, **pydeseq2, gseapy, scanpy, anndata, gprofiler-official, mygene**,
biopython, pysam, requests, xgboost, optuna (+ matplotlib-venn, upsetplot,
openpyxl, h5py, tqdm).

**CLI tools:** BUSCO, PhyKIT, MAFFT, MUSCLE, ClipKIT, IQ-TREE, FastTree, HMMER,
seqkit · BWA, bowtie2, samtools, bcftools, GATK4, Picard, Trimmomatic, fastp,
FastQC, bedtools, vcftools · wget, curl, tar, gzip, unzip.

**Scope cheat-sheet:** 124 questions · grading: llm 76 / range 27 / str 21 ·
answer types: estimate 60 / categorical 35 / count 20 / p-value 9 ·
ecosystems: Python 67, CLI 57 (all CLI questions are `llm_verifier`).
