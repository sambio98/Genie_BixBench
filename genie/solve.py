"""End-to-end per-question runner (DESIGN.md — the orchestration glue).

Assembles the built components into the loop a real model (or the BixBench
runtime) plugs into:

    route the question  ->  (capsule EDA manifest, cached per capsule)
        ->  Analyst.analyze(...)        # the one part that needs an LLM + sandbox
        ->  verify (is the answer grounded?)
        ->  answer policy (representation match / nearest-option / never refuse)

The `Analyst` is a Protocol so the analysis step is swappable. Two analysts ship
here for exercising the loop without an LLM:
  - `CallableAnalyst` wraps any function (unit tests).
  - `OracleAnalyst` returns the reference value +/- noise (clearly a test double)
    so we can run the *whole assembled system* and confirm it converts an
    approximately-correct analysis into points — the DESIGN claim, end to end.

A real `LLMAnalyst` (notebook execution + model) is the Phase-2 follow-up; it
implements the same `analyze` signature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from genie.answer import (
    choose_mcq,
    extract_number,
    format_numeric,
    grade_range_verifier,
    grade_str_verifier,
)
from genie.eda import CapsuleManifest, build_manifest
from genie.router import AnswerShape, RouteDecision, route


# --- Data model ------------------------------------------------------------


@dataclass
class Question:
    question: str
    question_id: str = ""
    options: list[str] | None = None      # MCQ choices (incl. the correct one); None = open
    refusal_option: str | None = None
    data_dir: str | None = None           # capsule data folder (for the manifest)
    capsule_uuid: str = ""
    # Optional ground truth, only used for offline grading/eval.
    ideal: str | None = None
    eval_mode: str | None = None


@dataclass
class AnalysisResult:
    """What an Analyst returns. `value` is numeric or a label; evidence grounds it."""

    value: float | str | None
    evidence: str | None = None    # the cell output / computed result supporting it
    code: str | None = None        # code the analyst ran (kept for the trajectory)
    confidence: float = 0.5
    error: str | None = None


@dataclass
class Solution:
    question_id: str
    final_answer: str
    shape: AnswerShape
    grounded: bool
    value: float | str | None
    evidence: str | None = None
    manifest: CapsuleManifest | None = None
    is_mcq: bool = False


# --- Analyst interface + test doubles --------------------------------------


class Analyst(Protocol):
    def analyze(
        self, question: Question, manifest: CapsuleManifest | None, decision: RouteDecision
    ) -> AnalysisResult: ...


@dataclass
class CallableAnalyst:
    """Wrap a plain function `(question, manifest, decision) -> AnalysisResult`."""

    fn: Callable[[Question, CapsuleManifest | None, RouteDecision], AnalysisResult]

    def analyze(self, question, manifest, decision) -> AnalysisResult:
        return self.fn(question, manifest, decision)


@dataclass
class OracleAnalyst:
    """TEST DOUBLE: returns the reference value +/- `noise` (relative).

    Not a real analyst — it reads `question.ideal` — but it lets us drive the
    full pipeline to validate that an *approximately-correct* analysis is turned
    into points by the answer policy.
    """

    noise: float = 0.0

    def analyze(self, question, manifest, decision) -> AnalysisResult:
        truth = _reference_value(question.ideal)
        if truth is None:
            return AnalysisResult(value=question.ideal, evidence="oracle:text")
        return AnalysisResult(value=truth * (1 + self.noise), evidence="oracle:numeric")


# --- Verifier --------------------------------------------------------------


def verify(result: AnalysisResult) -> bool:
    """Grounded = produced a value, with supporting evidence, no error.

    The real verifier re-derives the value from notebook outputs; this is the
    minimal contract that keeps ungrounded/hallucinated answers from counting.
    """
    return result.error is None and result.value is not None and bool(result.evidence)


# --- Manifest cache (DESIGN §2: build once per capsule) ---------------------


class ManifestCache:
    def __init__(self) -> None:
        self._cache: dict[str, CapsuleManifest] = {}

    def get(self, data_dir: str | None) -> CapsuleManifest | None:
        if not data_dir:
            return None
        if data_dir not in self._cache:
            self._cache[data_dir] = build_manifest(data_dir)
        return self._cache[data_dir]


# --- Core loop -------------------------------------------------------------


def _final_answer(value, decision: RouteDecision, q: Question) -> str:
    """Apply the answer policy: nearest-option for MCQ, representation for open."""
    numeric = value if isinstance(value, (int, float)) else extract_number(str(value))
    if q.options:  # MCQ: snap to nearest option; refuse only if no value at all
        return choose_mcq(numeric, q.options, refusal_option=q.refusal_option)
    if decision.is_numeric and numeric is not None:  # open numeric
        return format_numeric(numeric, decision.representation, decision.sig_figs)
    return "" if value is None else str(value)  # open text; never a refusal phrase


def solve(q: Question, analyst: Analyst, cache: ManifestCache | None = None) -> Solution:
    cache = cache or ManifestCache()
    manifest = cache.get(q.data_dir)
    decision = route(q.question, q.options)
    result = analyst.analyze(q, manifest, decision)
    grounded = verify(result)
    final = _final_answer(result.value, decision, q)
    return Solution(
        question_id=q.question_id,
        final_answer=final,
        shape=decision.shape,
        grounded=grounded,
        value=result.value,
        evidence=result.evidence,
        manifest=manifest,
        is_mcq=bool(q.options),
    )


def run(questions: list[Question], analyst: Analyst) -> list[Solution]:
    """Solve a batch, reusing one manifest per capsule (data_dir)."""
    cache = ManifestCache()
    return [solve(q, analyst, cache) for q in questions]


# --- Offline grading (when ground truth is present) ------------------------


def grade_solution(sol: Solution, ideal: str, eval_mode: str) -> bool | None:
    """Grade with the faithful replicas. Returns None for llm_verifier (needs a judge).

    MCQ correctness is "did we select the ideal option" — string identity of the
    chosen option — regardless of eval_mode. Open-ended uses the eval_mode verifier
    (range = is the scalar answer inside the reference interval).
    """
    if sol.is_mcq:
        return grade_str_verifier(ideal, sol.final_answer)
    if eval_mode == "range_verifier":
        try:
            return grade_range_verifier(ideal, sol.final_answer)
        except (ValueError, SyntaxError):
            return False
    if eval_mode == "str_verifier":
        return grade_str_verifier(ideal, sol.final_answer)
    return None  # llm_verifier


def _reference_value(ideal: str | None) -> float | None:
    """Numeric value of a reference answer, including range midpoints."""
    if ideal is None:
        return None
    from genie.answer import parse_option

    return parse_option(ideal).center


if __name__ == "__main__":
    # End-to-end demo on the real dataset (MCQ), driven by the OracleAnalyst at
    # increasing noise — reproduces the nearest-option win through the full loop.
    from collections import defaultdict

    from datasets import load_dataset

    ds = load_dataset("futurehouse/BixBench", split="train")
    questions = [
        Question(
            question=r["question"],
            question_id=r["question_id"],
            options=[r["ideal"], *list(r["distractors"])],
            ideal=r["ideal"],
            eval_mode=r["eval_mode"],
            capsule_uuid=r["capsule_uuid"],
        )
        for r in ds
        if _reference_value(r["ideal"]) is not None
    ]
    print(f"end-to-end runner on {len(questions)} numeric MCQ items\n")
    print(f"  {'noise':>7} | {'accuracy (full pipeline)':>24}")
    for noise in (0.0, 0.05, 0.10):
        sols = run(questions, OracleAnalyst(noise=noise))
        hits = sum(bool(grade_solution(s, q.ideal, q.eval_mode)) for s, q in zip(sols, questions))
        print(f"  {noise * 100:5.0f}%  | {hits / len(questions) * 100:22.0f}%")
    print("\n  (OracleAnalyst is a test double; this validates the assembly, not analysis.)")
