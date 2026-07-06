"""
ibiofoundry_ai/tools/eda
[[ibiofoundry_ai.tools.eda]]
https://github.com/iBioFoundry/iBioFoundry-AI/tree/main/ibiofoundry_ai/tools/eda.py
Test file: tests/ibiofoundry_ai/tools/test_eda.py

Generic, benchmark-agnostic exploratory-data-analysis guards for the code
agent. Three pure helpers, injected into the code_agent sandbox namespace so
generated Python can call them by name:

- ``inventory_inputs`` -- inspect every input file (shape, dtypes, what a
  row/column represents, identifiers encoded in filenames) BEFORE analysis, so
  the agent picks the right axis and the right join key.
- ``join_key_candidates`` -- when a cross-table join yields an empty
  intersection, surface alternate keys (value normalizations + filename tokens)
  instead of giving up. This is the recovery lever behind GH #955.
- ``sanity_check_value`` -- after computing a result, flag sign/range
  violations against the quantity's expectation (a replicate correlation is
  positive; a proportion lies in [0, 1]).

These address the data-discovery (wrong axis / empty join / wrong key) and
result-plausibility failure classes seen in GH #955 / #956. They hold no
benchmark-specific knowledge: every function operates on arbitrary tables and
values.
"""

import math
import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

# Suffixes inventory_inputs will attempt to parse as tables.
TABULAR_SUFFIXES: frozenset[str] = frozenset(
    {".csv", ".tsv", ".txt", ".parquet", ".xlsx", ".xls"}
)

# Files larger than this are listed but not parsed -- a multi-GB input should
# be inspected deliberately, not loaded whole by an inventory pass.
MAX_INVENTORY_BYTES: int = 200 * 1024 * 1024

