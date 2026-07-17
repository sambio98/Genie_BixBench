# Skill: cloning primer / enzyme selection

TASK: choose the correct primer pair (or restriction enzymes) to clone a gene
into a plasmid — by restriction–ligation, or into a blunt/linearized site.

Reason THEN verify with `run_python`:
1. Identify: the gene to amplify (its exact ends), the destination plasmid /
   restriction site(s) or blunt cut, and the method.
2. Requirements a correct primer pair must satisfy:
   - The 3' annealing region matches the gene's start (forward) and the reverse
     complement of the gene's end (reverse).
   - For restriction–ligation: each primer carries the required enzyme site as a
     5' tail (often + a few extra bases for efficient cutting), matching the
     plasmid MCS; the sites must NOT occur inside the gene; orientation and, if
     relevant, reading frame are preserved.
   - For blunt/linearized (e.g. SmaI): primers amplify the exact insert with no
     added sites; ends are blunt.
3. For "which enzymes": scan the given primers' 5' tails for restriction sites
   (`Bio.Restriction` on each primer), confirm they match the plasmid and are
   absent inside the insert.
4. Use `run_python` to VERIFY the chosen candidate: amplify with pydna, check the
   product carries the right sites (`Bio.Restriction.search`) and matches the
   gene. Pick the verified option and COMMIT.
