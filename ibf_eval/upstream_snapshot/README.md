# Upstream snapshot -- patched iBioFoundry-AI source

These files are full copies of the **actual production files** from the
`iBioFoundry-AI` codebase (uploaded this session as a zip, extracted to
`/tmp/ibf/iBioFoundry-AI-main` -- a path local to this container, not part of
any git history), after applying the fixes documented in
`ibf_eval/plans/plan.bixbench-cookbook-hardening-2026.07.02.md`.

They exist here for two reasons:

1. **Durability.** `/tmp/ibf` is ephemeral container state. Without this
   snapshot, the only record of Fixes 1/6/8 (all in `eda.py`, which the harness
   loads dynamically via `importlib` rather than mirroring as a string --
   see `ibf_eval/prompt_builder.py`) would be the plan doc's prose + illustrative
   snippets, not the actual runnable file.
2. **Deliverable.** This is what should be copied back over the corresponding
   paths in the real `iBioFoundry-AI` repo to ship the fixes:

   | Snapshot path | Real repo path |
   |---|---|
   | `ibiofoundry_ai/tools/eda.py` | `ibiofoundry_ai/tools/eda.py` |
   | `ibiofoundry_ai/tools/python_exec.py` | `ibiofoundry_ai/tools/python_exec.py` |
   | `ibiofoundry_ai/prompts/code_agent.md` | `ibiofoundry_ai/prompts/code_agent.md` |
   | `Dockerfile` | `Dockerfile` |

`python_exec.py` and `code_agent.md`'s changes (Fixes 2-5, Rule 18) are *also*
mirrored as literal Python strings in `ibf_eval/prompt_builder.py` so the
harness can run without this directory; `eda.py` and `Dockerfile` are not
(there is nothing to "mirror" for a Dockerfile -- the harness sandbox has the
tool pre-installed a different way), so for those two files this snapshot is
the only complete, durable copy. Re-sync this directory (`cp` the four files +
re-run `diff -q` to confirm) any time `/tmp/ibf`'s copies change.
