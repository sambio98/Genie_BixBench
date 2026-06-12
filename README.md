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
- **[`docs/DESIGN.md`](docs/DESIGN.md)** — the architecture that targets each
  failure, in ROI order, with an experiment plan and per-grading-bucket strategy.

## Reproduce the evidence

```bash
pip install -r requirements.txt

python analysis/profile_dataset.py          # dataset structure, eval_mode mix, answer shapes
python analysis/measure_recall_baseline.py  # the "no-notebook" recall ceiling
python analysis/inspect_capsule.py          # ground-truth notebook + its hidden choices
```

## The findings in one table

| What the data shows | Consequence for design |
|---|---|
| 88% of answers are numeric; 30% graded by **exact string match** | match representation; `0.00020 ≠ 0.0002` loses points |
| Ground truth = R/Bioconductor (some command-line) | run the agent in the **same ecosystem** |
| Reference numbers encode undocumented choices (sample drops, covariates, versions) | bias to **standard/default pipelines**; some exact values are unreproducible |
| MCQ ~34% answerable with **no data** (recall leak) | ground every answer; report lift over recall |
| A refusal option **halves** accuracy | be decisive; never refuse in open-answer |
| 30% graded by **range**, 40% by **LLM judge** | these are the **winnable** points — invest here |
