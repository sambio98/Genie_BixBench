"""Phase-1 failure-attribution harness.

Labels each run so we know *why* points are lost and where the ceiling is per
grading bucket — the question FINDINGS.md raises but can't answer without
trajectories. Operates on the trajectory JSON that BixBench's
`generate_trajectories.py::store_trajectory` writes (one file per question with
`agent_answer`, `ideal_answer`, `num_actions`, `notebook_stats`, ...), joined to
the dataset for `eval_mode` and `distractors`.

Labels:
  correct_grounded        passed, with real notebook work
  correct_ungrounded      passed, but ~no analysis -> recall leak suspected
  representation_loss     numeric value ~correct, graded wrong (formatting)
  method_divergence       numeric, but a different number than the reference
  refusal                 agent refused / returned nothing
  judge_dependent         llm_verifier miss (needs human/LLM adjudication)
  analysis_or_parse_error non-numeric miss, not a refusal

Usage:
  python analysis/attribute_failures.py --trajectories DIR
  python analysis/attribute_failures.py --demo      # validate on synthetic runs
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from genie.answer import (  # noqa: E402
    extract_number,
    grade_range_verifier,
    grade_str_verifier,
    parse_option,
)

REPR_TOL = 0.02  # within 2% counts as "numerically the same value"
_REFUSAL = re.compile(
    r"\b(cannot|can't|unable|insufficient|not enough|no answer|i don't know|unknown|n/?a)\b",
    re.I,
)


def grade(eval_mode: str, target: str, predicted: str) -> bool:
    if eval_mode == "range_verifier":
        try:
            return grade_range_verifier(target, predicted)
        except (ValueError, SyntaxError):
            return False
    return grade_str_verifier(target, predicted)  # str_verifier (llm handled separately)


def is_grounded(traj: dict) -> bool:
    """Did the agent actually compute something? Cheap proxy from stored stats."""
    if traj.get("num_actions", 0) >= 3:
        return True
    stats = traj.get("notebook_stats") or {}
    return bool(stats.get("n_code_cells") or stats.get("total_outputs"))


def classify(traj: dict, eval_mode: str) -> str:
    target, pred = str(traj.get("ideal_answer", "")), str(traj.get("agent_answer", ""))
    if not pred.strip() or _REFUSAL.search(pred):
        return "refusal"

    if eval_mode == "llm_verifier":
        # We can't replicate the judge offline; flag for adjudication unless it's
        # a clearly-correct numeric match.
        tv, pv = extract_number(target), extract_number(pred)
        if tv is not None and pv is not None and abs(pv - tv) <= REPR_TOL * abs(tv or 1):
            return "correct_grounded" if is_grounded(traj) else "correct_ungrounded"
        return "judge_dependent"

    if grade(eval_mode, target, pred):
        return "correct_grounded" if is_grounded(traj) else "correct_ungrounded"

    # Wrong under the verifier — is the underlying number right?
    tv = parse_option(target).center
    pv = extract_number(pred)
    if tv is not None and pv is not None:
        if abs(pv - tv) <= REPR_TOL * abs(tv or 1):
            return "representation_loss"  # right value, wrong string/range edge
        return "method_divergence"  # a genuinely different number
    return "analysis_or_parse_error"


def load_eval_modes() -> dict[str, dict]:
    """Map question_id -> {eval_mode, ideal, distractors} from the HF dataset."""
    from datasets import load_dataset

    ds = load_dataset("futurehouse/BixBench", split="train")
    out = {}
    for r in ds:
        out[r["question_id"]] = {"eval_mode": r["eval_mode"], "ideal": r["ideal"]}
    return out


def qid_of(traj: dict) -> str:
    pid = str(traj.get("problem_id", ""))
    return re.sub(r"_replica_\d+$", "", pid)


def report(labels_by_mode: dict[str, Counter]) -> None:
    modes = sorted(labels_by_mode)
    all_labels = sorted({lbl for c in labels_by_mode.values() for lbl in c})
    total = sum(sum(c.values()) for c in labels_by_mode.values())
    print(f"\nAttribution over {total} runs:\n")
    width = max(len(l) for l in all_labels) + 2
    header = " " * width + "".join(f"{m.replace('_verifier',''):>14}" for m in modes) + f"{'TOTAL':>10}"
    print(header)
    for lbl in all_labels:
        row = lbl.ljust(width)
        tot = 0
        for m in modes:
            v = labels_by_mode[m][lbl]
            tot += v
            row += f"{v:>14}"
        print(row + f"{tot:>10}")
    # Headline: what fraction of *misses* is cheaply recoverable?
    misses = {k: sum(c[k] for c in labels_by_mode.values()) for k in all_labels}
    recoverable = misses.get("representation_loss", 0)
    miss_total = sum(v for k, v in misses.items() if not k.startswith("correct"))
    if miss_total:
        print(
            f"\n  representation_loss = {recoverable}/{miss_total} of misses "
            f"({recoverable / miss_total * 100:.0f}%) — recoverable by formatting alone."
        )


def demo() -> None:
    """Synthetic trajectories that exercise every label (validates the classifier)."""
    rows = {
        "q_str": {"eval_mode": "str_verifier", "ideal": "0.0002"},
        "q_rng": {"eval_mode": "range_verifier", "ideal": "(1.50,1.54)"},
        "q_llm": {"eval_mode": "llm_verifier", "ideal": "upregulated"},
    }
    trajs = [
        {"problem_id": "q_str", "ideal_answer": "0.0002", "agent_answer": "0.0002", "num_actions": 9},
        {"problem_id": "q_str", "ideal_answer": "0.0002", "agent_answer": "2.0e-4", "num_actions": 9},
        {"problem_id": "q_str", "ideal_answer": "0.0002", "agent_answer": "0.013", "num_actions": 9},
        {"problem_id": "q_str", "ideal_answer": "0.0002", "agent_answer": "I cannot determine this", "num_actions": 1},
        {"problem_id": "q_rng", "ideal_answer": "(1.50,1.54)", "agent_answer": "OR = 1.52", "num_actions": 12},
        {"problem_id": "q_str", "ideal_answer": "0.0002", "agent_answer": "0.0002", "num_actions": 0, "notebook_stats": {}},
        {"problem_id": "q_llm", "ideal_answer": "upregulated", "agent_answer": "the gene is downregulated", "num_actions": 5},
    ]
    run(trajs, rows)


def run(trajs: list[dict], rows: dict[str, dict]) -> None:
    by_mode: dict[str, Counter] = defaultdict(Counter)
    skipped = 0
    for t in trajs:
        meta = rows.get(qid_of(t))
        if not meta:
            skipped += 1
            continue
        by_mode[meta["eval_mode"]][classify(t, meta["eval_mode"])] += 1
    report(by_mode)
    if skipped:
        print(f"\n  ({skipped} trajectories had no matching dataset question_id)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trajectories", type=Path, help="dir of *.json trajectory files")
    ap.add_argument("--demo", action="store_true", help="run on synthetic trajectories")
    args = ap.parse_args()
    if args.demo or not args.trajectories:
        print("[demo mode — synthetic trajectories]")
        demo()
        return
    trajs = [json.loads(p.read_text()) for p in sorted(args.trajectories.glob("*.json"))]
    run(trajs, load_eval_modes())


if __name__ == "__main__":
    main()
