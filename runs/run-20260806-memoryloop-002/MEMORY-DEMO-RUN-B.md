# Memory-loop demonstration -- Run B (load, select, apply, prove)

`run_id`: `run-20260806-memoryloop-002` -- `task_id`: `T-MEMLOOP-B`

## What this is, honestly

Same limitation as Run A: this is **not** a full, live four-phase `/work` pipeline run.
Discovery genuinely ran (three real `live_cli` calls: `retain_attempt`, `validate_scope`,
`promote_artifact`), producing a real, schema-valid `scope.json` -- but this run
intentionally stops there, exactly as Part 8 allows ("A complete live multi-agent run is
not required for this milestone if the assignment requirement can be satisfied by
deterministic evidence"). Research/Implementation/Verification were never attempted.
`run-summary.json`'s `final_verdict: "blocked"` reflects this by design, not a failure --
its own `objective_summary` states this explicitly, matching the harness's existing
dry-run-boundary convention (`state.py` has no "intentionally paused" value; this is a
known, already-documented representational gap, not something new).

## The full, real command sequence (every step actually executed)

All commands below are real invocations of `python -m harness.orchestrator.live_cli`,
run from the repository root, exit code `0` at every step. Every request/response pair
is retained verbatim under `live_cli/`.

| step | operation | request | response | result |
|---|---|---|---|---|
| 1 | `load_memory` | `req-01-load-memory.json` | `resp-01-load-memory.json` | selected `L-20260806-TRANSPORT-BUDGET` (and `FACT-20260803-ARCHFENCE`) as relevant, via deterministic keyword intersection -- no hand-picked list |
| 2 | `retain_attempt` (discovery) | `req-02-retain-discovery.json` | `resp-02-retain-discovery.json` | raw scope candidate retained before validation |
| 3 | `validate_scope` | `req-03-validate-scope.json` | `resp-03-validate-scope.json` | `status: "valid"` |
| 4 | `promote_artifact` (discovery) | `req-04-promote-scope.json` | `resp-04-promote-scope.json` | real, canonical `scope.json` written |
| 5 | `record_memory_applied` | `req-05-record-memory-applied.json` | `resp-05-record-memory-applied.json` | `status: "retained"` -- a real `memory_applied` event |
| 6 | `summarize_memory` | `req-06-summarize-memory.json` | `resp-06-summarize-memory.json` | `memory_influenced_run: true`, `memory_refs_used: ["L-20260806-TRANSPORT-BUDGET"]` -- derived from step 5's retained event, not asserted |
| 7 | `write_run_summary` | `req-07-write-run-summary.json` | `resp-07-write-run-summary.json` | schema-valid `run-summary.json` written |

## Step 1: the lesson is selected as relevant -- deterministically, not by hand

`raw_prompt` for this task ("Add a fixture-level pre-check confirming a new staged
agent's raw JSON transport ... is validated before any semantic or schema parsing is
attempted") was authored *without* looking at the lesson's own wording first, then run
through the real `load_memory` operation. `memory.derive_keywords()` tokenized it, and
`memory.select_relevant_lessons()` intersected those tokens against
`L-20260806-TRANSPORT-BUDGET`'s own text/tags -- the overlap ("transport", "json",
"validation"/"validated", "fence") is what actually produced the match; no keyword list
was hand-picked to force this result. The fact `FACT-20260803-ARCHFENCE` matched too
(shared "transport"/"fence" vocabulary) -- both are reported as *relevant*, which is a
weaker claim than *applied*. Only the lesson is cited in the concrete decision below.

## Step 2-4: Discovery genuinely ran and cites the lesson's exact ID

`runs/run-20260806-memoryloop-002/scope.json` (the real, promoted artifact) contains:

```json
"constraints": [
  "Apply lesson L-20260806-TRANSPORT-BUDGET (source run run-20260803-riskband-001, memory/lessons-learned.md): a new staged agent's raw JSON transport format (no Markdown code fence, no leading/trailing prose) must be validated and, if malformed, classified as a transport failure and handled via the one-per-phase correction budget BEFORE any semantic or schema validation of its content is attempted -- never validate content first and treat transport as an afterthought."
],
"acceptance_criteria": [
  {
    "id": "AC-1",
    "description": "Given a raw agent reply, the transport-format check (strict JSON parse, no fence, no prose) runs and can block before any semantic/schema validator is ever invoked on that reply, matching lesson L-20260806-TRANSPORT-BUDGET's ordering requirement."
  }
]
```

This ordering requirement is not hypothetical -- it is exactly what
`harness/orchestrator/core.py`'s own `_parse_raw()`/`_drive_staged_protocol()` already do
(strict JSON parsing happens before `validate_against_schema`/semantic validation is ever
called), and what `work/SKILL.md`'s Architect/Engineer/Quality-Engineer transport-repair
sections already codify. `N-1`'s `task_graph` description names this exact citation as
the Research-phase job a real continuation of this task would do next.

## Step 5: the concrete decision and its evidence, cited explicitly

The retained `memory_applied` event (`logs/policy-events.jsonl`, reproduced below)
names, in one record: the exact entry (`L-20260806-TRANSPORT-BUDGET`), its source run
(`run-20260803-riskband-001`), the current run (`run-20260806-memoryloop-002`), the phase
(`discovery`), the concrete decision in prose, and `evidence_path` pointing at the real
`scope.json` where that decision is visible -- independently re-checked by
`record_memory_applied` to actually exist before the event was allowed to be retained.

```json
{"kind": "memory_applied", "entry_id": "L-20260806-TRANSPORT-BUDGET", "entry_type": "lesson", "source_run_id": "run-20260803-riskband-001", "current_run_id": "run-20260806-memoryloop-002", "phase": "discovery", "decision": "scope.json's constraints and AC-1 require that a new staged agent's raw JSON transport format (no fence, no prose) is validated and, if malformed, handled as a transport failure BEFORE any semantic/schema validation of its content is attempted -- adopted directly from lesson L-20260806-TRANSPORT-BUDGET rather than decided independently.", "evidence_path": "runs/run-20260806-memoryloop-002/scope.json"}
```

## Step 6-7: loaded vs. applied, honestly distinguished in the run summary

`summarize_memory` re-derives `memory_loaded`/`memory_influenced_run`/`memory_refs_used`
from `logs/policy-events.jsonl` itself -- it does not trust an assertion. Because a real
`memory_applied` event (step 5) exists in this run's own log, `memory_influenced_run` is
genuinely `true` and `memory_refs_used` genuinely lists `L-20260806-TRANSPORT-BUDGET`.
Had step 5 never happened -- had this run only loaded memory and stopped -- `summarize_memory`
would have returned `memory_influenced_run: false`, exactly as
`tests/test_orchestrator_memory.py::TestActualUseProof::test_reading_a_lesson_alone_does_not_set_memory_influenced_run`
proves deterministically. `runs/run-20260806-memoryloop-002/run-summary.json` carries
these three fields verbatim from step 6's real output, never hand-asserted.

## What this does and does not prove

**Proves, live and for real:** memory loads before Discovery (Phase 0's ordering);
deterministic tag/keyword relevance selection (no hand-picked list, no LLM similarity
judgment); a genuine `scope.json` citing the exact lesson ID in its own constraints and
acceptance criteria; a `memory_applied` event whose `evidence_path` independently
resolves to that real artifact; and a run summary whose `memory_influenced_run: true` is
backed by that retained event, not by assertion.

**Does not prove:** a full four-phase pipeline where memory influenced a later
Implementation/Verification decision (Research/Implementation/Verification were never
attempted here, by design); or that a live subagent, rather than the operator following
`work/SKILL.md`'s own documented steps, produced this citation. Both are the same,
already-stated limitation as Run A -- see `MEMORY-DEMO-RUN-A.md`.