# Valid expectation kinds for sanity_check_value.
VALID_SANITY_KINDS: frozenset[str] = frozenset(
    {
        "proportion",
        "probability",
        "percentage",
        "correlation",
        "replicate_correlation",
        "odds_ratio",
        "ratio_nonneg",
        "pvalue",
        "count",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z]*\d+[A-Za-z]*")
_DOT_ZERO_RE = re.compile(r"\.0+$")
_NON_DIGIT_RE = re.compile(r"\D")


class ColumnProfile(BaseModel):
    """Per-column profile of a tabular input file."""

    name: str = Field(frozen=True, description="Column name")
    dtype: str = Field(frozen=True, description="pandas dtype string")
    n_unique: int = Field(frozen=True, description="Distinct non-null values")
    sample_values: list[str] = Field(
        default_factory=list, description="Up to 5 example non-null values"
    )


class FileInventory(BaseModel):
    """Inventory of a single input file (tabular or otherwise)."""

    path: str = Field(frozen=True, description="Path relative to the inventory root")
    suffix: str = Field(frozen=True, description="File extension, lowercased")
    size_bytes: int = Field(frozen=True, description="File size on disk")
    n_rows: int | None = Field(
        default=None,
        frozen=True,
        description="Row count for tabular files; None if not tabular/unreadable",
    )
    n_cols: int | None = Field(
        default=None, frozen=True, description="Column count for tabular files"
    )
    columns: list[ColumnProfile] = Field(
        default_factory=list, description="Per-column profile (tabular only)"
    )
    axis_hint: str | None = Field(
        default=None,
        frozen=True,
        description=(
            "Heuristic note when a wide matrix looks like features x samples "
            "(a transpose-before-clustering warning); None otherwise"
        ),
    )
    filename_tokens: list[str] = Field(
        default_factory=list,
        description=(
            "Identifier-like tokens parsed from the filename -- IDs are often "
            "encoded in names (e.g. CHIP_185-PF.xlsx), not in a column"
        ),
    )
    read_error: str | None = Field(
        default=None,
        frozen=True,
        description="Set when the file could not be parsed; surfaced, never silently dropped",
    )
    other_sheets: list[str] = Field(
        default_factory=list,
        description=(
            "Other sheet names in a multi-sheet Excel workbook, not covered by the "
            "profiled n_rows/n_cols/columns above (which reflect only the largest "
            "sheet). Empty for single-sheet or non-Excel files."
        ),
    )
    preview_note: str | None = Field(
        default=None,
        frozen=True,
        description=(
            "Set when n_rows/n_cols/columns come from a BOUNDED preview (the file "
            "was too large to fully load) rather than a full read -- surfaced "
            "alongside the shape/columns, unlike read_error which means no "
            "information could be extracted at all."
        ),
    )


class InputInventory(BaseModel):
    """Recursive inventory of an input directory."""

    root: str = Field(frozen=True, description="Directory inventoried")
    files: list[FileInventory] = Field(
        default_factory=list, description="One entry per file found (recursive)"
    )

    def summary(self) -> str:
        """Return a compact, LLM-readable digest of the inventory."""
        lines = [f"Inventoried {len(self.files)} file(s) under {self.root}:"]
        for f in self.files:
            if f.read_error is not None:
                lines.append(f"  {f.path} [{f.suffix}] -- UNREADABLE: {f.read_error}")
                continue
            if f.n_rows is not None:
                shown = [c.name for c in f.columns[:12]]
                more = "" if len(f.columns) <= 12 else f" (+{len(f.columns) - 12} more)"
                lines.append(
                    f"  {f.path} [{f.suffix}] shape=({f.n_rows},{f.n_cols}) "
                    f"cols=[{', '.join(shown)}{more}]"
                )
                if f.preview_note is not None:
                    lines.append(f"      NOTE: {f.preview_note}")
                if f.axis_hint is not None:
                    lines.append(f"      AXIS: {f.axis_hint}")
                if f.filename_tokens:
                    lines.append(f"      filename ids: {f.filename_tokens}")
                if f.other_sheets:
                    lines.append(
                        f"      OTHER SHEETS not shown above (this workbook has "
                        f"{len(f.other_sheets) + 1} sheets total; only the largest is "
                        f"profiled) -- inspect these yourself if relevant: "
                        f"{f.other_sheets}"
                    )
            else:
                toks = (
                    f" filename ids: {f.filename_tokens}" if f.filename_tokens else ""
                )
                lines.append(
                    f"  {f.path} [{f.suffix}] non-tabular size={f.size_bytes}B{toks}"
                )
        return "\n".join(lines)


class JoinKeyCandidate(BaseModel):
    """A candidate join key recovered between two tables after a failed join."""

    left_col: str = Field(
        frozen=True,
        description="Left column, or '<filename>' for a filename-token key",
    )
    right_col: str = Field(
        frozen=True,
        description="Right column, or '<filename>' for a filename-token key",
    )
    normalization: str = Field(
        frozen=True,
        description=(
            "Transform that made the keys overlap: strip | casefold | "
            "strip_trailing_dot_zero | strip_leading_zeros | extract_digits | "
            "basename_token"
        ),
    )
    overlap: int = Field(frozen=True, description="Count of shared normalized values")
    left_coverage: float = Field(
        frozen=True, description="Fraction of distinct left keys matched"
    )
    right_coverage: float = Field(
        frozen=True, description="Fraction of distinct right keys matched"
    )


class PlausibilityFlag(BaseModel):
    """Outcome of a result-plausibility sanity check."""

    ok: bool = Field(
        frozen=True, description="True when the value satisfies the expectation"
    )
    kind: str = Field(frozen=True, description="Expectation kind checked")
    message: str = Field(
        frozen=True,
        description="Why it failed and what to re-examine; empty string when ok",
    )


def _filename_tokens(name: str) -> list[str]:
    """Parse identifier-like tokens (containing a digit) from a filename stem."""
    return _TOKEN_RE.findall(Path(name).stem)


def _string_values(series: pd.Series) -> list[str]:  # type: ignore[type-arg]
    """Return non-null cell values of a column as plain strings."""
    return [str(v) for v in series.dropna().tolist()]


def _axis_hint(df: pd.DataFrame) -> str | None:
    """Flag a wide numeric matrix whose entity axis is easy to mistake.

    Two orientations are flagged, because a wrong-axis correlation/aggregation
    silently returns a label from the wrong axis:

    - features-as-ROWS (``n_rows >> n_cols``): the columns are samples; to
      cluster/correlate/regress over samples, transpose first.
    - features-as-COLUMNS (``n_cols >> n_rows``): the columns are the entities
      (e.g. genes) and the rows are samples (e.g. cell lines), so a per-entity
      answer is a COLUMN label -- a returned row/sample id means the wrong axis
      was used. This is the bix-16-q1 CRISPRGeneEffect (cell-lines x genes)
      trap: correlating along the rows yields a cell-line id, not a gene.

    The ``numeric_cols >= 10`` guard keeps tidy long-format tables (few numeric
    columns) from tripping either branch.
    """
    n_rows, n_cols = int(df.shape[0]), int(df.shape[1])
    numeric_cols = sum(1 for c in df.columns if pd.api.types.is_numeric_dtype(df[c]))
    if n_rows >= 50 and numeric_cols >= 10 and n_rows > 3 * n_cols:
        return (
            f"wide matrix: {n_cols} columns look like samples/observations and "
            f"{n_rows} rows look like features (e.g. genes). To analyze samples "
            "(cluster/correlate/regress over observations), operate on COLUMNS "
            "-- transpose first."
        )
    if n_cols >= 50 and numeric_cols >= 10 and n_cols > 3 * n_rows:
        return (
            f"wide matrix: {n_cols} columns look like the entities/features "
            f"(e.g. genes) and {n_rows} rows look like samples/observations "
            "(e.g. cell lines). A per-entity (per-gene) answer is a COLUMN label "
            "-- if your result is a row/sample/cell-line id you "
            "correlated/aggregated along the WRONG axis. Operate on COLUMNS "
            "(transpose or select columns)."
        )
    return None


def _profile_columns(df: pd.DataFrame, max_samples: int = 5) -> list[ColumnProfile]:
    """Build a per-column profile for a parsed table."""
    profiles: list[ColumnProfile] = []
    for col in df.columns:
        series = df[col]
        samples = [str(v) for v in series.dropna().tolist()[:max_samples]]
        profiles.append(
            ColumnProfile(
                name=str(col),
                dtype=str(series.dtype),
                n_unique=int(series.nunique(dropna=True)),
                sample_values=samples,
            )
        )
    return profiles


def _read_table(path: Path, suffix: str) -> tuple[pd.DataFrame, list[str]]:
    """Parse a tabular file into a DataFrame (delimiter sniffed for text).

    ``.xls``/``.xlsx`` need an explicit engine (pandas cannot auto-detect legacy
    ``.xls`` vs ``.xlsx``) -- but some BixBench capsules ship plain-text/TSV data
    under a misleading ``.xls`` extension (not a real Excel binary at all), which
    raises from the Excel engine rather than parsing. Fall back to delimited-text
    parsing in that case rather than surfacing a false "unreadable".

    A workbook can have multiple sheets (e.g. a small sample-metadata sheet plus a
    large expression-matrix sheet); reading only the default/first sheet silently
    hides the others from the mandatory pre-analysis inventory. Read every sheet,
    profile the LARGEST one (by cell count) as the file's main table, and return
    the other sheets' names so they are still surfaced, not lost.

    Returns:
        (chosen DataFrame, names of the other sheets not profiled -- empty for
        single-sheet or non-Excel files).
    """
    if suffix in (".parquet",):
        return pd.read_parquet(path), []
    if suffix in (".xlsx", ".xls"):
        engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
        try:
            sheets = pd.read_excel(path, sheet_name=None, engine=engine)
        except Exception:  # noqa: BLE001 - genuinely not an Excel binary; try text
            return pd.read_csv(path, sep=None, engine="python"), []
        if len(sheets) <= 1:
            return next(iter(sheets.values())), []
        ranked = sorted(sheets.items(), key=lambda kv: kv[1].size, reverse=True)
        chosen_name, chosen_df = ranked[0]
        return chosen_df, [name for name, _ in ranked[1:]]
    return pd.read_csv(path, sep=None, engine="python"), []


_PREVIEW_NROWS = 5
"""Rows read for an oversized file's column/dtype preview -- cheap regardless of
file size, since pandas stops reading after this many data rows."""


def _fast_line_count(path: Path) -> int | None:
    """Count newlines by streaming raw bytes, without loading the file as a table.

    O(file size) I/O but O(1) memory (fixed-size chunks) -- feasible even for a
    600 MB+ file in well under a second, unlike a full ``pd.read_csv``. Returns
    None on any failure (e.g. a real binary format); the caller treats that as
    "row count unknown" rather than failing the whole preview.
    """
    try:
        with path.open("rb") as fh:
            n = sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 20), b""))
        return max(n - 1, 0)  # subtract the header line
    except OSError:
        return None


