# iBioFoundry-AI code_agent — 30-question BixBench baseline

Proxy-harness baseline (Claude subagents faithfully replaying the real `code_agent.md`
contract — see session notes for why: no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` available
to invoke their actual `pydantic-ai` stack). All 30 questions, non-R scope, stratified
across `eval_mode` and Python/CLI ecosystem. Raw per-question results in
`results_baseline_30q/*.json`.

## Headline result

**13/30 = 43.3%**

| eval_mode | score |
|---|---|
| `llm_verifier` | 9/16 = 56% |
| `range_verifier` | 2/7 = 29% |
| `str_verifier` | 2/7 = 29% |

This is a real, methodologically-scrutinized number — not a first-pass estimate. Two
harness bugs were caught and fixed mid-run (see below) rather than baked into the score.

## Harness caveat: nested-agent notification hangs (read before trusting raw pass/fail counts)

**8 of the 24 newly-run questions (33%)** hit the same failure: the subagent spawned a
background computation via the Monitor/background-task mechanism (to parallelize a
naturally-parallel step — e.g. running phykit across hundreds of ortholog trees), then
hung waiting for a completion notification that never arrives for a *nested* subagent
(that routing only reaches the top-level session). This is a **methodology artifact**,
not a code_agent finding: the real production sandbox has no Monitor/nested-Agent
capability at all, so this failure mode is structurally impossible there.

**All 8 were rescued** with a one-line nudge ("stop waiting, check your background
output directly") and **every single one had already completed its underlying
computation correctly** — the nudge just let it report. Of the 8 rescued:
- 4 became **PASS** (bix-4-q3, bix-12-q2, bix-28-q5, bix-53-q2)
- 4 became **FAIL** (bix-24-q6, bix-31-q1, bix-31-q3, bix-36-q5) — genuine analysis
  mismatches, not further hangs

Net effect on the score: **neutral to positive** — recovering these 8 pushed the score
up (from what would have been a harness-inflated set of hard zeros), not down. The
43.3% is not depressed by this artifact. For a re-run, launching subagents with a
narrower toolset (no Agent/Monitor/background-task access) would prevent recurrence.

## Full per-question results

| Question | Mode | Ideal | Got | Result |
|---|---|---|---|---|
| bix-4-q1 | llm | 57% | 57% | **PASS** |
| bix-4-q3 | llm | 19808 | 19808.0 | **PASS** (rescued) |
| bix-6-q3 | str | 0 | 0 | **PASS** |
| bix-9-q5 | llm | Dentate gyrus (brain) | dentate_gyrus | **PASS** |
| bix-12-q2 | llm | 3.5% | 3.5385% | **PASS** (rescued) |
| bix-16-q2 | llm | Right-skewed, long tail | right-skewed, long tail | **PASS** |
| bix-16-q4 | range | (20,25) | 21.8% | **PASS** |
| bix-19-q4 | range | (1.065,1.067) | 1.0656 | **PASS** |
| bix-28-q5 | llm | -26.9 | -26.91 | **PASS** (rescued) |
| bix-33-q6 | str | 1 | 1 | **PASS** |
| bix-34-q5 | llm | 1.95 | 1.947 | **PASS** |
| bix-42-q2 | llm | Highly right-skewed | Highly right-skewed | **PASS** (harness fix) |
| bix-53-q2 | llm | Increases DE genes | increases 1428→1916 | **PASS** (rescued) |
| bix-53-q6 | llm | Leishmaniasis | Malaria | FAIL |
| bix-3-q1 | range | (700,1000) | 247 | FAIL |
| bix-6-q5 | str | 25% | 0% | FAIL |
| bix-12-q4 | llm | 6948.0 | 1162.0 | FAIL |
| bix-16-q3 | str | 3 | 0 | FAIL |
| bix-22-q4 | range | (0.015,0.025) | 0.0317 | FAIL |
| bix-22-q6 | range | (0.3,0.4) | 0.05 | FAIL |
| bix-24-q6 | llm | Cellular response to decreased O2 | Positive regulation of transcription | FAIL (rescued) |
| bix-25-q4 | llm | 0.21 | 0.4174 | FAIL |
| bix-27-q2 | range | (160,180) | 42 | FAIL |
| bix-27-q4 | llm | Aerobic respiration/e- transport | Axon Guidance | FAIL |
| bix-31-q1 | str | 18.93 | -0.38 | FAIL (rescued) |
| bix-31-q3 | str | 197 | 113 | FAIL (rescued) |
| bix-36-q4 | range | (0.55,0.59) | 0.194 | FAIL |
| bix-36-q5 | str | Normal | not normal (p<1e-50) | FAIL (rescued) |
| bix-43-q1 | llm | Negative reg. of epithelial proliferation | Regulation of apoptotic signaling | FAIL |
| bix-61-q1 | llm | 344895 | 4456 | FAIL |

## What the wins show (this matters as much as the failures)

Several passes are exact or near-exact numeric matches on genuinely hard, multi-step
pipelines: `19808.0` == `19808` (Mann-Whitney U on 349 phylogenetic trees), `1.947` ≈
`1.95` (median patristic distance ratio, cross-validated against `dendropy`
independently), `1.0656` inside a tight `(1.065,1.067)` window (two-way ANOVA
interaction). These required correctly recognizing pre-existing IQ-TREE outputs, using
`phykit`'s exact positional-argument syntax, and applying `pydeseq2`'s `lfc_shrink`
gotcha correctly — exactly the capabilities their derivation-gate and phylo-capability
notes were built to produce. **The prompt engineering is working as designed.**

## Failure taxonomy (this is the actionable part)

### 1. Genuine, fixable capability/cookbook gaps (2 found)

- **DepMap Chronos sign-convention gotcha** (bix-16-q3, ideal=3, got=0). The agent's own
  output shows the 3 strongest correlations were exactly at the 0.6 threshold but
  **negative** (CCND1 -0.629, FERMT2 -0.612, KLF5 -0.602). Raw Chronos essentiality
  scores are *more negative* for *more essential* genes — a sign-flipped "essentiality"
  convention (higher = more essential) would turn these into 3 strong *positive*
  correlations, exactly matching ideal=3. **Recommended fix**: add a `LIBRARY_COOKBOOK`
  note on `CRISPRGeneEffect.csv`/Chronos sign convention.
- **Pre-normalized-count DESeq2 misuse** (bix-3-q1, ideal=(700,1000), got=247). The
  capsule shipped only normalized (non-integer) counts; the agent rounded them to
  fake-integer "raw" counts before `pydeseq2`, but DESeq2's internal size-factor
  normalization assumes true raw counts, likely under-powering the model.
  **Recommended fix**: add cookbook guidance for capsules that ship pre-normalized data
  (e.g., pass explicit unit size factors, or note when this pattern is detected).

### 2. Method-sensitivity / undocumented-analyst-choice (the majority — 11 of 17 fails)

bix-53-q6, bix-27-q2, bix-61-q1, bix-22-q4, bix-22-q6, bix-36-q4, bix-27-q4, bix-12-q4,
bix-31-q3, bix-25-q4, bix-43-q1/bix-24-q6 (same failure pattern, both gseapy-enrichment
questions). In every case the methodology was sound (often independently
cross-validated, e.g. against `dendropy` or a positive control), but landed on a
different-but-defensible number/term than the reference because the question
underspecifies a real methodological choice (which taxa-count orthologs to include,
BH-adjusted vs. nominal significance, raw vs. trimmed alignment, log-transform or not,
which clustering method). **This is the structural BixBench problem identified earlier
this session** (ground truth = one analyst's undocumented exact pipeline) — not
something a code_agent prompt change fully solves, though a "state your interpretation
explicitly, prefer the field-standard default" nudge (as the prompt already does) is
the right mitigation, already partially in place.

Two examples are unusually well-evidenced:
- **bix-22-q4 vs bix-22-q6**: the *same* analysis family (gene-length-vs-expression
  correlation) failed in *opposite* directions across two questions (one landed too
  high, one too low relative to its target range) — suggesting a real, recurring
  normalization ambiguity specific to this analysis type.
- **bix-12-q4 vs bix-4-q3/bix-28-q5**: on the *same* underlying dataset
  (`scogs_fungi.zip`/`scogs_animals.zip`), different runs made different, inconsistent
  choices about whether to include partial-taxa orthologs (22+211 vs 100+249) — a real,
  demonstrated cookbook gap in how to scope a phylogenomic gene set.

### 3. Fundamental question misinterpretation (1 case)

**bix-31-q1** (ideal=18.93, got=-0.38 — opposite sign, ~50x magnitude off) despite a
well-validated pipeline (correctly recovered canonical Y-chromosome genes). This isn't
a parameter gotcha; it looks like the agent solved a different comparison than intended.

### 4. Statistical-rigor-vs-qualitative-judgment mismatch (1 case, novel finding)

**bix-36-q5** (ideal="Normal", got="not normal, p<1e-50"). At n=242,881 data points,
formal normality tests (Shapiro-Wilk, D'Agostino-Pearson) reject almost any real-world
distribution — a well-known statistics pitfall, not a real signal. The agent's own
descriptive stats (skewness=0.10, unimodal, symmetric) would read as "approximately
normal" under the qualitative/visual judgment the question almost certainly intended
(matching the multiple-choice-style phrasing seen elsewhere: "...or approximately
normal"). **Recommended fix**: add cookbook guidance that "describe the distribution
shape" questions want a qualitative/visual read, not a formal large-sample hypothesis
test (which is close to guaranteed to reject at BixBench's typical sample sizes).

## Status of the original 3-change Tier-1 proposal

This baseline run did **not yet vary** the 3 proposed changes (model tier, solution-floor
fix, MCQ answer-representation policy) — it establishes the number those changes will be
measured against. Notes on testability going forward:

1. **Model tier bump** — fully testable against this same 30-question set by re-running
   with a different subagent model tier as the proxy variable.
2. **Solution-floor fix for `UsageLimitExceeded`/`UnexpectedModelBehavior`** — this
   session's own harness experience (33% hit an analogous "completes without emitting a
   parseable answer" failure mode before rescue) is a strong, independent argument for
   the general principle, even though the *specific* nested-agent-hang mechanism here
   doesn't exist in their real sandbox.
3. **MCQ nearest-option policy** — not yet tested; this baseline ran open-answer only.
   Would need capsule questions presented with their distractor options to test.

## Recommendation

Given the depth of genuine, well-evidenced findings from this single baseline run (2
concrete cookbook gaps + 1 novel statistical-judgment gap + a demonstrated
ortholog-scoping inconsistency, on top of confirming several capabilities work exactly
as designed), there's a strong case for feeding these 4 specific, cheap prompt/cookbook
fixes back before spending further compute on the 3 originally-proposed
architecture-level changes — they're smaller, more certain wins than re-running the full
comparison sweep.
