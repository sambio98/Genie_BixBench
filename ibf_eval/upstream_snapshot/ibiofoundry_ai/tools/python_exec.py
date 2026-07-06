"""
ibiofoundry_ai/tools/python_exec
[[ibiofoundry_ai.tools.python_exec]]
https://github.com/iBioFoundry/iBioFoundry-AI/tree/main/ibiofoundry_ai/tools/python_exec
Test file: tests/ibiofoundry_ai/tools/test_python_exec.py

Orchestrator-facing tool that delegates to the coding sub-agent.
The orchestrator describes what it wants in natural language; the sub-agent
writes, runs, debugs, and retries Python code in its own context window.
"""

import ast
import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from ..config import WorkspaceConfig
from ..model_config import MODEL_CONFIG, to_pydantic_ai_model_id
from ..request_keys import RequestKeyPlan
from ..request_models import ModelRole, build_turn_model
from ._registry import (
    TOOL_CATALOG_SPIN_THRESHOLD,
    TOOL_METADATA,
    ToolError,
    agent_tool,
    cap_tool,
    get_python_exec_count,
    increment_python_exec_count,
    register_output,
    set_forced_emission,
)

logger = logging.getLogger(__name__)

MAX_CODE_AGENT_CALLS_PER_TURN = 5
"""Max run_python_code invocations per orchestrator turn.

Resets every turn via the ContextVar counter in ``_registry.py``, which is
zeroed by ``set_budget_context()`` at the start of each ``agent.run()`` call.
In 20-turn case studies this gives the agent up to 100 total code executions
instead of 5 total across the session.
"""

CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS: float = float(
    os.environ.get("CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS", "600")
)
"""Spawn-level wall-clock watchdog (seconds) on a whole ``run_code_agent`` call.

bix-31 (session ``bixsafe_1782703701_21``) emitted a ``tool_invoke`` with no
matching ``tool_result``: the code_agent sub-run hung at an await point (or died
silently) and the orchestrator turn never resumed -- a hard no-answer with no
``<solution>`` to salvage. Nothing in-process bounds a turn by wall-clock time
(``chat_handler`` caps only the request *count*), so the only existing clock is
viku's external ~900s BixBench harness cutoff, which arrives as an unrecoverable
hard kill.

This watchdog wraps the whole spawn so a hang/crash instead returns a
recoverable ``ToolError`` (``timeout``/``upstream`` -> ``retry_transient``)
before that cutoff, keeping control in the orchestrator loop (retry, or emit a
held partial via Block A's ``<solution>`` floor). It is the SPAWN-level guard,
distinct from and nested above Block B's per-attempt SIGALRM cap
(``EXECUTE_CODE_TIMEOUT_SECONDS`` in ``code_agent.py``): the invariant is
``per_attempt(180s) < watchdog(600s) < harness_envelope(~900s)``.

Scope: it bounds hangs at ``await`` points inside ``run_code_agent`` (the
model-provider HTTP call, network awaits, deadlocks) and converts spawn-level
raises into a ``ToolError``. It does NOT interrupt a synchronous blocking
``exec()`` (that holds the event-loop thread, so the timeout callback can never
fire) -- that hang is Block B's SIGALRM domain and, durably, the subprocess
refactor in `#979`. Env-overridable via ``CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS``;
``<= 0`` disables the timeout (the crash guard still applies)."""

CODE_AGENT_EMIT_MARGIN_SECONDS: float = float(
    os.environ.get("CODE_AGENT_EMIT_MARGIN_SECONDS", "30")
)
"""Block G: seconds reserved AFTER a code_agent's wall-clock budget so the
genie-api handler can still force-emit + persist the ``<solution>`` floor before
its turn deadline. The code_agent's effective budget is bounded to
``deadline - now - this`` so the sub-run finishes by ``deadline - margin``,
leaving this window for the result to propagate, the between-nodes deadline check
to fire, and ``messages.json``/``done`` to be written. Env-overridable."""

CODE_AGENT_MIN_USEFUL_SECONDS: float = float(
    os.environ.get("CODE_AGENT_MIN_USEFUL_SECONDS", "60")
)
"""Block G: if the remaining turn budget would give a code_agent less than this,
``run_python_code`` REFUSES to launch it (returns a ToolError) rather than start
a doomed spin that the turn deadline would kill mid-run (bix-61: two ~590s spins
into the ~900s harness reaper). The model is told to report the best result
already computed -- never to invent one. Env-overridable."""

_LAST_COMPUTED_VALUE_MAX_CHARS = 2000
"""Cap on ``deps.last_computed_value`` (stdout snapshot for the forced-emission
exit, `#954`). Bounds what a runaway print could push into the final <solution>
answer; a real scalar answer (e.g. ``"CD14"``) is far under this."""

_SCALAR_PARTIAL_MAX_CHARS = 240
_SCALAR_PARTIAL_MAX_LINES = 8


def _looks_like_scalar(text: str) -> bool:
    """True if *text* is a short, value-like stdout worth surfacing as a partial.

    Block B incremental stash: a NON-success ``run_python_code`` output is a
    salvaged partial from a code_agent run that timed out or exhausted its budget
    (``code_agent`` returns ``last_success_output or last_computed_partial``). It
    only feeds ``deps.last_computed_value`` -- and thus Block A's terminal-path
    ``<solution>`` floor -- when it actually reads like an answer: a scalar or a
    short token list. A directory listing (bix-32's bare rglob), a large dump, or
    a traceback is NOT a plausible answer and must not masquerade as one. The
    no-fabrication contract still holds either way: the value was really printed
    by the code, never invented. Pure inspection -- never raises.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > _SCALAR_PARTIAL_MAX_CHARS:
        return False
    nonblank = [line for line in stripped.splitlines() if line.strip()]
    if len(nonblank) > _SCALAR_PARTIAL_MAX_LINES:
        return False
    if "traceback (most recent call last)" in stripped.lower():
        return False
    return True


# ── Package allowlist ────────────────────────────────────
# Map pip names to import names. None = internal package (hidden from LLM).

_PIP_TO_IMPORT: dict[str, str | None] = {
    # Internal packages (hidden from LLM)
    "pydantic-ai": None,
    "pydantic-ai-slim": None,
    "typer[all]": None,
    "rich": None,
    "httpx": None,
    "python-dotenv": None,
    "openai": None,
    "tabulate": None,
    "pydantic": None,
    # CLI-only console scripts (invoked via run_tool, never imported) -- hide
    # them from AVAILABLE_IMPORTS so the agent does not try `import phykit`.
    "phykit": None,
    "clipkit": None,
    # Packages where pip name != import name
    "biopython": "Bio",
    "rdkit": "rdkit (e.g. from rdkit import Chem)",
    "scikit-learn": "sklearn",
    "scikit-bio": "skbio",
    "scikit-image": "skimage",
    "beautifulsoup4": "bs4",
    "python-libsbml": "libsbml",
    "umap-learn": "umap",
    "biom-format": "biom",
    "primer3-py": "primer3",
    "PyVCF3": "vcf",
    "epam.indigo": "indigo",
    "ChemFormula": "chemformula",
    "cobra": "cobra (COBRApy)",
    "snfpy": "snf",
    "gprofiler-official": "gprofiler",
    "chembl-webresource-client": "chembl_webresource_client",
    "matplotlib-venn": "matplotlib_venn",
    "optuna-integration": "optuna_integration",
    # pip name is capitalized but the importable module is lowercase
    "GSEApy": "gseapy",
}

# Terse one-line purpose for the long-tail / specialty bio libs only. Keyed by
# IMPORT name (the string rendered in AVAILABLE_IMPORTS, post pip->import
# resolution). The common stack (pandas/numpy/scipy/matplotlib/seaborn/sklearn/
# Biopython/RDKit) is intentionally omitted -- the model already knows those, so
# annotating them would add noise. These specialty libs are the ones the code
# agent is most likely to mis-use without a hint about what they are for.
_IMPORT_PURPOSE: dict[str, str] = {
    "pydeseq2": "DESeq2 differential expression analysis",
    "pyreadr": "read R .rds/.RData files (e.g. DESeq2 result tables)",
    "rdata": "pure-Python reader for R .rds/.RData files",
    "gseapy": "GO/KEGG/Reactome/GSEA functional enrichment",
    "scanpy": "single-cell RNA-seq analysis",
    "anndata": "annotated single-cell data matrices",
    "pysam": "read/parse SAM/BAM/VCF alignment files",
    "gprofiler": "g:Profiler functional enrichment API",
    "mygene": "gene-ID mapping API (e.g. Ensembl->Entrez)",
    "chembl_webresource_client": "ChEMBL compound/bioactivity API",
    "optuna": "hyperparameter optimization",
    "optuna_integration": "Optuna + sklearn/xgboost integrations",
    "matplotlib_venn": "Venn diagrams",
    "upsetplot": "UpSet plots",
}

_ENV_DIR = Path(__file__).resolve().parent.parent.parent / "env"


def _build_available_imports() -> str:
    """Build a curated list of import names from requirements.txt + mapping.

    Parses each requirement line with ``packaging.requirements.Requirement`` so
    that PEP-508 specifiers (``>=``, ``==``, ``~=``, extras like
    ``package[extra]``, environment markers) are stripped correctly. The
    previous sequential-split parser mishandled extras (``package[extra]``
    survived as the key) and tilde-equal specifiers.

    Specialty/long-tail imports present in ``_IMPORT_PURPOSE`` render with a
    terse ``-- {purpose}`` suffix so the code agent knows what they are for;
    common, well-known imports render bare.
    """
    lines = []
    for raw_line in (_ENV_DIR / "requirements.txt").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            pip_name = Requirement(line).name
        except InvalidRequirement:
            logger.warning("Skipping unparsable requirement line: %r", line)
            continue
        if pip_name in _PIP_TO_IMPORT:
            import_name = _PIP_TO_IMPORT[pip_name]
            if import_name is None:
                continue
        else:
            # Not in mapping -- show as-is (assume pip name == import name)
            import_name = pip_name
        purpose = _IMPORT_PURPOSE.get(import_name)
        if purpose is not None:
            lines.append(f"  - {import_name} — {purpose}")
        else:
            lines.append(f"  - {import_name}")
    return "\n".join(sorted(lines))


AVAILABLE_IMPORTS = _build_available_imports()

# Canonical-usage cards for the highest-value / most-doubtful specialty libs.
# Interpolated into the code-agent system prompt (prompts/code_agent.md) via the
# {library_cookbook} placeholder, AFTER the AVAILABLE PACKAGES section. Each card
# is a tight, runnable snippet showing the standard API an expert would write --
# the goal is to stop the agent inventing wrong function names for libraries it
# rarely sees. Keep this lean: only libs where the canonical call pattern is
# non-obvious and easy to get wrong.
LIBRARY_COOKBOOK = """\
LIBRARY COOKBOOK (canonical usage for specialty libraries):

