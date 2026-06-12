"""Capsule-level EDA -> a reusable data manifest (DESIGN.md §2).

BixBench gives 4-5 questions per capsule that all share the same data files, yet
the reference agent re-explores per question. This builds the data picture *once*
per capsule, deterministically (no LLM): file inventory, table schemas, likely
roles (expression matrix / sample metadata / annotation / variants / sequence /
image), low-cardinality group counts (the experiment's design), and candidate
joins between tables. The result is cheap, consistent context every question in
the capsule can start from.

    from genie.eda import build_manifest
    print(build_manifest("/path/to/CapsuleData-...").render())
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import pandas as pd
except ImportError:  # pandas is only needed for tabular inspection
    pd = None  # type: ignore[assignment]

TABULAR = {".csv", ".tsv", ".txt", ".tab", ".xlsx", ".xls"}
SEQUENCE = {".fasta", ".fa", ".fastq", ".fq", ".fna", ".gb", ".gbk"}
VARIANTS = {".vcf", ".bcf", ".maf"}
IMAGE = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}
_PREVIEW_ROWS = 5000
_MAX_FULL_BYTES = 25 * 1024 * 1024  # above this, preview only

# Name hints for role detection (checked against the lowercased filename).
_HINTS = {
    "sample_metadata": ("coldata", "metadata", "samples", "pheno", "design", "manifest"),
    "annotation": ("gencode", "annotation", "hgnc", "gtf", "gff", "biomart",
                   "reference", "ensembl", "geneinfo", "mapping"),
    "expression_matrix": ("count", "featurecounts", "expression", "tpm", "fpkm",
                          "matrix", "abundance", "salmon", "rsem"),
    "results_table": ("deg", "results", "deseq", "edger", "limma", "stats", "diffexp"),
}


@dataclass
class FileInfo:
    name: str
    ext: str
    size_bytes: int
    role: str = "unknown"
    n_rows: int | None = None
    n_cols: int | None = None
    columns: list[str] = field(default_factory=list)
    numeric_cols: int = 0
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    id_column: str | None = None
    id_values: list[str] = field(default_factory=list)


@dataclass
class CapsuleManifest:
    data_dir: str
    files: list[FileInfo] = field(default_factory=list)
    joins: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        """Compact human/agent-readable summary."""
        lines = [f"# Capsule data manifest: {Path(self.data_dir).name}", ""]
        for f in self.files:
            head = f"- {f.name}  [{f.role}]  {f.size_bytes / 1e6:.2f} MB"
            lines.append(head)
            if f.n_rows is not None:
                shape = f"{f.n_rows}{'+' if f.truncated else ''} rows x {f.n_cols} cols"
                lines.append(f"    {shape} ({f.numeric_cols} numeric)")
            if f.columns:
                shown = ", ".join(f.columns[:12]) + (" ..." if len(f.columns) > 12 else "")
                lines.append(f"    columns: {shown}")
            for n in f.notes:
                lines.append(f"    note: {n}")
        if self.joins:
            lines += ["", "## candidate joins"]
            lines += [f"- {j}" for j in self.joins]
        return "\n".join(lines)


def _role_from_name(name: str) -> str | None:
    low = name.lower()
    for role, keys in _HINTS.items():
        if any(k in low for k in keys):
            return role
    return None


def _table_quality(df: "pd.DataFrame") -> int:
    """Higher is better: maximize *named* columns, penalize fragmentation.

    A whitespace-aligned file mis-parsed with the wrong delimiter yields the
    right header names plus many empty 'Unnamed' columns; we must prefer the
    clean parse with the same names but fewer total columns.
    """
    if df is None or df.shape[1] <= 1:
        return -(10**9)
    named = sum(1 for c in df.columns if not str(c).startswith("Unnamed"))
    return named * 1000 - df.shape[1]


def _read_table(path: Path) -> "pd.DataFrame | None":
    """Read a preview, trying several delimiters and keeping the cleanest parse.

    Real BixBench tables are messy: featureCounts is whitespace-aligned, others
    are tab/comma. `sep=None` sniffing alone splits whitespace files into dozens
    of empty columns, so we score candidate parses and pick the best.
    """
    if pd is None:
        return None
    if path.suffix.lower() in {".xlsx", ".xls"}:
        try:
            return pd.read_excel(path, nrows=_PREVIEW_ROWS)
        except Exception:  # noqa: BLE001
            return None
    attempts = [
        {"sep": None, "engine": "python"},
        {"sep": r"\s+", "engine": "python"},
        {"sep": "\t"},
        {"sep": ","},
    ]
    best, best_q = None, -2
    for kw in attempts:
        try:
            df = pd.read_csv(path, nrows=_PREVIEW_ROWS, **kw)
        except Exception:  # noqa: BLE001 - try the next delimiter
            continue
        q = _table_quality(df)
        if q > best_q:
            best, best_q = df, q
    return best


def _count_data_rows(path: Path) -> int | None:
    """Cheap true row count for text tables (total lines minus a header)."""
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return None
    try:
        with path.open("rb") as fh:
            n = sum(buf.count(b"\n") for buf in iter(lambda: fh.read(1 << 20), b""))
        return max(n - 1, 0)  # assume one header line
    except OSError:
        return None


def _profile_table(info: FileInfo, path: Path) -> None:
    df = _read_table(path)
    if df is None:
        info.notes.append("could not parse as a table")
        return
    preview_rows, info.n_cols = int(df.shape[0]), int(df.shape[1])
    true_rows = _count_data_rows(path)
    info.n_rows = true_rows if true_rows is not None else preview_rows
    info.truncated = true_rows is None and preview_rows >= _PREVIEW_ROWS
    info.columns = [str(c) for c in df.columns]
    numeric = df.select_dtypes("number")
    info.numeric_cols = int(numeric.shape[1])

    # Refine role from shape when the name was uninformative.
    if info.role == "unknown":
        wide_numeric = info.numeric_cols >= max(2, info.n_cols - 2)
        if wide_numeric and info.n_rows > 5 * info.n_cols:
            info.role = "expression_matrix"  # genes (rows) x samples (cols)
        elif info.n_rows <= 50:
            info.role = "sample_metadata"
        else:
            info.role = "data_table"

    # Surface low-cardinality categorical columns = the experiment design.
    # Only when we read the whole table, else the counts would be a partial
    # (and misleading) slice of a large annotation file.
    saw_all = preview_rows < _PREVIEW_ROWS
    if saw_all:
        for col in df.columns:
            s = df[col]
            is_categorical = not pd.api.types.is_numeric_dtype(s) and not (
                pd.api.types.is_datetime64_any_dtype(s)
            )
            if is_categorical:
                vc = s.value_counts()
                if 1 < len(vc) <= 6:
                    groups = ", ".join(f"{k}={v}" for k, v in vc.items())
                    info.notes.append(f"groups in '{col}': {groups}")

    # Capture probable sample-ID values for value-based join detection.
    id_col = next(
        (c for c in df.columns
         if str(c).strip().lower() in {"sample", "samples", "sample_id", "sampleid", "id", "name"}),
        None,
    )
    if id_col is None and info.role == "sample_metadata":
        id_col = df.columns[0]
    if id_col is not None:
        vals = [str(v) for v in df[id_col].dropna().unique()[:300]]
        if vals:
            info.id_column = str(id_col)
            info.id_values = vals


def _detect_joins(files: list[FileInfo]) -> list[str]:
    joins: list[str] = []
    tables = [f for f in files if f.columns]
    # Shared column names between tables (case-insensitive).
    for i, a in enumerate(tables):
        for b in tables[i + 1:]:
            shared = {c.lower() for c in a.columns} & {c.lower() for c in b.columns}
            shared.discard("")
            if shared:
                joins.append(f"{a.name} <-> {b.name} on shared columns: {sorted(shared)}")
    # Value-based sample-axis link: do a metadata table's sample IDs appear as
    # another table's column headers? (classic RNA-seq: coldata sample IDs ==
    # count-matrix columns). This also surfaces mismatches — e.g. samples in the
    # counts matrix but absent from metadata, the tell-tale of a silent drop.
    for f in files:
        if not f.id_values:
            continue
        ids = set(f.id_values)
        for g in files:
            if g is f or not g.columns:
                continue
            cols = set(g.columns)
            matched = ids & cols
            if len(matched) < 2:
                continue
            msg = (
                f"{f.name}.{f.id_column} ({len(ids)} ids) matches "
                f"{len(matched)} columns of {g.name}"
            )
            extra = sorted(c for c in cols - ids if not str(c).lower().startswith(("unnamed", "gene")))
            missing = sorted(ids - cols)
            if extra:
                msg += f"; columns not in metadata (possible dropped samples): {extra[:6]}"
            if missing:
                msg += f"; metadata ids not in columns: {missing[:6]}"
            joins.append(msg)
    return joins


def build_manifest(data_dir: str | Path) -> CapsuleManifest:
    data_dir = Path(data_dir)
    manifest = CapsuleManifest(data_dir=str(data_dir))
    for path in sorted(p for p in data_dir.rglob("*") if p.is_file()):
        ext = path.suffix.lower()
        info = FileInfo(
            name=path.name,
            ext=ext,
            size_bytes=path.stat().st_size,
            role=_role_from_name(path.name) or "unknown",
        )
        if ext in IMAGE:
            info.role = "image"
        elif ext in SEQUENCE:
            info.role = info.role if info.role != "unknown" else "sequence"
        elif ext in VARIANTS:
            info.role = "variants"
        elif ext in TABULAR and path.stat().st_size <= _MAX_FULL_BYTES:
            _profile_table(info, path)
        elif ext in TABULAR:
            info.notes.append("large table; not profiled")
        manifest.files.append(info)
    manifest.joins = _detect_joins(manifest.files)
    return manifest


if __name__ == "__main__":
    import sys

    d = sys.argv[1] if len(sys.argv) > 1 else "."
    m = build_manifest(d)
    print(m.render())
    print("\n--- JSON ---")
    print(json.dumps(m.to_dict(), indent=2)[:1500])