def _preview_oversized_table(
    path: Path, rel: str, suffix: str, size_bytes: int, tokens: list[str]
) -> FileInventory:
    """Cheap preview for a file over ``MAX_INVENTORY_BYTES``.

    A full ``_read_table`` on a 400-600 MB file is exactly the multi-minute,
    memory-heavy load RULE 12's "inventory before you analyse" step exists to
    avoid triggering by accident -- but skipping it ENTIRELY (the prior
    behaviour) leaves the agent with zero column/shape information for every
    large real-world matrix (DepMap's CRISPRGeneEffect.csv/Omics expression
    files are consistently 400 MB+). Read only ``_PREVIEW_NROWS`` data rows
    (pandas stops after that many regardless of file size) for columns/dtypes,
    and get the true row count via a fast byte-level newline count instead of
    loading the whole table. Falls back to the old "inspect manually" note if
    even the bounded read fails (e.g. a real binary format misdetected as
    tabular).
    """
    try:
        if suffix in (".xlsx", ".xls"):
            engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
            preview = pd.read_excel(path, engine=engine, nrows=_PREVIEW_NROWS)
        else:
            preview = pd.read_csv(path, sep=None, engine="python", nrows=_PREVIEW_NROWS)
    except Exception as exc:  # noqa: BLE001 - fall back to the manual-inspect note
        return FileInventory(
            path=rel,
            suffix=suffix,
            size_bytes=size_bytes,
            filename_tokens=tokens,
            read_error=(
                f"file too large to fully inventory ({size_bytes // (1024 * 1024)} MB) "
                f"and a bounded preview also failed ({type(exc).__name__}); inspect manually"
            ),
        )
    n_rows = _fast_line_count(path)
    return FileInventory(
        path=rel,
        suffix=suffix,
        size_bytes=size_bytes,
        n_rows=n_rows,
        n_cols=int(preview.shape[1]),
        columns=_profile_columns(preview),
        axis_hint=None,  # axis heuristic needs real row/col magnitude judgment; skip on a preview
        filename_tokens=tokens,
        preview_note=(
            f"{size_bytes // (1024 * 1024)} MB -- too large to fully load; columns/"
            f"dtypes below are from the first {_PREVIEW_NROWS} rows only (real, not "
            "guessed), n_rows is a fast full-file line count (real). Load only the "
            "columns/rows you actually need (usecols=, nrows=, or chunksize=), "
            "never the whole file at once."
        ),
    )


