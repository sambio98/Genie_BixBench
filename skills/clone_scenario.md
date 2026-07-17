# Skill: molecular-cloning scenario simulation

TASK: a cloning scenario (plasmid(s) + oligos/fragments/primers) asking for the
resulting construct, a size/junction, an orientation, success/failure, or the
required reagent. Covers restriction–ligation, annealed-oligo insertion,
Gibson/Golden-Gate assembly, and site-directed modification.

SIMULATE with `run_python` (pydna / dnacauldron / Bio.Restriction) — do not reason
the product by hand:
1. Build every molecule as a `pydna` `Dseqrecord` (plasmids `circular=True`); copy
   sequences VERBATIM.
2. Determine the operation and simulate it exactly:
   - **Annealed oligos** → anneal the two oligos (reverse-complementary core) into
     a duplex with overhangs; check the overhangs match the cut vector.
   - **Restriction–ligation** → digest vector and insert with the named enzymes
     (`Bio.Restriction`/`pydna .cut`); ligate compatible ends; respect
     orientation.
   - **Gibson / Golden Gate** → assemble by terminal homology / Type-IIS overhangs
     (`pydna.assembly.Assembly`, `dnacauldron`).
     GOLDEN GATE recipe (BsaI/BsmBI/BbsI) — do it directly, do not thrash:
     ```
     from Bio.Seq import Seq
     from Bio.Restriction import BsaI            # or BsmBI / BbsI
     # 1. cut each circular part; a Type-IIS enzyme cuts OUTSIDE its site,
     #    leaving 4-nt 5' overhangs. Bio.Restriction .catalyse handles the offset.
     # 2. each cut yields fragments; DROP the fragment that still contains the
     #    enzyme recognition site (that piece is released, not in the product).
     # 3. read each kept fragment's two 4-nt overhangs; join fragments whose
     #    abutting overhangs are identical (complementary sticky ends), walking
     #    the ring until it closes. The closed ring is the product.
     ```
     If the pydna Assembly API stalls, fall back to this manual overhang walk in
     plain Python — enumerate fragment overhangs and match them yourself.
   - **Modify to a target sequence** → align current vs desired
     (`Bio.Align`/`difflib`) to find the exact edit; simulate the method that
     yields it.
3. Inspect the predicted product for exactly what is asked. Common asks and how
   to read them off the assembled construct:
   - **size / junction / reading frame / orientation / a restriction site.**
   - **antibiotic resistance** → find the resistance ORF carried onto the final
     circular product (AmpR/bla, KanR, CmR, …); only markers physically retained
     after assembly count.
   - **which RNA polymerase** → identify the promoter driving the transcript
     (T7 → T7 RNAP, SP6 → SP6, T3 → T3; a host σ70/PolII promoter → the host
     enzyme).
   - **which enzyme for the assembly** → the Type-IIS enzyme whose recognition
     sites flank the parts with the correct cut orientation (BsaI/BsmBI/BbsI…).
   - **purpose of a part / the construct** → read the assembled transcription unit
     (promoter → ORF/gRNA → terminator).
   - **a sequencing or screening primer** → pick the offered primer that anneals
     just OUTSIDE the region of interest and reads INTO it (≈50–100 bp away, on
     the correct strand); verify by locating it in the assembled sequence.
4. Match the computed result to an option and COMMIT; abstain only if the scenario
   stays under-determined after a correct simulation.

Be efficient: plan the few code steps you need, run them, and decide. If an
assembly call keeps erroring, switch to the manual overhang/feature reasoning
above rather than retrying the same call — then commit to the best-supported
option instead of looping.
