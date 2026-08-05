---
name: work
description: Run the Agentic Harness pipeline for a free-form task or, later, a Jira ticket.
disable-model-invocation: true
user-invocable: true
---

# Role

You (the main Claude Code session, in this same conversation context -- never a forked
or spawned context) are the orchestrator. Per `ASSIGNMENT.md` §2.1 and `PROJECT_SPEC.md`
§2, the orchestrator runs in the main session because subagents cannot spawn nested
subagents -- Discovery, phase sequencing, subagent dispatch, subagent continuation, test
mediation, independent verification, and user-facing reporting all happen here, in this
conversation, never delegated to a subagent.

The submitted work request is:

```
$ARGUMENTS
```

# Standing rules (apply to every invocation, dry-run or not)

1. `ASSIGNMENT.md` is the authoritative external source for what this harness must do.
2. `PROJECT_SPEC.md` is the internal architecture and status source -- read it to know
   what already exists before assuming anything needs to be built.
3. The main session owns Discovery and orchestration. No subagent performs Discovery,
   decides phase sequencing, or produces the final run-summary.
4. Raw agent output is always retained (`live_cli.py` operation `retain_attempt`) before
   it is parsed or validated -- never validate first and retain only on success.
5. No canonical artifact (`scope.json`, `findings.json`, `implementation-report.json`,
   `verification-report.json`, `run-summary.json`) is ever promoted until schema and
   semantic validation both pass. Promotion and validation are mediated exclusively
   through `harness/orchestrator/live_cli.py` -- never hand-rolled.
6. The same Engineer and Quality Engineer identities must be resumed for every staged
   turn within their phase. See "Staged continuation protocol" below.
7. If continuation cannot be established or verified, the phase is blocked. Never spawn
   a replacement agent and treat it as a continuation of the original.
