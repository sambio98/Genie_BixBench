"""Genie_BixBench agent components. See docs/DESIGN.md."""

from genie.answer import (
    choose_mcq,
    commit_open_answer,
    extract_number,
    format_like_options,
    grade_range_verifier,
    grade_str_verifier,
    parse_option,
    snap_to_nearest_option,
)
from genie.eda import CapsuleManifest, FileInfo, build_manifest
from genie.router import AnswerShape, RouteDecision, route
from genie.solve import (
    AnalysisResult,
    Analyst,
    CallableAnalyst,
    ManifestCache,
    OracleAnalyst,
    Question,
    Solution,
    grade_solution,
    run,
    solve,
    verify,
)

__all__ = [
    "AnalysisResult",
    "Analyst",
    "AnswerShape",
    "CallableAnalyst",
    "CapsuleManifest",
    "FileInfo",
    "ManifestCache",
    "OracleAnalyst",
    "Question",
    "RouteDecision",
    "Solution",
    "build_manifest",
    "choose_mcq",
    "commit_open_answer",
    "extract_number",
    "format_like_options",
    "grade_range_verifier",
    "grade_solution",
    "grade_str_verifier",
    "parse_option",
    "route",
    "run",
    "snap_to_nearest_option",
    "solve",
    "verify",
]
