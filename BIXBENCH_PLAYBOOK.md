# BixBench Playbook (Python + CLI scope) — self-contained

> **What this is.** A single, standalone briefing on the BixBench benchmark and
> how to maximize an agent's score on it. It assumes you already have a coding
> agent with a notebook/shell sandbox; it gives you the benchmark mechanics, the
> failure modes, the score-maximizing tricks, the full runtime to install, and
> how the numbers here were derived. **Everything is in this one file.**
>
> **Scope: Python + command-line tasks only — R is excluded.** That subset is
> **124 of 205 questions** (Python 67, command-line 57). Full-benchmark context
> is given where useful, but all strategy targets the non-R scope.

---

## 1. What BixBench is (mental model)

- **205 questions across 59 "capsules."** Each capsule is built from a real
  bioinformatics paper and bundles: **data files + the original analyst's
  notebook (removed before the agent sees it) + the paper's hypothesis.**
- The agent gets **only the data files** plus a **notebook/shell sandbox** and
  must **write and run code to compute each answer.** There is **no
  pure-knowledge Q&A** — every question is a computation over the provided data.
  (A model answering with no data still scores ~11% open / ~34% MCQ by recall and
  guessing; see §3. Your gains must beat that, so ground every answer in a
  computed result.)
- **Capsule → questions is one-to-many.** A capsule has 1–7 questions (usually
  4–5) that **share the same data files.** Explore the data once per capsule and
  reuse it across its questions.
- **This scope (non-R): 37 capsules / 124 questions** — Python 67, command-line 57.
- **It's one benchmark, not "Q&A vs coding."** The same questions are *evaluated*
  two ways: agentic (run code — the real benchmark) and a zero-shot baseline
  (no data — used to measure recall leakage).

**How to load it (Hugging Face dataset `futurehouse/BixBench`, split `train`):**

```python
from datasets import load_dataset
ds = load_dataset("futurehouse/BixBench", split="train")   # 205 rows
# Key fields per row:
#   question      - the task (often names the exact tool/params to use)
#   ideal         - the reference answer
#   distractors   - wrong MCQ options (every question is MCQ-able)
#   eval_mode     - how it is graded: llm_verifier | range_verifier | str_verifier
#   capsule_uuid  - groups questions that share data
#   question_id   - e.g. "bix-1-q1"
#   data_folder   - the capsule .zip on HF
#   categories    - domain tags (RNA-seq, WGS, Phylogenetics, Imaging, ...)
#   hypothesis/result/answer - source-paper provenance (NOT the graded answer)

# Download + unpack one capsule's data:
from huggingface_hub import hf_hub_download
import zipfile
p = hf_hub_download("futurehouse/BixBench", row["data_folder"],
                    repo_type="dataset", local_dir="caps")
zipfile.ZipFile(p).extractall("caps")     # -> caps/CapsuleData-<uuid>/<data files>
```

---

## 2. How scoring works — READ THIS, it dictates strategy

Each question carries an `eval_mode`. **In the non-R scope (124 q):**

| `eval_mode` | count | how it grades | brittleness |
|---|---|---|---|
| `llm_verifier` | **76 (61%)** | a judge LLM compares your answer to the reference → correct / incorrect / refused | **forgiving** (semantic) |
| `range_verifier` | **27 (22%)** | `lower ≤ float(your_answer) ≤ upper` | forgiving (ballpark) |
| `str_verifier` | **21 (17%)** | exact string match after stripping non-alphanumerics + lowercasing | **brutal** (exact) |

**Most important fact for this scope — grading is mostly semantic:**
- **All 57 command-line questions are `llm_verifier`.**
- The brittle `str_verifier` bucket (21) is **entirely inside the Python subset.**
- So ~83% of the scope (`llm` + `range`) rewards *correct standard analysis* and
  **tolerates representation**; exact-string pain is confined to ~21 questions.
- (Full-benchmark split for contrast: llm 40% / str 30% / range 30% — i.e. the
  non-R scope is *more forgiving* than the whole benchmark.)

**Exact grader mechanics** (replicate them so you format winning answers):

```python
# str_verifier — numerically correct but mis-formatted still scores WRONG
clean = lambda s: re.sub(r"[^a-zA-Z0-9]", "", s).lower()
correct = clean(prediction) == clean(reference)
#   "0.0002" vs "0.00020" -> WRONG (trailing zero)
#   "0.0002" vs "2e-4"     -> WRONG (scientific)
#   "0.0002" vs "0.00019"  -> WRONG (rounding)

# range_verifier — reference looks like "(1.50,1.54)"
lo, hi = literal_eval(reference); correct = lo <= float(prediction) <= hi

# llm_verifier — a judge LLM sees (question, reference, prediction)
#   -> correct / incorrect / refused.  Semantic equivalence, not exact strings.
```