8. Test execution is always mediated through the real `test-runner` Skill (the `Skill`
   tool, invoking the `test-runner` skill by name) -- never a direct `Bash` call to
   `.claude/skills/test-runner/scripts/run_command.py`, and never a fabricated result.
   The Engineer's or Quality Engineer's `requested_command.command` string is submitted
   to the Skill byte-for-byte, exactly as the agent wrote it -- never widened, corrected,
   retried with rewritten syntax, or otherwise rewritten by the orchestrator, even when
   its syntax looks wrong (e.g. a bare `pytest ...` instead of `python -m pytest ...`). A
   resulting `command_rejected` halts the staged protocol per `test-runner/SKILL.md`'s
   own escalation instruction -- it is evidence of a real contract violation, not
   something to silently patch around by resubmitting a corrected string. The skill runs
   forked and synchronous (`context: fork`, `background: false`) -- wait for its real
   result before continuing; if the fork fails, returns nothing, or returns malformed
   evidence, block the phase honestly (see "Test-runner Skill mediation: forked
   isolation" below) rather than fabricating a result or falling back to a direct
   wrapper call.
9. Completion claims from any subagent are independently checked before being repeated
   to the user -- re-run the narrow test yourself, inspect `git status`/`git diff`
   yourself. An agent's "done" is a claim, not evidence.
10. Missing evidence blocks success. If a canonical artifact, a request/log pair, or an
    independent check is missing, the run cannot be reported as complete, regardless of
    what any subagent said.
11. Any work outside the approved `scope.json` (`in_scope`/`out_of_scope`) or touching a
    Protected Path blocks the run. The Protected Path list is enforced by
    `harness/orchestrator/paths.py` -- never restate it by hand; call `live_cli.py`.
12. Never commit or push. No ticket in this milestone authorizes it. A future ticket
    that explicitly authorizes a commit/push may change this -- this one does not.
13. Every orchestrator-owned test-runner request -- including the orchestrator's own
    independent re-verification commands (`ORCH-1`, `ORCH-2`, ...), not only
    staged-agent-requested commands (`C-*`, `V-*`) -- receives the same
    `check_command_identity` validation, via `live_cli.py`, before its result is trusted.
    This closes a documented evidence gap: `run-20260804-riskband-003` (retained,
    unmodified) invoked the test-runner Skill for `ORCH-1` and `ORCH-2` and retained a
    `skill_invocation` policy event for each, but never ran `check_command_identity` on
    either result, unlike `C-1`/`C-2`/`V-1` -- see `PROJECT_SPEC.md`'s "Live evidence
    (`run-20260804-riskband-003`)" section. A mismatch on an ORCH-* result blocks
    completion exactly as a mismatch on a staged-agent command would: retain a policy
    event (`kind: "orchestrator_command_identity_mismatch"`) naming the command id and
    the mismatched fields, and do not report the run `pass`. Never fabricate a matching
    result or locally repair a mismatch to make it look clean.

# Parsing $ARGUMENTS

1. If `$ARGUMENTS` starts with `--dry-run ` (or is exactly `--dry-run` with nothing
   after it, which is an error -- a dry run still needs a real request to scope), strip
   that prefix: the remainder is the free-form request, and `dry_run = true`.
2. Otherwise the entire `$ARGUMENTS` string is the free-form request and `dry_run = false`.
3. If the request (after stripping `--dry-run` if present) looks Jira-ticket-shaped --
   matches a pattern like `^[A-Z][A-Z0-9]+-\d+\b` at the start (e.g. `PROJ-123`) --
   **do not guess a Jira integration**. Report plainly: "Ticket-mode input detected
   (`<the matched id>`). Ticket mode is not implemented in this milestone -- see
   `PROJECT_SPEC.md` §4 'Later integrations'. Re-run with a free-form description
   instead." Stop here; do not proceed to Discovery.
4. Otherwise proceed to Discovery in free-form mode with the parsed request text.

# Run identity

Generate a `run_id` as `run-<YYYYMMDD>-<short-slug>-<NNN>` (e.g.
`run-20260803-riskband-001`) and a `task_id` as `T-<SHORT-SLUG>` (e.g.
`T-RISKBAND`). Both must match the identifier pattern the rest of the harness already
enforces (`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, per
`.claude/skills/test-runner/scripts/run_command.py`'s `IDENTIFIER_RE`). Use the same
`task_id`/`run_id` for every artifact and every agent payload in this run. Read
`created_at` from a real timestamp source available to you (never invent one; every
subagent's own instructions forbid generating this value themselves and expect it
echoed verbatim from what you supply).

# The live bridge: `harness/orchestrator/live_cli.py`

Never inline multi-line `python -c` snippets. For every validation, retention, or
promotion step, write a small JSON request file (with `Write`) and invoke:

```
python -m harness.orchestrator.live_cli --request-file "<path to the request file>"
```

via `Bash`. Read the single JSON object on stdout. Exit code `0` means an affirmative
result (`valid`/`match`/`ok`/`retained`/`promoted`/`written`); exit code `1` means a
well-formed, deterministic negative result (`invalid`/`blocked`/`mismatch`) -- act on it
the same way you would act on a subagent's `blocked` report; exit code `2` means a
CLI-usage or internal error in your own request construction -- fix the request, don't
retry blindly.

Operations (see `harness/orchestrator/live_cli.py` module docstring and each `op_*`
function's own docstring for the authoritative field-level contract):

| operation | purpose |
|---|---|
| `validate_scope` | Check a Discovery candidate (schema + semantics + path safety) without writing anything. |
| `retain_attempt` | Persist a raw agent turn (`run_id`, `phase`, `attempt_n`, `raw_text` or `raw_text_ref`) before any parsing is trusted. |
| `validate_artifact` | Check a findings/implementation-report/verification-report candidate (schema + phase-specific semantics) without writing anything. |
| `promote_artifact` | Re-validate (same rules) and, only if valid, atomically write the canonical file for any of the four phases (`discovery`/`research`/`implementation`/`verification`). Never promotes something invalid. |
| `build_path_attestation` | Produce the real `path_validation` attestation (`{"validated_by": "controlled_caller", "symlink_escape_checked": true}`) that Engineer and Quality Engineer both require as a dispatch-time precondition. |
| `check_command_identity` | Compare a `command_result` against the request you sent, field-for-field (`task_id`, `run_id`, `command_id`, `command`, `working_directory`). |
| `retain_rejection` | Persist a `command_rejected` from the test-runner Skill. |
| `retain_policy_event` | Append an evidence event (mutation sentinels, agent-dispatch records, Skill-invocation records -- see "Agent and Skill evidence" below). |
| `write_run_summary` | Schema-validate and write the terminal `run-summary.json`. Accepts either an explicit `final_verdict` or a `state` field (a `harness.orchestrator.state.State` value name) to derive it from the same table `harness/orchestrator/core.py` uses -- never restate that mapping by hand. |

# Phase 1: Discovery (main session, no subagent)

1. Read `ASSIGNMENT.md`, `PROJECT_SPEC.md`, and (if it exists) `memory/lessons-learned.md`
   -- it does not exist yet in this milestone; note that plainly and continue rather than
   blocking on it.
2. Investigate the target area of the repository yourself (`Read`/`Grep`/`Glob`) enough
   to ground `objective`, `in_scope`, `out_of_scope`, `constraints`, `acceptance_criteria`,
   and a `task_graph` covering the phases this request actually needs. A trivial request
   may legitimately skip Research or Implementation -- say so explicitly in the task
   graph rather than defaulting to all four phases unexamined.
3. Author the scope candidate as a `scope.schema.json`-shaped object with
   `status: "approved"` (or `"refused"` with a `refusal_reason`, if the pre-dispatch
   checklist in `ASSIGNMENT.md` §2.1 fails -- e.g. the request targets a Protected Path
   or isn't a real product change).
4. `retain_attempt` (`phase: "discovery"`, `attempt_n: 1`, the candidate as JSON text)
   -- always, before validating.
5. `validate_scope` against the candidate. If invalid, the run ends here: still write a
   `run-summary.json` (`write_run_summary`, `state: "discovery_invalid"`) referencing the
   retained raw attempt, and report the failure honestly. Do not retry by silently
   patching the candidate more than once without re-grounding in the repository.
6. If valid, `promote_artifact` (`phase: "discovery"`) to produce the canonical
   `scope.json`. If `status` was `"refused"`, the run also ends here (a refusal is a
   legitimate, promoted outcome -- `state: "discovery_refused"` for the run summary),
   without dispatching the Architect.

# Phase 2: Research (real Architect dispatch)

1. Dispatch with the `Agent` tool: `subagent_type: "architect"`, `run_in_background:
   false` (you need the result before continuing), `prompt` containing exactly the
   Input fields `architect.md` documents (`task_id`, `run_id`, `created_at`,
   `scope_ref.path`).
2. Capture the agent identifier the tool call returns. `retain_policy_event` with
   `kind: "agent_dispatch"` and a payload of `{"phase": "research", "subagent_type":
   "architect", "agent_id": "<the returned id>", "dispatch_sequence": 1}` -- this is the
   evidence trail for "was the real Architect actually dispatched," independent of
   anything the Architect itself claims.
3. `retain_attempt` (`phase: "research"`, `attempt_n: 1`) with the Architect's raw final
   message, verbatim, before parsing it as JSON.
4. `validate_artifact` (`phase: "research"`). If valid, skip straight to step 5. If
   invalid, first classify *why*, because only one of the two failure kinds below is
   eligible for the transport repair described in "Architect transport repair" below:
   - **Transport/parse failure**: the raw text does not parse as strict JSON at all, or
     it parses but was wrapped in a Markdown code fence, or it has leading/trailing prose
     -- i.e. it violates `architect.md`'s own Output contract ("no fence, no prose, begins
     with `{` and ends with `}`") before content is even assessable. Attempt this
     classification yourself (parse the retained raw text as-is, unmodified -- never
     fence-strip it in order to make it parse); do not guess.
   - **Content failure**: the raw text parses as strict JSON but `validate_artifact`
     reports schema or semantic errors about the content itself (missing fields, invalid
     classifications, broken `supporting_finding_ids`, etc). This is not a transport
     problem and is not eligible for correction -- go straight to "the run ends" below.
5. If valid (either on the first attempt or after a successful transport repair below),
   `promote_artifact` (`phase: "research"`) to produce the canonical `findings.json`.
6. Absent a transport repair, the Architect returns one final message and is not resumed
   -- its own contract (`architect.md`) is single-shot, not staged. The transport repair
   below is the one narrow, explicitly-scoped exception to that single-shot contract.

## Architect transport repair (at most one correction attempt)

If step 4 above classified the failure as a **transport/parse failure**, exactly one
correction attempt is permitted before the phase is blocked. This repairs the wire
format only -- it never asks the Architect to redo research or change what it found.

1. Retain the malformed output exactly as received -- already done by step 3's
   `retain_attempt`. Never locally strip Markdown fences, trim prose, or otherwise
   rewrite the artifact yourself to make it parse; a locally repaired artifact is not
   evidence that the Architect itself can produce a conforming response, and silently
   fixing it would hide a real contract violation (see `PROJECT_SPEC.md`'s recorded
   dry-run finding on exactly this failure mode).
2. `retain_policy_event` (`kind: "transport_parse_failure"`) with the raw attempt
   reference and a description of exactly what was wrong (unparsable / fenced /
   leading-or-trailing prose).
3. Send a transport-only correction request to the **same Architect agent id** captured
   in this Research phase's `agent_dispatch` policy event (step 2 above) -- use
   `SendMessage` to that exact id, never a new `Agent` call. The message must tell the
   Architect explicitly, in these terms:
   - do not redo research
   - do not change findings
   - return the same artifact as raw JSON
   - no Markdown fence
   - no leading or trailing prose
4. Wait for the completion notification from that same agent id before proceeding --
   exactly as the staged continuation protocol requires for Engineer/Quality Engineer
   (see "Staged continuation protocol" below); do not act on a partial or absent result.
5. Retain the corrected response as the next Research attempt: `retain_attempt`
   (`phase: "research"`, `attempt_n: 2`), verbatim, before parsing it.
6. `validate_artifact` (`phase: "research"`) on this second attempt, exactly as step 4
   validated the first. This is normal validation -- no relaxed rules for a corrected
   response.
7. This is the **one and only** correction attempt permitted for this Research phase,
   regardless of outcome:
   - If attempt 2 is valid, proceed to `promote_artifact` (step 5 above) as normal.
   - If attempt 2 is still malformed (another transport failure) or is now invalid for
     content reasons, do **not** send a second correction request. The run ends
     (`state: "research_blocked"`); `retain_policy_event`
     (`kind: "transport_repair_exhausted"`) noting that the single permitted correction
     attempt was used and did not resolve the failure.
8. Never spawn a replacement Architect (a new `Agent` call) for this phase at any point
   in this sequence and present it as a continuation of the original -- that fabricates
   continuity exactly the way the cardinal "never trust/fabricate a completion claim"
   rule (`ASSIGNMENT.md`, and the "Staged continuation protocol" section below) forbids.
   If the same agent id cannot be resumed (the tool errors, or the reply cannot be
   matched to the id), treat that identically to a broken staged continuation: block the
   phase, `retain_policy_event` (`kind: "continuity_broken"`), do not retry with a fresh
   agent.

# Dry-run boundary

If `dry_run` is true, **stop after Phase 2** plus one test-runner Skill smoke check
(below). Do not dispatch the Engineer. Do not touch any file under `demo-repo/` other
than reading it. Say so explicitly in the final report, and write a `run-summary.json`
with `final_verdict: "blocked"` (there is no schema value for "intentionally paused" --
`state.py`'s vocabulary only models pipeline problems, not an intentional dry-run stop;
this is a known, accepted representational gap, not a claim that anything failed) whose
`objective_summary`/`follow_up_actions` say, unambiguously, that Implementation and
Verification were never attempted by design, not skipped due to failure, and that this
is not a completed four-phase run.

## Dry-run test-runner Skill smoke check

1. Build a test-runner request body: `task_id`, `run_id`, a fresh `command_id` (e.g.
   `C-1`), `command: "python -m pytest tests/unit/test_explanations.py -q"`,
   `working_directory: "demo-repo"`, `target_repo_path: "demo-repo"`, `purpose`
   describing this as a dry-run smoke check against an already-passing file (read-only
   in effect -- no source changes precede it).
2. Write that body with `Write` to `runs/<run_id>/requests/<command_id>.json` --
   required by `test-runner/SKILL.md`: the Skill cannot create its own request file.
3. Invoke the **`Skill` tool** with `skill: "test-runner"` and `args:
   "runs/<run_id>/requests/<command_id>.json"`. Do not call
   `.claude/skills/test-runner/scripts/run_command.py` via `Bash` directly -- that would
   prove the wrapper works, not that the Skill mechanism was actually used. The skill
   runs forked and synchronous (`context: fork`, `background: false` --
   see "Test-runner Skill mediation: forked isolation" below); wait for its real result
   in this same turn before proceeding, exactly as you would have before the fork.
   `retain_policy_event` with `kind: "skill_invocation"` and a payload naming the skill,
   the request path, and that it was invoked via the `Skill` tool -- this is what
   distinguishes "the Skill was really invoked" from "a wrapper log merely exists,"
   which is not sufficient evidence on its own (a log could exist from a direct `Bash`
   call, and must not be conflated with real Skill mediation).
4. `check_command_identity` between the request you built and the `command_result`
   the Skill call returned. A mismatch blocks the phase.
5. Confirm `exit_code == 0` and that it is a real `command_result`, not
   `command_rejected` (`retain_rejection` if it was).
6. Independently confirm no file under `demo-repo/` changed: run `git status --short --
   demo-repo` yourself and expect empty output -- this is true by construction for a
   read-only test run, but verify it rather than assuming it.

# Phase 3: Implementation (real Engineer dispatch) -- not exercised in a dry run

1. `build_path_attestation` for `target_repo_path` (e.g. `"demo-repo"`) before dispatch.
2. Dispatch with `Agent`: `subagent_type: "engineer"`, `run_in_background: false`,
   `prompt` containing the exact Input fields `engineer.md` documents, including the
   `path_validation` attestation and `findings_ref.finding_ids` restricted to `found`
   classifications from the promoted `findings.json`.
3. Capture the agent identifier. `retain_policy_event` (`kind: "agent_dispatch"`,
   `phase: "implementation"`, `subagent_type: "engineer"`, the id, dispatch sequence).
4. Enter the staged continuation protocol (below) for every subsequent turn:
   `pre_test_requested` -> mediate via the real `test-runner` Skill -> reply -> ... ->
   `post_test_requested` -> mediate -> reply -> `finalization_evidence_requested` ->
   supply real diff stats -> reply -> final `ready_for_verification` or `blocked` report.
5. **Mandatory TDD orchestration check, before accepting `pre_test_requested` as
   legitimate and before mediating its command through the test-runner Skill:**
   independently run `git status --short -- demo-repo` and `git diff --stat -- demo-repo`
   yourself. Confirm:
   - Only in-scope test files changed (compare against `scope.json`'s `in_scope`).
   - No production source file changed yet (a failing test must precede any
     implementation edit -- if source already changed, the Engineer skipped TDD; treat
     this as a blocked phase, not something to wave through).
   - No out-of-scope file changed.
   - No Protected Path changed (cross-check against `paths.py`'s list yourself; do not
     trust the Engineer's own Step 1e self-check alone).
6. After the pre-test command executes (via the Skill, per the "test mediation"
   section below) and returns a nonzero exit code, confirm the failure is the *expected
   absent-behavior* failure, not one of: a syntax error, an import error, an environment
   error, or an unrelated regression. Read the retained log
   (`runs/<run_id>/logs/<command_id>.log`) yourself before accepting it. Only a
   confirmed, correctly-reasoned failure clears the Engineer to proceed to implementation
   -- an accidental pass, or a failure for the wrong reason, must be sent back
   (`rejected_reply`-equivalent reasoning) rather than accepted.
7. Only after step 6 passes may you resume the same Engineer with the real
   `command_result` and allow it to proceed toward `post_test_requested`.
8. `retain_attempt` every raw turn (`phase: "implementation"`, incrementing `attempt_n`)
   before parsing it, exactly as for Research. If a raw turn does not parse as strict
   JSON, or parses but is wrapped in a Markdown fence or carries leading/trailing prose,
   classify and handle it via "Engineer transport repair" below before concluding the
   turn is simply invalid content.
9. On the final artifact: `validate_artifact` then `promote_artifact`
   (`phase: "implementation"`, `context_refs.findings` pointing at the promoted
   `findings.json`). Independently re-check `changed_files` against the Protected Path
   list yourself (`build_path_attestation`/`live_cli` does not re-derive this for you;
   it is the same list `paths.py` enforces -- inspect the promoted report's
   `changed_files` directly).

## Engineer transport repair (Implementation phase, at most one correction total)

Exactly one transport-only correction attempt is permitted per Implementation phase,
shared across all of that phase's staged turns (`pre_test_requested`,
`post_test_requested`, `finalization_evidence_requested`, and the final report) -- not
one per turn. This mirrors "Architect transport repair" above but is scoped to the
Engineer's multi-turn staged protocol instead of a single-shot report. It repairs the
wire format only: it never asks the Engineer to redo work, change `changed_files`,
`tests`, `minimal_change_rung`, or any `requested_command` content, or make any further
`Edit`/`Write` call.

1. Classify the failure exactly as in Research: a **transport/parse failure** is raw
   text that does not parse as strict JSON at all, or parses but was wrapped in a
   Markdown code fence, or carries leading/trailing prose -- a violation of
   `engineer.md`'s own "Exactly one raw JSON object per turn, no fence, no prose"
   contract, assessable before the turn's content is even read. A **content failure**
   -- the JSON parses cleanly but is the wrong `response_type`, fails schema validation,
   or fails a semantic check `engineer.md` defines (a stale `command_id`, a
   `task_id`/`run_id` mismatch, an unrecognized reference) -- is not a transport problem
   and is not eligible for this repair; that is handled through `engineer.md`'s own
   `rejected_reply` re-request mechanism, which the Engineer itself drives (capped at 3
   consecutive rejections of the same outstanding request), or, if that is exhausted or
   inapplicable, the phase blocks.
2. Retain the malformed output exactly as received -- already done by step 8's
   `retain_attempt`. Never locally strip Markdown fences, trim prose, or otherwise
   rewrite the response yourself to make it parse; a locally repaired response is not
   evidence that the Engineer itself can produce a conforming one.
3. `retain_policy_event` (`kind: "transport_parse_failure"`, `phase: "implementation"`)
   with the raw attempt reference and a description of exactly what was wrong
   (unparsable / fenced / leading-or-trailing prose).
4. Only if this phase's one-correction budget has not already been spent: send a
   transport-only correction request to the **same Engineer agent id** captured at this
   phase's `agent_dispatch` policy event -- use `SendMessage` to that exact id, never a
   new `Agent` call. The message must tell the Engineer explicitly, in these terms:
   - do not make any further `Edit`/`Write` call
   - do not change `changed_files`, `tests`, `minimal_change_rung`, or any other content
   - return the exact same envelope content as raw JSON
   - no Markdown fence
   - no leading or trailing prose
   If the budget has already been spent this phase, skip straight to blocking (step 7
   below) without sending a second correction request.
5. Wait for the completion notification from that same agent id before proceeding --
   exactly as the staged continuation protocol requires (see "Staged continuation
   protocol" below); do not act on a partial or absent result.
6. Retain the corrected response as the next Implementation attempt: `retain_attempt`
   (`phase: "implementation"`, next `attempt_n`), verbatim, before parsing it. Then
   validate it exactly as any other turn -- no relaxed rules for a corrected response. If
   it is now a valid envelope or final artifact, resume the staged protocol normally at
   the point this turn left off.
7. This is the **one and only** correction attempt permitted for this Implementation
   phase, regardless of which turn triggered it. If the corrected response is itself
   still malformed (another transport failure) or is now invalid for content reasons,
   do **not** send a second correction request under any circumstance: the phase ends
   (`state: "implementation_blocked"`); `retain_policy_event`
   (`kind: "transport_repair_exhausted"`) noting that the single permitted correction was
   used and did not resolve the failure.
8. Never spawn a replacement Engineer (a new `Agent` call) for this phase at any point in
   this sequence and present it as a continuation of the original -- that fabricates
   continuity exactly the way the cardinal "never trust/fabricate a completion claim"
   rule forbids. If the same agent id cannot be resumed (the tool errors, or the reply
   cannot be matched to the id), treat that identically to a broken staged continuation:
   block the phase, `retain_policy_event` (`kind: "continuity_broken"`), do not retry
   with a fresh agent.

# Phase 4: Verification (real Quality Engineer dispatch) -- not exercised in a dry run

Broadly the same shape as Phase 3: dispatch, capture and retain the agent id as a policy
event, retain every raw turn before parsing, mediate every `attempt_requested` through
the real `test-runner` Skill, resume the *same* Quality Engineer for every reply
including any evidence-supported `infrastructure_flake` retry (max 2, per
`quality-engineer.md`), `validate_artifact`/`promote_artifact` (`phase: "verification"`,
`context_refs.scope` pointing at the promoted `scope.json`) on the final report. If a raw
turn does not parse as strict JSON, or parses but is wrapped in a Markdown fence or
carries leading/trailing prose, classify and handle it via "Quality Engineer transport
repair" below -- do **not** treat it as merely "the same protocol as the Engineer's" by
informal analogy; the rules below are this phase's own explicit, authoritative protocol.

After a `pass`/`fail`/`inconclusive` verdict, independently re-verify before repeating it
to the user: re-run the same narrow command yourself through the Skill (`ORCH-1`) and,
per scope.json's task graph, run the full demo-repo suite as a health check (`ORCH-2`).
For **each** ORCH-* command: build the canonical request exactly as for any Skill
invocation, retain a `skill_invocation` policy event, and -- per Standing rule 13 --
call `check_command_identity` between the request you built and the result the Skill
returned, exactly as you already do for `C-1`/`C-2`/`V-1`. A mismatch on an ORCH-*
command blocks completion honestly rather than being silently accepted. Also run
`git status`/`git diff --stat -- demo-repo` to confirm `changed_files` matches reality.
Never report "verification passed" solely because the Quality Engineer said so.

## Same-run logic-failure route-back to the Engineer

A `fail` verdict is not automatically terminal. Classify it first, then act:

1. **Classify the verdict before doing anything else.** Read `final_verdict` and
   `attempts[]` from the promoted `verification-report.json`:
   - `final_verdict: "fail"` with `routed_back_to_engineer.routed: true` and at least one
     `attempts[]` entry classified `logic_bug` -- per `harness/evidence.py`'s own
     semantic rule, a schema-valid `fail` verdict can only exist in this shape -- is a
     **genuine logic bug**. This is the only case eligible for route-back.
   - `final_verdict: "inconclusive"` backed by an unresolved `infrastructure_flake`
     attempt is an **infrastructure flake**. `quality-engineer.md`'s own bounded retry
     (max 2, evidence-supported, within the *same* Quality Engineer turn) already
     covers this -- it is resolved or exhausted before a final verdict is ever reported
     to you. Never re-route an infrastructure flake to the Engineer; if it is still
     unresolved by the time you see a verdict, treat it exactly like the `"inconclusive"`
     handling already documented above (terminal, no route-back).
   - `final_verdict: "inconclusive"` backed by an `environment` attempt is an
     **environment failure**. Never route this to the Engineer and never retry it --
     retrying does not fix a broken environment. Terminal, exactly as documented above.
   - Anything else -- the Verification phase never produced a validly promoted
     `verification-report.json` at all (a transport failure exhausted its one
     correction, a schema/semantic validation failure, a broken continuity, a
     `command_rejected` escalation) -- is **malformed or insufficient evidence**. There
     is no real verdict to act on. Never treat this as a logic bug and never route it
     back; it already blocks the phase via the existing rules above
     (`state: "verification_blocked"`).
   - `retain_policy_event` (`kind: "logic_failure_detected"`) once you have confirmed the
     genuine-logic-bug case, naming the `verification-report.json` path and the failing
     `attempts[]`/`acceptance_criteria_results[]` ids. Do this only for the genuine
     logic-bug case -- it is not a generic "verification failed" event.
2. **Route back only a genuine logic bug, at most once.** `MAX_LOGIC_REPAIR_ATTEMPTS = 1`
   for this milestone's demonstration -- exactly one logic-repair cycle is permitted per
   run. If a logic bug is detected and no repair attempt has been used yet:
   - `retain_policy_event` (`kind: "engineer_route_back"`) naming the repair attempt
     number (`1`), the same Engineer agent id captured at this run's `agent_dispatch`
     policy event for the Implementation phase, and the `verification-report.json`
     reference.
   - `SendMessage` to that **exact same Engineer agent id** -- never a new `Agent` call --
     carrying the real Quality Engineer failure evidence: the failing `attempts[]`
     entries (command, exit code, output reference, classification) and the failed
     `acceptance_criteria_results[]` ids from the promoted `verification-report.json`.
     Never summarize, soften, or partially redact this evidence -- the Engineer must see
     exactly what the Quality Engineer actually observed.
   - Wait for that same agent's reply before proceeding, exactly as the staged
     continuation protocol requires. If the same agent id cannot be resumed, this is a
     **broken agent continuity** failure: `retain_policy_event` (`kind:
     "continuity_broken"`) and block the run (`state: "implementation_blocked"`) --
     never dispatch a replacement Engineer and call it a continuation.
   - The resumed Engineer is expected to write or update one failing regression test
     first, per its own contract (see "Engineer route-back repair protocol" in
     `engineer.md`), before making any correction. Apply the same "Mandatory TDD
     orchestration check" from Phase 3 to this repair's pre-test command: independently
     confirm via `git status`/`git diff --stat -- demo-repo` that only test files changed
     before the repair's pre-test command runs, and that the failure is the expected
     one, not an accident.
   - Mediate every repair-cycle command (`C-3`, `C-4`, ... -- fresh command ids, never
     reused) through the real, forked `test-runner` Skill exactly as in Phase 3, with the
     same request/result identity checking.
   - On the Engineer's new final report: `retain_attempt`, `validate_artifact`, then
     `promote_artifact` (`phase: "implementation"`) to a **new** canonical path this
     repair round owns exclusively (e.g. `implementation-report.repair-1.json` --
     `promote_artifact`'s collision guard refuses to silently overwrite the original
     `implementation-report.json`, which remains retained, untouched, as the pre-repair
     evidence). Update your own working notion of `artifact_refs.implementation_report`
     to this new path for the rest of this run, but never delete or rewrite the original.
     Independently re-check the repair's `changed_files` against the Protected Path list
     yourself, exactly as in Phase 3.
3. **Re-verify with the exact same Quality Engineer.** Once the repair's implementation
   report is promoted:
   - `retain_policy_event` (`kind: "re_verification"`) naming the repair attempt number
     and the same Quality Engineer agent id captured at this run's `agent_dispatch`
     policy event for the Verification phase.
   - `SendMessage` to that **exact same Quality Engineer agent id** -- never a new
     `Agent` call -- with the updated `implementation_ref.path` (the repair round's new
     canonical implementation report). Ask it to re-run its own Steps 3-12 against this
     updated implementation report, in this same continued conversation.
   - Wait for that same agent's reply. If the same agent id cannot be resumed, this is
     again a **broken agent continuity** failure: `retain_policy_event` (`kind:
     "continuity_broken"`) and block the run (`state: "verification_blocked"`) -- never
     dispatch a replacement Quality Engineer.
   - Mediate every re-verification command (`V-2`, ... -- a fresh command id) through the
     real, forked `test-runner` Skill, exactly as in Phase 4 above.
   - On the final re-verification report: `retain_attempt`, `validate_artifact`, then
     `promote_artifact` (`phase: "verification"`) to a new canonical path this repair
     round owns exclusively (e.g. `verification-report.repair-1.json`), leaving the
     original `verification-report.json` retained, untouched, as the pre-repair evidence.
     Update `artifact_refs.verification_report` to this new path.
4. **Reach a final, honest terminal outcome.** Classify the repair round's verdict exactly
   as step 1:
   - `"pass"` -- every criterion now passes: proceed to `state: "completed"`, and say
     plainly in your final report that this run required one logic-bug repair cycle,
     citing both the original and the repaired `verification-report.json` paths.
   - `"fail"` again with a genuine logic bug -- the repair budget (`1`) is now exhausted.
     `retain_policy_event` (`kind: "route_back_exhaustion"`) naming the exhausted budget
     and both verification-report paths, and end the run honestly
     (`state: "verification_failed"`). Do not attempt a second repair cycle, and do not
     soften this into anything but a real failure.
   - Infrastructure flake / environment / malformed-or-insufficient-evidence on the
     repair round -- terminal exactly as step 1 describes for the original round; the
     one-repair budget was already spent on a genuine logic bug and is not reset by a
     different kind of failure appearing afterward.
5. Independently re-verify the repair's outcome exactly as the top of this section
   requires (`ORCH-1`/`ORCH-2`, `check_command_identity`, `git status`/`git diff`) before
   reporting anything to the user -- a repaired run earns no less scrutiny than a
   first-pass one.

## Quality Engineer transport repair (Verification phase, at most one correction total)

Exactly one transport-only correction attempt is permitted per Verification phase,
shared across all of that phase's staged turns (`attempt_requested` and the final
report) -- not one per turn. This mirrors "Engineer transport repair" above but is
scoped to the Quality Engineer's staged protocol instead of the Engineer's. It repairs
the wire format only: it never asks the Quality Engineer to redo verification, request a
different command, change any `requested_command`/`command`/`working_directory`, change
any acceptance-criteria classification, `final_verdict`, or any other substantive
content, or make any further tool call (the Quality Engineer holds no `Edit`/`Write`/
`Bash` tool to begin with, so there is nothing further for it to do beyond replying).

1. Classify the failure exactly as in Research/Implementation: a **transport/parse
   failure** is raw text that does not parse as strict JSON at all, or parses but was
   wrapped in a Markdown code fence, or carries leading/trailing prose -- a violation of
   `quality-engineer.md`'s own "Exactly one raw JSON object per turn, no fence, no prose"
   contract, assessable before the turn's content is even read. A **content failure** --
   the JSON parses cleanly but is the wrong `response_type`, fails schema validation, or
   fails a semantic check `quality-engineer.md` defines (a stale `command_id`, a
   `task_id`/`run_id` mismatch, an unrecognized reference, a findings_ref/changed_files
   mismatch) -- is not a transport problem and is not eligible for this repair; that is
   handled through `quality-engineer.md`'s own `rejected_reply` re-request mechanism,
   which the Quality Engineer itself drives, or, if that is exhausted or inapplicable,
   the phase blocks.
2. Retain the malformed output exactly as received -- via `retain_attempt`, before any
   parsing. Never locally strip Markdown fences, trim prose, or otherwise rewrite the
   response yourself to make it parse; a locally repaired response is not evidence that
   the Quality Engineer itself can produce a conforming one.
3. `retain_policy_event` (`kind: "transport_parse_failure"`, `phase: "verification"`)
   with the raw attempt reference and a description of exactly what was wrong
   (unparsable / fenced / leading-or-trailing prose).
4. Only if this phase's one-correction budget has not already been spent: send a
   transport-only correction request to the **same Quality Engineer agent id** captured
   at this phase's `agent_dispatch` policy event -- use `SendMessage` to that exact id,
   never a new `Agent` call. The message must tell the Quality Engineer explicitly, in
   these terms:
   - do not request a different command, criterion, or classification
   - do not change `final_verdict`, `acceptance_criteria_results`, `attempts`, or any
     other content
   - return the exact same envelope content as raw JSON
   - no Markdown fence
   - no leading or trailing prose
   If the budget has already been spent this phase, skip straight to blocking (step 7
   below) without sending a second correction request.
5. Wait for the completion notification from that same agent id before proceeding --
   exactly as the staged continuation protocol requires (see "Staged continuation
   protocol" below); do not act on a partial or absent result.
6. Retain the corrected response as the next Verification attempt: `retain_attempt`
   (`phase: "verification"`, next `attempt_n`), verbatim, before parsing it. Then
   validate it exactly as any other turn -- no relaxed rules for a corrected response. If
   it is now a valid envelope or final artifact, resume the staged protocol normally at
   the point this turn left off.
7. This is the **one and only** correction attempt permitted for this Verification
   phase, regardless of which turn triggered it. If the corrected response is itself
   still malformed (another transport failure) or is now invalid for content reasons, do
   **not** send a second correction request under any circumstance: the phase ends
   (`state: "verification_blocked"`); `retain_policy_event`
   (`kind: "transport_repair_exhausted"`) noting that the single permitted correction was
   used and did not resolve the failure.
8. Never spawn a replacement Quality Engineer (a new `Agent` call) for this phase at any
   point in this sequence and present it as a continuation of the original -- that
   fabricates continuity exactly the way the cardinal "never trust/fabricate a completion
   claim" rule forbids. If the same agent id cannot be resumed (the tool errors, or the
   reply cannot be matched to the id), treat that identically to a broken staged
   continuation: block the phase, `retain_policy_event` (`kind: "continuity_broken"`), do
   not retry with a fresh agent.

# Staged continuation protocol (documented now, not live-exercised until Phase 3/4 run)

For every staged agent (Engineer, Quality Engineer):

```
Agent tool call (subagent_type: "engineer" | "quality_engineer", run_in_background: false)
  -> retain the returned agent identifier immediately (policy event, kind: "agent_dispatch")
  -> retain_attempt the raw response, then parse it
  -> if it is a protocol envelope (response_type present): mediate per its kind
       (a command envelope -> test-runner Skill invocation; finalization_evidence_requested
       -> real diff stats) and build the exact reply message the agent's own contract defines
  -> SendMessage to that exact same agent identifier -- never a new Agent call for this phase
  -> wait for that same agent's reply (do not assume SendMessage returns the resumed
     agent's completed response synchronously -- confirm you actually have a full,
     parseable reply before proceeding; if the tool surfaces the reply asynchronously,
     wait for it rather than acting on a partial or absent result)
  -> retain_attempt the next raw response, then parse it
  -> repeat until a final artifact (no response_type key) is returned, or MAX_STAGED_TURNS
     (20, matching harness/orchestrator/core.py's own ceiling) is reached
```

If continuation cannot be observed or clearly associated with the original agent
identifier -- the tool errors, the reply cannot be matched to the id you retained, or
the agent's own reply indicates it doesn't recognize the state you sent it (an
unrecognized `command_id`, changed files it never reported, a `task_id`/`run_id`
mismatch, per `engineer.md`/`quality-engineer.md`'s own "detect a broken handoff"
language) -- **block the phase**: `retain_policy_event` (`kind:
"continuity_broken"`) describing exactly what could not be confirmed, and report
`state: "implementation_blocked"` / `"verification_blocked"` accordingly. Never
dispatch a fresh `Agent` call for the same phase and present it as a continuation --
that would fabricate continuity precisely the way `ASSIGNMENT.md`'s cardinal rule (never
trust a completion claim) forbids.

This protocol is fully specified here but is **not live-verified** until a real
Implementation/Verification phase actually runs one -- the dry run in this milestone
exercises Research (single-shot, no staging) and one standalone test-runner Skill
invocation, not this staged loop.

# Test-runner Skill mediation: forked isolation (not inline turn-wide denial)

`test-runner/SKILL.md` declares `context: fork` and `background: false`. Every
invocation of the `test-runner` Skill therefore runs in its own isolated forked
subagent context, not inline in the caller's own turn. `background: false` (requires
Claude Code v2.1.218+; confirmed present in the installed version at the time this
section was written) keeps the call synchronous -- you still wait for the real result
in the same turn before continuing, exactly as before. What changed is isolation, not
waiting semantics: the forked subagent's own `disallowed-tools` restriction (`Write`,
`Edit`, ...) is scoped to that fork's own context. It must not be relied upon to affect,
and must not be treated as if it affects, the main-session orchestrator's own tools or
any subagent (Engineer, Quality Engineer) resumed after the fork completes.

**This is not the normal path for live Implementation:** relying on an inline,
turn-wide `Write`/`Edit` denial after a `test-runner` invocation -- and routing around
it with `Bash` heredocs for evidence operations, or worse, blocking Implementation
entirely because a resumed Engineer lost its own `Edit`/`Write` -- is exactly the
failure this fork repairs, not a state to plan around. Do not reintroduce workarounds
from before the fork (e.g. "avoid `Write`/`Edit` for the rest of the turn") as if they
were still required; they applied only to the old inline invocation.

## Why this exists: `run-20260804-riskband-002` (retained evidence, not a template)

`runs/run-20260804-riskband-002/` is a real, live-executed `/work` attempt, retained
unmodified as evidence, that motivated this fork. It independently live-verified
several things that remain true and are not being redone here: the Engineer requesting
the accepted `python -m pytest ...` grammar, the Engineer transport-repair protocol
resuming the same Engineer id successfully after a Markdown-fenced reply, a genuine
failing regression test produced by the Engineer *before* any production change, and a
real test-runner Skill invocation returning the expected failure with request/result
identity matching. It then blocked: invoking the (at the time, inline) `test-runner`
Skill removed `Write`/`Edit` from the rest of that user turn, and that removal was
observed to propagate into the *resumed Engineer's own tool availability* -- not just
the main session's -- so the same Engineer could reach its pre-test failing state but
could not then make the implementation edit. See
`runs/run-20260804-riskband-002/run-summary.json` and
`runs/run-20260804-riskband-002/logs/policy-events.jsonl`'s
`cross_agent_tool_restriction_observed` event for the full evidence. That run is not to
be re-derived, edited, or deleted -- it is the documented reason the fork exists, and
the fork itself is **not yet live-verified** by an actual `/work` run (see
`PROJECT_SPEC.md`).

## What the orchestrator must actually do, every invocation

1. Write the request file with `Write` as before (unaffected -- the fork isolates the
   *skill's own* tool pool, not the caller's).
2. Invoke the **`Skill` tool** with `skill: "test-runner"` as before. Because the skill
   is forked and `background: false`, wait for its real result in this same turn --
   never proceed as if a result arrived when none has, and never poll or guess at a
   pending fork's outcome.
3. If the fork genuinely returns a `command_result` or `command_rejected`: handle it
   exactly as documented above (`check_command_identity`, sentinel exit codes, escalation
   on rejection) -- nothing about validation, identity checking, or escalation changes
   because the invocation is forked.
4. **If the fork fails outright** -- the `Skill` tool call errors, times out, or the
   subagent otherwise never returns a result -- or **returns something that is not a
   well-formed `command_result`/`command_rejected`** (malformed JSON, missing required
   fields, a response that doesn't match the request you sent): treat this identically
   to a broken staged continuation. `retain_policy_event` (`kind:
   "test_runner_fork_failure"`) describing exactly what was missing or malformed, block
   the phase, and report it honestly. Never fabricate a `command_result`, never assume a
   pass or a fail, and never fall back to a direct `Bash` call to
   `.claude/skills/test-runner/scripts/run_command.py` to "get an answer anyway" -- that
   would prove the wrapper works, not that the Skill mechanism mediated the command,
   which "Standing rules" item 8 already forbids regardless of the pressure to produce a
   result.
5. `Bash` remains available to the orchestrator throughout -- for writing `live_cli.py`
   request files and invoking `live_cli.py` itself -- but must **never** be used to edit
   application source under `demo-repo/`. Only the **Engineer** subagent (via its own
   `Edit`/`Write` tool grant, per `engineer.md`) may modify approved files under
   `demo-repo/`, and only within its `path_validation` attestation and Protected Path
   constraints. This boundary is unconditional, independent of the fork.
6. After a valid pre-test `command_result` confirming the expected TDD failure (per the
   "Mandatory TDD orchestration check" above), resume the **same Engineer agent id**
   captured at this phase's `agent_dispatch` policy event with that real result. Because
   the test-runner Skill's tool restriction is now confined to its own fork, the resumed
   Engineer's `Edit`/`Write` grant (per `engineer.md`) is expected to remain intact for
   its Step 9 implementation edit -- this expectation is the fork's whole purpose, and it
   still requires a real `/work` run to confirm live before it can be reported as proven.

# Reporting

## Completion-guardrail marker (required before reporting any run as complete)

Before reporting a run as complete to the user -- `state: "completed"` only, never a
blocked/refused/inconclusive outcome, which are reported directly with no marker -- write
`runs/<run_id>/.completion_claim.json` (via `Write`), containing exactly
`{"task_id": "<task_id>", "run_id": "<run_id>"}`. This is what
`.claude/hooks/completion_guardrail.py` (a `Stop` hook) uses to know which run to
independently re-validate before this turn is allowed to end -- it does nothing for any
turn that never writes this marker, so it never interferes with unrelated conversation.
If the hook finds a problem (missing/invalid/deleted verification evidence, a non-`pass`
verdict, a canonical-artifact task/run mismatch, or a real Git-reality conflict with the
implementation report's `changed_files`), it blocks this turn from ending and reports the
exact reason -- treat that exactly as seriously as any other independent check failing:
the run is not actually complete, regardless of what was about to be reported. Do not
delete or hand-edit this marker yourself to work around a block; only a genuinely passing
re-check clears it (the hook removes it itself once satisfied).

## What to state, every time

State plainly, every time:
- Which phases actually ran.
- Which canonical artifacts exist and validated, with their paths.
- What independent checks you personally ran and what they showed (not what an agent
  claimed).
- Whether this run required a same-run logic-bug repair cycle (Part 2), and if so, both
  the original and repaired `implementation-report`/`verification-report` paths.
- For a dry run: that Implementation and Verification were intentionally not attempted,
  and that this is not a completed pipeline run.
- Any evidence gap. If required evidence is missing, say the run cannot be called
  successful -- do not soften this into "mostly done."
