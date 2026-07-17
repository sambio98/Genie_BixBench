# Skill: highest-translation-efficiency ORF (human cell)

TASK: "which of the following RNA sequences contains an ORF most likely to have
HIGH translation efficiency in a human cell?" — a ranking over candidate RNAs.

Score each candidate by the sequence determinants of cap-dependent initiation,
using `run_python` to extract the exact context — do not eyeball it:

1. For each option, copy the RNA VERBATIM, convert U→T. Locate the ORF's start
   codon (the AUG that opens the main/longest ATG→stop ORF).
2. **Kozak context is the dominant factor.** Optimal is `gccRccATGG`. Score the
   two critical positions relative to the A of ATG (A = +1):
   - position **-3** (3 nt upstream of the A): a **purine (A or G)**, best = A.
   - position **+4** (the base immediately after the ATG): best = **G**.
   Strong Kozak = purine at -3 AND G at +4; moderate = one of them; weak = neither.
3. **Upstream AUGs / uORFs penalise efficiency:** count AUG codons in the 5' UTR
   (before the main start). Any upstream AUG (especially with its own stop = a
   uORF) lowers efficiency by diverting scanning ribosomes.
4. Tie-breakers only if Kozak + uORF are equal: shorter, less-structured 5' UTR
   and higher codon optimality (CAI) translate better.
5. Rank the candidates: strongest Kozak with the fewest upstream AUGs wins. Pick
   that option and COMMIT; abstain only if two candidates are truly
   indistinguishable on all criteria.