# pydeseq2 -- differential expression (counts: samples x genes, raw integers)
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
dds = DeseqDataSet(counts=counts_df, metadata=meta_df, design="~condition")
dds.deseq2()
stats = DeseqStats(dds, contrast=["condition", "treated", "control"])
stats.summary()
res = stats.results_df  # per-gene log2FoldChange, pvalue, padj
# apeglm LFC shrinkage -- USE pydeseq2, NOT R: DESeq2/edgeR/limma and Rscript are
# NOT installed and subprocess is blocked, so never write or run a .R script.
# coeff is a COLUMN of dds.varm["LFC"] (patsy style, e.g. "condition[T.treated]"),
# NOT a contrast list and NOT "condition_treated_vs_control":
coeff = [c for c in dds.varm["LFC"].columns if c != "Intercept"][-1]
stats.lfc_shrink(coeff=coeff)  # apeGLM prior; pass coeff= NOT contrast=; p-values unchanged
res = stats.results_df  # log2FoldChange column now holds the shrunk LFCs

# If the ONLY count data on disk is already normalized (non-integer values -- check
# with (counts_df % 1 != 0).any().any()), rounding it and feeding it to DeseqDataSet as
# if raw double-normalizes (pydeseq2 re-fits its own size factors on top of the
# existing normalization). VERIFIED: naively setting `dds.obs["size_factors"] = 1.0`
# before calling `dds.deseq2()` does NOT work with pydeseq2==0.5.4 -- deseq2() silently
# re-fits and overwrites it. To actually pin size factors to 1, call the individual
# fit steps directly instead of the deseq2() wrapper:
#   dds = DeseqDataSet(counts=counts_df.round().astype(int), metadata=meta_df, design="~condition")
#   dds.obs["size_factors"] = 1.0
#   dds.layers["normed_counts"] = dds.X  # pre-seed so deseq2() doesn't re-derive it
#   dds.fit_genewise_dispersions()
#   dds.fit_dispersion_trend()
#   dds.fit_dispersion_prior()
#   dds.fit_MAP_dispersions()
#   dds.fit_LFC()
#   dds.calculate_cooks()
#   dds.refit()
# Caution: in practice, when the data is already well-normalized, pydeseq2's own
# auto-fit size factors often land close to 1.0 anyway (verify by printing
# dds.obs["size_factors"] after a normal deseq2() run) -- so this may be a smaller
# effect than expected. If pinning size factors doesn't materially change your
# significant-gene count, the discrepancy from a reference value likely has a
# different cause (design/contrast/threshold, or a missed data sheet -- see the
# multi-sheet Excel note above).

# Pre-computed R result tables (.rds / .RData): READ them, do NOT re-run R. A
# DESeq2 results .rds is just a serialized data frame -- never report NA because
# the table is in an R format. pyreadr returns an ordered dict; an unnamed .rds
# object is keyed by None (.RData objects are keyed by their R variable names):
import pyreadr
res = pyreadr.read_r("deseq2_results.rds")[None]  # -> DataFrame (rdata is a pure-Python fallback)
# If a SHRUNKEN-LFC DE table already exists among the artifacts (a DESeq2
# lfcShrink/apeglm output -- a .csv/.tsv/.rds with a log2FoldChange column),
# READ it; do NOT re-derive LFC from raw counts when the answer is already on disk.

# gseapy -- functional enrichment
import gseapy as gp
enr = gp.enrichr(gene_list=genes, gene_sets=["KEGG_2021_Human", "GO_Biological_Process_2021"], outdir=None)
print(enr.results.head())  # Term, Overlap, Adjusted P-value, Genes
pre = gp.prerank(rnk=ranked_df, gene_sets="KEGG_2021_Human", outdir=None)  # ranked_df: gene, score
print(pre.res2d.head())  # Term, NES, FDR q-val

# scanpy -- single-cell RNA-seq (AnnData: cells x genes)
import scanpy as sc
adata = sc.read_h5ad("data.h5ad")
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, n_top_genes=2000)
sc.pp.pca(adata)
sc.pp.neighbors(adata)
sc.tl.leiden(adata)  # clusters in adata.obs["leiden"]
sc.tl.umap(adata)

# mygene -- gene-ID mapping (batch)
import mygene
mg = mygene.MyGeneInfo()
out = mg.querymany(["TP53", "ENSG00000141510"], scopes="symbol,ensembl.gene",
                   fields="entrezgene,symbol", species="human", as_dataframe=True)

# gprofiler -- g:Profiler enrichment
from gprofiler import GProfiler
gpro = GProfiler(return_dataframe=True)
res = gpro.profile(organism="hsapiens", query=["TP53", "BRCA1", "EGFR"])
print(res[["source", "name", "p_value"]].head())  # GO/KEGG/REAC terms

# DepMap CRISPRGeneEffect.csv -- Chronos essentiality score SIGN CONVENTION.
# A MORE NEGATIVE Chronos score means a gene is MORE essential (knocking it out is
# more harmful to cell viability) -- the opposite of the intuitive "higher = more
# essential" reading. If a question frames "essentiality" as a positive-scaled
# quantity (e.g. "strong POSITIVE correlation between expression and essentiality"),
# check whether you should negate the raw Chronos score before correlating -- the
# question's intended sign convention isn't always the raw file's sign convention.

# pysam -- read alignments / variants (BAM and VCF must be indexed for fetch)
import pysam
bam = pysam.AlignmentFile("aln.bam", "rb")
for read in bam.fetch("chr1", 1000, 2000):
    print(read.query_name, read.reference_start, read.mapping_quality)
bam.close()
vcf = pysam.VariantFile("variants.vcf.gz")
for rec in vcf.fetch("chr1", 1000, 2000):
    print(rec.chrom, rec.pos, rec.ref, list(rec.alts))

# phylogenomics from single-copy orthologs (run via run_tool) -- BUSCO / orthogroup
# archives hold SEQUENCES, not finished trees; BUILD each tree, never scan for a
# pre-existing .nwk/.treefile and report NA. Per shared single-copy ortholog:
# 1. write one sequence per taxon into a FASTA; 2. align (mafft -> STDOUT, capture
# it); 3. infer (fasttree -> Newick on STDOUT; -lg for protein); 4. measure with
# phykit total_tree_length. For a MULTI-ortholog set, aggregate the per-ortholog
# tree-length VALUES within each group (e.g. their median per group) -- this is
# the only sanctioned median: never median arbitrary numeric fields as a
# stand-in for a metric you could not compute (see treeness card below).
aln = output_file("ortholog.aln")
aln.write_text(run_tool("mafft", "--auto", str(ortholog_fasta)).stdout)  # protein/nt
nwk = output_file("ortholog.nwk")
nwk.write_text(run_tool("fasttree", "-lg", str(aln)).stdout)  # drop -lg / add -nt for DNA
# phykit single-input subcommands take the tree as a POSITIONAL arg (NOT -t):
tree_length = float(run_tool("phykit", "total_tree_length", str(nwk)).stdout.strip())