**MCQ vs open-ended.** Every question ships distractors, so it can be posed
open-answer *or* as multiple choice. In MCQ mode, correct = **you selected the
reference option**; distractors usually cluster numerically *near* the truth.

**Measured leverage of answer handling (on the real options):**
- A *numerically correct* value, expressed naturally, clears `str_verifier` only
  **~33%** of the time → representation matters on those 21 questions.
- On MCQ, an analysis within ~5% of the truth scores **~2% with naive
  exact-match vs ~94% if you snap to the nearest option** (82% even at 10% off).
- Offering/choosing a "refusal" option roughly **halves** accuracy.

---

## 3. The recall floor (what you must beat)

A strong model given **no data** still gets, by recall + guessing:
- **~11%** open-ended, **~34%** MCQ (4–5 options).

So a chunk of any MCQ score is not analysis. **Ground every answer in a computed
cell output**, and report improvements relative to this floor.

---

## 4. Task types in scope

**Answer types** (what the answer *is*): **estimate 60** (odds ratios,
fold-changes, means, correlations, percentages) · **categorical 35** (which
gene / pathway / group) · **count 20** (how many …) · **p-value 9**.

**Four workflow families cover almost everything:**

- **Python — differential expression → enrichment:** `pydeseq2` (DESeq2 in
  Python) → `gseapy` (GO / KEGG / Reactome / GSEA). *(Highest-leverage pair.)*
- **Python — other:** `scanpy`/`anndata` (single-cell), `statsmodels`
  (logistic/ordinal regression), `scikit-learn` / `xgboost` (ML), `scipy.stats`.
- **CLI — phylogenetics / comparative genomics** (~12 caps): **BUSCO** (ortholog
  ID), **PhyKIT** (tree stats: tree length, treeness, DVMC, parsimony-informative
  sites — the most-named tool), **MAFFT/MUSCLE** (alignment), **ClipKIT**
  (trimming), **IQ-TREE/FastTree** (tree building).
- **CLI — read alignment & variant calling / WGS** (~6 caps): **Trimmomatic/
  fastp + FastQC** → **BWA** (indexes provided) → **samtools** → **GATK4 /
  bcftools** (VCF) → **Picard / bedtools / vcftools**.

---

## 5. Why agents lose points (failure modes, ranked)

1. **Toolchain gaps** — a bare Python kernel can't run pydeseq2/gseapy/BUSCO/BWA;
   the task is unsolvable before reasoning starts. (See §8 for the full runtime.)
2. **Method divergence** — the reference is *one analyst's exact pipeline*
   (specific params, sample exclusions, covariates, tool/DB versions). A
   different-but-valid choice → a different number. Worst on `str_verifier`.
3. **Representation / precision** — correct value, wrong string (bites the 21
   `str_verifier` questions hard; irrelevant to the 103 llm/range questions).
4. **Recall contamination** — ungrounded answers that just echo priors.
5. **Refusal / abstention** — bailing under uncertainty ≈ halves the score.
6. **Long-horizon execution** — failed installs, dead kernels, unhandled
   exceptions, large files, undecompressed inputs.

**Worked example of #2 and #3.** A typical question: *"…perform DESeq2
differential expression, then GO enrichment; what is the adjusted p-value for
'regulation of T cell activation'?"* → reference `0.0002`, `eval_mode=str_verifier`.
To reproduce `0.0002` exactly you must independently match a chain of choices the
question never states: which samples to drop (the reference notebook silently
removed 2 for clinical reasons noted only in a code comment), the design formula
(e.g. `~sex + condition` vs `~condition`), the explicit background gene universe
for enrichment, the redundancy-cutoff for term simplification, and the *versioned*
annotation database. Independently hitting all of them ≈ impossible, and the MCQ
distractors are often the *same term under other valid methods*. Lesson: on exact
`str_verifier` numbers, prefer **standard defaults**, and on MCQ **snap to the
nearest option** instead of chasing the exact value. (This case is R, but the
identical hidden-choice problem applies to the Python `pydeseq2`→`gseapy` tasks.)

---

## 6. The tricks to maximize score

