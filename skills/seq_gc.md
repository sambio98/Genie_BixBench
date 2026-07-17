# Skill: GC content

TASK: the percent GC of a DNA sequence.

With `run_python` + biopython:
1. Copy the sequence VERBATIM.
2. `from Bio.SeqUtils import gc_fraction; pct = gc_fraction(seq) * 100`
   (or `(seq.count('G') + seq.count('C')) / len(seq) * 100`).
3. Round exactly as the question says (default: nearest integer).
4. Match to an option and COMMIT.