# Ortholog SET SCOPE for cross-group statistics (Mann-Whitney U, medians, ratios, etc.
# across all orthologs in a group): default to using EVERY ortholog that has the
# artifact the metric needs (a .treefile for tree-length/treeness/DVMC/patristic-
# distance/long-branch-score stats -- these are well-defined even for a 2-taxa tree,
# a single branch with a real length, so do NOT exclude 2-taxa orthologs for tree-based
# metrics). The one documented exception: parsimony-informative-sites is
# MATHEMATICALLY GUARANTEED to be 0% for any alignment with <3 taxa (you cannot have 2
# character states each occurring >=2 times with fewer than 3 sequences) -- for THIS
# SPECIFIC metric only, restrict to >=3-taxa orthologs, since <3-taxa values are a
# constant mathematical artifact, not a real measurement. Do not generalize this
# exception to other metrics without first checking whether the metric is actually
# degenerate at that taxon count. Never restrict further to full-4-taxa orthologs
# "because partial ones look degenerate" without checking first -- that shrinks and
# biases the sample when the metric doesn't require it. Sanity-check your sample size
# against the statistic's bounds (e.g. Mann-Whitney U cannot exceed n1*n2) -- if the
# expected/target value exceeds n1*n2 for your chosen subset, your subset is too small.

# treeness / RCV -- DERIVE them with PhyKIT from a tree/alignment you already
# have; they are NOT stored fields to grep for, and a metric you cannot compute
# is NA, never a fabricated median of unrelated numbers (a wrong number is worse
# than NA). An IQ-TREE .treefile IS the Newick tree (phykit input); the .iqtree
# file is a run LOG, not a tree. Single-input subcommands are positional:
treeness = float(run_tool("phykit", "treeness", str(treefile)).stdout.strip())  # sum(internal BL)/total BL, in [0,1]
rcv = float(run_tool("phykit", "relative_composition_variability", str(alignment)).stdout.strip())  # RCV takes the ALIGNMENT, not a tree
# treeness is a proportion -> sanity_check_value flags an out-of-range fabrication (e.g. 228):
flag = sanity_check_value(treeness, "proportion")

# Trimmomatic (run via run_tool) -- read/adapter quality trimming. When a question
# gives exact Trimmomatic flags (ILLUMINACLIP/LEADING/TRAILING/SLIDINGWINDOW/MINLEN),
# run the REAL tool with those exact flags -- do not hand-roll the algorithm in
# pandas/numpy; ILLUMINACLIP's seed-and-extend adapter matching and the sliding-window
# average-quality cutoff are not simple threshold checks, and a from-scratch
# reimplementation can miss the true count by 1-2 orders of magnitude even when the
# overall approach looks reasonable.
#
# ILLUMINACLIP's adapter FASTA (TruSeq3-PE.fa, NexteraPE-PE.fa, ...) ships with the
# Trimmomatic install, NOT in DATA_DIR -- a question naming one by filename does not
# mean it was uploaded. subprocess/`find` are blocked, so locate it with a bounded
# glob over the install roots a package manager would use:
import glob, sys
from pathlib import Path
_roots = [sys.prefix, str(Path(sys.prefix).parent), "/usr/share", "/usr/local/share", "/opt"]
_hits = [p for r in _roots for p in glob.glob(f"{r}/**/TruSeq3-PE.fa", recursive=True)]
if _hits:
    adapter_fa = _hits[0]
else:
    # Fallback: the standard 2-record TruSeq3-PE.fa content (verified to reproduce
    # the reference count below) -- write it out if no installed copy is found.
    adapter_fa = output_file("TruSeq3-PE.fa")
    adapter_fa.write_text(
        ">PrefixPE/1\nTACACTCTTTCCCTACACGACGCTCTTCCGATCT\n"
        ">PrefixPE/2\nGTGACTGGAGTTCAGACGTGTGCTCTTCCGATCT\n"
    )

# args mirror the CLI verbatim, one per run_tool arg:
r = run_tool(
    "trimmomatic", "PE", "-phred33",
    str(r1_fastq), str(r2_fastq),
    str(out1_paired), str(out1_unpaired), str(out2_paired), str(out2_unpaired),
    f"ILLUMINACLIP:{adapter_fa}:2:30:10", "LEADING:3", "TRAILING:3",
    "SLIDINGWINDOW:4:15", "MINLEN:36",
)
if r.returncode != 0:
    raise RuntimeError(f"trimmomatic failed: {r.stderr}")
# Trimmomatic PE prints its summary to STDERR, one line per sample, e.g.:
#   Input Read Pairs: 1000000 Both Surviving: 940000 (94.00%) Forward Only Surviving:
#   40000 (4.00%) Reverse Only Surviving: 8000 (0.80%) Dropped: 12000 (1.20%)
# "how many reads/pairs were discarded/removed by QC" means pairs that did NOT come
# through as an intact pair -- Input Read Pairs MINUS Both Surviving (equivalently
# Forward Only + Reverse Only + Dropped) -- NOT the "Dropped" field alone, which
# counts only pairs where BOTH mates failed. A mate demoted to the unpaired output
# is still conventionally "discarded" from the paired-end result. Verified against a
# real BixBench capsule: summing (Input Read Pairs - Both Surviving) across the
# provided samples landed within 0.005% of the reference value, where the "Dropped"-
# only reading was off by ~2 orders of magnitude. If more than one paired-end sample
# is provided and the question does not name one specifically, sum this count across
# all of them.
import re as _re
m = _re.search(r"Input Read Pairs: (\d+) Both Surviving: (\d+)", r.stderr)
discarded = int(m.group(1)) - int(m.group(2))

# "Describe/characterize the SHAPE of a distribution" (e.g. skewed vs Normal vs
# bimodal) wants a QUALITATIVE/visual read from descriptive statistics (skewness,
# kurtosis, a histogram), NOT a formal normality hypothesis test. At large n (tens of
# thousands+), Shapiro-Wilk / D'Agostino-Pearson / Kolmogorov-Smirnov reject normality
# for almost ANY real dataset, however normal-looking, because their power grows with
# sample size while detecting arbitrarily small deviations -- a formally-significant
# rejection at n=100,000+ is not evidence the shape looks non-normal. Judge shape from
# skewness (|skew| < ~0.5 reads as approximately symmetric/Normal-like), kurtosis, and
# a plotted histogram -- not from a p-value.

# EDA / analysis guards (pre-injected -- DO NOT import; call directly)
# inventory_inputs(root): inspect every input file BEFORE analysing it.
inv = inventory_inputs(DATA_DIR)
print(inv.summary())  # shape, columns, an AXIS warning for wide gene x sample
                      # matrices, and ids parsed from filenames
# join_key_candidates(left_df, right_df, left_filenames=..., right_filenames=...):
# call this when a merge/intersection is unexpectedly EMPTY -- it ranks alternate
# keys (value normalizations + filename-encoded ids) so you can recover the join
# instead of reporting NA.
for c in join_key_candidates(left_df, right_df, right_filenames=chip_file_names):
    print(c.left_col, "<->", c.right_col, "via", c.normalization, "overlap", c.overlap)
# sanity_check_value(value, kind): check a result's sign/range before reporting.
# kinds: proportion, probability, percentage, correlation, replicate_correlation,
# odds_ratio, ratio_nonneg, pvalue, count.
flag = sanity_check_value(rho, "replicate_correlation")
if not flag.ok:
    print("IMPLAUSIBLE:", flag.message)  # re-examine inputs/method, do not report it
"""

# ── Code agent gate ────────────────────────────────────
# LLM introspection gate: before code_agent runs, a cheap model checks
# whether a dedicated tool should be used instead.

_CODE_AGENT_BYPASS_KEYWORDS: frozenset[str] = frozenset(
    {
        # Visualization (always code agent)
        "plot",
        "chart",
        "graph",
        "histogram",
        "scatter",
        "heatmap",
        "barplot",
        "boxplot",
        "violin",
        "matplotlib",
        "seaborn",
        # Explicit library references (user wants code)
        "pandas",
        "dataframe",
        "numpy",
        "scipy",
        "sklearn",
        "biopython",
        "rdkit",
        "cobra",
        "scanpy",
        "mdanalysis",
    }
)

_REDIRECT_PATTERNS: frozenset[str] = frozenset(
    {
        "read file",
        "read the file",
        "load file",
        "load the file",
        "open file",
        "open the file",
        "display file",
        "show file",
        "print file",
        "extract from file",
        "read csv",
        "read the csv",
        "load csv",
        "load the csv",
        # W2 additions: single-file inspection tasks the agent kept trying
        # to route through run_python_code. The lit-pah1 batch logged
        # turn 6 where the router correctly rejected these -- but only
        # after the code agent had already burned its 5-attempt budget.
        # Rejecting earlier keeps the shared budget intact for real work.
        "view file",
        "view the file",
        "view the csv",
        "view csv",
        "inspect file",
        "inspect the file",
        "inspect csv",
        "inspect the csv",
        "cat file",
        "tail file",
        "head file",
        "preview csv",
        "preview the csv",
    }
)
"""Multi-word phrases that indicate file-read tasks.

