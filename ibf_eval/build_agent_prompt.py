"""Assemble the exact Agent-tool prompt for one BixBench question, verbatim.

Exists to close a fidelity bug found during the 6-question pilot: hand-writing
per-question Agent prompts let interpretive "helpful" hints creep in (e.g.
adding a log2-transform requirement the real question never stated, or
describing the wrong clustering procedure entirely). Three of six pilot
failures traced back to this, not to any real code_agent capability gap.

The fix: assemble the wrapper programmatically from the manifest's raw
`question` field, so scaling to 30 questions can never re-introduce the same
transcription drift. The real orchestrator's own contract is `f"Task: {task}"`
verbatim (see run_code_agent in code_agent.py) -- this mirrors that exactly.

Usage:
    python build_agent_prompt.py workspace/manifest.json bix-4-q1
    # prints the full ready-to-paste Agent prompt for that question_id
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_builder import build_system_prompt  # noqa: E402

OPERATIONAL_BRIDGE = """\
=== OPERATIONAL BRIDGE (how to actually execute, since you have Bash not a custom exec tool) ===
- A Python virtualenv with the packages listed above is at /opt/bixbench-venv. Activate it in
  every Bash call: `source /opt/bixbench-venv/bin/activate && python3 your_script.py`
- The command-line tools listed above are directly on PATH -- call them directly via Bash
  exactly as shown in the cookbook (equivalent to the production run_tool() wrapper).
- Write your Python code to .py files and run them with python3 (not a REPL), so you can
  iterate: read error output, fix, retry.
- You do NOT have network access beyond what's already downloaded, EXCEPT that gseapy's
  enrichr() and similar API-backed calls may work if egress is available -- try them; if they
  fail, that is a genuine environment limitation to note, not something to fabricate around.
- The task below is copied VERBATIM from the benchmark -- it may be underspecified in places
  (exact method details left to you). Make the most standard, defensible methodological choice
  for each ambiguity, state it clearly in your work log, and proceed. Do not add requirements
  the question does not state (e.g. do not assume a particular transform/normalization unless
  the question specifies one).
- Budget: aim to converge within roughly 15-20 Bash/tool actions (more if the data is large,
  e.g. raw FASTQ alignment)."""

ANSWER_FORMAT = """\
=== FINAL ANSWER FORMAT (mandatory) ===
When you are done (or when you must give up per the rules above), your VERY LAST line of output
must be exactly: <solution>YOUR_ANSWER</solution>
Do not add anything after this tag. This is the only thing that will be graded -- everything
else you say is just your work log."""


def build_agent_prompt(row: dict) -> str:
    """The complete, ready-to-paste Agent-tool prompt for one manifest row."""
    system_prompt = build_system_prompt(row["output_dir"], row["input_dir"])
    return (
        "You are role-playing a specific production coding sub-agent EXACTLY as specified "
        "below. Follow the system prompt as your governing behavioral contract. This is a "
        "research evaluation of that sub-agent's real-world design, using real data.\n\n"
        "=== BEGIN SYSTEM PROMPT (your governing instructions) ===\n"
        f"{system_prompt}\n"
        "=== END SYSTEM PROMPT ===\n\n"
        f"{OPERATIONAL_BRIDGE}\n\n"
        "=== YOUR TASK (verbatim, exactly as given in the benchmark) ===\n"
        f"{row['question']}\n\n"
        f"{ANSWER_FORMAT}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_agent_prompt.py <manifest.json> <question_id>")
    manifest = json.loads(Path(sys.argv[1]).read_text())
    row = next(r for r in manifest if r["question_id"] == sys.argv[2])
    print(build_agent_prompt(row))
