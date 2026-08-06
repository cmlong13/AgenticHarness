# Memory-loop demonstration -- Run A (terminal memory append)

`run_id`: `run-20260806-memoryloop-001` -- `task_id`: `T-MEMLOOP-A`

## What this is, honestly

This is **not** a full, live four-phase `/work` pipeline run. Per Part 8's explicit
allowance ("A complete live multi-agent run is not required for this milestone if the
assignment requirement can be satisfied by deterministic evidence"), this is a real,
live invocation of the *terminal memory-append step* in isolation: two genuine calls to
`python -m harness.orchestrator.live_cli` (the same CLI `/work` itself calls), against
this repository's real `memory/` directory, with real stdout captured verbatim below and
in `live_cli/resp-01-append-fact.json` / `live_cli/resp-02-append-lesson.json`. No agent
was dispatched, no source file under `demo-repo/` was touched, and no other canonical
artifact (`scope.json`, `findings.json`, etc.) was produced or needed for this narrow
proof.

## The lesson is grounded in real, pre-existing project history

The fact and lesson appended here are **not invented for this fixture**. Both cite
`source_run_id: "run-20260803-riskband-001"` and `evidence_ref:
"runs/run-20260803-riskband-001/logs/policy-events.jsonl"` -- a real, already-retained
run in this repository (see `PROJECT_SPEC.md`'s "Dry-run evidence
(2026-08-03, `run-20260803-riskband-001`)"). That run's own retained
`logs/policy-events.jsonl` records a genuine live Architect dispatch whose raw response
was wrapped in a Markdown code fence, which `architect.md`'s Output contract forbids;
`harness/orchestrator/core.py`'s no-fence-stripping policy correctly treated this as a
parse failure and blocked Research rather than silently repairing the response. This is
exactly the kind of "strict JSON transport failures consume a bounded correction budget"
lesson the assignment names as a legitimate example (`ASSIGNMENT.md`/task instructions),
already supported by real project history, not manufactured for this demonstration.

## What was actually done (in order)

1. `runs/run-20260806-memoryloop-001/live_cli/req-01-append-fact.json` -- a request to
   `append_memory` (`kind: "fact"`) with the fact below.
2. Invoked for real: `python -m harness.orchestrator.live_cli --request-file
   runs/run-20260806-memoryloop-001/live_cli/req-01-append-fact.json`. Real stdout,
   exit code `0`, captured verbatim at `live_cli/resp-01-append-fact.json`.
3. `runs/run-20260806-memoryloop-001/live_cli/req-02-append-lesson.json` -- a request to
   `append_memory` (`kind: "lesson"`) with the lesson below.
4. Invoked for real: `python -m harness.orchestrator.live_cli --request-file
   runs/run-20260806-memoryloop-001/live_cli/req-02-append-lesson.json`. Real stdout,
   exit code `0`, captured verbatim at `live_cli/resp-02-append-lesson.json`.
5. Both operations retained their own policy events automatically, visible unmodified at
   `logs/policy-events.jsonl` in this run directory: `memory_fact_append` (`result:
   "appended"`) and `memory_lessons_append` (`appended_ids: ["L-20260806-TRANSPORT-BUDGET"]`).
6. The real, persistent files this repository now carries as a result:
   `memory/facts.jsonl` (one line, the fact below) and `memory/lessons-learned.md` (the
   lesson below, appended after the file's header, which `append_lessons` created since
   the file did not previously exist).

## The persisted fact

```json
{
  "id": "FACT-20260803-ARCHFENCE",
  "content": "In run-20260803-riskband-001, the live Architect dispatch returned findings wrapped in a Markdown code fence, violating architect.md's no-fence Output contract; harness/orchestrator/core.py's no-fence-stripping policy required treating this as a parse failure and blocking Research rather than silently repairing the response.",
  "source_run_id": "run-20260803-riskband-001",
  "evidence_ref": "runs/run-20260803-riskband-001/logs/policy-events.jsonl",
  "recorded_at": "2026-08-06",
  "status": "confirmed",
  "tags": ["transport", "architect", "fence", "research"]
}
```

## The persisted lesson

`- [L-20260806-TRANSPORT-BUDGET] (source_run: run-20260803-riskband-001, evidence:
runs/run-20260803-riskband-001/logs/policy-events.jsonl, date: 2026-08-06, tags:
transport,validation,json,correction-budget) Strict JSON transport failures (a fenced or
prose-wrapped raw agent reply) must be classified and repaired separately from content
failures, before any semantic or schema validation is attempted, and are only ever
granted a single bounded correction attempt per phase rather than an unbounded retry
loop.`

## What this does not prove

This does not prove a live agent produced this lesson by reasoning about a fresh failure
in real time -- the lesson text was authored by the operator (me) from the existing,
retained `run-20260803-riskband-001` evidence, exactly as `work/SKILL.md`'s own
"Terminal memory append" step instructs the orchestrator to do at the end of a real run.
What *is* proven live here: the real `append_memory` operation, the real duplicate/
provenance/evidence/workflow-relevance validation inside `harness/orchestrator/memory.py`,
and the real, durable persistence to `memory/facts.jsonl` / `memory/lessons-learned.md`
that Run B (`runs/run-20260806-memoryloop-002/`) then genuinely loads, selects, and
cites.