These should always use ``read_file``, never ``code_agent``.
Checked via substring match BEFORE bypass keywords, so
"load csv" routes to ``read_file`` instead of bypassing via "csv".
"""

_TEXT_GEN_PATTERNS_STRONG: frozenset[str] = frozenset(
    {
        # Q&A reasoning offloaded ("decide which option matches" -- DbQA pattern)
        "decide which option",
        "decide which gene",
        "decide which variant",
        "decide which sequence",
        "pick the option",
        "pick the correct",
        "answer the question",
        "answer the multiple-choice",
        "answer the benchmark",
        "answer benchmark",
        "reason about which",
        "reason about whether",
        "reason from your knowledge",
        "infer which option",
        "infer whether",
        # Drafting completion blocks / summaries from session memory
        "draft a completion",
        "draft completion",
        "draft a status",
        "draft a summary",
        "draft a verification",
        "draft a response",
        "write a paragraph",
        "write a markdown",
        "write a status",
        "write a note",
        "create a completion",
        "create a small note",
        "create a status",
        "completion block",
        "summarize from memory",
        "summarize the session",
        "summarize the question",
        "summarize the spec",
        "summarize from this chat",
        # Phrases that demand PROSE output -- unambiguous text generation even
        # next to a computation, so they stay strong (unlike "explain why").
        "explain in prose",
        "explain in a paragraph",
        "format as a markdown",
        "format the answer",
        "no-op",
        "no op tool call",
        "satisfy tool requirement",
        "required by system",
        "required before responding",
    }
)
"""Multi-word phrases that ALWAYS indicate text-generation / pure-reasoning.

These tasks must NOT route to the code agent regardless of any computation or
value-emission signal in the same task. The orchestrator must respond directly
with its own reasoning. Checked FIRST (after ``_REDIRECT_PATTERNS``) -- this
ordering is load-bearing: it keeps the "draft a summary wrapped in <solution>"
backdoor closed (the emission signal must not turn an explicit prose-draft into
a code task). Checked BEFORE ``_CODE_AGENT_BYPASS_KEYWORDS`` so "summarize as a
markdown histogram description" hits text-gen rejection rather than the bypass.

Seeded from the failing DbQA rows where ``code_agent`` self-refusal at
``prompts/code_agent.md:45`` fired correctly but the orchestrator
paraphrased the prose task and re-issued, defeating dedup. See
``[[plan.block-run-python-code-text-gen-misuse.2026.05.05]]``.
"""

_TEXT_GEN_PATTERNS_WEAK: frozenset[str] = frozenset(
    {
        "explain why",
        "explain that",
    }
)
"""Explanatory phrases that legitimately attach to a real computation.

