# Genie_BixBench — a design that targets the real failures

This design follows directly from `docs/FINDINGS.md`. The guiding principle: most
lost points are **not** "the model can't do biology." They are representation
mismatch, method divergence, a Python/R ecosystem gap, recall-leak, and
over-refusal. So we spend effort where points are recoverable.

## Findings → levers → components

| Finding (from FINDINGS.md) | Lever | Component | Targets |
|---|---|---|---|
| 60 numeric `str_verifier` Qs lost on representation | match the expected representation | **Answer Formatter** | str_verifier |
| MCQ distractors cluster near truth; exact-match is the wrong game | snap to nearest option | **MCQ nearest-option policy** | all MCQ |
| Refusal halves score | be decisive | **Answering policy** | all |
| Ground truth is R/Bioconductor (or CLI) | ecosystem parity | **R + CLI sandbox** | str/range pipelines |
| Reference = standard pipeline w/ defaults | bias to conventional workflow | **Standard-pipeline prior + templates** | str/range |
| 4–5 Qs share one capsule's data | explore once, reuse | **Capsule EDA / data manifest** | all |
| Temperature voting failed (correlated errors) | diversify *methods*, not samples | **Multi-method consistency** | range/llm |
| Recall leak (~34% MCQ ungrounded) | require evidence | **Verifier pass** | all |
| Plots unseen (`avoid_images`) | enable vision + image I/O | **Vision/image path** | imaging |

## Architecture

```
              ┌────────────────────────────────────────────────┐
   capsule →  │  CAPSULE EDA (once)                             │
              │  inventory files, schemas, sample counts,       │
              │  joins, modality → cached DATA MANIFEST         │
              └───────────────┬────────────────────────────────┘
                              │  (shared by all Qs in capsule)
        ┌─────────────────────┼─────────────────────┐
        ▼ per question                                │
  ┌───────────────┐   ┌────────────────────────┐   ┌───────────────────┐
  │ ANSWER-SHAPE  │→  │ ANALYST (×K methods)    │→  │ VERIFIER          │
  │ ROUTER        │   │ standard-pipeline prior │   │ re-derive number  │
  │ numeric/stat/ │   │ in R or Python or CLI   │   │ from cell outputs;│
  │ descr/concept │   │ → value + evidence cell │   │ reject ungrounded │
  └───────────────┘   └────────────┬───────────┘   └─────────┬─────────┘
                                    ▼                          ▼
                       ┌────────────────────────┐   ┌───────────────────┐
                       │ CONSISTENCY/SENSITIVITY │→  │ FORMATTER + POLICY│
                       │ agree→confident;        │   │ canonical repr;   │
                       │ diverge→standard variant│   │ MCQ→nearest option│
                       │ + low confidence        │   │ never refuse (open)│
                       └────────────────────────┘   └───────────────────┘
```

## Orchestration — IMPLEMENTED

`genie/solve.py` wires the components into one loop: `route → (capsule manifest,
cached per `capsule_uuid`) → Analyst.analyze → verify → answer policy`. The
`Analyst` is a swappable `Protocol`, so the only piece needing an LLM + notebook
sandbox is isolated behind one interface; everything around it is pure and
tested. `python -m genie.solve` runs the whole assembly with a test analyst and
reproduces the nearest-option result end-to-end (100/94/82% at 0/5/10% analysis
error). The remaining work is the real `LLMAnalyst` (Phase 2) — same signature.

## Components

### 1. Environment parity (R + Bioconductor + CLI)
The single biggest structural fix. The reference notebooks are mostly R
(`DESeq2`, `clusterProfiler`, `edgeR`, `limma`), and some tasks are command-line
(alignment, variant calling, tree-building). An agent confined to Python can
never converge on the reference number. Provision a sandbox with R + key
Bioconductor packages, common bioinformatics CLIs, and Python — and let the
analyst **choose the ecosystem the task implies**. Pin versions toward those the
dataset era used where known.

### 2. Capsule EDA → data manifest (amortize, don't repeat) — IMPLEMENTED
Built in `genie/eda.py` (tested in `tests/test_eda.py`). One deterministic pass
per `capsule_uuid`: file inventory, robust delimiter handling, true row counts,
role detection (expression matrix / metadata / annotation / variants / sequence
/ image), low-cardinality **group counts** (the experiment design), and
**value-based joins**. On the real RNA-seq capsule it auto-surfaces the design
(Control=11 / ASXL1=8, sex F=11 / M=8) **and the two silently-dropped samples**
(`MGD1640B`, `MGD1641B`) — i.e. it hands the agent the exact judgment call that
FINDINGS §4 flagged as otherwise unrecoverable. Cache the manifest and reuse it
across the capsule's 4–5 questions.