### Trick 0 — Read the question for the exact method spec (highest leverage)
BixBench v1.5 questions are often **prescriptive** and name the tool, gene-set,
and cutoffs — e.g. *"using `gseapy` and `GO_Biological_Process_2021`"*,
*"padj < 0.05 and |log2FC| ≥ 0.5"*, *"PhyKIT's `tree_length` function"*,
*"`KEGG_2019_Mouse`"*, *"with LFC shrinkage"*. **These are the analyst's hidden
choices made explicit.** Extract every named tool, library, gene-set, parameter,
and threshold and follow them *exactly* — do not substitute your own. This is the
cheapest, biggest counter to method-divergence.

### Trick 1 — Bring the full toolchain & install on the fly
Provision the Python omics + CLI bioinformatics runtime up front (§8), and let the
agent `pip`/`conda`/`apt` install mid-run (reference notebooks themselves do
`!pip install` and `!wget`). Allow **outbound network** (Zenodo data downloads,
BUSCO lineage DBs, mygene/g:Profiler/ChEMBL APIs) and **decompression**
(`.gz/.zip/.tar.gz`).

### Trick 2 — Make the execution loop robust
Persistent kernel state; on an exception, **read the traceback and repair**
instead of aborting; auto-install missing packages; set timeouts; handle large
FASTQ/BAM; inspect intermediate outputs. Core loop = *write code → run → observe
real output → iterate.*

### Trick 3 — Ground in the data before analyzing (EDA, once per capsule)
Inventory files, schemas, and **group structure**; detect joins. This catches the
traps that sink analyses — e.g. sample IDs present in the count matrix but
**missing from the metadata** (the tell-tale of samples the analyst dropped), or a
`condition`/`sex` design you must model. Build this manifest once and reuse it
across the capsule's questions.

### Trick 4 — Use the standard pipeline with library defaults
When the question doesn't pin a parameter, use the **conventional workflow and
default settings** — the reference came from biologists using standard pipelines,
so defaults land closest. Don't get creative.

### Trick 5 — Answer policy (format to the grader)
- **`str_verifier` (21, Python numeric):** compute at full precision; emit the
  number in the form the question implies (scientific vs decimal, sig figs). In
  MCQ, **snap to the nearest option** by numeric distance (≈2% → ≈94%).
- **`range_verifier` (27):** just be in the ballpark; return a clean scalar.
- **`llm_verifier` (76, incl. all CLI):** state the answer clearly with the
  computed value and a one-line justification; exact formatting not required —
  correctness is semantic.
- **MCQ:** always commit to the nearest option; choose a refusal option *only* if
  no computation was possible.
- **Open-ended: never refuse** — give your best computed estimate.

### Trick 6 — Verify and re-run
Add a verification pass that **re-derives the final value from the notebook's
actual outputs** (kills hallucinations, beats the recall floor), checks
units/sign/magnitude, and **re-runs the final analysis clean** to confirm the
number is reproducible (not a stale-kernel artifact).

### Trick 7 — Prefer diverse-method consistency over sampling
Re-sampling the same approach at temperature gives correlated errors (didn't help
in the paper). Instead compute via **two different valid methods**: agreement =
high confidence; disagreement = a method-sensitive question (treat its exact value
as low-confidence; on MCQ lean on nearest-option).

### Trick 8 — Triage effort by question type
~83% of the scope (`llm` + `range`) rewards correct standard analysis and
tolerates representation — spend budget here and you win. The ~17% `str_verifier`
exact-number questions can be unreproducible; do the standard pipeline, format
carefully, snap on MCQ, and **don't burn a 40-step budget chasing one exact value.**

### Trick 9 — Read what's provided; you may start mid-pipeline
Inputs can be partial: a `.bam` present → skip alignment, go to variant calling;
BWA index files (`.amb/.ann/.bwt/.pac/.sa`) + `.fastq` → align first;
pre-extracted `.faa` proteins → go straight to BUSCO/PhyKIT. Inspect the file set
and enter at the right step.

---

## 7. Per-question checklist (the loop to follow)

1. **Parse the question** → answer type + every named tool, gene-set, parameter,
   threshold. (Trick 0)
2. **Load the capsule manifest** (files, schemas, groups, joins, dropped-sample /
   covariate flags; build once per capsule). (Trick 3)
3. **Plan the standard pipeline** for that family, honoring every named tool/param,
   defaults for the rest. (Tricks 4, 0)
4. **Execute**, recovering from errors and installing as needed; keep outputs as
   evidence. (Trick 2)
5. **Verify**: re-derive from a real cell output; sanity-check magnitude/sign/units;
   re-run clean. (Trick 6)
6. **Format & commit**: representation per `eval_mode`; MCQ → nearest option; never
   refuse. (Trick 5)