"compute the differential-expression table from counts.csv and explain why gene
X is up-regulated" is genuine analysis, not text generation -- yet the bare
substring ``explain why`` used to substring-reject it (bix-25). These refuse
ONLY when the task carries no ``_has_computation_intent`` (no compute verb and no
named-file value emission); with intent present they fall through to the
computation-allow / LLM gate. Strong patterns above still refuse first, so a
prose draft that happens to contain "explain why" is unaffected.
"""

# Arithmetic / statistical verbs that denote genuine numeric computation.
# Word-boundary matched (not bare substring) so "mean" never fires on
# "meaning" and "sum" never fires on "summary" -- the substring collisions
# that would otherwise let a prose task masquerade as computation. See
# ``[[plan.fix-run-python-code-gate-compute-emission.2026.06.21]]``.
_COMPUTATION_SIGNAL_RE = re.compile(
    r"\b("
    r"comput\w*|calculat\w*|"
    r"statistic\w*|p-?values?|t-?tests?|chi-?squared?|"
    r"correlat\w*|regress\w*|"
    r"means?|medians?|averages?|variances?|std|standard deviations?|"
    r"quantiles?|percentiles?|ratios?|proportions?|fractions?"
    r")\b"
)

# Phrases that request EMISSION of an already-computed value -- the BixBench
# finalize step (e.g. "wrapped in <solution> tags with the ratio value ...").
# Emitting a computed value is a code task, not text generation; it must reach
# the code agent, never the text-gen refusal. Distinctive enough for substring
# matching (no word-boundary collisions).
_EMISSION_SIGNALS: frozenset[str] = frozenset(
    {
        "<solution>",
        "</solution>",
        "wrapped in",
        "return the final",
        "final answer",
        "final value",
    }
)


def _has_computation_signal(task_lower: str) -> bool:
    """True when *task_lower* names an arithmetic / statistical computation."""
    return bool(_COMPUTATION_SIGNAL_RE.search(task_lower))


def _has_emission_signal(task_lower: str) -> bool:
    """True when *task_lower* asks to emit a computed value (e.g. <solution>)."""
    return any(sig in task_lower for sig in _EMISSION_SIGNALS)


def _has_computation_intent(task_lower: str) -> bool:
    """Gate-level computation detector: a real code task.

    A task is computational intent if it either names a numeric computation
    OR asks to emit a computed value. Used to (a) exempt "load the CSV AND
    compute X" from the file-read redirect, (b) short-circuit straight to the
    code agent before the LLM gate, and (c) gate the WEAK text-gen patterns
    ("explain why"/"explain that") so they refuse only when no compute intent is
    present. Ordered AFTER ``_TEXT_GEN_PATTERNS_STRONG`` in
    ``_should_use_code_agent`` so an explicit prose-draft phrase still wins --
    "draft a summary wrapped in <solution> tags" stays text-gen, not code.
    """
    return _has_computation_signal(task_lower) or _has_emission_signal(task_lower)


_tool_catalog_cache: str | None = None


def _build_tool_catalog() -> str:
    """Build a compact catalog of all registered tools for the gate prompt.

    Lazily built on first call to ensure all tool modules have been imported
    and registered in ``TOOL_METADATA`` (avoids import-order dependency).
    """
    global _tool_catalog_cache  # noqa: PLW0603
    if _tool_catalog_cache is not None:
        return _tool_catalog_cache

    lines: list[str] = []
    for name, meta in sorted(TOOL_METADATA.items()):
        if name == "run_python_code":
            continue
        first_sentence = meta.description.split(".")[0]
        lines.append(f"- {name}: {first_sentence}")

    _tool_catalog_cache = "\n".join(lines)
    return _tool_catalog_cache


class GateDecision(BaseModel):
    """Routing verdict from the run_python_code gate.

    Three states for the orchestrator: proceed to the code agent, redirect to a
    dedicated tool, or refuse entirely as a text-generation task that the
    orchestrator should answer from its own reasoning.
    """

    decision: Literal["use_code_agent", "redirect_to_tool", "respond_directly"] = Field(
        description="Routing verdict for this task"
    )
    suggested_tool: str | None = Field(
        default=None,
        description=(
            "Dedicated tool name to call instead. Required when "
            "decision='redirect_to_tool'; ignored otherwise."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "One-line reason surfaced in the orchestrator-facing error "
            "message. Empty when the gate proceeds with code_agent."
        ),
    )


_GATE_SYSTEM_PROMPT: str = (
    "You are a tool router. Given a task description and a catalog of "
    "available tools, decide one of three outcomes:\n\n"
    "1. use_code_agent -- the task requires actual computation: parsing a "
    "file the user named, plotting a chart, running a statistical test, "
    "comparing sequences from a named FASTA, transforming a CSV.\n"
    "2. redirect_to_tool -- a dedicated tool from the catalog is the right "
    "fit (e.g. zotero_library, ncbi_blast_search, predict_kcat). Set "
    "suggested_tool to the exact tool name.\n"
    "3. respond_directly -- this is text generation or pure reasoning. The "
    "orchestrator should answer from its own knowledge. NO tool should be "
    "called.\n\n"
    "File/data heuristic for use_code_agent vs respond_directly:\n"
    "- If the task names a specific file path, column, or input artifact "
    "(e.g. 'compare sequences in seqs.fasta', 'plot histogram of df'), "
    "use_code_agent.\n"
    "- If the answer comes from prior knowledge alone with no file or data "
    "to process (e.g. 'decide which gene is annotated in MGI', 'pick the "
    "correct multiple-choice option', 'draft a completion summary from "
    "session memory'), respond_directly.\n\n"
    "Concrete examples to REJECT as respond_directly:\n"
    "- 'Determine which option (PCSK5, MCTP1, DMXL1, MNX1) is associated "
    "with Currarino syndrome'\n"
    "- 'Create a small note: explain that the benchmark spec requires "
    "exactly one save_results call'\n"
    "- 'Create and print a completion block text with fields STATUS, "
    "DATASET, ANSWER_COUNT'\n"
    "- 'Pick option A-D most likely to contain a pathogenic variant per "
    "ClinVar' (no file given)\n\n"
    "Concrete examples to ACCEPT as use_code_agent (real computation, "
    "even when the prose reads like Q&A):\n"
    "- 'Compare four protein sequences in data/input/seqs.fasta and "
    "identify amino-acid differences' (string diff)\n"
    "- 'Determine which sequence in seqs.csv is shortest' (len() compare)\n"
    "- 'Plot a histogram of column kcat from kcat_results.csv'\n\n"
    "Always set the rationale field to a one-sentence reason."
)

_gate_agent: Agent[None, GateDecision] = Agent(
    to_pydantic_ai_model_id(MODEL_CONFIG.tool_result_summarizer),
    output_type=GateDecision,
    system_prompt=_GATE_SYSTEM_PROMPT,
    model_settings=MODEL_CONFIG.cache_settings(),
    defer_model_check=True,
)


async def _should_use_code_agent(task: str, key_plan: RequestKeyPlan) -> GateDecision:
    """Decide how the run_python_code tool should route *task*.

    Ordering: substring redirects (file-reads) -> substring text-gen
    rejections -> bypass-keyword fast-path -> LLM gate. Most-specific
    signals win first; the LLM gate is the fallback that catches the
    long tail. Returning ``GateDecision`` (not a tuple) lets the caller
    branch on three states without losing the failure-mode distinction.
    """
    task_lower = task.lower()
    has_computation_intent = _has_computation_intent(task_lower)

    # File-read redirect -- but NOT when the task also computes/emits. A bare
    # "read the file" routes to read_file; "load the CSV AND compute X" must
    # reach the code agent (the #945 Mode-1 fix: the redirect substring used to
    # fire first and dead-end the analysis).
    for pattern in _REDIRECT_PATTERNS:
        if pattern in task_lower and not has_computation_intent:
            return GateDecision(
                decision="redirect_to_tool",
                suggested_tool="read_file",
                rationale=f"file-read substring matched: {pattern!r}",
            )

    # Strong text-generation refusal -- checked BEFORE the computation allow below
    # so an explicit prose-draft phrase ("draft a summary wrapped in <solution>
    # tags") still refuses rather than being mistaken for value emission. This
    # ordering is load-bearing: inverting it re-opens the text-gen backdoor.
    for pattern in _TEXT_GEN_PATTERNS_STRONG:
        if pattern in task_lower:
            return GateDecision(
                decision="respond_directly",
                rationale=f"text-generation substring matched: {pattern!r}",
            )

    # Weak text-generation refusal -- explanatory phrases ("explain why",
    # "explain that") that legitimately attach to a real computation. Refuse ONLY
    # when the task has no computation/emission intent; with intent present, a
    # genuine analysis ("compute the DE table and explain why gene X is up") must
    # fall through to the computation allow below rather than being substring-
    # rejected (bix-25). Strong patterns above already refuse prose drafts.
    if not has_computation_intent:
        for pattern in _TEXT_GEN_PATTERNS_WEAK:
            if pattern in task_lower:
                return GateDecision(
                    decision="respond_directly",
                    rationale=f"text-generation substring matched: {pattern!r}",
                )

    # Computation allow -- a genuine compute or value-emission task
    # short-circuits to the code agent deterministically, before the LLM gate
    # (the #945 fix: Mode-1 fall-through and Mode-2 <solution> emission both
    # land here, so the BixBench path never needs an LLM round-trip).
    if has_computation_intent:
        return GateDecision(
            decision="use_code_agent",
            rationale="computation or value-emission signal present",
        )

    task_words = set(task_lower.split())
    if task_words & _CODE_AGENT_BYPASS_KEYWORDS:
        return GateDecision(
            decision="use_code_agent",
            rationale="bypass keyword present (visualization or library reference)",
        )

    catalog = _build_tool_catalog()
    prompt = f"Task: {task}\n\nAvailable tools:\n{catalog}"
    # USER path: run the routing gate on the user's key + provider cache
    # settings from the factory. ENV_DEFAULT: do NOT override -- _gate_agent was
    # constructed with model_settings=MODEL_CONFIG.cache_settings(), and passing
    # the factory's empty ModelSettings() would clobber it, so the env path must
    # call .run(prompt) with no overrides to stay byte-for-byte unchanged.
    role_model = build_turn_model(ModelRole.TOOL_RESULT_SUMMARIZER, key_plan)
    if role_model.model is not None:
        result = await _gate_agent.run(
            prompt, model=role_model.model, model_settings=role_model.settings
        )
    else:
        result = await _gate_agent.run(prompt)
    decision = result.output

    if decision.decision == "redirect_to_tool":
        if decision.suggested_tool not in TOOL_METADATA:
            logger.warning(
                "Gate returned redirect_to_tool with unknown tool %r; "
                "falling back to tool_catalog",
                decision.suggested_tool,
            )
            return GateDecision(
                decision="redirect_to_tool",
                suggested_tool="tool_catalog",
                rationale="gate returned unknown tool name",
            )
    return decision


# ── Sandbox: blocked imports ────────────────────────────

BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "requests",
        "urllib",
        "httpx",
        "http",
        "socket",
        "aiohttp",
        "subprocess",
        "ftplib",
        "smtplib",
        "xmlrpc",
    }
)


def _check_blocked_imports(code: str) -> str | None:
    """AST-scan *code* for imports of network/shell modules.

    Returns an error message string if any blocked import is found,
    or ``None`` if the code is clean.  If *code* has a syntax error
    the function returns ``None`` and lets ``exec()`` surface the
    real ``SyntaxError``.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BLOCKED_MODULES:
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in BLOCKED_MODULES:
                    found.append(node.module)

    if not found:
        return None

    for name in sorted(set(found)):
        logger.info("Code agent wanted blocked import: %s", name)

    names = ", ".join(sorted(set(found)))
    return (
        f"Blocked import(s): {names}. "
        "Network and subprocess access is not allowed in the sandbox. "
        "Use the dedicated tools (e.g. search_uniprot, ebi_mafft, predict_kcat) "
        "instead of making HTTP requests or spawning subprocesses. "
        "Bioinformatics command-line tools (samtools, bwa, phykit, ...) are "
        "available via the injected run_tool(name, *args) helper -- call it "
        "directly; do not import subprocess."
    )


# ── Sandbox: approved network domains ──────────────────

ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {
        # NCBI (HTTPS only -- FTP is legacy, use Datasets API)
        "eutils.ncbi.nlm.nih.gov",
        "api.ncbi.nlm.nih.gov",
        # UniProt
        "rest.uniprot.org",
        # EBI
        "www.ebi.ac.uk",
        # PDB / RCSB
        "files.rcsb.org",
        "data.rcsb.org",
        # AlphaFold
        "alphafold.ebi.ac.uk",
        # Ensembl
        "rest.ensembl.org",
        # KEGG
        "rest.kegg.jp",
        # BixBench supplementary-data downloads (via download_file) (#919)
        "zenodo.org",
        "figshare.com",
        # BixBench gene-ID / functional-enrichment APIs (#919). NOTE: the
        # mygene / gprofiler / enrichr client packages make their OWN HTTP
        # calls that bypass fetch_url, so these entries only gate fetch_url /
        # download_file usage; the packages' reachability depends on container
        # egress, not this allow-list.
        "mygene.info",
        "biit.cs.ut.ee",
        "maayanlab.cloud",
    }
)