### 3. Standard-pipeline prior ("conventional analyst")
The reference answers come from domain experts using **standard workflows with
library defaults**. So bias hard toward conventional pipelines and default
parameters; treat creative deviation as a bug. Ship a small library of canonical
templates (RNA-seq DE→GO, differential abundance, logistic/ordinal regression,
survival, variant filtering) the analyst adapts rather than inventing. This is
the main lever against **method divergence**.

### 4. Answer-shape router — IMPLEMENTED
Built in `genie/router.py` (tested in `tests/test_router.py`). Infers the target
shape from the question and any MCQ options — p-value / estimate / count /
categorical / boolean / descriptive — and returns the representation
(scientific / decimal / integer / text), suggested sig figs, and a per-shape
strategy. This sets analysis depth, sensitivity strategy, and formatter behavior.

### 5. Multi-method consistency + sensitivity (not temperature voting)
The paper showed 10× sampling didn't help — errors are correlated. Instead run
K *methodologically distinct* attempts, or perturb the load-bearing choices
(±covariate, default vs explicit background, filtering threshold):
- **converge** → confident; the value is method-robust → wins `range_verifier`
  and `llm_verifier`.
- **diverge** → the question is method-sensitive (a likely `str_verifier` trap);
  emit the **most standard** variant and mark low confidence.
The spread *is* the confidence signal, and it tells us which questions are
unwinnable so we don't burn budget on them.

### 6. Verifier pass (close the recall gap)
A second agent re-derives the final value from the notebook's **actual executed
cell outputs**, checks units/sign/magnitude plausibility, and rejects any answer
not traceable to a computed result. This forces gains to come from analysis, so
they survive comparison to the ~34% MCQ / ~11% open recall baselines.

### 7. Formatter + answering policy (cheapest, do first) — IMPLEMENTED
Built in `genie/answer.py` (tested in `tests/test_answer.py`). Measured on real
targets/distractors by `analysis/grading_sensitivity.py`:
- A value within **5%** of truth scores **2% (naive exact-match) vs 94%
  (nearest-option)** on MCQ; **82%** even at 10% error.
- An *exact* value expressed naturally passes `str_verifier` only **~33%** of the
  time — confirming representation, not analysis, is the binding constraint here.

- **Representation:** emit the number in the form the question implies — sig
  figs, decimal vs scientific — and, where ambiguous, the most conventional form.
- **MCQ:** compute the estimate, then **snap to the nearest option** by numeric
  distance. This converts an unwinnable exact-match into a winnable
  nearest-neighbor and exploits the fact that distractors cluster near the truth.
- **Decisiveness:** **never refuse** in open-answer; in MCQ choose the refusal
  option only when computation was impossible. (Refusal halves the score.)

### 8. Vision / image path
For the 21 imaging questions, drop `avoid_images`: allow the analyst to read
plots and process image files programmatically (e.g. scikit-image, cellpose).

## Experiment plan (ROI order)

**Phase 0 — Reproduce baselines.** Stand up the harness; reproduce the recall
baselines (`analysis/measure_recall_baseline.py`) and a vanilla agentic run.
Establish our number *per eval_mode*.

**Phase 1 — Failure-attribution study (validate the thesis).** Run a sample,
then label every miss as: `analysis-wrong` / `right-but-representation` /
`right-but-method-divergent` / `recall-only` / `judge-noise`. FINDINGS predicts
`representation` + `method-divergent` are a large share. This tells us the true
ceiling per bucket and confirms where to invest. (Build the labeling harness as
`analysis/attribute_failures.py`.)

**Phase 2 — Implement in ROI order.**
1. Formatter + MCQ nearest-option + no-refusal policy *(cheap, broad)*
2. R/CLI environment parity *(structural; unlocks str/range pipelines)*
3. Standard-pipeline prior + capsule EDA manifest
4. Multi-method consistency + verifier

**Phase 3 — Ablate & report.** For every change report: **accuracy split by
`eval_mode`**, **lift over recall baseline**, **refusal rate**, and a
**representation-loss rate** (numerically correct but mis-formatted) — the key
diagnostic that tells us if the formatter is doing its job.

## How we expect to win, by bucket

- **`range_verifier` (30%)** — biggest real prize. Standard analysis + parity +
  point estimates. Robust to minor method differences.
- **`llm_verifier` (40%)** — evidence-grounded answers beat the recall floor;
  decisiveness avoids refusal losses.
- **`str_verifier` (30%)** — hardest. Parity + default-pipeline prior recover
  some exact numbers; for the rest, MCQ nearest-option salvages points and the
  consistency signal tells us when to stop trying. We explicitly do **not**
  over-invest in chasing unreproducible exact values.
