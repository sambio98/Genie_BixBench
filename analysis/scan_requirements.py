"""Derive the agent's capability/library requirements for the non-R scope.

Goes through every Python capsule (extracts notebook imports + pip/shell calls)
and every command-line capsule (file types + tool mentions in the question text)
to ground docs/ENVIRONMENT.md. Uses random-access reads of the remote zips
(only the notebook entry / central directory is fetched, not the big data).

    python analysis/scan_requirements.py
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter, defaultdict

from datasets import load_dataset
from huggingface_hub import HfFileSystem

BASE = "datasets/futurehouse/BixBench/"
ALIAS = {
    "sklearn": "scikit-learn", "skimage": "scikit-image", "cv2": "opencv-python",
    "PIL": "pillow", "Bio": "biopython", "sm": "statsmodels", "smf": "statsmodels",
    "np": "numpy", "pd": "pandas", "plt": "matplotlib", "sns": "seaborn", "sc": "scanpy",
}
STD = set(
    "os sys re json math time random itertools collections pathlib glob io warnings functools "
    "typing subprocess shutil gzip csv string datetime copy pickle argparse abc contextlib "
    "operator __future__".split()
)
TOOLS = (
    "gseapy gsea scanpy anndata pydeseq2 deseq2 edger limma biopython pysam scipy statsmodels "
    "sklearn lifelines networkx scikit-image opencv cellpose umap leidenalg busco phykit samtools "
    "bcftools bwa bowtie star salmon kallisto hisat mafft muscle clustal iqtree raxml fasttree "
    "blast diamond gatk picard bedtools vcftools plink snpeff trimmomatic fastqc hmmer orthofinder "
    "mrbayes seqkit minimap2 clipkit"
).split()


def lang_of(nb: dict) -> str:
    name = nb.get("metadata", {}).get("kernelspec", {}).get("name", "?")
    return {"ir": "R", "python3": "Python"}.get(name, name)


def imports(nb: dict) -> tuple[set[str], set[str], set[str]]:
    libs, shell, installs = set(), set(), set()
    for c in nb.get("cells", []):
        if c.get("cell_type") != "code":
            continue
        for line in "".join(c["source"]).split("\n"):
            m = re.match(r"\s*(?:import|from)\s+([a-zA-Z0-9_]+)", line)
            if m:
                lib = ALIAS.get(m.group(1), m.group(1))
                if lib not in STD:
                    libs.add(lib)
            inst = re.match(r"\s*!\s*pip\s+install\s+(.+)", line)
            if inst:
                installs.update(re.findall(r"[a-zA-Z0-9_\-]+", inst.group(1)))
            if re.match(r"\s*!", line) or "subprocess" in line or "os.system" in line:
                shell.add(line.strip()[:90])
    return libs, shell, installs


def main() -> None:
    fs = HfFileSystem()
    ds = load_dataset("futurehouse/BixBench", split="train")
    qcount = Counter(r["data_folder"] for r in ds)
    qtext: dict[str, list[str]] = defaultdict(list)
    for r in ds:
        qtext[r["data_folder"]].append(r["question"])

    lib_caps, lib_q = Counter(), Counter()
    ext_caps, ext_q = Counter(), Counter()
    installs_all, shell_all = Counter(), Counter()
    py, cli = [], []

    for f in sorted(qcount):
        with fs.open(BASE + f, "rb") as fh:
            zf = zipfile.ZipFile(fh)
            names = zf.namelist()
            nbs = [n for n in names if n.endswith(".ipynb")]
            lang = "CLI" if not nbs else lang_of(json.loads(zf.read(nbs[0])))
            if lang == "Python":
                libs, shell, inst = imports(json.loads(zf.read(nbs[0])))
                py.append(f)
                for x in libs:
                    lib_caps[x] += 1
                    lib_q[x] += qcount[f]
                installs_all.update(inst)
                shell_all.update(shell)
            elif lang == "CLI":
                cli.append(f)
                for e in Counter(n.rsplit(".", 1)[-1].lower() for n in names if "." in n.rsplit("/", 1)[-1]):
                    ext_caps[e] += 1
                    ext_q[e] += qcount[f]

    print(f"PYTHON: {len(py)} capsules / {sum(qcount[f] for f in py)} questions")
    print(f"{'library':24s}{'#caps':>6}{'#q':>6}")
    for l, c in lib_caps.most_common():
        print(f"  {l:22s}{c:>6}{lib_q[l]:>6}")
    print("\n  notebook pip-installs:", dict(installs_all.most_common()))
    print("  uses wget/subprocess:", sum("wget" in s for s in shell_all), "wget lines")

    print(f"\nCOMMAND-LINE: {len(cli)} capsules / {sum(qcount[f] for f in cli)} questions")
    print(f"{'file ext':14s}{'#caps':>6}{'#q':>6}")
    for e, c in ext_caps.most_common(20):
        print(f"  .{e:12s}{c:>6}{ext_q[e]:>6}")

    notr = set(py) | set(cli)
    tool_q = Counter()
    for f in notr:
        for q in qtext[f]:
            ql = q.lower()
            for t in TOOLS:
                if t in ql:
                    tool_q[t] += 1
    print("\nTOOL mentions in question text (non-R scope):")
    for t, c in tool_q.most_common():
        print(f"  {t:16s}{c:>4} q")


if __name__ == "__main__":
    main()
