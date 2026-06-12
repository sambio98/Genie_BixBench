"""Measure the BixBench *recall ceiling*: accuracy with no notebook, no data.

These are the "free points" any agentic score sits on top of. Numbers come from
the baseline CSVs shipped in the BixBench repo (model answers the question from
parametric knowledge alone). Run:

    python analysis/measure_recall_baseline.py

Pulls the CSVs from raw GitHub so it is self-contained.
"""

from __future__ import annotations

import csv
import io
import urllib.request

RAW = (
    "https://raw.githubusercontent.com/Future-House/BixBench/main/"
    "bixbench_results/baseline_eval_data/"
)
FILES = {
    "open-ended (refusal)  claude": "bixbench_llm_baseline_refusal_True_openended_claude-3-5-sonnet-latest_1.0.csv",
    "open-ended (refusal)  gpt-4o": "bixbench_llm_baseline_refusal_True_openended_gpt-4o_1.0.csv",
    "MCQ no-refusal        claude": "bixbench_llm_baseline_refusal_False_mcq_claude-3-5-sonnet-latest_1.0.csv",
    "MCQ no-refusal        gpt-4o": "bixbench_llm_baseline_refusal_False_mcq_gpt-4o_1.0.csv",
    "MCQ with-refusal      claude": "bixbench_llm_baseline_refusal_True_mcq_claude-3-5-sonnet-latest_1.0.csv",
    "MCQ with-refusal      gpt-4o": "bixbench_llm_baseline_refusal_True_mcq_gpt-4o_1.0.csv",
}


def accuracy(url: str) -> tuple[int, int]:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 (trusted host)
        rows = list(csv.DictReader(io.TextIOWrapper(resp, "utf-8")))
    correct = sum(1 for r in rows if str(r.get("correct", "")).strip().lower() == "true")
    return correct, len(rows)


def main() -> None:
    print("Recall ceiling (no notebook, no data) — every agentic gain must beat this:\n")
    print(f"  {'regime':30s} {'acc':>7}   n")
    for label, fn in FILES.items():
        c, n = accuracy(RAW + fn)
        print(f"  {label:30s} {c / n * 100:6.1f}%  ({c}/{n})")
    print(
        "\nTakeaways: MCQ leaks (~34% with zero analysis); a refusal option "
        "halves accuracy. Design implications in docs/FINDINGS.md."
    )


if __name__ == "__main__":
    main()