def inventory_inputs(root: Path | str) -> InputInventory:
    """Inventory every file under ``root`` before analysis.

    Walks ``root`` recursively. For each tabular file (CSV/TSV/TXT/Parquet/
    Excel) under the size cap it records shape, per-column dtype/cardinality/
    samples, and an ``axis_hint`` when the file looks like a features-x-samples
    matrix that should be transposed. For every file it parses identifier-like
    ``filename_tokens`` so keys encoded in names (not columns) are visible.

    A per-file parse failure is recorded in ``FileInventory.read_error`` rather
    than raised -- the contract is to report everything found, including the
    unreadable, so the caller sees the full picture in one pass.

    Args:
        root: Directory to inventory (e.g. the read-only ``DATA_DIR``).

    Returns:
        An ``InputInventory`` whose ``summary()`` is a compact digest to print.
    """
    root_path = Path(root)
    files: list[FileInventory] = []
    for path in sorted(p for p in root_path.rglob("*") if p.is_file()):
        suffix = path.suffix.lower()
        size_bytes = path.stat().st_size
        rel = str(path.relative_to(root_path))
        tokens = _filename_tokens(path.name)
        if suffix not in TABULAR_SUFFIXES:
            files.append(
                FileInventory(
                    path=rel,
                    suffix=suffix,
                    size_bytes=size_bytes,
                    filename_tokens=tokens,
                )
            )
            continue
        if size_bytes > MAX_INVENTORY_BYTES:
            files.append(_preview_oversized_table(path, rel, suffix, size_bytes, tokens))
            continue
        try:
            df, other_sheets = _read_table(path, suffix)
        except Exception as exc:  # surfaced in read_error, never swallowed
            files.append(
                FileInventory(
                    path=rel,
                    suffix=suffix,
                    size_bytes=size_bytes,
                    filename_tokens=tokens,
                    read_error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        files.append(
            FileInventory(
                path=rel,
                suffix=suffix,
                size_bytes=size_bytes,
                n_rows=int(df.shape[0]),
                n_cols=int(df.shape[1]),
                columns=_profile_columns(df),
                axis_hint=_axis_hint(df),
                filename_tokens=tokens,
                other_sheets=other_sheets,
            )
        )
    return InputInventory(root=str(root_path), files=files)


def build_inventory_prompt_block(root: Path | str) -> str:
    """Render the auto-injected input inventory for the code agent's prompt.

    The opt-out-free counterpart to ``inventory_inputs`` for the code-agent
    startup path (GH #961): the code agent never reliably *chose* to inventory
    before analysis, so the inventory is run for it and dropped into the system
    prompt. The returned block LEADS with any wide-matrix AXIS warnings -- a
    single ``AXIS:`` line buried mid-digest is easy to skim past, and surfacing
    it first is the whole point -- followed by the full per-file digest under a
    self-describing header.

    Returns an empty string when ``root`` is not a directory or holds no files,
    so a no-upload session injects no inventory section at all (the header lives
    here, not in the prompt template, so an empty block leaves no dangling
    heading). No try/except: ``inventory_inputs`` already records per-file parse
    failures in ``FileInventory.read_error`` rather than raising.

    Args:
        root: Directory to inventory (the read-only sandbox ``input_dir``).

    Returns:
        A prompt-ready inventory block, or ``""`` when there is nothing to show.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return ""
    inventory = inventory_inputs(root_path)
    if not inventory.files:
        return ""
    axis_warnings = [
        f"  - {f.path}: {f.axis_hint}"
        for f in inventory.files
        if f.axis_hint is not None
    ]
    blocks = ["INPUT INVENTORY (auto-generated -- read before you analyse):", ""]
    if axis_warnings:
        blocks.append(
            "AXIS WARNING -- a wide matrix was auto-detected. The entity a "
            "question asks about (a gene symbol, a sample id) may live on the "
            "COLUMN axis: make your result index the correct axis (transpose "
            "before you correlate/cluster/regress) so you return that entity, "
            "not a label from the wrong axis."
        )
        blocks.extend(axis_warnings)
        blocks.append("")
    blocks.append(inventory.summary())
    return "\n".join(blocks)


def _norm_strip(v: str) -> str:
    return v.strip()


def _norm_casefold(v: str) -> str:
    return v.strip().casefold()


def _norm_strip_dot_zero(v: str) -> str:
    return _DOT_ZERO_RE.sub("", v.strip())


def _norm_strip_leading_zeros(v: str) -> str:
    s = v.strip()
    return s.lstrip("0") or ("0" if s else "")


def _norm_extract_digits(v: str) -> str:
    return _NON_DIGIT_RE.sub("", v)


# Ordered least-aggressive first, so ties keep the gentlest normalization.
_NORMALIZERS: list[tuple[str, Callable[[str], str]]] = [
    ("strip", _norm_strip),
    ("casefold", _norm_casefold),
    ("strip_trailing_dot_zero", _norm_strip_dot_zero),
    ("strip_leading_zeros", _norm_strip_leading_zeros),
    ("extract_digits", _norm_extract_digits),
]


def _best_normalization(
    lvals: list[str], rvals: list[str]
) -> tuple[str, int, float, float] | None:
    """Return the gentlest normalization giving the largest non-zero overlap."""
    best: tuple[str, int, float, float] | None = None
    for name, fn in _NORMALIZERS:
        lset = {fn(v) for v in lvals if fn(v) != ""}
        rset = {fn(v) for v in rvals if fn(v) != ""}
        overlap = len(lset & rset)
        if overlap == 0:
            continue
        lcov = overlap / len(lset) if lset else 0.0
        rcov = overlap / len(rset) if rset else 0.0
        if best is None or overlap > best[1]:
            best = (name, overlap, lcov, rcov)
    return best


def _filename_token_set(filenames: list[str]) -> set[str]:
    """Flatten identifier tokens across a list of filenames into a set."""
    tokens: set[str] = set()
    for name in filenames:
        tokens.update(_filename_tokens(name))
    return tokens


def join_key_candidates(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    left_filenames: list[str] | None = None,
    right_filenames: list[str] | None = None,
) -> list[JoinKeyCandidate]:
    """Recover candidate join keys between two tables after an empty join.

    For every (left column, right column) pair, tries a ladder of value
    normalizations (strip, casefold, strip a float-formatting ``.0``, strip
    leading zeros, extract digits) and keeps the gentlest one with the largest
    non-zero overlap. When filename lists are given, also matches each table's
    columns against identifier tokens parsed from the other side's filenames --
    the case where the join key lives in per-sample filenames, not a column
    (GH #955).

    Args:
        left: Left table.
        right: Right table.
        left_filenames: Filenames the left rows came from, if IDs are in names.
        right_filenames: Filenames the right rows came from.

    Returns:
        Candidates ranked by ``overlap`` descending; empty if nothing overlaps.
    """
    candidates: list[JoinKeyCandidate] = []
    for lcol in left.columns:
        lvals = _string_values(left[lcol])
        for rcol in right.columns:
            rvals = _string_values(right[rcol])
            best = _best_normalization(lvals, rvals)
            if best is None:
                continue
            name, overlap, lcov, rcov = best
            candidates.append(
                JoinKeyCandidate(
                    left_col=str(lcol),
                    right_col=str(rcol),
                    normalization=name,
                    overlap=overlap,
                    left_coverage=lcov,
                    right_coverage=rcov,
                )
            )
    if right_filenames:
        rtokens = _filename_token_set(right_filenames)
        for lcol in left.columns:
            lset = {_norm_strip(v) for v in _string_values(left[lcol])}
            overlap = len(lset & rtokens)
            if overlap > 0:
                candidates.append(
                    JoinKeyCandidate(
                        left_col=str(lcol),
                        right_col="<filename>",
                        normalization="basename_token",
                        overlap=overlap,
                        left_coverage=overlap / len(lset) if lset else 0.0,
                        right_coverage=overlap / len(rtokens) if rtokens else 0.0,
                    )
                )
    if left_filenames:
        ltokens = _filename_token_set(left_filenames)
        for rcol in right.columns:
            rset = {_norm_strip(v) for v in _string_values(right[rcol])}
            overlap = len(rset & ltokens)
            if overlap > 0:
                candidates.append(
                    JoinKeyCandidate(
                        left_col="<filename>",
                        right_col=str(rcol),
                        normalization="basename_token",
                        overlap=overlap,
                        left_coverage=overlap / len(ltokens) if ltokens else 0.0,
                        right_coverage=overlap / len(rset) if rset else 0.0,
                    )
                )
    candidates.sort(key=lambda c: c.overlap, reverse=True)
    return candidates


def sanity_check_value(value: float, kind: str) -> PlausibilityFlag:
    """Flag sign/range violations of a computed result against an expectation.

    A cheap last line of defence against silently-wrong numbers: a proportion
    outside [0, 1], a negative replicate correlation, a non-positive odds
    ratio. Catches range and sign errors only -- not logical or magnitude
    errors -- so a passing flag is necessary, not sufficient.

    Args:
        value: The computed result.
        kind: Expected quantity, one of ``VALID_SANITY_KINDS``.

    Returns:
        A ``PlausibilityFlag``; ``ok=False`` carries a re-examination hint.

    Raises:
        ValueError: If ``kind`` is not a recognised expectation.
    """
    if kind not in VALID_SANITY_KINDS:
        raise ValueError(
            f"unknown sanity-check kind {kind!r}; expected one of "
            f"{sorted(VALID_SANITY_KINDS)}"
        )
    v = float(value)
    ok = True
    message = ""
    if kind in ("proportion", "probability", "pvalue"):
        ok = 0.0 <= v <= 1.0
        message = (
            ""
            if ok
            else f"a {kind} must lie in [0, 1]; got {v:g} -- re-check the "
            "denominator / normalization / filtering"
        )
    elif kind == "percentage":
        ok = 0.0 <= v <= 100.0
        message = "" if ok else f"a percentage must lie in [0, 100]; got {v:g}"
    elif kind == "correlation":
        ok = -1.0 <= v <= 1.0
        message = "" if ok else f"a correlation must lie in [-1, 1]; got {v:g}"
    elif kind == "replicate_correlation":
        if not -1.0 <= v <= 1.0:
            ok = False
            message = f"a correlation must lie in [-1, 1]; got {v:g}"
        elif v < 0.0:
            ok = False
            message = (
                f"a replicate-replicate correlation should be positive; got {v:g} "
                "-- likely the wrong replicate columns or gene subset"
            )
    elif kind == "odds_ratio":
        ok = v > 0.0
        message = "" if ok else f"an odds ratio must be > 0; got {v:g}"
    elif kind == "ratio_nonneg":
        ok = v >= 0.0
        message = "" if ok else f"a non-negative ratio must be >= 0; got {v:g}"
    elif kind == "count":
        ok = v >= 0.0 and math.isclose(v, round(v))
        message = "" if ok else f"a count must be a non-negative integer; got {v:g}"
    return PlausibilityFlag(ok=ok, kind=kind, message=message)
