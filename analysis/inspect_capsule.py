"""Download one BixBench capsule and surface the *ground-truth* derivation.

The capsule zip ships the original analyst notebook that produced the reference
answers. Reading it is the only way to see the undocumented method choices that
make exact reproduction hard. Usage:

    python analysis/inspect_capsule.py                      # default capsule
    python analysis/inspect_capsule.py <capsule_uuid>

Prints: the questions+ideal answers+eval_mode for the capsule, the notebook
language, the analysis code, and a scan for "judgment-call" keywords (silent
sample drops, manual exclusions, non-default covariates, etc.).
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile

from datasets import load_dataset
from huggingface_hub import hf_hub_download

HF_REPO = "futurehouse/BixBench"
DEFAULT = "33b801bb-9b47-4a0a-9314-05325c82fde7"  # RNA-seq DESeq2 -> GO enrichment

# Choices an analyst makes that the question text does NOT specify, yet which
# move the final number — i.e. the things an agent cannot recover by reasoning.
JUDGMENT = re.compile(
    r"\b(remove|removed|exclud|drop|dropped|outlier|manually|by hand|"
    r"we chose|note:|design\s*=|~\s*\w+\s*\+|universe\s*=|cutoff\s*=|"
    r"qvalueCutoff|pAdjustMethod|lfcShrink|simplify)\b",
    re.I,
)


def main() -> None:
    uuid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    ds = load_dataset(HF_REPO, split="train")
    qs = [r for r in ds if r["capsule_uuid"] == uuid]
    if not qs:
        sys.exit(f"No questions for capsule {uuid}")

    print(f"# Capsule {uuid}  ({len(qs)} questions)\n")
    for r in qs:
        print(f"- [{r['eval_mode']}] {r['question'][:110]}")
        print(f"      ideal={r['ideal']!r}  distractors={r['distractors']}")

    folder = qs[0]["data_folder"]
    path = hf_hub_download(HF_REPO, folder, repo_type="dataset", local_dir="/tmp/bixcaps")
    with zipfile.ZipFile(path) as z:
        data_files = [n for n in z.namelist() if not n.endswith("/") and ".ipynb" not in n]
        nb_names = [n for n in z.namelist() if n.endswith(".ipynb")]
        print(f"\n## Data files agent receives ({len(data_files)}):")
        for d in data_files:
            print(f"  {d.split('/')[-1]}")
        if not nb_names:
            print("\n## NO ground-truth notebook -> canonical workflow was command-line.")
            return
        nb = json.load(io.TextIOWrapper(z.open(nb_names[0])))

    ks = nb.get("metadata", {}).get("kernelspec", {})
    # kernelspec often omits "language"; map the kernel name (e.g. "ir" -> R).
    lang = ks.get("language") or {"ir": "R", "python3": "python"}.get(
        ks.get("name", ""), ks.get("name", "?")
    )
    code = "\n".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )
    print(f"\n## Ground-truth notebook language: {lang}")
    print(f"## Judgment-call signals (agent cannot recover these from the question):")
    for m in sorted({m.group(0).strip().lower() for m in JUDGMENT.finditer(code)}):
        print(f"  - {m}")
    print("\n## Analysis code (the canonical pipeline):\n")
    print(code)


if __name__ == "__main__":
    main()
