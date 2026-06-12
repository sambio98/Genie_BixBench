# Why agents fail on BixBench — an evidence-grounded analysis

Every number here is reproduced live from the dataset and the published results
by `analysis/profile_dataset.py` and `analysis/inspect_capsule.py`. Sources:
the `futurehouse/BixBench` HF dataset (205 questions), the
[BixBench repo](https://github.com/Future-House/BixBench) grader/agent code, and
the shipped recall-baseline CSVs in `bixbench_results/baseline_eval_data/`.

---

## TL;DR

BixBench is not mainly a test of "can the model do bioinformatics." Three
structural facts about the benchmark dominate the score:

1. **88% of answers are numeric** (180/205), and **30% are graded by exact
   string match** (`str_verifier`, 60/61 of them numeric). A *correct* analysis
   that prints `1.9e-4` instead of `0.0002` is scored **wrong**.
2. **The ground truth encodes undocumented analyst choices** (silent sample
   drops, non-default covariates, explicit background universes, specific
   library/annotation versions) that the question text never states. For
   multi-step pipelines, the reference number is effectively unreproducible
   without the original notebook.
3. **The canonical notebooks are R/Bioconductor** (and some tasks ship no
   notebook — they were command-line). The reference agent runs **Python**.
   Different ecosystem → different numbers.

So failures split into roughly: *analysis genuinely wrong*, *analysis right but
representation/method-divergent* (a large, under-appreciated share), *question
answerable by recall alone*, and *judge/format noise*. The biggest **cheap
wins** are not "smarter biology" — they are (a) matching the answer
representation, (b) aligning to the standard/default pipeline, (c) running the
agent in the **same language ecosystem** as the ground truth, and (d) being
**decisive** instead of refusing.

---

## 1. What BixBench actually is

| Property | Value | Source |
|---|---|---|
| Questions | 205 | `profile_dataset.py` |
| Capsules (shared data + notebook) | 59 | " |
| Questions per capsule | 1–7 (mostly 4–5) | " |
| Numeric answers | **180/205 = 88%** | " |
| Top domains | Genomics 74, RNA-seq/Transcriptomics 69, Differential Expression 67, WGS 48, Phylogenetics 47, Imaging 21 | " |

Each capsule is a real paper's dataset + the analyst's executed notebook. The
agent gets the **data files only** (the notebook and any `.ipynb` are stripped
during loading — see `generate_trajectories.py::_extract_and_process_files`) and
must reconstruct the analysis from scratch.

## 2. The grading reality (this is the crux)

Grading is per-question via an `eval_mode` field. The three buckets behave
completely differently, and a good design must treat them differently:

| `eval_mode` | Share | How it grades | Answer shapes | Brittleness |
|---|---|---|---|---|
| `str_verifier` | **30%** (61) | strip non-alphanumerics, lowercase, **exact ==** | 60 numeric, 1 categorical | **Brutal.** `0.00020`≠`0.0002`, `2e-4`≠`0.0002` |
| `range_verifier` | **30%** (61) | `lower ≤ x ≤ upper` | 61 numeric ranges | **Forgiving** — rewards ballpark-correct analysis |
| `llm_verifier` | **40%** (83) | LLM judge: correct/incorrect/refused | 59 numeric + 24 categorical/phrase | tolerant of representation; adds judge variance + recall leak |

Concretely, `str_verifier` (from `graders.py`):
```python
cleaned_target    = re.sub(r"[^a-zA-Z0-9]", "", target).lower()
cleaned_predicted = re.sub(r"[^a-zA-Z0-9]", "", predicted).lower()
correct = cleaned_predicted == cleaned_target     # exact string equality
```
`0.0002` → `00002`; `0.00020` → `000020`; `2e-4` → `2e4`. None match each other.
**A numerically perfect answer in the wrong representation scores zero.**

**Design consequence:** ~30% of points (`str_verifier` numeric) are won or lost
on exact reproduction + representation; ~30% (`range_verifier`) reward robust
analysis and are the highest-value target; ~40% (`llm_verifier`) reward
semantically-grounded answers but leak to recall.

## 3. The recall ceiling — measured, not assumed

The repo ships "LLM baseline" CSVs: the model answering with **no notebook and no
data**. That measures how many points are pure recall. Measured by
`analysis/measure_recall_baseline.py`:

| Regime | Claude-3.5 | GPT-4o |
|---|---|---|
| Open-ended (with refusal option) | **11.5%** | 9.1% |
| MCQ **without** refusal option | **33.8%** | 32.1% |
| MCQ **with** refusal option | 13.9% | 10.8% |

Two things jump out:

- **MCQ leaks badly.** With no data at all, you already clear ~34% on MCQ
  (4–5 options, so well above the ~20–25% random line). A chunk of any MCQ
  "performance" is recall, not analysis. The BixBench paper's best *agentic*
  open-answer scores sit only in the high-teens % — i.e. the analytic lift over
  the recall floor is small, single-digit to ~10 points.
- **Refusal is a score sink.** Adding a refusal option roughly halves accuracy
  (34% → 14%) because the model bails under uncertainty. **In open-answer, never
  refuse**; in MCQ, refuse only when you truly have nothing.

Any improvement Genie claims must be reported **against these baselines**, or it
isn't measuring analysis.

## 4. Worked example: why `0.0002` is effectively unwinnable under `str_verifier`

Capsule `33b801bb` (RNA-seq → GO enrichment). Question `bix-1-q1`:
*"adjusted p-value for 'regulation of T cell activation'"*, ideal `0.0002`,
`eval_mode=str_verifier`, distractors `7.82e-05`, `0.0003`, `1.85e-05`.

`inspect_capsule.py` dumps the canonical notebook. To get **exactly** `0.0002`
the agent must independently reproduce *all* of these — none stated in the
question:

| Hidden choice in the ground-truth notebook | Why an agent misses it |
|---|---|
| Written in **R** with `DESeq2` + `clusterProfiler` | Agent runs Python; `pydeseq2`/`gseapy` give different numbers |
| **Drops samples `MGD1640B`, `MGD1641B`** ("alcohol use disorder", "valproic acid") | Clinical judgment in a *code comment*; unrecoverable |
| Design `~sex + condition` (sex as covariate) | Obvious choice is `~condition`; changes every p-value |
| `enrichGO(universe = all gencode genes)` (explicit background) | Default universe differs → enrichment p-values shift |
| `simplify(cutoff=0.7, by="p.adjust", select_fun=min)` | Whether the term survives + its value depend on these knobs |
| `org.Hs.eg.db` / DESeq2 v1.32 / R 4.1.1 (**versioned annotation**) | Re-running the *same code* today gives a different number |

And the distractors (`0.0002`, `7.82e-05`, `0.0003`, `1.85e-05`) look like the
**same GO term under different valid method variants** — so the "wrong" options
are themselves legitimate pipeline outputs. The question silently asks *"did you
make the identical arbitrary choices as this one analyst,"* not *"can you do the
biology."* Under exact-match this is a near-zero-probability event.

This generalizes: a notebook scan across capsules shows **R is the dominant
language**, several capsules ship **no notebook** (command-line phylogenetics /
variant calling), and undocumented exclusions/covariates recur.

## 5. Failure taxonomy (ranked by addressable impact)

1. **Representation / precision mismatch** — *analysis right, string wrong.*
   Hits the 60 numeric `str_verifier` questions hardest. Cheapest to fix
   (formatting + precision inference), independent of biology skill.
2. **Method divergence** — a *different valid* pipeline yields a different
   number; fails exact/range match. Fix: bias to standard defaults + run the
   same ecosystem (R) the ground truth used.
3. **Language/tooling gap** — Python agent vs R/Bioconductor (or CLI) ground
   truth. Structural; fix with environment parity.
4. **Recall contamination** — points winnable with no analysis (MCQ ~34%).
   Not a "fix," but the baseline every gain must beat; counter with
   evidence-grounding.
5. **Refusal under uncertainty** — halves the score the moment it's offered.
   Fix with a decisive answering policy.
6. **Long-horizon execution** — 40-step notebooks, env/install failures. Real
   but already partly mitigated (per-question envs, `hide_old_env_states`).
7. **Image/plot blindness** — agent runs with `avoid_images: true`; the 21
   imaging questions are answered blind. Fix: enable vision / image I/O.
8. **Judge variance** — `llm_verifier` noise on the 83 judged questions.

## 6. Per-task-type failure map (the actionable part)

| Task archetype | Typical `eval_mode` | Primary failure cause | Winnable? | Highest-ROI lever |
|---|---|---|---|---|
| Multi-step pipeline → one exact number (RNA-seq DE→GO, variant calling) | `str_verifier` | language gap + undocumented choices + versioned DBs | **Low** exact / **Med** as MCQ | R env + default-pipeline prior + **nearest-option** MCQ policy |
| Statistical estimate (odds ratio, regression coef, correlation) | `range_verifier` | wrong model spec / covariates — but range is forgiving | **High** | standard model, report point estimate + sanity-check sign/magnitude |
| Descriptive / extraction (counts, max, which group) | `str/llm` | data parsing & file-join errors | **High** | careful capsule-level EDA + a verification pass |
| Conceptual / hypothesis support | `llm_verifier` | recall leak (passes ungrounded) or judge noise | **Med** (easy points) | force evidence-grounded answers to beat the recall floor |
| Image-derived quantity | any | `avoid_images` → blind; needs image libs | **Lost → High** | enable vision + programmatic image I/O |

The strategic reading: **`range_verifier` + descriptive + conceptual questions
(~well over half the benchmark) are genuinely winnable with disciplined, standard
analysis.** The `str_verifier` pipeline numbers are where most effort is wasted
chasing an unreproducible exact value — there, the right move is to match the
*standard* method and, in MCQ, snap to the nearest option rather than exact-match.

→ The design that follows from these findings is in **`docs/DESIGN.md`**.
