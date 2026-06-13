# Genie_BixBench

Building a stronger agent for [BixBench](https://github.com/Future-House/BixBench)
— a benchmark of 205 real bioinformatics analysis tasks. This repo starts from an
**evidence-grounded study of why agents fail**, then proposes a design that targets
those specific failures.

## Start here

- **[`docs/FINDINGS.md`](docs/FINDINGS.md)** — why agents fail, grounded in the
  actual dataset, grader code, and recall baselines. The core insight: BixBench
  is dominated by *numeric exact-match grading*, *undocumented analyst choices*,
  and an *R-vs-Python ecosystem gap* — so much of the loss is correct analysis
  scored wrong, not failed analysis.
- **[`docs/TASK_TYPES.md`](docs/TASK_TYPES.md)** — the task-type taxonomy: it's
  one benchmark (agentic data analysis), not separate "Q&A" vs "code-running"
  benchmarks, varying along six measured axes (regime, format, grading, answer
  type, domain, ecosystem).
- **[`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md)** — the agent's required
  libraries & capabilities for the Python + command-line scope (124/205 qs):
  pip set (`requirements-agent.txt`) + bioconda CLI tools (`environment.yml`).
- **[`docs/DESIGN.md`](docs/DESIGN.md)** — the architecture that targets each
  failure, in ROI order, with an experiment plan and per-grading-bucket strategy.

## Reproduce the evidence

```bash
pip install -r requirements.txt

python analysis/profile_dataset.py          # dataset structure, eval_mode mix, answer shapes
python analysis/measure_recall_baseline.py  # the "no-notebook" recall ceiling
python analysis/inspect_capsule.py          # ground-truth notebook + its hidden choices
python analysis/grading_sensitivity.py      # how much the answer module recovers (sim)
python analysis/attribute_failures.py --demo # failure-attribution harness (Phase 1)
```

## What's built

- **`genie/answer.py`** (DESIGN §7) — faithful grader replicas, representation
  matching, nearest-option MCQ snapping, no-refuse policy.
- **`genie/eda.py`** (DESIGN §2) — capsule data-manifest builder. On the real
  RNA-seq capsule it auto-detects the design and the **two silently-dropped
  samples** the canonical pipeline removes (`python -m genie.eda <capsule_dir>`).
- **`genie/router.py`** (DESIGN §4) — answer-shape router (p-value / estimate /
  count / categorical / boolean) → representation + strategy.
- **`genie/solve.py`** — the end-to-end runner that assembles them:
  `route → (cached capsule manifest) → Analyst → verify → answer policy`. The
  `Analyst` is a pluggable Protocol; a real LLM+notebook analyst drops in behind
  the same interface. `python -m genie.solve` drives the whole loop with a test
  analyst and reproduces the nearest-option win end-to-end (100/94/82% at
  0/5/10% analysis error).
- **`analysis/`** — reproducible evidence + the Phase-1 attribution harness.
- **`tests/`** — 47 tests (`python -m pytest`).

Measured payoff of the answer module (`grading_sensitivity.py`, on real
targets/distractors): an analysis within 5% of the truth scores **2% on MCQ with
naive exact-match vs 94% with nearest-option snapping**; and even an *exact* value
expressed naturally clears `str_verifier` only ~33% of the time.

## The findings in one table

| What the data shows | Consequence for design |
|---|---|
| 88% of answers are numeric; 30% graded by **exact string match** | match representation; `0.00020 ≠ 0.0002` loses points |
| Ground truth = R/Bioconductor (some command-line) | run the agent in the **same ecosystem** |
| Reference numbers encode undocumented choices (sample drops, covariates, versions) | bias to **standard/default pipelines**; some exact values are unreproducible |
| MCQ ~34% answerable with **no data** (recall leak) | ground every answer; report lift over recall |
| A refusal option **halves** accuracy | be decisive; never refuse in open-answer |
| 30% graded by **range**, 40% by **LLM judge** | these are the **winnable** points — invest here |
