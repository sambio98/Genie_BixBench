# Agent environment requirements — Python + command-line scope (not R)

**Scope:** the 37 non-R capsules = **124 / 205 questions** (Python: 16 capsules /
67 q; command-line: 21 capsules / 57 q). The 81 R questions are excluded per the
stated goal. Every number below is produced by `analysis/scan_requirements.py`
(reads each Python notebook's imports and each CLI capsule's file types + the
tools named in its questions).

---

## 1. Core capabilities (not just packages)

To solve these 124 questions the agent's runtime must be able to:

1. **Run a stateful Python kernel** (Jupyter / ipykernel) — all 67 Python questions.
2. **Run arbitrary shell commands** (bash) — required for all 57 command-line
   questions, and the Python notebooks themselves shell out (`!pip`, `!wget`).
3. **Install packages at runtime** via **pip _and_ conda/mamba (bioconda) _and_
   apt.** The reference notebooks `!pip install` pydeseq2 / gseapy / scanpy /
   anndata / gprofiler / xgboost / optuna at run time, so the env can't be frozen.
4. **Outbound network access** — Python capsules `!wget` supplemental data from
   Zenodo (8 download lines), and `mygene` / `gprofiler` / `chembl_webresource_client`
   call web APIs. BUSCO may fetch lineage datasets.
5. **Decompress archives** — `.gz` (51 q), `.zip` (48 q), `.tar.gz`. Most CLI
   inputs ship compressed.
6. **Handle large genomic files** (FASTQ / BAM / genomes) with adequate disk + RAM.
7. **Parse many file formats**: tabular (CSV/TSV/whitespace-`.txt`/`.xls`/`.xlsx`),
   FASTA (`.faa`/`.fna`), FASTQ, SAM/BAM, VCF, BWA index, Newick/`.treefile`,
   HDF5 / AnnData (`.h5ad`).

---

## 2. Python libraries (16 capsules / 67 questions)

| Capability | Libraries (with question coverage) |
|---|---|
| **Core data** | `pandas` (67q), `numpy` (59q), `scipy` (49q) |
| **Visualization** | `matplotlib` (59q), `seaborn` (59q), `matplotlib-venn` (8q), `upsetplot` (5q), `altair` (2q) |
| **Statistics / modeling** | `statsmodels` (31q — logistic/ordinal regression, tests), `scipy.stats` (Mann-Whitney, correlations, chi-square / Fisher) |
| **Differential expression** | `pydeseq2` (29q) — the Python DESeq2 |
| **Functional enrichment** | `gseapy` (21q — GO/KEGG/Reactome/GSEA), `gprofiler-official` (3q), `mygene` (6q — gene-ID mapping) |
| **Single-cell** | `scanpy` (18q), `anndata` (2q) |
| **Machine learning** | `scikit-learn` (29q), `xgboost` (2q), `optuna` + `optuna-integration` (2q) |
| **Web / API clients** | `requests` (13q), `chembl_webresource_client` (3q) |
| **Utility / IO** | `tqdm`, `openpyxl`/`xlrd` (xls/xlsx), `h5py`/`tables` (h5ad) |
| **For CLI formats in Python** (recommended) | `biopython`, `pysam` (parse FASTA / BAM / VCF) |

> `pydeseq2` + `gseapy` are the highest-leverage pair: many Python omics capsules
> reproduce the R "DESeq2 → enrichment" pipeline with exactly these two.
> (`google` appears once = `google.colab`; **not** needed outside Colab.)

---

## 3. Command-line tools (21 capsules / 57 questions)

The CLI scope is two workflow families:

### Family A — Phylogenetics / comparative genomics  (~12 capsules / ~48 q)
*Evidence:* `.faa` (48q), `.fna`, `*.busco.zip`, `eukaryota_odb10` lineage,
`.treefile`, `.clipkit`, `.mafft`; questions name **phykit (12q)**, busco (3q),
mafft (1q), clipkit (1q).

| Tool | Role |
|---|---|
| **BUSCO** | single-copy ortholog identification (+ lineage DBs, e.g. `eukaryota_odb10`) |
| **PhyKIT** | tree statistics — tree length, treeness, DVMC, parsimony-informative sites, long-branch scores (**most-cited, 12q**) |
| **MAFFT / MUSCLE** | multiple sequence alignment |
| **ClipKIT** | alignment trimming |
| **IQ-TREE** (or RAxML / FastTree) | phylogenetic tree inference (`.treefile`) |
| **HMMER** | BUSCO dependency |
| **seqkit** | FASTA manipulation/stats |

### Family B — Read alignment & variant calling / WGS  (~6 capsules)
*Evidence:* `.fastq` (6q), BWA index `.amb/.ann/.bwt/.pac/.sa` (6q), `.fai`,
`.bam` (4q), `.vcf` (3q); questions name gatk, samtools, bwa, trimmomatic.

| Tool | Role |
|---|---|
| **Trimmomatic / fastp** + **FastQC** | read QC and trimming |
| **BWA** (and/or **bowtie2**) | read alignment (index files are provided) |
| **samtools** | BAM sort/index/stats |
| **GATK4 / bcftools** | variant calling & filtering (`.vcf`) |
| **Picard** | BAM utilities (dedup, metrics) |
| **bedtools, vcftools** | interval / variant operations |

### General CLI utilities
`wget`/`curl`, `tar`, `gzip`, `unzip`.

---

## 4. TL;DR install set

- **Capabilities:** Python kernel + bash + (pip & conda/bioconda & apt) + network
  + decompression + large-file handling.
- **Python (pip):** see `requirements-agent.txt` — pandas, numpy, scipy,
  statsmodels, scikit-learn, matplotlib, seaborn, **pydeseq2, gseapy, scanpy,
  anndata, gprofiler-official, mygene**, biopython, pysam, requests, xgboost,
  optuna, (+ matplotlib-venn, upsetplot, altair, openpyxl, h5py, tqdm).
- **CLI (bioconda):** see `environment.yml` — **busco, phykit, clipkit, mafft,
  muscle, iqtree, fasttree, hmmer, seqkit** (phylogenetics) and **bwa, bowtie2,
  samtools, bcftools, gatk4, picard, bedtools, vcftools, trimmomatic, fastqc,
  fastp** (alignment/variants).

Reproduce / refresh all counts: `python analysis/scan_requirements.py`.