**Do:** follow named methods/params exactly · ground every answer in a computed
result · commit to a best estimate · snap MCQ to nearest option · use defaults.
**Don't:** invent a different tool/cutoff than the question names · refuse · report
an exact number you didn't compute · chase an unreproducible exact value on a
low-value `str_verifier` MCQ when nearest-option already wins.

---

## 8. The runtime to install (covers this scope)

**Conda environment (Python omics + CLI bioinformatics tools):**

```yaml
# environment.yml  ->  conda env create -f environment.yml
name: bixbench-agent
channels: [conda-forge, bioconda]
dependencies:
  - python=3.11
  # core python / data / stats
  - pandas
  - numpy
  - scipy
  - statsmodels
  - scikit-learn
  - matplotlib
  - seaborn
  # IO + notebook execution
  - jupyter
  - ipykernel
  - nbformat
  - openpyxl
  - xlrd
  - h5py
  - pytables
  - requests
  - tqdm
  # sequence / alignment parsing in Python
  - biopython
  - pysam
  # CLI: phylogenetics / comparative genomics
  - busco
  - phykit
  - clipkit
  - mafft
  - muscle
  - iqtree
  - fasttree
  - hmmer
  - seqkit
  # CLI: read alignment & variant calling / WGS
  - bwa
  - bowtie2
  - samtools
  - bcftools
  - gatk4
  - picard
  - bedtools
  - vcftools
  - trimmomatic
  - fastqc
  - fastp
  # general CLI utilities
  - wget
  - curl
  # pip-only omics packages
  - pip
  - pip:
      - pydeseq2
      - gseapy
      - gprofiler-official
      - mygene
      - scanpy
      - anndata
      - matplotlib-venn
      - upsetplot
      - altair
      - xgboost
      - optuna
      - optuna-integration
      - chembl-webresource-client
```

**Python-only (pip) subset, if you don't need the CLI tools:**

```text
pandas numpy scipy statsmodels scikit-learn matplotlib seaborn
pydeseq2 gseapy gprofiler-official mygene scanpy anndata
biopython pysam requests xgboost optuna optuna-integration
chembl-webresource-client matplotlib-venn upsetplot altair
openpyxl xlrd h5py tables tqdm jupyter ipykernel nbformat
```

---

## 9. Requirements reference (with usage counts)

**Python libraries** (across the 16 Python capsules / 67 questions). `#caps` =
capsules importing it, `#q` = questions covered:

| Library | Category | #caps | #q | Purpose |
|---|---|---:|---:|---|
| pandas | core data | 16 | 67 | dataframes / IO |
| numpy | core data | 14 | 59 | arrays / math |
| matplotlib | visualization | 14 | 59 | plotting |
| seaborn | visualization | 14 | 59 | statistical plots |
| scipy | statistics | 11 | 49 | tests, distributions |
| statsmodels | stats / modeling | 7 | 31 | logistic/ordinal regression |
| scikit-learn | machine learning | 7 | 29 | ML models |
| pydeseq2 | differential expression | 7 | 29 | DESeq2 in Python |
| gseapy | enrichment | 5 | 21 | GO/KEGG/Reactome/GSEA |
| scanpy | single-cell | 4 | 18 | scRNA-seq |
| requests | web / API | 3 | 13 | HTTP downloads |
| matplotlib-venn | visualization | 2 | 8 | Venn diagrams |
| mygene | enrichment / IDs | 1 | 6 | gene-ID mapping (API) |
| upsetplot | visualization | 1 | 5 | UpSet plots |
| gprofiler-official | enrichment | 1 | 3 | g:Profiler (API) |
| chembl_webresource_client | web / API | 1 | 3 | ChEMBL API |
| tqdm | utility | 1 | 3 | progress bars |
| xgboost | machine learning | 1 | 2 | gradient boosting |
| optuna (+integration) | machine learning | 1 | 2 | hyperparameter search |
| anndata | single-cell | 1 | 2 | annotated data (.h5ad) |
| altair | visualization | 1 | 2 | declarative viz |
| biopython | sequence IO | rec | — | parse FASTA (CLI-scope formats) |
| pysam | sequence IO | rec | — | parse BAM/VCF (CLI-scope formats) |
| openpyxl / xlrd | IO dep | — | — | read .xls / .xlsx |
| h5py / tables | IO dep | — | — | read .h5ad / HDF5 |
| jupyter / ipykernel / nbformat | runtime | — | — | execute notebook cells |

(`google` appears once = `google.colab` → **not** needed outside Colab.)

**Command-line tools** (across the 21 CLI capsules / 57 questions; counts are
question-text tool mentions and/or file-type evidence):

