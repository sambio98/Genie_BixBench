# Skill: PCR amplicon length

TASK: the expected amplicon length from a primer pair on a template.

With `run_python` + pydna (preferred) or manual matching:
1. pydna:
   ```
   from pydna.dseqrecord import Dseqrecord
   from pydna.amplify import pcr
   amplicon = pcr(fwd_primer, rev_primer, Dseqrecord(template))
   print(len(amplicon))
   ```
2. Manual fallback: the forward primer's 3' region matches the template; the
   reverse primer's 3' region matches the reverse complement. Locate both binding
   sites; the amplicon spans from the 5' end of the forward primer to the 5' end
   of the reverse primer (on the template coordinates), INCLUDING both primer
   regions. Length = that span.
3. Report the length and COMMIT. If pydna raises (primers don't anneal cleanly),
   re-check primer orientation before falling back to manual.
