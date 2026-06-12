"""Quantify, on the real dataset, how much the answer module recovers.

Two simulations over `futurehouse/BixBench`:

1. REPRESENTATION BRITTLENESS — take each `str_verifier` numeric target, assume
   the agent computed the *exact* true value, and express it the natural ways a
   correct analysis would (scientific, N significant figures, trailing zeros).
   Measure how often exact-string grading still passes. Isolates pure
   representation loss, no numeric error.

2. MCQ NEAREST-OPTION RECOVERY — model an "approximately-correct analyst" whose
   computed value is the truth +/- noise, then compare two MCQ strategies:
   naive exact-string match vs `snap_to_nearest_option`. Shows how many points
   the decisive nearest-option policy converts from near-misses into hits.

Both are clearly-labeled simulations grounded in the real targets/distractors.
Run: python analysis/grading_sensitivity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from datasets import load_dataset

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from genie.answer import (  # noqa: E402
    extract_number,
    grade_str_verifier,
    parse_option,
    snap_to_nearest_option,
)

HF_REPO = "futurehouse/BixBench"


def natural_representations(v: float) -> list[str]:
    """Strings a correct analysis might emit for the exact value `v`."""
    reps = [
        f"{v:.2e}", f"{v:.3e}",  # scientific
        f"{v:g}",                 # general
        repr(v),                  # full float
        f"{v:.2g}", f"{v:.3g}",  # 2-3 sig figs
    ]
    if abs(v) < 1:  # decimal expansions for small numbers
        reps += [f"{v:.4f}", f"{v:.5f}", f"{v:.6f}"]
    return reps


def representation_brittleness(ds) -> None:
    print("=" * 68)
    print("1. REPRESENTATION BRITTLENESS (str_verifier, exact value assumed)")
    print("=" * 68)
    targets = [
        r["ideal"]
        for r in ds
        if r["eval_mode"] == "str_verifier" and extract_number(r["ideal"]) is not None
    ]
    pass_counts = []
    any_pass = 0
    for t in targets:
        v = extract_number(t)
        reps = natural_representations(v)
        passed = sum(grade_str_verifier(t, rep) for rep in reps)
        pass_counts.append(passed / len(reps))
        any_pass += passed > 0
    n = len(targets)
    avg = sum(pass_counts) / n * 100
    print(f"  numeric str_verifier targets: {n}")
    print(f"  avg fraction of natural representations that pass exact-match: {avg:.0f}%")
    print(f"  targets where >=1 natural representation passes:               {any_pass}/{n}")
    print("  -> a correct value, expressed naturally, usually FAILS unless the")
    print("     agent happens to match the analyst's exact formatting.")


def mcq_recovery(ds) -> None:
    print("\n" + "=" * 68)
    print("2. MCQ NEAREST-OPTION RECOVERY (approximately-correct analyst)")
    print("=" * 68)
    # Build MCQ items: options = ideal + distractors; truth = ideal's value.
    items = []
    for r in ds:
        opts = [r["ideal"], *list(r["distractors"])]
        ideal_opt = parse_option(r["ideal"])
        truth = ideal_opt.center
        if truth is None or not any(parse_option(o).is_numeric for o in opts):
            continue
        items.append((r["ideal"], opts, truth))
    print(f"  numeric MCQ items: {len(items)}\n")
    print(f"  {'noise':>7} | {'naive exact-str':>16} | {'nearest-option':>15}")
    print(f"  {'-' * 7} | {'-' * 16} | {'-' * 15}")
    for noise in (0.0, 0.01, 0.05, 0.10):
        naive_hits = snap_hits = 0
        for ideal, opts, truth in items:
            v_est = truth * (1 + noise)
            # naive: format to 3 sig figs and require exact string match to an option
            naive = any(grade_str_verifier(o, f"{v_est:.3g}") for o in opts)
            naive_hits += naive
            # decisive: snap to nearest option, check it's the correct one
            snap_hits += snap_to_nearest_option(v_est, opts) == ideal
        na = naive_hits / len(items) * 100
        sn = snap_hits / len(items) * 100
        print(f"  {noise * 100:5.0f}%  | {na:14.0f}%  | {sn:13.0f}%")
    print("\n  -> nearest-option turns an 'approximately-correct' analysis into")
    print("     MCQ points; naive exact-match throws nearly all of them away.")


def main() -> None:
    ds = load_dataset(HF_REPO, split="train")
    representation_brittleness(ds)
    mcq_recovery(ds)


if __name__ == "__main__":
    main()