def _validate_url_domain(url: str) -> None:
    """Raise ``ValueError`` if *url*'s domain is not in ``ALLOWED_DOMAINS``.

    Uses exact hostname match (no subdomain wildcards).  Requires a scheme
    (``https://`` or ``http://``) to be present.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(
            f"Invalid URL (missing scheme or hostname): {url!r}. "
            "URLs must start with https:// or http://."
        )
    if parsed.hostname not in ALLOWED_DOMAINS:
        allowed = ", ".join(sorted(ALLOWED_DOMAINS))
        raise ValueError(
            f"Network access to {parsed.hostname!r} is not allowed. "
            f"Allowed domains: {allowed}. "
            "Use the appropriate orchestrator tool instead "
            "(e.g. ncbi_search, uniprot_search)."
        )


def fetch_url(url: str, **kwargs: Any) -> httpx.Response:
    """GET *url* after validating its domain against the sandbox allowlist.

    This is the only way to make HTTP requests from the code_agent sandbox.
    Direct ``import requests`` or ``import urllib`` is blocked.

    Args:
        url: Full URL (must include scheme).
        **kwargs: Passed to ``httpx.get`` (e.g. ``params``, ``headers``).
            ``timeout`` defaults to 60s if not provided.

    Returns:
        httpx.Response.

    Raises:
        ValueError: If the domain is not in ALLOWED_DOMAINS.
    """
    _validate_url_domain(url)
    kwargs.setdefault("timeout", 60)
    return httpx.get(url, **kwargs)


def download_file(url: str, dest_path: Path) -> Path:
    """Download *url* to *dest_path* after domain validation.

    Args:
        url: Full URL (must include scheme).
        dest_path: Local path to write the downloaded content.

    Returns:
        *dest_path* for chaining.

    Raises:
        ValueError: If the domain is not in ALLOWED_DOMAINS.
    """
    _validate_url_domain(url)
    with httpx.stream("GET", url, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=8192):
                f.write(chunk)
    return dest_path


# ── Sandbox: whitelisted command-line tools ────────────

DEFAULT_TOOL_TIMEOUT = 300
"""Per-call wall-clock ceiling (seconds) for a ``run_tool`` subprocess.

