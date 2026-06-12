"""Answer handling for BixBench — the cheapest, broadest source of recovered points.

FINDINGS.md showed that a large share of losses are *correct analysis scored
wrong*: 88% of answers are numeric and 30% are graded by exact string match, so
`1.9e-4`, `0.00019`, and `0.0002` are all "different" even when the analysis is
right. This module attacks that directly:

- `grade_str_verifier` / `grade_range_verifier` faithfully replicate the real
  BixBench grader (see Future-House/BixBench `bixbench/graders.py`), so we can
  reason about exactly what will and won't match.
- `format_like_options` emits a computed number in the representation the MCQ
  options use (scientific vs decimal, matching significant figures).
- `snap_to_nearest_option` converts an unwinnable exact-match into a winnable
  nearest-neighbour — exploiting that BixBench distractors cluster near the
  truth.
- `choose_mcq` / `commit_open_answer` encode the decisiveness policy (never
  refuse when a computation exists; a refusal option roughly halves the score).

Everything here is pure and unit-tested; no agent or API required.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

# --- Parsing ---------------------------------------------------------------

_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def extract_number(text: str) -> float | None:
    """Pull the first numeric value out of a possibly-messy answer string.

    Handles scientific notation, leading words/symbols, and thousands commas:
    "p-adj < 1.9E-05" -> 1.9e-05, "approximately 1,234.5" -> 1234.5.
    """
    if text is None:
        return None
    cleaned = str(text).replace(",", "")
    m = _NUM.search(cleaned)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


@dataclass(frozen=True)
class Option:
    """A parsed MCQ option: a point value, a numeric range, or free text."""

    raw: str
    point: float | None = None
    low: float | None = None
    high: float | None = None

    @property
    def is_numeric(self) -> bool:
        return self.point is not None or self.low is not None

    @property
    def center(self) -> float | None:
        if self.point is not None:
            return self.point
        if self.low is not None and self.high is not None:
            return (self.low + self.high) / 2
        return None


def parse_option(raw: str) -> Option:
    """Parse one option string into point / range / text form."""
    s = str(raw).strip()
    # Range tuple like "(1.50,1.54)" — BixBench range_verifier format.
    if s.startswith("(") and s.endswith(")") and "," in s:
        try:
            lo, hi = ast.literal_eval(s)
            return Option(raw=raw, low=float(lo), high=float(hi))
        except (ValueError, SyntaxError):
            pass
    v = extract_number(s)
    # Only treat as a point value if the option is essentially just that number,
    # so categorical text containing a digit ("Group 2") stays text.
    if v is not None and re.fullmatch(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s.replace(",", "")):
        return Option(raw=raw, point=v)
    return Option(raw=raw)


# --- Faithful replicas of the real graders ---------------------------------


def grade_str_verifier(target: str, predicted: str) -> bool:
    """Replicate BixBench `str_verifier`: strip non-alphanumerics, lower, ==."""
    ct = re.sub(r"[^a-zA-Z0-9]", "", str(target)).lower()
    cp = re.sub(r"[^a-zA-Z0-9]", "", str(predicted)).lower()
    return cp == ct


def grade_range_verifier(target: str, predicted: str) -> bool:
    """Replicate BixBench `range_verifier`: lower <= float(pred) <= upper."""
    lo, hi = ast.literal_eval(str(target))
    v = extract_number(predicted)
    return v is not None and float(lo) <= v <= float(hi)


# --- Representation matching (the cheap win) --------------------------------


def _sig_figs(num_str: str) -> int:
    """Count significant figures in a numeric string (best effort)."""
    s = re.sub(r"[eE].*$", "", str(num_str).strip().lstrip("+-"))
    digits = s.replace(".", "")
    digits = digits.lstrip("0") or "0"  # drop leading zeros
    if "." not in s:
        digits = digits.rstrip("0") or "0"  # trailing zeros in ints aren't sig
    return max(len(digits), 1)


def format_numeric(value: float, representation: str = "decimal", sig_figs: int = 3) -> str:
    """Format a number per an explicit representation (scientific/decimal/integer).

    Shared by the MCQ formatter and the open-ended path so the agent emits the
    representation the question implies.
    """
    if value is None:
        return ""
    if representation == "integer":
        return str(int(round(value)))
    if value == 0:
        return "0"
    if representation == "scientific":
        return f"{value:.{max(sig_figs - 1, 0)}e}"
    digits = sig_figs - 1 - math.floor(math.log10(abs(value)))
    return f"{value:.{max(digits, 0)}f}"


def format_like_options(value: float, options: list[str]) -> str:
    """Format `value` to match the style (sci/decimal + sig figs) of the options.

    On MCQ we know the target's representation family from the distractors;
    matching it is what makes a numerically-correct value also string-correct.
    """
    nums = [o for o in options if parse_option(o).is_numeric]
    use_sci = sum(1 for o in nums if re.search(r"[eE]", o)) > len(nums) / 2 if nums else False
    figs = sorted(_sig_figs(o) for o in nums) if nums else [3]
    sig = figs[len(figs) // 2]  # median sig figs of the options
    return format_numeric(value, "scientific" if use_sci else "decimal", sig)


def snap_to_nearest_option(value: float, options: list[str]) -> str | None:
    """Return the option closest to `value` (range-aware), or None if no numeric option.

    Containment in a range scores best; otherwise smallest relative distance.
    Distractors cluster near the truth, so an approximately-correct analysis
    snaps to the right option even when exact-match would fail.
    """
    best, best_score = None, math.inf
    for o in options:
        opt = parse_option(o)
        if opt.low is not None and opt.low <= value <= opt.high:
            return o  # inside the range: unambiguous best
        ref = opt.center
        if ref is None:
            continue
        score = abs(value - ref) / (abs(ref) if ref else 1.0)
        if score < best_score:
            best, best_score = o, score
    return best


# --- Answering policy (decisiveness) ---------------------------------------


def choose_mcq(
    value: float | None,
    options: list[str],
    refusal_option: str | None = None,
) -> str:
    """Pick an MCQ option. Refuse only when we truly have no computed value.

    A refusal option roughly halves accuracy (FINDINGS §3), so we commit to the
    nearest option whenever the analysis produced a number.
    """
    if value is not None:
        snapped = snap_to_nearest_option(value, options)
        if snapped is not None:
            return snapped
    if refusal_option is not None:
        return refusal_option
    return options[0] if options else ""


def commit_open_answer(value: float | None, options: list[str] | None = None) -> str:
    """Produce a committed open-answer string; never refuse if we have a value."""
    if value is None:
        return ""  # caller should treat empty as "needs more analysis", not refusal
    if options:
        return format_like_options(value, options)
    return repr(value)
