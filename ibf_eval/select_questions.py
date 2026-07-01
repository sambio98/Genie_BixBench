"""Select a 30-question, non-R BixBench subset for the iBioFoundry-AI code_agent eval.

Stratifies across eval_mode (str/range/llm) and ecosystem (Python/CLI) so the
harness exercises both workflow families their code_agent targets: pydeseq2/
gseapy/scanpy (Python) and mafft->fasttree->phykit / bwa->samtools->bcftools
(CLI, via run_tool). R-canonical capsules are excluded per project scope.

Downloads each selected capsule's data (NOT its stripped ground-truth notebook
-- the code_agent, like the real agent, only ever sees data files) into
workspace/<question_id>/data/input/, matching WorkspaceConfig's real layout
(DATA_DIR = data/input/, OUTPUT_DIR = data/output/).

Usage: python select_questions.py [--n 30] [--out workspace]
"""

from __future__ import annotations

import argparse
import json
import random
import zipfile
from collections import defaultdict
from pathlib import Path

from datasets import load_dataset
from huggingface_hub import HfFileSystem, hf_hub_download

HF_REPO = "futurehouse/BixBench"
SEED = 20260615  # fixed for reproducible sampling across harness runs


def classify_capsule_language(fs: HfFileSystem, data_folder: str) -> str:
    """R / Python / CLI, by reading only the notebook's kernelspec (or its absence)."""
    import json as _json

    with fs.open(f"datasets/{HF_REPO}/{data_folder}", "rb") as fh:
        zf = zipfile.ZipFile(fh)
        nbs = [n for n in zf.namelist() if n.endswith(".ipynb")]
        if not nbs:
            return "CLI"
        ks = _json.loads(zf.read(nbs[0])).get("metadata", {}).get("kernelspec", {})
        return {"ir": "R", "python3": "Python"}.get(ks.get("name", ""), "?")


def stratified_sample(ds, n: int) -> list[dict]:
    """Pick n non-R questions, balanced across (ecosystem, eval_mode) buckets."""
    fs = HfFileSystem()
    lang_cache: dict[str, str] = {}
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for row in ds:
        folder = row["data_folder"]
        if folder not in lang_cache:
            lang_cache[folder] = classify_capsule_language(fs, folder)
        lang = lang_cache[folder]
        if lang == "R":
            continue
        buckets[(lang, row["eval_mode"])].append(row)

    print("Non-R pool by (ecosystem, eval_mode):")
    for k, v in sorted(buckets.items()):
        print(f"  {k}: {len(v)} questions")

    rng = random.Random(SEED)
    for rows in buckets.values():
        rng.shuffle(rows)

    # Round-robin across buckets so no single family dominates the 30.
    selected: list[dict] = []
    bucket_keys = sorted(buckets.keys())
    idx = 0
    while len(selected) < n and any(buckets[k] for k in bucket_keys):
        k = bucket_keys[idx % len(bucket_keys)]
        if buckets[k]:
            selected.append(buckets[k].pop())
        idx += 1
    return selected[:n]


def prepare_workspace(row: dict, out_root: Path) -> dict:
    """Download the capsule's data files into <out>/<question_id>/data/input/."""
    qdir = out_root / row["question_id"]
    input_dir = qdir / "data" / "input"
    output_dir = qdir / "data" / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    zpath = hf_hub_download(
        HF_REPO, row["data_folder"], repo_type="dataset", local_dir=str(out_root / "_zips")
    )
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            if name.endswith("/") or ".ipynb" in name or "CapsuleNotebook" in name:
                continue  # the agent never sees the ground-truth notebook
            target = input_dir / Path(name).name
            with zf.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())

    return {
        "question_id": row["question_id"],
        "capsule_uuid": row["capsule_uuid"],
        "question": row["question"],
        "ideal": row["ideal"],
        "distractors": list(row["distractors"]),
        "eval_mode": row["eval_mode"],
        "workspace_dir": str(qdir),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", type=Path, default=Path("workspace"))
    args = ap.parse_args()

    ds = load_dataset(HF_REPO, split="train")
    selected = stratified_sample(ds, args.n)
    print(f"\nSelected {len(selected)} questions; downloading capsule data...")

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, row in enumerate(selected, 1):
        print(f"  [{i}/{len(selected)}] {row['question_id']} ({row['eval_mode']})")
        manifest.append(prepare_workspace(row, args.out))

    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote manifest: {manifest_path} ({len(manifest)} questions)")


if __name__ == "__main__":
    main()
