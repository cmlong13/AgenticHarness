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
   something to silently patch around by resubmitting a corrected string.
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
   prove the wrapper works, not that the Skill mechanism was actually used.
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

Same shape as Phase 3: dispatch, capture and retain the agent id as a policy event,
retain every raw turn before parsing, mediate every `attempt_requested` through the real
`test-runner` Skill, resume the *same* Quality Engineer for every reply including any
evidence-supported `infrastructure_flake` retry (max 2, per `quality-engineer.md`),
`validate_artifact`/`promote_artifact` (`phase: "verification"`,
`context_refs.scope` pointing at the promoted `scope.json`) on the final report.

After a `pass`/`fail`/`inconclusive` verdict, independently re-verify before repeating
it to the user: re-run the same narrow command yourself through the Skill, and run
`git status`/`git diff --stat -- demo-repo` to confirm `changed_files` matches reality.
Never report "verification passed" solely because the Quality Engineer said so.

This milestone does not implement automatic route-back to the Engineer on a `logic_bug`
verdict (deferred per `PROJECT_SPEC.md` §4 "Later integrations" / this ticket's Scope
restrictions) -- a `fail` verdict ends the run and is reported honestly as such.

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

# Test-runner Skill restriction lifecycle

Once the real `test-runner` Skill has been invoked (the `Skill` tool call itself, in the
dry-run smoke check or in any Phase 3/4 staged test mediation), its `disallowed-tools`
restrictions (`Write`, `Edit`, ...) remain enforced at the tool layer for the remainder
of the **current user turn** -- not just for the single invoking call. This was observed
live during this milestone's dry run (see `PROJECT_SPEC.md`'s dry-run evidence) and is
**expected turn-lifecycle behavior, not an unexplained defect** -- do not treat a denied
`Write`/`Edit` after a `test-runner` invocation as a bug to route around by retrying; it
is the expected state for the rest of the turn.

Concretely, for the remainder of the turn after the first `test-runner` Skill invocation:

1. The main-session orchestrator must not depend on `Write` or `Edit` for any further
   evidence operation. Every subsequent `live_cli.py` request-file write, retention, or
   promotion must go through `Bash` (e.g. a heredoc or redirect writing the request file,
   then invoking `python -m harness.orchestrator.live_cli --request-file ...`), not a
   direct `Write`/`Edit` call that may now be denied.
2. `Bash` may be used to write `live_cli.py` request files and invoke `live_cli.py`
   itself, but must **never** be used to edit application source under `demo-repo/`.
   That boundary is independent of this restriction and does not relax because `Write`/
   `Edit` are unavailable -- only the **Engineer** subagent (via its own `Edit`/`Write`
   tool grant, per `engineer.md`) may modify approved files under `demo-repo/`, and only
   within its `path_validation` attestation and Protected Path constraints.
3. Test execution must still always go through the real `test-runner` Skill (the `Skill`
   tool) -- never fall back to invoking
   `.claude/skills/test-runner/scripts/run_command.py` directly via `Bash` merely because
   `Write`/`Edit` are restricted. A direct wrapper call proves the wrapper works, not
   that the Skill mechanism was used, which is exactly what "Standing rules" item 8
   already forbids regardless of tool-availability pressure.
4. This restriction is scoped to "the remainder of the current user turn." A genuinely
   new user turn is not bound by a prior turn's `test-runner` invocation.

# Reporting

State plainly, every time:
- Which phases actually ran.
- Which canonical artifacts exist and validated, with their paths.
- What independent checks you personally ran and what they showed (not what an agent
  claimed).
- For a dry run: that Implementation and Verification were intentionally not attempted,
  and that this is not a completed pipeline run.
- Any evidence gap. If required evidence is missing, say the run cannot be called
  successful -- do not soften this into "mostly done."
