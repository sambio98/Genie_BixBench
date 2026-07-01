"""Grade code_agent outputs against BixBench's real per-question eval_mode.

Extracts <solution>...</solution> (their actual emission-floor convention --
see provider_failover.solution_floor_message) from each run's raw output, then
grades:

  - str_verifier / range_verifier: exact deterministic replicas of BixBench's
    own graders (bixbench/graders.py), reused from genie/answer.py -- built and
    unit-tested earlier this session against real BixBench targets/distractors.
  - llm_verifier: needs semantic judgment. Rows needing it are flagged for a
    single batched judge call (see judge_llm_verifier below) rather than N
    separate ones.

Usage (as a library, called from the harness driver):
    from grade import extract_solution, grade_row
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from genie.answer import grade_range_verifier, grade_str_verifier  # noqa: E402

_SOLUTION_RE = re.compile(r"<solution>\s*(.*?)\s*</solution>", re.DOTALL | re.IGNORECASE)


def extract_solution(raw_output: str) -> str | None:
    """Pull the <solution> tag content BixBench's grader reads.

    Returns None if no tag is present -- production treats a tagless turn as a
    hard zero (see chat_handler.md: "BixBench scores a tagless turn as a hard
    noans"); the harness should score it the same way, not silently skip it.
    """
    m = _SOLUTION_RE.search(raw_output)
    return m.group(1).strip() if m else None


def grade_row(row: dict, raw_output: str) -> dict:
    """Grade one question. Returns row + {solution, correct, needs_llm_judge}."""
    solution = extract_solution(raw_output)
    result = {**row, "solution": solution, "correct": None, "needs_llm_judge": False}

    if solution is None:
        result["correct"] = False
        result["grade_note"] = "no <solution> tag emitted (hard zero, matches production)"
        return result

    mode = row["eval_mode"]
    if mode == "str_verifier":
        result["correct"] = grade_str_verifier(row["ideal"], solution)
    elif mode == "range_verifier":
        try:
            result["correct"] = grade_range_verifier(row["ideal"], solution)
        except (ValueError, SyntaxError):
            result["correct"] = False
            result["grade_note"] = "range_verifier: could not parse ideal or solution as numeric"
    elif mode == "llm_verifier":
        result["needs_llm_judge"] = True
    else:
        raise ValueError(f"unknown eval_mode: {mode}")
    return result


def build_judge_prompt(pending: list[dict]) -> str:
    """One batched prompt judging all llm_verifier rows in a single call.

    Mirrors BixBench's own OPEN_ENDED_EVAL_PROMPT contract (binary correct/
    incorrect vs the reference), batched for efficiency rather than one Agent
    call per row.
    """
    lines = [
        "You are grading answers to bioinformatics analysis questions, exactly as "
        "the BixBench benchmark's LLM verifier does: judge whether the PREDICTED "
        "answer is semantically equivalent to the REFERENCE answer for each "
        "question. Output ONLY a fenced block with one line per item in the exact "
        "form `<id>: correct` or `<id>: incorrect` -- no other text.",
        "",
    ]
    for row in pending:
        lines.append(f"### {row['question_id']}")
        lines.append(f"Question: {row['question']}")
        lines.append(f"Reference answer: {row['ideal']}")
        lines.append(f"Predicted answer: {row['solution']}")
        lines.append("")
    return "\n".join(lines)


def apply_judge_verdicts(graded: list[dict], judge_text: str) -> list[dict]:
    """Parse `<id>: correct|incorrect` lines and fill in judged rows' `correct`."""
    verdicts: dict[str, bool] = {}
    for line in judge_text.splitlines():
        m = re.match(r"\s*([\w-]+)\s*:\s*(correct|incorrect)\s*$", line.strip(), re.IGNORECASE)
        if m:
            verdicts[m.group(1)] = m.group(2).lower() == "correct"
    for row in graded:
        if row["needs_llm_judge"]:
            if row["question_id"] not in verdicts:
                raise ValueError(f"judge did not verdict {row['question_id']}")
            row["correct"] = verdicts[row["question_id"]]
    return graded


def summarize(graded: list[dict]) -> None:
    from collections import Counter

    n = len(graded)
    correct = sum(1 for r in graded if r["correct"])
    print(f"\n{'=' * 60}\nOVERALL: {correct}/{n} = {correct / n * 100:.1f}%\n{'=' * 60}")

    by_mode: dict[str, list[bool]] = {}
    for r in graded:
        by_mode.setdefault(r["eval_mode"], []).append(r["correct"])
    print("\nBy eval_mode:")
    for mode, vals in sorted(by_mode.items()):
        c = sum(vals)
        print(f"  {mode:16s} {c}/{len(vals)} = {c / len(vals) * 100:.0f}%")

    no_solution = sum(1 for r in graded if r["solution"] is None)
    na_solution = sum(1 for r in graded if r["solution"] == "NA")
    print(f"\nNo <solution> tag (hard zero): {no_solution}/{n}")
    print(f"Explicit NA: {na_solution}/{n}")

    print("\nPer-question:")
    for r in graded:
        mark = "PASS" if r["correct"] else "FAIL"
        sol = (r["solution"] or "(none)")[:50]
        print(f"  [{mark}] {r['question_id']:12s} ({r['eval_mode']:14s}) sol={sol!r}")
