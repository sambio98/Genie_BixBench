"""Profile the BixBench dataset to ground the failure analysis in real numbers.

Reproduces every quantitative claim in docs/FINDINGS.md. Run:

    pip install datasets huggingface_hub
    python analysis/profile_dataset.py

All numbers are computed live from `futurehouse/BixBench` (HF, 205 questions).
No HF token required (public dataset), though one raises the rate limit.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from datasets import load_dataset

HF_REPO = "futurehouse/BixBench"


def classify_answer(target: str) -> str:
    """Coarse answer-shape classifier used for the brittleness histogram."""
    t = str(target).strip()
    if re.fullmatch(r"[-+]?\d*\.?\d+([eE][-+]?\d+)?", t):
        return "pure-number"
    if re.fullmatch(r"\(\s*[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*\)", t):
        return "range-tuple"
    if re.search(r"\d", t) and len(t.split()) <= 3:
        return "number+unit/short"
    if len(t.split()) <= 4:
        return "short-categorical"
    return "phrase/descriptive"


def main() -> None:
    ds = load_dataset(HF_REPO, split="train")
    n = len(ds)
    print(f"BixBench: {n} questions | columns: {ds.column_names}\n")

    # --- Grading mode: this is the single most important structural fact. -----
    print("=== eval_mode distribution (how each question is GRADED) ===")
    for mode, c in Counter(ds["eval_mode"]).most_common():
        print(f"  {mode:16s} {c:4d}  ({c / n * 100:.0f}%)")

    # --- str_verifier is the brittle bucket: exact string match. -------------
    print("\n=== answer shapes WITHIN each eval_mode ===")
    by_mode: dict[str, Counter] = defaultdict(Counter)
    for r in ds:
        by_mode[r["eval_mode"]][classify_answer(r["ideal"])] += 1
    for mode in ("str_verifier", "range_verifier", "llm_verifier"):
        print(f"  [{mode}]")
        for shape, c in by_mode[mode].most_common():
            print(f"      {shape:20s} {c:4d}")

    # --- Capsule structure: questions share data; EDA can be amortized. -------
    per_capsule = Counter(ds["capsule_uuid"])
    print(f"\n=== capsules: {len(per_capsule)} | questions/capsule histogram ===")
    for k, v in sorted(Counter(per_capsule.values()).items()):
        print(f"  {k} question(s): {v} capsules")

    # --- Domain mix: where the difficulty concentrates. ----------------------
    print("\n=== top categories ===")
    cats: Counter = Counter()
    for c in ds["categories"]:
        for t in re.split(r"[,\[\]']", str(c)):
            t = t.strip()
            if t:
                cats[t] += 1
    for k, v in cats.most_common(12):
        print(f"  {k:48s} {v}")

    # --- Overall numeric dominance (the representation problem). --------------
    shapes = Counter(classify_answer(r["ideal"]) for r in ds)
    numeric = sum(v for k, v in shapes.items() if "number" in k or k == "range-tuple")
    print(f"\n=== numeric-answer dominance: {numeric}/{n} ({numeric / n * 100:.0f}%) ===")
    for k, v in shapes.most_common():
        print(f"  {k:20s} {v:4d}")


if __name__ == "__main__":
    main()
