# BixBench agent requirements — per-item table (Python + command-line scope)

Scope: 37 non-R capsules = **124 / 205 questions** (Python 16 caps / 67 q;
command-line 21 caps / 57 q). Counts reproduced by `analysis/scan_requirements.py`.
Grouped prose version: `docs/ENVIRONMENT.md`.

## Capabilities (runtime, not packages)

| Capability | Why | Needed by |
|---|---|---|
| Stateful Python kernel (Jupyter/ipykernel) | run analysis cells | 67 Python q |
| Shell / bash | run CLI tools; notebooks also `!pip`/`!wget` | 57 CLI q (+Python) |
| Runtime installs: pip + conda/bioconda + apt | notebooks install at run time; env not freezable | all |
| Outbound network | `!wget` from Zenodo; mygene/g:Profiler/ChEMBL APIs; BUSCO lineage DBs | many |
| Decompression: gz / zip / tar.gz | inputs ship compressed | .gz 51q, .zip 48q |
| Large-file + adequate disk/RAM | FASTQ/BAM/genomes | CLI WGS |
| Format parsing: CSV/TSV/whitespace-TXT/XLS(X), FASTA, FASTQ, SAM/BAM, VCF, BWA-index, Newick, HDF5/AnnData | read all inputs | all |

## Python libraries (16 capsules / 67 questions)

| Library | Category | Install | #caps | #q | Purpose |
|---|---|---|---:|---:|---|
| pandas | core data | pip | 16 | 67 | dataframes / IO |
| numpy | core data | pip | 14 | 59 | arrays / math |
| matplotlib | visualization | pip | 14 | 59 | plotting |
| seaborn | visualization | pip | 14 | 59 | statistical plots |
| scipy | statistics | pip | 11 | 49 | tests, distributions |
| statsmodels | stats / modeling | pip | 7 | 31 | logistic/ordinal regression |
| scikit-learn | machine learning | pip | 7 | 29 | ML models |
| pydeseq2 | differential expression | pip | 7 | 29 | DESeq2 in Python |
| gseapy | enrichment | pip | 5 | 21 | GO/KEGG/Reactome/GSEA |
| scanpy | single-cell | pip | 4 | 18 | scRNA-seq |
| requests | web / API | pip | 3 | 13 | HTTP downloads |
| matplotlib-venn | visualization | pip | 2 | 8 | Venn diagrams |
| mygene | enrichment / IDs | pip | 1 | 6 | gene-ID mapping (API) |
| upsetplot | visualization | pip | 1 | 5 | UpSet plots |
| gprofiler-official | enrichment | pip | 1 | 3 | g:Profiler (API) |
| chembl_webresource_client | web / API | pip | 1 | 3 | ChEMBL API |
| tqdm | utility | pip | 1 | 3 | progress bars |
| xgboost | machine learning | pip | 1 | 2 | gradient boosting |
| optuna (+optuna-integration) | machine learning | pip | 1 | 2 | hyperparameter search |
| anndata | single-cell | pip | 1 | 2 | annotated data (.h5ad) |
| altair | visualization | pip | 1 | 2 | declarative viz |
| biopython | sequence IO | pip | rec | — | parse FASTA (CLI-scope formats) |
| pysam | sequence IO | pip | rec | — | parse BAM/VCF (CLI-scope formats) |
| openpyxl / xlrd | IO | pip | dep | — | read .xls / .xlsx |
| h5py / tables | IO | pip | dep | — | read .h5ad / HDF5 |
| jupyter / ipykernel / nbformat | runtime | pip | dep | — | execute notebook cells |

`rec` = recommended for CLI-format parsing; `dep` = supporting IO/runtime dep.
(`google` = `google.colab`, 1 cap — **not** needed outside Colab.)

## Command-line tools (21 capsules / 57 questions)

### Family A — Phylogenetics / comparative genomics (~12 caps / ~48 q)

| Tool | Install | Evidence | Purpose |
|---|---|---|---|
| BUSCO | bioconda | 3 q mention; `*.busco.zip`, `eukaryota_odb10` | single-copy ortholog ID (+lineage DBs) |
| PhyKIT | bioconda | **12 q mention**; `.treefile` | tree stats: length, treeness, DVMC, PI-sites |
| MAFFT | bioconda | 1 q mention; `.mafft` | multiple sequence alignment |
| MUSCLE | bioconda | (alt MSA) | multiple sequence alignment |
| ClipKIT | bioconda | 1 q mention; `.clipkit` | alignment trimming |
| IQ-TREE | bioconda | `.treefile` | phylogenetic tree inference |
| FastTree / RAxML | bioconda | (alt tree) | phylogenetic tree inference |
| HMMER | bioconda | (BUSCO dependency) | profile-HMM search |
| seqkit | bioconda | `.faa` 48q, `.fna` | FASTA manipulation/stats |

### Family B — Read alignment & variant calling / WGS (~6 caps)

| Tool | Install | Evidence | Purpose |
|---|---|---|---|
| Trimmomatic | bioconda | 1 q mention; `.fastq` 6q | read trimming |
| fastp | bioconda | (alt QC/trim) | read QC + trimming |
| FastQC | bioconda | `.fastq` | read quality control |
| BWA | bioconda | 1 q mention; index `.amb/.ann/.bwt/.pac/.sa` 6q | read alignment |
| bowtie2 | bioconda | (alt aligner) | read alignment |
| samtools | bioconda | 1 q mention; `.bam` 4q | BAM sort/index/stats |
| bcftools | bioconda | `.vcf` 3q | variant calling/filtering |
| GATK4 | bioconda | 1 q mention | variant calling |
| Picard | bioconda | (BAM dedup/metrics) | BAM utilities |
| bedtools | bioconda | (interval ops) | genome arithmetic |
| vcftools | bioconda | `.vcf` | variant operations |

### General CLI utilities
`wget`, `curl`, `tar`, `gzip`, `unzip`.

---
Install everything: `pip install -r requirements-agent.txt` (Python) and
`conda env create -f environment.yml` (Python + CLI tools).
