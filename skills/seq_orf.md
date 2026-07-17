# Skill: longest-ORF translation

TASK: "the amino acid at position N in", or "the amino-acid sequence of", the
LONGEST open reading frame in a given DNA/RNA sequence.

Do this exactly, with `run_python` + biopython — never by hand:

1. Copy the input sequence VERBATIM into code. If it is RNA, convert U→T for
   frame scanning.
2. Enumerate all SIX reading frames: frames 0/1/2 of the sequence, and 0/1/2 of
   its reverse complement (`Bio.Seq.reverse_complement`).
3. In every frame, find each ORF = from an `ATG` to the next IN-FRAME stop
   (`TAA`/`TAG`/`TGA`); the ORF spans the ATG up to (not including) the stop.
4. Choose the LONGEST ORF across all six frames (most codons). On ties, the
   convention is the first encountered scanning frames in order — but a true tie
   is rare; if two ORFs tie, compute both and see which yields an option.
5. Translate the chosen ORF with the standard genetic code
   (`Bio.Seq.translate(..., to_stop=True)`). Position 1 is the initiator Met (M);
   amino-acid positions are 1-indexed.
6. Report the answer in the SAME notation the options use — inspect the options:
   they may be 1-letter (`M`), 3-letter (`Met`), or full names (`Methionine`).
   Convert accordingly (`Bio.SeqUtils.seq1`/`seq3`).
7. Match to an option and COMMIT. Do not abstain if a frame/ORF yields a listed
   amino acid.
