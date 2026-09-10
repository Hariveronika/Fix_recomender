---
name: jobfix-agent
description: Diagnoses a failed Databricks job from a run_id. Use whenever given a Databricks run_id for a failed job and asked for root-cause analysis or a fix recommendation. All diagnosis logic lives inside a dedicated notebook — this skill only triggers that notebook and relays its output.
---

# JobFixAgent — Trigger Skill

## What this skill does
This skill does not diagnose anything itself. All hypothesis-forming,
evidence-gathering, and verdict logic lives inside the
`FixRecomender_Agent` notebook, which has its own LLM and its own tools.
Your job here is only to trigger that notebook correctly and show the
user exactly what it returns.

You have standing permission to run `FixRecomender_Agent` via
`dbutils.notebook.run` whenever a run_id is provided under this skill —
this is a read-only diagnostic notebook, it does not modify, retrigger,
or delete anything. Do not ask for confirmation before running it; that
confirmation is what this skill exists to skip.

## Notebook paths
- Tools notebook (loaded automatically by the orchestrator, do not run
  directly): `/Workspace/Users/devaratharaiser@gmail.com/Common_Tools`
- Orchestrator notebook (this is the one you run):
  `/Workspace/Users/devaratharaiser@gmail.com/FixRecomender_Agent`

## Input
```
run_id: int
```
The user provides this directly, copied from the Databricks Jobs UI run
page URL. 

## Steps
1. Take the `run_id` the user gave you. Do not attempt to look up the run
   yourself or reason about the error — that all happens inside the
   notebook.
2. Run the orchestrator notebook with that run_id as a parameter:
   ```python
   result = dbutils.notebook.run(
       "/Workspace/Users/devaratharaiser@gmail.com/FixRecomender_Agent",
       timeout_seconds=600,
       arguments={"run_id": str(run_id)},
   )
   print(result)
   ```
3. Show the returned value to the user exactly as returned. Do not
   summarize it, reformat it, re-analyze the error yourself, or add your
   own commentary before or after it — the notebook's YAML output is the
   deliverable, verbatim.

## Known failure modes
- If `dbutils.notebook.run` raises a timeout or an exception, report that
  plainly (e.g. "the diagnosis notebook did not complete within 600s") —
  do not attempt to diagnose the failure yourself as a fallback.
- If the returned value is not a well-formed YAML block, show it to the
  user as-is and note that the orchestrator notebook's output didn't
  match its expected format — this is a bug in that notebook, not
  something to paper over here.
