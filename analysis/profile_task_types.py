"""Profile the *task types* in BixBench across every axis of variation.

BixBench is one benchmark (agentic data analysis), but its tasks vary along
several axes. This enumerates them with live counts. Run:

    python analysis/profile_task_types.py                # axes 3-5 (fast, dataset only)
    python analysis/profile_task_types.py --scan-ecosystem 22   # axis 6 (downloads N capsules)

See docs/TASK_TYPES.md for the writeup.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from datasets import load_dataset

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from genie.router import route  # noqa: E402

HF = "futurehouse/BixBench"


def answer_types(ds) -> None:
    print("\n=== AXIS: answer/question type (router, MCQ framing) ===")
    shapes, by_mode = Counter(), {}
    for r in ds:
        d = route(r["question"], [r["ideal"], *list(r["distractors"])])
        shapes[d.shape] += 1
        by_mode.setdefault(r["eval_mode"], Counter())[d.shape] += 1
    n = len(ds)
    for s, c in shapes.most_common():
        print(f"  {s:12s} {c:4d}  ({c / n * 100:.0f}%)")
    modes = ["str_verifier", "range_verifier", "llm_verifier"]
    print("\n  answer-type x grading-mode:")
    print(f"  {'shape':12s}" + "".join(f"{m.replace('_verifier', ''):>9}" for m in modes))
    for s, _ in shapes.most_common():
        print(f"  {s:12s}" + "".join(f"{by_mode.get(m, Counter()).get(s, 0):>9}" for m in modes))


def grading_types(ds) -> None:
    print("=== AXIS: grading type (eval_mode) ===")
    for m, c in Counter(ds["eval_mode"]).most_common():
        print(f"  {m:16s} {c:4d}  ({c / len(ds) * 100:.0f}%)")


def domains(ds) -> None:
    print("\n=== AXIS: scientific domain (multi-label) ===")
    cats: Counter = Counter()
    for c in ds["categories"]:
        for t in str(c).replace("[", "").replace("]", "").replace("'", "").split(","):
            if t.strip():
                cats[t.strip()] += 1
    for k, v in cats.most_common(12):
        print(f"  {k:42s} {v}")


def regimes_and_formats(ds) -> None:
    print("=== AXIS: evaluation regime ('Q&A vs code-running') ===")
    print("  1. agentic / code-execution  (the real benchmark: agent runs notebook code)")
    print("  2. zero-shot Q&A baseline    (answer from knowledge, no data/code)")
    print("     -> same 205 questions; the gap measures the value of analysis")
    print("\n=== AXIS: delivery format (paper's eval configs) ===")
    mcqable = sum(1 for r in ds if r["distractors"])
    print(f"  all {mcqable}/{len(ds)} questions ship distractors -> posable as either:")
    print("    - open-answer (free text)")
    print("    - MCQ with refusal option")
    print("    - MCQ without refusal option      (x with/without images)")


def scan_ecosystem(n: int) -> None:
    """Axis 6: language/ecosystem of the canonical analysis (downloads N capsules)."""
    import io
    import json
    import zipfile

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    info = api.repo_info(HF, repo_type="dataset", files_metadata=True)
    size = {s.rfilename: s.size for s in info.siblings}
    ds = load_dataset(HF, split="train")
    folders = sorted({r["data_folder"] for r in ds}, key=lambda f: size.get(f, 0))[:n]
    eco: Counter = Counter()
    for f in folders:
        p = hf_hub_download(HF, f, repo_type="dataset", local_dir="/tmp/bix_eco")
        with zipfile.ZipFile(p) as z:
            nbs = [x for x in z.namelist() if x.endswith(".ipynb")]
            if not nbs:
                eco["command-line (no notebook)"] += 1
            else:
                ks = json.load(io.TextIOWrapper(z.open(nbs[0]))).get("metadata", {}).get("kernelspec", {})
                eco[{"ir": "R", "python3": "Python"}.get(ks.get("name", ""), ks.get("name", "?"))] += 1
        Path(p).unlink()
    print(f"\n=== AXIS: canonical-analysis ecosystem (smallest {n} capsules) ===")
    for k, v in eco.most_common():
        print(f"  {k:28s} {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan-ecosystem", type=int, metavar="N", help="download N capsules to profile language")
    args = ap.parse_args()
    ds = load_dataset(HF, split="train")
    print(f"BixBench: {len(ds)} questions, {len(set(ds['capsule_uuid']))} capsules\n")
    regimes_and_formats(ds)
    print()
    grading_types(ds)
    answer_types(ds)
    domains(ds)
    if args.scan_ecosystem:
        scan_ecosystem(args.scan_ecosystem)


if __name__ == "__main__":
    main()