A sync ``subprocess.run`` blocks the single-worker genie-api loop for its
duration; the timeout bounds that. Mirrors the in-process ``exec()`` already
blocking the loop today (viku's eval is sequential)."""

TOOL_TIMEOUTS: dict[str, int] = {
    "phykit": 60,
    "clipkit": 120,
    "fasttree": 300,
    "samtools": 120,
    "bcftools": 120,
    "bedtools": 120,
    "mafft": 300,
    "hmmer": 600,
    "bwa": 600,
    "bowtie2": 600,
    "trimmomatic": 600,
}
"""Per-binary wall-clock ceilings (seconds) overriding ``DEFAULT_TOOL_TIMEOUT``.

The single 300 s default is wrong in both directions: a phykit summary is a
sub-second stats call (60 s is generous) while a genome-scale alignment (bwa,
bowtie2) or a profile-HMM search (hmmer) legitimately runs for minutes. A tool
absent from this map falls back to ``DEFAULT_TOOL_TIMEOUT`` via ``.get``."""

MAX_TOOL_OUTPUT_BYTES = 262144
"""Byte ceiling per captured stream (stdout/stderr) before truncation.

``capture_output=True`` buffers the whole child stream before we can slice it,
so a multi-GB SAM/VCF dump would OOM the 4 GB worker. The cookbook teaches the
agent to redirect large output to a file (``-o OUTPUT_DIR/...``, or
``run_tool(..., stdout_path=...)`` for a tool like ``bwa mem`` that writes to
stdout with no ``-o``) and parse it; this cap is the backstop for streams that
still come back on stdout in the default in-memory mode."""

ALLOWED_BINARIES: dict[str, str] = {
    "phykit": "phylogenetic tree/alignment statistics (PhyKIT)",
    "clipkit": "alignment trimming for phylogenetics (ClipKIT)",
    "fasttree": "approximate-maximum-likelihood phylogenetic tree inference (FastTree)",
    "samtools": "manipulate/query SAM/BAM/CRAM alignments",
    "bcftools": "manipulate/query VCF/BCF variant calls",
    "bwa": "Burrows-Wheeler short-read aligner",
    "bowtie2": "fast short-read aligner",
    "mafft": "multiple sequence alignment",
    "bedtools": "genome arithmetic on BED/GFF/VCF intervals",
    "hmmer": "profile HMM search (HMMER suite)",
    "trimmomatic": "read/adapter quality trimming for Illumina reads (Trimmomatic PE/SE)",
}
"""Whitelist of sanctioned command-line binaries (name -> one-line purpose).

The structural twin of ``ALLOWED_DOMAINS``: it feeds BOTH ``run_tool``'s
validation AND the code-agent prompt's tool list. Raw ``subprocess`` stays
hard-blocked for generated code; ``run_tool`` is the only sanctioned way to
invoke a binary, and only one in this list."""


class RunToolResult(BaseModel):
    """Result of invoking a whitelisted command-line tool via ``run_tool``."""

    returncode: int = Field(
        description="Process exit status; non-zero on tool error, timeout, or OS failure"
    )
    stdout: str = Field(
        description=(
            "Captured standard output (byte-capped at MAX_TOOL_OUTPUT_BYTES); "
            "empty string when stdout_path streaming mode was used"
        )
    )
    stderr: str = Field(
        description="Captured standard error (byte-capped at MAX_TOOL_OUTPUT_BYTES)"
    )
    truncated: bool = Field(
        description=(
            "True if either captured stream hit the byte cap and was truncated; "
            "always False for the stdout stream in stdout_path streaming mode "
            "(it is written to the file complete, never capped)"
        )
    )
    stdout_path: str | None = Field(
        default=None,
        description=(
            "Absolute path stdout was streamed to when run_tool was called with "
            "stdout_path=; None in the default in-memory capture mode"
        ),
    )
    stdout_bytes: int | None = Field(
        default=None,
        description=(
            "Size in bytes of the streamed stdout file (so the caller knows how "
            "much landed); None in the default in-memory capture mode"
        ),
    )


def _cap_stream(text: str) -> tuple[str, bool]:
    """Byte-cap *text* at ``MAX_TOOL_OUTPUT_BYTES``; return (capped, was_truncated)."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_TOOL_OUTPUT_BYTES:
        return text, False
    capped = encoded[:MAX_TOOL_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    return capped, True


def _run_tool_streaming(
    name: str,
    resolved: str,
    args: tuple[str, ...],
    timeout: int,
    stdout_path: str,
) -> RunToolResult:
    """Run *resolved* with stdout streamed straight to *stdout_path* (uncapped).

    The streaming arm of ``run_tool`` (see its docstring). The child's stdout is
    redirected to the open file's ``fileno()`` so a multi-hundred-MB SAM/VCF dump
    is written complete, never buffered into the 256 KB ``_cap_stream`` ceiling
    that corrupts mid-record output. ``stderr`` is still captured via ``PIPE``
    and capped (it is small log/progress for these tools). Timeout and ``OSError``
    (a bad *stdout_path* or a launch failure) return a non-zero ``RunToolResult``
    rather than raising, preserving the worker-teardown safety contract.
    """
    dest = Path(stdout_path)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out_fh:
            completed = subprocess.run(
                [resolved, *args],
                stdout=out_fh,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        return RunToolResult(
            returncode=124,
            stdout="",
            stderr=(
                f"Command-line tool {name!r} timed out after "
                f"{timeout}s and was terminated."
            ),
            truncated=False,
            stdout_path=str(dest),
            stdout_bytes=dest.stat().st_size if dest.exists() else None,
        )
    except OSError as exc:
        return RunToolResult(
            returncode=1,
            stdout="",
            stderr=(
                f"Failed to launch command-line tool {name!r} or open "
                f"stdout_path {stdout_path!r}: {exc}"
            ),
            truncated=False,
            stdout_path=str(dest),
            stdout_bytes=dest.stat().st_size if dest.exists() else None,
        )
    stderr, err_trunc = _cap_stream(completed.stderr or "")
    return RunToolResult(
        returncode=completed.returncode,
        stdout="",
        stderr=stderr,
        truncated=err_trunc,
        stdout_path=str(dest),
        stdout_bytes=dest.stat().st_size,
    )


def run_tool(name: str, *args: str, stdout_path: str | None = None) -> RunToolResult:
    """Run a whitelisted command-line bioinformatics tool, never a shell.

    The sanctioned wrapper around ``subprocess`` for the code_agent sandbox:
    generated code calls ``run_tool("samtools", "view", ...)`` and never imports
    ``subprocess`` (which stays hard-blocked). Mirrors ``fetch_url``: validate
    against an allow-list, then perform the capability host-side.

    Runs in the process CWD (no workspace handle, like ``fetch_url``); the
    caller's generated code already operates on ``OUTPUT_DIR``/``DATA_DIR``
    Paths, so pass absolute paths in *args*. ``shell=False`` -- *args* are never
    interpolated into a shell.

    Two output modes:

    - **In-memory (default).** ``stdout`` and ``stderr`` are captured and each
      byte-capped at ``MAX_TOOL_OUTPUT_BYTES`` (256 KB). Use for tools whose
      stdout is a small scalar/summary, or pass ``-o str(OUTPUT_DIR / "...")``
      when the binary has an output-path flag and parse the file.
    - **stdout streaming (``stdout_path=``).** For a tool that emits its primary
      result on stdout with no ``-o`` flag -- canonically ``bwa mem``, which
      streams SAM to stdout -- pass ``stdout_path=str(OUTPUT_DIR / "aln.sam")``.
      stdout is written to that file **complete and uncapped** (the in-memory
      256 KB cap silently truncates a SAM mid-record, corrupting every
      downstream ``samtools`` call); ``stdout`` comes back empty and the result
      carries ``stdout_path`` / ``stdout_bytes`` so you can parse the file
      (e.g. with pysam). ``stderr`` is still captured and capped.

    Args:
        name: Binary name; must be a key of ``ALLOWED_BINARIES``.
        *args: Positional arguments passed verbatim to the binary.
        stdout_path: If set, stream stdout to this file (uncapped) instead of
            capturing it in memory. Parent directories are created as needed.

    Returns:
        ``RunToolResult`` with the exit status and captured streams. A timeout
        or OS-level launch failure returns a non-zero ``returncode`` with an
        explanatory ``stderr`` rather than raising -- an escaping exception here
        would tear down the genie-api worker (the 2026-06-04 SystemExit class).

    Raises:
        ValueError: If *name* is not in ``ALLOWED_BINARIES`` or is not on PATH.
    """
    if name not in ALLOWED_BINARIES:
        allowed = ", ".join(sorted(ALLOWED_BINARIES))
        raise ValueError(
            f"Command-line tool {name!r} is not allowed. "
            f"Allowed tools: {allowed}. "
            "Use the appropriate orchestrator tool, or one of the allowed "
            "binaries above via run_tool(name, *args)."
        )
    resolved = shutil.which(name)
    if resolved is None:
        raise ValueError(
            f"Command-line tool {name!r} is whitelisted but not installed on "
            "PATH in this environment. It ships in the genie-api container "
            "image; this may be a local dev environment without it."
        )
    timeout = TOOL_TIMEOUTS.get(name, DEFAULT_TOOL_TIMEOUT)
    if stdout_path is not None:
        return _run_tool_streaming(name, resolved, args, timeout, stdout_path)
    try:
        completed = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return RunToolResult(
            returncode=124,
            stdout="",
            stderr=(
                f"Command-line tool {name!r} timed out after "
                f"{timeout}s and was terminated."
            ),
            truncated=False,
        )
    except OSError as exc:
        return RunToolResult(
            returncode=1,
            stdout="",
            stderr=f"Failed to launch command-line tool {name!r}: {exc}",
            truncated=False,
        )
    stdout, out_trunc = _cap_stream(completed.stdout or "")
    stderr, err_trunc = _cap_stream(completed.stderr or "")
    return RunToolResult(
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        truncated=out_trunc or err_trunc,
    )


# ── Models ──────────────────────────────────────────────


class PythonExecResult(BaseModel):
    """Result of executing Python code."""

    status: str = Field(description="Execution outcome: 'success' or 'error'")
    output: str = Field(
        default="", description="Captured stdout from the executed code"
    )
    saved_files: list[str] = Field(
        default_factory=list, description="Paths to files saved via output_file()"
    )
    manifests: list[dict[str, Any]] = Field(
        default_factory=list,
        description="FileManifest entries for each saved file (format, columns, row_count, etc.)",
    )
    error: str | None = Field(
        default=None, description="Error message if execution failed"
    )
    error_type: str | None = Field(
        default=None, description="Exception class name if execution failed"
    )


# ── Agent Tool ─────────────────────────────────────────


@agent_tool(
    name="run_python_code",
    description=(
        "Delegate a Python computation task to a coding sub-agent. Capability "
        "classes: data analysis, visualization, file/sequence parsing, "
        "statistical tests, differential expression, functional enrichment, "
        "single-cell analysis, and variant/BAM parsing -- backed by 100+ "
        "scientific Python packages (e.g. pandas, BioPython). "
        "NOT for: writing reports, markdown, or natural language text. "
        "Describe what you want done -- a coding agent writes and executes code."
    ),
    category="utility",
    display_name="Python",
)
async def run_python_code_tool(
    ctx: RunContext[Any], task: str
) -> dict[str, Any] | ToolError:
    """Run a Python data analysis or plotting task via a coding sub-agent.

    The coding agent writes, runs, and debugs Python code autonomously in its
    own context window. You do NOT write code yourself -- describe the task.

    Capability classes the coding agent covers: data analysis, visualization,
    file/sequence parsing, statistical tests, differential expression,
    functional enrichment, single-cell analysis, and variant/BAM parsing --
    backed by 100+ scientific Python packages (e.g. pandas, BioPython). It can
    read session files (CSVs, FASTAs, PDBs) and create plots, tables, and
    analysis outputs.

    Args:
        task: Natural language description of what the Python code should do.
            Be specific: name the files to read, the columns to use, the type
            of plot or analysis, and the desired output filenames.

    Returns:
        Dict with 'status', 'output' (stdout), 'saved_files', 'manifests',
        'error' (if failed), and 'attempts' (number of code iterations).
    """
    workspace: WorkspaceConfig = ctx.deps.workspace

    # Author-time gate: a caller may set deps.allow_code_agent=False to forbid
    # the nested code-agent where there is no headroom for its 25-request x
    # 5-attempt budget (the b3890ba1 thrash). When disabled, return a terminal
    # ToolError before the count check so the turn never starts the sub-agent.
    # Interactive, sim, and Slurm-completion turns all leave it True.
    if not ctx.deps.allow_code_agent:
        cap_tool(ctx.deps, "run_python_code")
        return ToolError(
            kind="validation",
            message=(
                "run_python_code is unavailable in this context. Summarize the "
                "result files directly from their names and the previews you "
                "were given; do not attempt to run code."
            ),
            tool_name="run_python_code",
            params={"task": task},
        )

    # Per-turn invocation cap via ContextVar (resets each agent.run() turn)
    rpc_count = get_python_exec_count()
    if rpc_count >= MAX_CODE_AGENT_CALLS_PER_TURN:
        cap_tool(ctx.deps, "run_python_code")
        return ToolError(
            kind="validation",
            message=(
                f"run_python_code limit reached ({MAX_CODE_AGENT_CALLS_PER_TURN}/turn). "
                f"Tool capped -- use a different approach or respond with progress."
            ),
            tool_name="run_python_code",
            params={"task": task},
        )
    increment_python_exec_count()

    # LLM gate: route to dedicated tool, refuse as text-gen, or proceed.
    decision = await _should_use_code_agent(task, ctx.deps.key_plan)
    if decision.decision == "redirect_to_tool":
        suggested = decision.suggested_tool or "tool_catalog"
        return ToolError(
            kind="validation",
            message=(
                f"Don't use run_python_code for this task. Use the dedicated tool "
                f"'{suggested}' instead. Call {suggested} directly."
            ),
            tool_name="run_python_code",
            params={"task": task},
        )
    if decision.decision == "respond_directly":
        # Single-strike cap defeats the dedup-bypass class where the orchestrator
        # paraphrases a prose task and re-issues; keyed on deps.capped_tools (not a
        # ContextVar) so it survives pydantic-ai's per-call asyncio.create_task
        # copy and the short-circuit actually fires (#947). But never cap when a
        # value was already produced this turn or the task is genuinely
        # computational -- the agent must keep a path to emit its result (#945). A
        # bare text-gen task (no compute signal, nothing produced) is still capped.
        # See [[plan.block-run-python-code-text-gen-misuse.2026.05.05]].
        if not (
            ctx.deps.value_produced_this_turn or _has_computation_signal(task.lower())
        ):
            cap_tool(ctx.deps, "run_python_code")
        # Forced-emission exit (#954): a text-gen-gate refusal AFTER a value was
        # produced this turn is the emission dead-end -- the model is trying to
        # "report" a held value via a tool and will otherwise spin on the uncapped
        # tool_catalog to request_limit. Arm the deterministic turn-end so the
        # iter-loop delivers the value as the final <solution> answer. Guarded by a
        # corroborator (an explicit emission phrase, or an in-progress tool_catalog
        # spin) so a genuine mid-analysis text-gen refusal does not truncate the
        # turn. Still returns the refusal below -- never raises (#821/#886).
        if ctx.deps.value_produced_this_turn and (
            _has_emission_signal(task.lower())
            or ctx.deps.tool_catalog_count >= TOOL_CATALOG_SPIN_THRESHOLD
        ):
            set_forced_emission(ctx.deps, "emission_refusal")
        logger.info("run_python_code text-gen rejection (gate): %s", decision.rationale)
        return ToolError(
            kind="validation",
            message=(
                "This task is text generation, not computation. EMIT the answer "
                "itself in your reply now: if it is a definitional or knowledge "
                "question (e.g. 'what is X'), state the definition directly from "
                "your own knowledge. Do NOT tell the user to look it up, run a web "
                "search, or otherwise defer to an external / web-sourced check, "
                "and do not call run_python_code again this turn for this question."
            ),
            tool_name="run_python_code",
            params={"task": task},
        )

    from .code_agent import run_code_agent

    # Block G: bound the code_agent's internal wall-clock budget by the remaining
    # genie-api turn deadline so a sub-run can never outlive the turn -- the
    # in-handler <solution> floor must always have time to emit before the
    # external ~900s harness reaper. ctx.deps.turn_deadline_monotonic is set ONLY
    # by the genie-api chat_handler; None on chat-api / sim-runner, so the bound
    # is inert there (only the outer watchdog applies, as before). When too little
    # time remains for a useful run, REFUSE to launch a doomed spin (bix-61: two
    # ~590s code_agents into the reaper) and tell the model to report what it has.
    wall_clock_timeout: float | None = None
    deadline = ctx.deps.turn_deadline_monotonic
    if deadline is not None:
        remaining = deadline - time.monotonic()
        effective = remaining - CODE_AGENT_EMIT_MARGIN_SECONDS
        if CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS > 0:
            effective = min(effective, CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS)
        if effective < CODE_AGENT_MIN_USEFUL_SECONDS:
            logger.info(
                "run_python_code: turn budget nearly spent (%.0fs left); refusing "
                "to launch code_agent (task: %.100s)",
                remaining,
                task,
            )
            return ToolError(
                kind="validation",
                message=(
                    "The turn is almost out of wall-clock time, so a new code "
                    "analysis cannot finish before the deadline. Report the best "
                    "result already computed this turn as your final answer -- do "
                    "not start another computation and do not invent a value."
                ),
                tool_name="run_python_code",
                params={"task": task},
            )
        wall_clock_timeout = effective

    # Spawn-level watchdog (bix-31): a hung or silently-raising code_agent sub-run
    # must never strand the turn on a tool_invoke that never gets a tool_result.
    # Wrap the whole spawn so an await-point hang (model/network) returns a
    # recoverable `timeout` ToolError and a crash returns an `upstream` one --
    # both retry_transient, so the orchestrator retries (bounded by
    # MAX_CODE_AGENT_CALLS_PER_TURN) or emits a held partial via Block A's
    # <solution> floor. The try/except is UNCONDITIONAL so the crash guard
    # protects the turn even when the watchdog is disabled (<= 0); only the
    # wait_for is conditional. `except TimeoutError` MUST precede `except
    # Exception` because asyncio.wait_for raises the builtin TimeoutError, itself
    # an Exception subclass. BaseException control-flow signals
    # (RUN_BOUNDARY_RERAISE in orchestrator.py: CancelledError/GeneratorExit/
    # KeyboardInterrupt) are not Exception subclasses, so they propagate untouched
    # and the run-boundary contract is preserved. This is the spawn-level guard,
    # distinct from Block B's per-attempt SIGALRM exec cap. wall_clock_timeout
    # (Block G) is the code_agent's INTERNAL salvaging budget, nested below this
    # outer watchdog; in the critical late-turn case it is far smaller than the
    # outer 600s, so the internal timeout fires first and returns a salvaged
    # partial rather than this arm's bare ToolError.
    spawn = run_code_agent(
        workspace, task, ctx.deps.key_plan, wall_clock_timeout=wall_clock_timeout
    )
    try:
        if CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS > 0:
            result = await asyncio.wait_for(
                spawn, timeout=CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS
            )
        else:
            result = await spawn
    except TimeoutError:
        logger.warning(
            "run_python_code watchdog: code_agent spawn exceeded %.0fs with no "
            "result (task: %.100s)",
            CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS,
            task,
        )
        return ToolError(
            kind="timeout",
            message=(
                f"The code agent did not return a result within "
                f"{CODE_AGENT_WATCHDOG_TIMEOUT_SECONDS:.0f}s and was stopped. "
                "Retry with a simpler, more targeted task, or report progress "
                "from any results already produced -- do not invent an answer."
            ),
            tool_name="run_python_code",
            params={"task": task},
        )
    except Exception as exc:
        logger.exception(
            "run_python_code watchdog: code_agent spawn raised before returning "
            "a result (task: %.100s)",
            task,
        )
        return ToolError(
            kind="upstream",
            message=(
                f"The code agent failed before returning a result: {exc}. "
                "Retry with a simpler, more targeted task, or report progress "
                "from any results already produced -- do not invent an answer."
            ),
            tool_name="run_python_code",
            params={"task": task},
        )

    # Layer-2 promotion: when the code agent itself self-refused as text-gen
    # (rule 9 in prompts/code_agent.md, or the post-exec md-only heuristic),
    # promote that decision to the same cap + ToolError exit as the gate
    # path. Without this, the orchestrator would paraphrase past dedup and
    # re-issue, identical to the layer-1 failure mode.
    if result.is_text_gen_refusal:
        # Same cap as the layer-1 gate (deps-keyed, #947), with the #945 escape: a
        # value already produced this turn, or a genuinely computational task,
        # keeps the emission path open instead of locking the tool.
        if not (
            ctx.deps.value_produced_this_turn or _has_computation_signal(task.lower())
        ):
            cap_tool(ctx.deps, "run_python_code")
        # Forced-emission exit (#954/#959): the emission dead-end also lands here,
        # not just at the layer-1 gate. An emission-signal finalize task (e.g.
        # "report the final answer wrapped in <solution>") makes
        # _has_computation_intent short-circuit the gate to use_code_agent, so it
        # routes INTO the code_agent and self-refuses there -- the layer-1
        # detector at L1062 is unreachable on exactly this task class. Mirror the
        # same arming here so a value held this turn is delivered as the final
        # <solution> instead of spinning the uncapped tool_catalog to
        # request_limit. Identical corroborator guard (an explicit emission
        # phrase, or an in-progress tool_catalog spin) so a genuine mid-analysis
        # self-refusal with no value does not truncate the turn. set_forced_emission
        # is one-shot and never raises -- still returns the refusal below (#821/#886).
        if ctx.deps.value_produced_this_turn and (
            _has_emission_signal(task.lower())
            or ctx.deps.tool_catalog_count >= TOOL_CATALOG_SPIN_THRESHOLD
        ):
            set_forced_emission(ctx.deps, "emission_refusal")
        logger.info(
            "run_python_code text-gen rejection (code_agent self-refusal): %s",
            (result.error or "")[:200],
        )
        return ToolError(
            kind="validation",
            message=(
                "This task is text generation, not computation. Respond "
                "directly to the user with your reasoning. Do not call "
                "run_python_code again this turn for this question."
            ),
            tool_name="run_python_code",
            params={"task": task},
        )

    # Register output files in SessionMemory via the @agent_tool wrapper's ContextVar
    for file_path_str in result.saved_files:
        file_path = Path(file_path_str)
        if file_path.exists():
            desc = file_path.name
            # Check if manifest has a description
            for m in result.manifests:
                if m.get("path") == file_path.name:
                    desc = m.get("description", file_path.name)
                    break
            register_output(file_path, description=desc, created_by="run_python_code")

    # A successful code-agent run produced a value this turn. Record it on deps
    # (mutated in place -- survives pydantic-ai's per-tool-call create_task
    # context copy where a ContextVar write would drop) so a later emission /
    # follow-up call this turn is never single-strike-capped (#945). Also capture
    # the printed value (truncated) as the forced-emission answer source (#954):
    # if the model then gets stuck emitting it, the iter-loop wraps this in
    # <solution> as the final answer (e.g. result.output == "CD14").
    if result.status == "success":
        ctx.deps.value_produced_this_turn = True
        ctx.deps.last_computed_value = (result.output or "")[
            :_LAST_COMPUTED_VALUE_MAX_CHARS
        ]
    elif (
        not ctx.deps.value_produced_this_turn
        and result.output
        and _looks_like_scalar(result.output)
    ):
        # Incremental stash (Block B): a NON-success run can still carry a real
        # partial scalar the code printed before it timed out / exhausted its
        # budget -- code_agent salvages it as result.output with status="error"
        # (last_success_output or last_computed_partial). Surface it to
        # deps.last_computed_value so Block A's terminal-path <solution> floor
        # emits the real partial instead of NA (bix-3/-32/-38). We deliberately
        # do NOT set value_produced_this_turn: the run did not cleanly succeed, so
        # the text-gen gate / forced-emission exit must not treat it as a produced
        # value -- only the abnormal-termination floor reads last_computed_value
        # as a last resort. A clean success later this turn still overwrites it.
        ctx.deps.last_computed_value = result.output[:_LAST_COMPUTED_VALUE_MAX_CHARS]

    return result.model_dump()