| Tool | Family | Evidence | Purpose |
|---|---|---|---|
| BUSCO | phylogenetics | 3 q; `*.busco.zip`, `eukaryota_odb10` | single-copy ortholog ID (+lineage DBs) |
| PhyKIT | phylogenetics | **12 q**; `.treefile` | tree stats (length, treeness, DVMC, PI-sites) |
| MAFFT | phylogenetics | 1 q; `.mafft` | multiple sequence alignment |
| MUSCLE | phylogenetics | (alt MSA) | multiple sequence alignment |
| ClipKIT | phylogenetics | 1 q; `.clipkit` | alignment trimming |
| IQ-TREE | phylogenetics | `.treefile` | tree inference |
| FastTree / RAxML | phylogenetics | (alt tree) | tree inference |
| HMMER | phylogenetics | (BUSCO dependency) | profile-HMM search |
| seqkit | phylogenetics | `.faa` 48q, `.fna` | FASTA manipulation/stats |
| Trimmomatic | alignment/variants | 1 q; `.fastq` 6q | read trimming |
| fastp | alignment/variants | (alt QC/trim) | read QC + trimming |
| FastQC | alignment/variants | `.fastq` | read quality control |
| BWA | alignment/variants | 1 q; index `.amb/.ann/.bwt/.pac/.sa` 6q | read alignment |
| bowtie2 | alignment/variants | (alt aligner) | read alignment |
| samtools | alignment/variants | 1 q; `.bam` 4q | BAM sort/index/stats |
| bcftools | alignment/variants | `.vcf` 3q | variant calling/filtering |
| GATK4 | alignment/variants | 1 q | variant calling |
| Picard | alignment/variants | (BAM dedup/metrics) | BAM utilities |
| bedtools | alignment/variants | (interval ops) | genome arithmetic |
| vcftools | alignment/variants | `.vcf` | variant operations |

---

## 10. How these numbers were derived (and how to refresh)

The counts here are a **snapshot** of `futurehouse/BixBench` (version 1.5, 205
rows). They were produced by:

1. Reading each capsule's reference notebook from its HF zip and taking the
   Jupyter **kernel name** to label the ecosystem: `ir` → R, `python3` → Python,
   **no `.ipynb`** → command-line. Then keeping only Python/CLI capsules.
2. Reading the dataset's `eval_mode` field per question for the grading split.
3. Extracting `import`/`!pip install` lines from the Python notebooks for the
   library usage counts, and file extensions + tool names mentioned in question
   text for the CLI tools.

Reproduce/refresh with this snippet (it streams just the notebook entry out of
each remote zip, so it's fast):

```python
import zipfile, json
from collections import Counter
from huggingface_hub import HfFileSystem
from datasets import load_dataset

fs = HfFileSystem(); base = "datasets/futurehouse/BixBench/"
ds = load_dataset("futurehouse/BixBench", split="train")
lang = {}
for f in sorted({r["data_folder"] for r in ds}):
    with fs.open(base + f, "rb") as fh:
        nbs = [n for n in zipfile.ZipFile(fh).namelist() if n.endswith(".ipynb")]
        if not nbs:
            lang[f] = "CLI"
        else:
            ks = json.loads(zipfile.ZipFile(fh).read(nbs[0])).get("metadata", {}).get("kernelspec", {})
            lang[f] = {"ir": "R", "python3": "Python"}.get(ks.get("name", ""), "?")
scope = [r for r in ds if lang[r["data_folder"]] in ("Python", "CLI")]
print("non-R questions:", len(scope))                                  # 124
print("eval_mode:", Counter(r["eval_mode"] for r in scope))            # llm 76 / range 27 / str 21
print("ecosystem:", Counter(lang[r["data_folder"]] for r in scope))   # Python 67 / CLI 57
```

---

## Scope cheat-sheet (TL;DR)

- **124 questions** (R excluded): Python 67, command-line 57.
- **Grading:** `llm_verifier` 76 · `range_verifier` 27 · `str_verifier` 21.
  **All 57 CLI questions are `llm_verifier`;** the brittle exact-string bucket is
  the 21 Python ones. → ~83% tolerates representation.
- **Answer types:** estimate 60 · categorical 35 · count 20 · p-value 9.
- **Recall floor to beat:** ~11% open / ~34% MCQ.
- **Biggest wins:** follow the question's named tools/params exactly · full
  toolchain + robust execution · EDA grounding · standard-pipeline defaults ·
  eval-aware formatting (nearest-option on MCQ) · never refuse · verify by
  re-deriving from real outputs.
