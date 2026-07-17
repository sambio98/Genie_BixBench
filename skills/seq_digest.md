# Skill: restriction-digest fragment lengths

TASK: the fragment lengths after digesting a sequence with named restriction
enzyme(s).

With `run_python` + `Bio.Restriction`:
1. Build the sequence (`Bio.Seq.Seq`). Decide linear vs circular: a bare sequence
   is linear; a "plasmid" is circular (`Dseq(..., circular=True)` in pydna, or set
   `linear=False`).
2. Import the named enzyme(s): `from Bio.Restriction import TspRI, EcoRI, ...`, or
   build a `RestrictionBatch([...])`.
3. Get cut sites: `enzyme.search(seq, linear=True/False)` returns 1-based cut
   positions. For multiple enzymes, combine all sites.
4. Compute fragment lengths from the sorted cut sites:
   - linear: sizes between 0, the sorted cuts, and len(seq).
   - circular: sort cuts around the circle; the fragment sizes are the gaps,
     wrapping the last cut back to the first.
5. Report the fragment lengths (sorted; match the option's ordering/format).
   COMMIT.
