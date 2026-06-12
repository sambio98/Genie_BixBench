"""Answer-shape router (DESIGN.md §4).

The agent never sees `eval_mode`, but the *shape* of the expected answer is
usually legible from the question text (and, for MCQ, the options). Routing on
that shape lets us pick the right analysis depth, representation, and answering
policy — e.g. p-value questions need scientific notation + the standard pipeline
prior; odds-ratio questions are forgiving estimates; "how many" needs an integer.

    from genie.router import route
    d = route("What is the adjusted p-value for ...?")
    d.shape         # AnswerShape.P_VALUE
    d.representation # 'scientific'
    d.strategy      # actionable note
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from genie.answer import _sig_figs, parse_option


class AnswerShape(StrEnum):
    P_VALUE = "p_value"          # significance value: scientific, small, method-sensitive
    ESTIMATE = "estimate"        # continuous statistic: OR, coef, mean, fold-change
    COUNT = "count"              # integer
    CATEGORICAL = "categorical"  # a name/label/group
    BOOLEAN = "boolean"          # yes/no, true/false, supported/not
    DESCRIPTIVE = "descriptive"  # free text


_STRATEGY = {
    AnswerShape.P_VALUE: (
        "Run the conventional pipeline with library defaults; report 2-3 sig figs "
        "in scientific notation. Exact value is method-sensitive (FINDINGS §4) — "
        "in MCQ, snap to the nearest option rather than chasing an exact match."
    ),
    AnswerShape.ESTIMATE: (
        "Fit the standard model and report the point estimate. Often range-graded "
        "and forgiving — a ballpark-correct value wins; sanity-check sign/magnitude."
    ),
    AnswerShape.COUNT: (
        "Return an exact integer; double-check the filtering thresholds that define "
        "what is being counted."
    ),
    AnswerShape.CATEGORICAL: (
        "Extract the label/name from the computed result and verify it against the data."
    ),
    AnswerShape.BOOLEAN: (
        "Decide from the evidence and commit to yes/no — never refuse (FINDINGS §3)."
    ),
    AnswerShape.DESCRIPTIVE: (
        "Answer concisely, grounded in a specific computed result; cite the cell."
    ),
}

# Keyword groups, checked in precedence order (first hit wins).
_PVAL = re.compile(r"\b(p[\s-]?val(ue)?s?|p[\s-]?adj|padj|adjusted p|q[\s-]?val(ue)?|fdr)\b", re.I)
_COUNT = re.compile(r"\b(how many|number of|count of|total number)\b", re.I)
_ESTIMATE = re.compile(
    r"\b(odds ratio|hazard ratio|risk ratio|coefficient|correlation|r[\s-]?squared|"
    r"\br2\b|mean|median|average|fold[\s-]?change|log2|log[\s-]?fold|percentage|"
    r"proportion|\brate\b|threshold|value of|estimate|how much)\b",
    re.I,
)
_BOOLEAN = re.compile(r"^(is|are|does|do|did|was|were|can|should|has|have)\b|\b(yes or no|true or false)\b", re.I)
_CATEGORICAL = re.compile(
    r"\b(which|what is the name|name the|identify|what gene|what term|what pathway|"
    r"what type|what group|highest|lowest|most |least |top |which of)\b",
    re.I,
)
_DESCRIPTIVE = re.compile(r"\b(describe|explain|interpret|what does|why |how does|summari[sz]e)\b", re.I)


@dataclass
class RouteDecision:
    shape: AnswerShape
    representation: str       # 'scientific' | 'decimal' | 'integer' | 'text'
    sig_figs: int            # suggested significant figures for numeric answers
    strategy: str
    is_mcq: bool = False

    @property
    def is_numeric(self) -> bool:
        return self.representation in {"scientific", "decimal", "integer"}


def _shape_from_text(q: str) -> AnswerShape:
    # COUNT before P_VALUE: "how many ... p-adj < 0.05" wants an integer, not a p-value.
    if _COUNT.search(q):
        return AnswerShape.COUNT
    if _PVAL.search(q):
        return AnswerShape.P_VALUE
    if _ESTIMATE.search(q):
        return AnswerShape.ESTIMATE
    if _DESCRIPTIVE.search(q):
        return AnswerShape.DESCRIPTIVE
    if _CATEGORICAL.search(q):
        return AnswerShape.CATEGORICAL
    if _BOOLEAN.search(q.strip()):
        return AnswerShape.BOOLEAN
    return AnswerShape.DESCRIPTIVE


def _representation(shape: AnswerShape) -> str:
    return {
        AnswerShape.P_VALUE: "scientific",
        AnswerShape.ESTIMATE: "decimal",
        AnswerShape.COUNT: "integer",
    }.get(shape, "text")


def route(question: str, options: list[str] | None = None) -> RouteDecision:
    """Infer the expected answer shape, representation, and strategy."""
    shape = _shape_from_text(question or "")
    representation = _representation(shape)
    sig_figs = 3

    if options:
        parsed = [parse_option(o) for o in options]
        numeric = [o for o, p in zip(options, parsed) if p.is_numeric]
        if numeric and len(numeric) == len(options):
            # All options numeric: representation is dictated by the options.
            use_sci = sum(1 for o in numeric if re.search(r"[eE]", o)) > len(numeric) / 2
            representation = "scientific" if use_sci else "decimal"
            figs = sorted(_sig_figs(o) for o in numeric)
            sig_figs = figs[len(figs) // 2]
            if shape not in {AnswerShape.P_VALUE, AnswerShape.ESTIMATE, AnswerShape.COUNT}:
                shape = AnswerShape.P_VALUE if use_sci else AnswerShape.ESTIMATE
        elif not numeric:
            shape, representation = AnswerShape.CATEGORICAL, "text"

    return RouteDecision(
        shape=shape,
        representation=representation,
        sig_figs=sig_figs,
        strategy=_STRATEGY[shape],
        is_mcq=bool(options),
    )


if __name__ == "__main__":
    examples = [
        ("What is the adjusted p-value for regulation of T cell activation?", None),
        ("What is the odds ratio for COVID-19 severity associated with BCG?", None),
        ("How many GO terms show p-adj < 0.05?", None),
        ("Which patient volume group shows significant differences?", None),
        ("Is the ASXL1 hypothesis supported by the expression data?", None),
        ("p-adj?", ["7.82E-05", "0.0003", "1.85E-05", "0.0002"]),
    ]
    for q, opts in examples:
        d = route(q, opts)
        print(f"[{d.shape:11}] repr={d.representation:10} sf={d.sig_figs}  {q[:55]}")
