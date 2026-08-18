# Project Spec: Agentic Coding Harness

Status: **Implementation in progress — artifact contracts, validation, all three phase
subagents (Architect, Engineer, Quality Engineer, each with independently verified permission
boundaries), the code-craftsmanship and test-runner skills (each live-verified through
Claude Code's actual `Skill` mechanism, not just statically), and the deterministic orchestrator
core (`harness/orchestrator/` — a one-pass Discovery → Research → Implementation → Verification
state machine, automatically tested end-to-end against scripted fake adapters only) are
complete. This satisfies the Core MVP's skill requirement (§4), but not the assignment's
broader "≥4 skill packs" requirement (§3) — the GitHub and Jira skill packs are not yet built.
Three real, live `/work` attempts have now run against `demo-repo/`
(`run-20260803-riskband-001`, `run-20260804-riskband-002`, `run-20260804-riskband-003` — see
§10), all retained as evidence. The first two ended `final_verdict: "blocked"`, not completed;
each surfaced and led to a repair of a real defect (fenced Architect transport, then an inline
test-runner Skill invocation stripping the resumed Engineer's own `Edit`/`Write`). **The third,
`run-20260804-riskband-003`, is the first successful literal `/work` execution: a real, live
four-phase Discovery → Research → Implementation → Verification pipeline that completed
end-to-end against `demo-repo/`, ending `final_verdict: "pass"`.** It produced the first
canonical `implementation-report.json` with `ready_for_verification` and the first canonical
passing `verification-report.json`, live-verified the forked test-runner isolation repair (the
resumed Engineer's `Edit`/`Write` remained available), and independently confirmed 102 passing
demo tests, a minimal three-file diff, and a genuine failing test committed before the
production change. See "Live evidence (`run-20260804-riskband-003`) — completed" in §10 for the
full account. The harness's own hooks/guardrails, same-run Quality-Engineer logic-failure
route-back, checkpoint/resume, memory loop, token/cost accounting, GitHub/Jira skills and
connectors, Obsidian integration, and false-push/flaky-test demonstrations remain open — this
milestone closes the core loop once, it does not close the project. The `demo-repo/` target
application (`loanflow`, 55 files) is implemented, automatically tested, and verified compatible
with the test-runner wrapper and the orchestrator's path rules. The Skills requirement and the
project as a whole are still not complete.**

**Milestone update (2026-08-05):** on top of `run-20260804-riskband-003`, this session closes a
second milestone: (1) **ORCH-* request/result identity enforcement** — every orchestrator-owned
test-runner command (`ORCH-1`, `ORCH-2`, ...), not only staged-agent commands, must now pass
`check_command_identity` before its result is trusted (`work/SKILL.md` standing rule 13); (2) a
**bounded same-run logic-failure route-back loop** in `harness/orchestrator/core.py`
(`classify_verification_outcome`, `_handle_verification_failure`) that resumes the *exact same*
Engineer and Quality Engineer handles for at most one repair cycle
(`MAX_LOGIC_REPAIR_ATTEMPTS = 1`) when — and only when — a `verification-report.json` verdict is
a genuine `logic_bug`; infrastructure flakes, environment failures, and malformed/insufficient
evidence remain terminal, never routed back; (3) matching route-back/re-verification protocols
added to `engineer.md`/`quality-engineer.md`; and (4) three real hooks —
`.claude/hooks/pre_dispatch_check.py` (`PreToolUse`:`Agent`),
`.claude/hooks/skill_enforcement.py` (`PreToolUse`:`Bash`), and
`.claude/hooks/completion_guardrail.py` (`Stop`) — implemented, registered in
`.claude/settings.json`, and exercised both deterministically (`tests/test_hooks.py`,
`tests/test_route_back_protocol.py`) and, for the two `PreToolUse` hooks, live against real
Claude Code tool calls refused in this session (`docs/hooks-permission-verification.md`,
`docs/hooks-signal-verification.md`). **None of this has yet appeared in a new live `/work`
run** — the route-back loop, the ORCH-* identity check, and the completion guardrail's actual
`Stop`-event blocking are all deterministically verified but not yet demonstrated end-to-end
against a real pipeline; see §10 "Hooks and same-run route-back milestone (2026-08-05)" for the
full, honest account of what is and is not live-proven. Full suite: 442 tests passing, up from
350 in the previous committed milestone (`4f98077`); `demo-repo/` unaffected (102 tests,
unchanged; `git status --short -- demo-repo` empty).
**None of this is committed yet either** — check `git status` before assuming otherwise.

**Live Stop-hook demonstration (2026-08-05, same day):** on top of the hooks milestone above,
the completion-guardrail `Stop` hook's missing-verification-evidence block has now been
demonstrated live against a real Claude Code `Stop` event, not just via direct script
invocation. A dedicated fixture run, `runs/completion-guardrail-live-001/` (task
`DEMO-COMPLETION-GUARDRAIL-1`), wrote a genuine, schema-valid, `final_verdict: "pass"`
`verification-report.json`, moved it aside to `verification-report.json.moved-aside`, wrote a
`run-summary.json` claiming `final_verdict: "pass"`, and wrote `.completion_claim.json` — the
exact marker `work/SKILL.md`'s Reporting step writes. Ending the turn triggered the real,
registered `Stop` hook, which blocked with the exact reason
`runs/completion-guardrail-live-001/verification-report.json is missing (deleted or never
produced)`; the turn continued rather than ending. The completion marker was then safely
deactivated (renamed to `.completion_claim.json.consumed`, content unchanged) so no future
turn is blocked by this fixture, and no active `.completion_claim.json` remains anywhere under
`runs/`. See `runs/completion-guardrail-live-001/STOP-HOOK-LIVE-BLOCK-EVIDENCE.md`,
`runs/completion-guardrail-live-001/CLEANUP-NOTE.md`, and
`docs/hooks-permission-verification.md` §4. This proves the missing-verification-evidence
branch specifically; the hook's other four check branches (invalid JSON, non-`pass` verdict,
canonical-artifact identity mismatch, Git-reality conflict) remain deterministic-only, as
before. The cooperation boundary is unchanged: this hook activates only when `/work` itself
writes `.completion_claim.json` — no new live `/work` run was performed to produce this
demonstration, and none of the other hooks-milestone open items (ORCH-* identity live proof,
route-back live proof, a full live `/work` pipeline exercising these hooks) are affected by
it.

**Milestone update (2026-08-06): checkpoint persistence and genuine process-resume.** On top
of the hooks/route-back milestone above, this session closes the checkpoint/resume milestone:
(1) `checkpoint.schema.json` gained one precise, documented extension, `target_repo_path`
(required) -- the only fact resume genuinely cannot recover otherwise, since no other
canonical artifact persists it; (2) `harness/orchestrator/checkpoint.py` (new) -- four explicit
builders (`record_phase_progress`, `record_completion`, `record_interruption`,
`record_terminal_failure`) that assemble, schema/semantically validate, and atomically write
(`evidence_io.atomic_write`: temp file + `os.replace`, extended from the existing collision-guarded
write pattern to allow legitimate overwrite) `runs/<run_id>/checkpoint.json`, plus a
side-effect-free `evaluate_resume()` implementing every resume precondition: existence, schema/
semantic validity, run_id match, terminal-status refusal (`complete`/`failed`, plus an
independent `run-summary.json`-exists guard), a real `target_repo_path` re-check, and for every
completed phase, artifact existence, full schema/semantic revalidation, and task_id/run_id
identity cross-check against the checkpoint's own claims; (3) two new semantic rules in
`harness/evidence.py`'s `validate_checkpoint_semantics` closing real gaps -- every
`completed_phases` entry must have a matching `artifact_refs` entry (blocks a false
completed-phase claim) and `completed_phases` must be a contiguous prefix of
discovery→research→implementation→verification (enforces predecessor relationships
structurally); (4) `harness/orchestrator/core.py`'s phase logic extracted into shared
`_do_research`/`_do_implementation`/`_do_verification` helpers so the existing `run()` and a new
`resume()` share them, with progress checkpoints threaded through `run()` and a centralized
terminal-checkpoint write inside `_finalize`; (5) two new `live_cli.py` operations,
`write_checkpoint` and `evaluate_resume`, delegating entirely to `checkpoint.py`; (6) `/work`
SKILL.md gained the one and only resume syntax, `/work --resume <run_id>`, checkpoint-write steps
after Discovery/Research/Implementation/Verification, and a full "Checkpointing and resume"
section describing the fresh-agent-dispatch, no-redispatch-of-completed-phases, no-overwrite,
and honest-policy-event rules. `run-summary.schema.json` gained two optional fields,
`phases_reused`/`phases_restarted`, present only on a resumed run's summary. 47 new deterministic
tests (`tests/test_orchestrator_checkpoint.py`, additions to `tests/test_orchestrator_core.py`,
`tests/test_orchestrator_live_cli.py`, `tests/test_work_skill.py`); full suite 489 passing (up
from 442), `demo-repo/` unaffected as a baseline (106 tests). **Live-demonstrated the same day**:
a real, live `/work`-shaped run (`run-20260806-refid-001`, task `T-REFID`, targeting a genuine,
previously-uncovered bug in `demo-repo/src/loanflow/applicant_validation.py` --
`validate_reference_id`'s regex uses a bare `$` anchor, which Python's `re` module matches
immediately before a trailing newline as well as at the true end of string, so
`validate_reference_id("AP-000123\n")` was incorrectly accepted) ran Discovery (approved,
checkpointed), a real Architect dispatch (Research, checkpointed, `findings.json` with one
`found`, one `inferred`, one honest `not_found` finding), and began Implementation with a real
Engineer dispatch that wrote a genuine failing regression test
(`demo-repo/tests/unit/test_applicant_validation.py`, parametrized case `"AP-000123\n"`, real
`pytest` failure confirmed) before the run was deliberately interrupted -- retained as an
`intentional_interruption` policy event -- before mediating that test through the test-runner
Skill and before resuming the Engineer. `checkpoint.json`'s last safely completed phase remains
Research; no `implementation-report.json` was ever promoted; no `.completion_claim.json` was
written. See `runs/run-20260806-refid-001/` for the full retained evidence.

**Second process (2026-08-06, same day): genuine fresh-process resume, terminated blocked.**
A separate Claude Code process invoked `/work --resume run-20260806-refid-001`, closing the
deliberate handoff boundary named above. `evaluate_resume` returned `status: "resumable"`; the
resuming process reused Discovery and Research unchanged (`phase_reused` retained for both,
neither the Architect nor a new Discovery pass dispatched), retained `phase_restarted` for
Implementation, and dispatched a brand-new Engineer agent instance (`a9f9581966b31dc62`) rather
than claiming any live handle from the first, already-exited process survived. That fresh
Engineer produced a genuine failing pre-implementation test (`C-1`, real `pytest` exit code 1,
`DID NOT RAISE ValidationError` for the trailing-newline case) and, after the same one-character
regex-anchor fix (`'$'` -> `'\Z'`) in `applicant_validation.py`, a genuine passing
post-implementation test (`C-2`, real `pytest` exit code 0, all 6 tests pass) -- both
independently mediated through the real test-runner Skill with matching request/result identity.
The phase then blocked honestly: its first staged turn (`pre_test_requested`) was
Markdown-fenced, a real transport violation that consumed this phase's single permitted
transport-only correction (successfully resolved); a second, unrelated transport violation
(leading prose before the JSON object on the `finalization_evidence_requested` turn) was not
eligible for a second correction under the one-per-phase budget, so the phase blocked before any
`implementation-report.json` could be validated or promoted. The run wrote a terminal
`checkpoint.json` (`status: "failed"`, `completed_phases: ["discovery", "research"]`) and a
`run-summary.json` with `final_verdict: "blocked"`; Verification was never attempted; no
`.completion_claim.json` was written. This live-proves a genuine process restart, checkpoint
loading and validation, reuse of completed Discovery and Research without redispatch, no
Architect redispatch, a fresh-agent restart of the incomplete Implementation phase, refusal to
reuse an old process-local agent handle, and honest terminal blocking after resume. It does
**not** prove a successfully completed resumed pipeline, successful Verification after resume,
or a final passing verdict after resume -- those remain deterministic-test-proven only
(`tests/test_orchestrator_core.py`'s resume tests), pending a future live run that reaches
Verification. The two unpromoted `demo-repo/` working-tree changes this blocked run left behind
(`applicant_validation.py`, `test_applicant_validation.py`) have since been reverted by the user;
`demo-repo/` carries no residual changes from this run. See
`runs/run-20260806-refid-001/checkpoint.json`, `run-summary.json`, and
`logs/policy-events.jsonl` for the full record.

**Milestone update (2026-08-06, same day): persistent memory and the lessons-learned
feedback loop.** On top of the checkpoint/resume milestone above, this session closes the
memory-loop milestone (`ASSIGNMENT.md` §2.6; the "Persistent memory and lessons-learned
feedback loop" scope for this session, narrower than checkpoint/resume): (1)
`harness/orchestrator/memory.py` (new) -- fact/lesson validation, append-safe
persistence, deterministic keyword-based relevance selection (`derive_keywords`,
`select_relevant_facts`/`select_relevant_lessons`, no embeddings), duplicate suppression
via text normalization, and `summarize_memory_events`, which derives a run's
`memory_loaded`/`memory_influenced_run`/`memory_refs_used` from that run's own retained
`policy-events.jsonl` rather than from caller assertion; (2) two new durable files,
`memory/facts.jsonl` (append-only, one evidence-backed fact per line) and
`memory/lessons-learned.md` (a deterministic `- [ID] (source_run: ..., evidence: ...,
date: ..., tags: ...) text` bullet grammar, capped at 5 new entries per terminal run);
(3) four new `harness/orchestrator/live_cli.py` operations -- `load_memory`,
`append_memory` (`kind: "fact"|"lesson"`), `record_memory_applied`, and
`summarize_memory` -- each delegating entirely to `memory.py`, consistent with Option C;
(4) three new, narrowly-scoped optional fields on `run-summary.schema.json`
(`memory_loaded`, `memory_influenced_run`, `memory_refs_used`), plus an `if/then` making
`memory_refs_used` non-empty mandatory whenever `memory_influenced_run` is `true`
(`lessons_learned_appended` already existed in the schema from an earlier milestone and
did not need to change); (5) `work/SKILL.md` gained a "Phase 0: Memory load" section
(fixed order: memory load -> Discovery -> Research -> Implementation -> Verification ->
terminal memory append), a "Memory: loaded vs. applied" section defining exactly when
`record_memory_applied` may be called, a "Terminal memory append" section (facts/lessons
evaluated only from the terminal branch, never on an interruption), and standing rule 14
(memory is context, never authority over scope/Protected Paths/evidence). 62 new
deterministic tests (`tests/test_orchestrator_memory.py` -- 41 tests covering fact
validation, lesson behavior, load order, actual-use proof, and checkpoint/resume
interaction; additions to `tests/test_orchestrator_live_cli.py` -- 10 tests exercising
the four new operations through the real CLI dispatch path; additions to
`tests/test_work_skill.py` -- 11 structural tests on the new SKILL.md sections); full
suite 551 passing (up from 489), `demo-repo/` unaffected (106 tests, unchanged).
**Proven the same day via a real, two-run `live_cli.py` demonstration** -- an Option-C
memory-operation demonstration through direct, real invocations of the same CLI `/work`
itself calls, explicitly **not** a live `/work` four-phase pipeline run, per this
milestone's own allowance that deterministic evidence suffices for the memory-influence
requirement when a full live pipeline is not otherwise required: Run A
(`runs/run-20260806-memoryloop-001/`) made two real `live_cli.py append_memory` calls,
persisting a fact and a lesson (`L-20260806-TRANSPORT-BUDGET`) grounded in real,
pre-existing evidence from `runs/run-20260803-riskband-001/` (the genuine live Architect
transport-fence failure documented in that run's own retained
`logs/policy-events.jsonl`) -- not invented for the fixture. Run B
(`runs/run-20260806-memoryloop-002/`) made seven real `live_cli.py` calls: `load_memory`
selected that lesson as relevant via deterministic keyword intersection (no hand-picked
list); a genuine Discovery step (`retain_attempt` -> `validate_scope` ->
`promote_artifact`) produced a real `scope.json` whose `constraints`/`AC-1` explicitly
cite `L-20260806-TRANSPORT-BUDGET` by ID; `record_memory_applied` retained a
`memory_applied` event naming that exact lesson id, this run, the `discovery` phase, the
concrete decision, and `evidence_path: "runs/run-20260806-memoryloop-002/scope.json"`
(independently re-checked to exist before the event was accepted); and `summarize_memory`
-- reading only the retained policy events, not an assertion -- confirmed
`memory_influenced_run: true` and `memory_refs_used: ["L-20260806-TRANSPORT-BUDGET"]`,
written verbatim into Run B's own `run-summary.json`. Run B intentionally stops after
Discovery (`final_verdict: "blocked"` for the same reason the existing dry-run boundary
uses that value -- an intentional pause, not a failure); Research/Implementation/
Verification were never attempted. See `runs/run-20260806-memoryloop-001/
MEMORY-DEMO-RUN-A.md` and `runs/run-20260806-memoryloop-002/MEMORY-DEMO-RUN-B.md` for the
full, itemized account, including what this narrow proof does and does not establish.
Not built this session, per the assignment's explicit exclusions: token/cost tracking,
GitHub/Jira skills, Obsidian integration, connector authentication, REST fallback, a
false-push demonstration, a flaky-test demonstration, architecture diagrams, or the final
write-up. Neither historical run evidence nor `demo-repo/` was modified; nothing was
committed or pushed.

**Post-audit hardening (2026-08-06, same day): memory-applied enforcement tightened, no
new feature run.** A narrowly-scoped audit of the memory-loop milestone above found two
real enforcement gaps in `validate_memory_applied` and corrected them: (1) it validated
`entry_id` against memory *globally* (any currently-valid fact/lesson anywhere in
`memory/`), not against what *this run's own* retained `memory_loaded` event(s) actually
selected as relevant -- `harness/orchestrator/memory.py` gained
`_relevant_ids_from_memory_loaded_events` (unions every `memory_loaded` event's
`relevant_fact_ids`/`relevant_lesson_ids` found in `runs/<run_id>/logs/policy-events.jsonl`)
and `validate_memory_applied` now refuses an entry that exists but was never loaded and
selected by this run, an entry selected only by a different run, a fact claimed as a
lesson (or vice versa), and any claim with zero retained `memory_loaded` events at all;
(2) it confirmed `evidence_path` merely *existed*, not that the file's own content
actually cited the applied entry -- `memory.py` gained a bounded (2 MB cap), exact-
boundary-matched text search (`_evidence_file_contains_entry_id`, using
`(?<![A-Za-z0-9_-])<id>(?![A-Za-z0-9_-])` so "L-1" cannot false-match inside "L-10" or
"L-1-EXTRA") and now refuses an existing-but-silent evidence file, and a directory path
is now correctly rejected (`.is_file()`, not merely `.exists()`) rather than crashing or
silently passing. `harness/orchestrator/live_cli.py`'s `op_record_memory_applied` now
threads `run_directory` through to `validate_memory_applied` accordingly. Also resolved:
`work/SKILL.md`'s "memory is loaded exactly once per run" wording was ambiguous about
resumed invocations -- it now states precisely that this guarantee is **one load per
process invocation, not one load per `run_id`**, adds an explicit `load_memory` step to
"Resuming an interrupted run" (a second, independent `memory_loaded` event under the
same `run_id`, retained before the restarted phase's fresh agent is dispatched), and adds
a "Loading memory on a resumed invocation" section stating that newly loaded memory may
inform the restarted phase and later, never retroactively reinterpret a reused phase's
already-promoted artifact. 28 new deterministic tests total: 18 in
`tests/test_orchestrator_memory.py` (8 in `TestMemoryAppliedRunScopedSelection`, 8 in
`TestMemoryAppliedEvidenceCitesEntry`, and 2 in a dedicated
`TestRunBRealEvidenceStillValidatesUnderStricterContract` that reads Run B's real,
unmodified retained evidence read-only and confirms its exact original
`memory_applied` payload still validates under the strengthened contract), 3 in
`tests/test_orchestrator_live_cli.py`, and 7 structural tests in
`tests/test_work_skill.py`; full suite 579 passing (up from 551). A small, justified
complexity reduction was also applied: `select_relevant_facts`/`select_relevant_lessons`
now share one private `_select_relevant` helper, and the evidence-reference-exists check
duplicated across `_fact_evidence_errors`/`load_lessons`/`append_lessons` was factored
into one `_evidence_ref_exists` helper -- both mechanical, behavior-preserving, verified
by the unchanged test results. No other complexity issue (dead code, unreachable branch,
unsafe path handling, nondeterministic ordering, or accidental append-only-file rewrite)
was found; `memory.py` was not otherwise refactored. This audit also corrected several
instances of ambiguous "live-demonstrated"/"live pipeline" wording in this document (see
the memory-loop milestone paragraph above, §3 "Memory loop," §4, and §10 below) to state
plainly that the memory-influence proof is a **real, two-run `live_cli.py` CLI
demonstration**, not a live `/work` four-phase pipeline run -- the underlying evidence
(Run A/Run B, `runs/run-20260806-memoryloop-001/`, `runs/run-20260806-memoryloop-002/`)
is unchanged and was not reprocessed; only this document's prose was corrected. No new
`/work` run was performed, `demo-repo/` was not touched, no historical run evidence
(including Run A's and Run B's own retained files) was modified, and nothing was
committed or pushed.

**Milestone update (2026-08-14): per-agent token usage and cost accounting.** This
session closes the fourth hook the assignment names (`ASSIGNMENT.md` §2.5's "post-agent
hook that extracts usage") and the accompanying cost-accounting milestone, continuing
from a prior interrupted session that had already performed the sanctioned diagnostic
(`docs/usage-hook-signal-verification.md`, `runs/hooks-diagnostic/
usage-hook-diagnostic-log.jsonl`) but had not begun implementation. (1)
`harness/orchestrator/usage.py` (new) -- transcript parsing (exact structured
`input_tokens`/`output_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`
categories, with the `cache_creation` 5m/1h sub-split preserved for correct per-rate
pricing; never the rounded `totalTokens` convenience figure), `agent_id` ->
retained-`agent_dispatch`-policy-event identity matching (matched / unmatched /
ambiguous, scanning every run's own `logs/policy-events.jsonl`), Decimal-exact pricing
against a repository-local pricing table, and `build_run_usage_summary`'s explicit
subagent-subtotal-vs-orchestrator-vs-full-pipeline coverage semantics -- never reporting
a subagent subtotal as the full pipeline cost. (2) `harness/model-pricing.json` (new) --
official Anthropic per-model USD rates, live-fetched and verified this session
(2026-08-14) directly from `https://platform.claude.com/docs/en/about-claude/pricing`,
**not** reused from the prior interrupted session's own 2026-08-06 pricing note: that
fetch surfaced a real, material pricing change -- Claude Sonnet 5's introductory $2/$10
per-MTok rate, which the 2026-08-06 note recorded as reverting to $3/$15 on
2026-09-01, has since been made the permanent standard price, and Anthropic's own page
states the scheduled increase "will not occur." Reusing the stale note would have
silently mispriced every Sonnet-5 agent by 50%. (3) `.claude/hooks/record_agent_usage.py`
(new), registered in `.claude/settings.json` for both `SubagentStop` (no matcher) --
the authoritative capture path, since it fires on every stop of a staged agent
including every `SendMessage` resume, confirmed by the retained diagnostic evidence --
and `PostToolUse` (matcher `Agent`) -- a secondary, explicitly non-authoritative
corroboration signal retained as its own sidecar record, never merged into or allowed to
override the authoritative per-agent total. Both branches fail open unconditionally (an
evidence-capture hook, never a guardrail). (4) A real architectural finding, made before
and confirmed during this session's own live demonstration: `SubagentStop` fires (and
the hook's capture runs) *before* the orchestrator's own turn resumes to retain that
dispatch's `agent_dispatch` policy event, so a fresh (non-staged) dispatch's *first*
capture attempt is always identity-unmatched by construction, not by defect. `usage.py`
gained `reconcile_quarantined_usage` (exposed via `live_cli.py`'s
`reconcile_quarantined_usage` operation) for the orchestrator to call immediately after
retaining the dispatch event -- it re-derives the record from the same already-retained
transcript path, and, once matched, moves it into the run's own `usage/` directory and
marks the original quarantine record consumed (renamed to `.reconciled`, content
unchanged -- evidence is preserved, never deleted). (5) `live_cli.py` also gained
`build_usage_summary`, a thin delegation to `usage.build_run_usage_summary`/
`write_run_usage_summary`. (6) `run-summary.schema.json` gained one optional field,
`usage_summary_ref` (a pointer to `runs/<run_id>/usage-summary.json`, produced only by
`build_usage_summary`, never hand-authored), following the same additive-optional-field
pattern the checkpoint/resume and memory-loop milestones already established. 56 new
deterministic tests (40 in the new `tests/test_orchestrator_usage.py`, 8 added to
`tests/test_hooks.py`, and 8 added to `tests/test_orchestrator_live_cli.py` -- confirmed
by `git diff`'s own added/removed-function count, not estimated: zero existing test
functions were removed or modified in either file, the only deletions being four
docstring lines in `test_hooks.py`'s module header, and no test in this milestone uses
`@pytest.mark.parametrize`), covering parsing, identity, Decimal pricing,
idempotent/duplicate/resumed capture, quarantine, reconciliation, the corroboration
sidecar, run-summary aggregation, the real hook script, and run-summary schema
integration); full suite 635 passing (up from 579 -- 579 + 56 = 635, exactly), `demo-repo/`
unaffected (106 tests, unchanged). (A prior draft of this note miscounted the new tests as
"62"; that arithmetic did not even reconcile against its own claimed 579-to-635 delta and
is corrected here after an explicit `git diff`/`pytest --collect-only`-based recount --
see the "Integration audit (2026-08-14, same day)" note below for the full accounting of
the discrepancy.) **Live-demonstrated the same day**: a single, brand-new, read-only
`Agent` dispatch (`run-20260814-usagecapture-001`, agent id `a0afd1446050e7213`, task
"read README.md's first line") produced a real `SubagentStop` event that the real,
registered hook captured and (as predicted) initially quarantined as unmatched; a real
`retain_policy_event(kind: "agent_dispatch")` call followed by a real
`reconcile_quarantined_usage` call moved it into
`runs/run-20260814-usagecapture-001/usage/a0afd1446050e7213.json` with exact captured
tokens (`input_tokens: 6`, `output_tokens: 130`, `cache_creation_input_tokens: 39679`,
`cache_read_input_tokens: 19201`, model `claude-sonnet-5`) and a genuine Decimal cost
(`$0.1043497`, independently verified by hand against `model-pricing.json`'s own rates);
a real `PostToolUse:Agent` corroboration sidecar was also captured
(`runs/_usage_corroboration/a0afd1446050e7213.json`), showing a smaller, single-iteration
usage snapshot than the full transcript recompute -- live, first-hand confirmation of
why `PostToolUse:Agent` alone would have undercounted; `build_usage_summary` produced
`runs/run-20260814-usagecapture-001/usage-summary.json` with `coverage_status:
"partial"` and `full_pipeline_total: null` (orchestrator usage genuinely not captured
this milestone). No Engineer/Quality-Engineer records were manufactured, `demo-repo/`
was not touched, no historical run evidence was modified, and nothing was committed or
pushed. Not built this session, per the assignment's explicit exclusions and this
milestone's own scope: GitHub/Jira, Obsidian, REST fallback, false-push/flaky-test
demos, architecture diagrams, the final write-up, and orchestrator-side usage capture
(structurally deferred -- see `usage.py`'s own module docstring for why the run summary
is always written before the orchestrator's own `Stop` event could ever reveal its final
usage).

**Integration audit (2026-08-14, same day): usage accounting wired into `/work`, test-count
correction, evidence-tracking review.** A narrowly-scoped follow-up audit of the
per-agent usage/cost-accounting milestone above, closing integration/documentation gaps
before commit -- no GitHub/Jira/Obsidian/connector/false-push/flaky-test/architecture-
diagram/write-up work, no `demo-repo/` product changes, no historical run evidence
modified, nothing committed or pushed. (1) **`/work` now actually calls the accounting
operations the prior note only implemented.** `work/SKILL.md` gained: two new rows in
the `live_cli.py` operations table (`reconcile_quarantined_usage`, `build_usage_summary`);
a new "Per-agent usage accounting" section stating the fixed post-dispatch sequence
(dispatch -> `retain_policy_event(kind: "agent_dispatch")` -> immediately
`reconcile_quarantined_usage`) and how to interpret every possible result
(`"captured"` normal; `"no_quarantine_record"`/`"quarantined"`/`"ambiguous"`/`"error"`
all retained as a `usage_accounting_gap` policy event and treated as an accounting gap,
never a pipeline failure -- never fabricating a record, never redispatching solely for
accounting); explicit wiring of that sequence into Phase 2 (Architect), Phase 3
(Engineer), Phase 4 (Quality Engineer), and the checkpoint-resume section's freshly
restarted-phase dispatch (a genuinely new `agent_id`, reconciled as its own separate
record); an explicit statement that resumed/staged identities (transport repairs, staged
continuation turns, same-run route-back) never repeat the sequence, since the original
dispatch's own `agent_dispatch` event already lets every later `SubagentStop` capture for
that same `agent_id` match directly without quarantine; a new "Terminal usage summary"
section calling `build_usage_summary` immediately before **every** `write_run_summary`
call this document makes (deliberately broader than "Terminal memory append"'s
checkpoint-gated scope -- including `discovery_invalid`/`discovery_refused`, which write
no checkpoint at all, since an empty usage summary is still honest, cheap evidence) and
setting `run-summary.json`'s `usage_summary_ref` to the exact returned path, never a
hand-authored total; two new standing rules (15: dispatch-time reconciliation: 16:
accounting is evidence, not verdict -- a `usage_accounting_gap` never changes
`final_verdict`/`phases_completed`/any artifact's validity, and `subagent_subtotal` may
never be described as "total pipeline cost"); and a "What to state, every time" bullet
requiring every report to read the usage picture from `usage-summary.json` itself,
including the honest `full_pipeline_total: null`. 25 new deterministic tests (20 in
`tests/test_work_skill.py::TestUsageAccounting`, proving -- via string/ordering checks
against the real document text, the same technique every other `TestWork*`/`Test*`
class in that file already uses -- that each phase's dispatch step precedes its
reconciliation call, that resumed identities are documented as never repeating it, and
that the coverage-vs-pipeline-correctness distinction is stated explicitly; 5 in
`tests/test_orchestrator_usage.py::TestAccountingFailureNeverCorruptsPipelineResult`,
proving at the code level -- not just by prose assertion -- that a quarantined, errored,
or gap-reporting usage capture/reconciliation/summary call leaves a real
`verification-report.json` and `run-summary.json` byte-for-byte untouched, and that
`build_run_usage_summary`'s own return value contains no `final_verdict` key at all, so
there is no code path by which accounting could veto a pass). No live `/work` run was
performed for this audit -- deterministic integration tests were sufficient to prove the
wiring; see §10 for why a new live four-phase run was judged unnecessary here. (2)
**Test-count discrepancy corrected.** The prior note's own "62 new tests" claim was
simply wrong arithmetic -- it did not even reconcile against its own stated 579-to-635
delta (579 + 62 = 641 != 635). A `git diff`/`pytest --collect-only`-based recount found
the true figure: 56 new tests for the implementation milestone (40 in the new
`tests/test_orchestrator_usage.py`, 8 added to `tests/test_hooks.py`, 8 added to
`tests/test_orchestrator_live_cli.py` -- confirmed via `git diff`'s own added-function
count; zero existing tests were removed, replaced, or modified in either modified file
beyond four docstring lines in `test_hooks.py`'s module header; no test in this milestone
uses `@pytest.mark.parametrize`, so parametrization explains none of the discrepancy),
579 + 56 = 635 exactly. This audit then added a further 25 (per item 1 above),
635 + 25 = **660**, the harness suite's current, confirmed-by-`pytest --collect-only`
total. (3) **Evidence-tracking reviewed.** `docs/usage-hook-signal-verification.md` and
`runs/hooks-diagnostic/usage-hook-diagnostic-log.jsonl` were, at the start of this audit,
real but still-untracked working-tree files (confirmed via `git ls-files`) -- exactly the
same untracked state every other file this two-part milestone touched was in, since
nothing has been committed yet; they are not ephemeral or unsafe, and are the intended
milestone file set precisely as `docs/hooks-signal-verification.md` and
`runs/hooks-diagnostic/skill-enforcement-events.jsonl` already established the committed
precedent for (git history confirms those two are tracked, from the earlier
skill-enforcement diagnostic). Both new files were re-scanned this audit for
credential/secret-shaped content (`api_key`/`secret`/`password`/`bearer`/`token`/
`sk-ant`/`ghp_`-style patterns) and found clean; no sanitized-summary substitute was
needed. `runs/_unmatched_usage/`, `runs/_usage_corroboration/`, and
`runs/run-20260814-usagecapture-001/` were likewise re-scanned and confirmed to hold only
small, minimal, appropriate evidence (39-46 lines per file: aggregated token counts and
identity metadata only -- never a copied-in full subagent transcript, which remains
referenced only by its absolute path, outside the repository, exactly as designed).
Nothing was deleted from any of these to make `git status` prettier. (4) **"Subagent
accounting coverage: complete" wording corrected.** The prior session's final report
conflated two different claims: that the capture *mechanism* (transcript parsing,
identity matching, pricing) supports every controlled subagent type -- true, and
unchanged -- versus that *live, operational* accounting had been demonstrated across a
full four-phase `/work` pipeline -- which was never actually true; reconciliation was
still manual and `/work` had not yet been wired to call it. The retained evidence remains
exactly what it always was: one real, controlled single-agent dispatch
(`run-20260814-usagecapture-001`), not a live four-phase proof. This document, and any
future report, should state the two claims separately: the capture mechanism is
complete and covers Architect/Engineer/Quality-Engineer alike (now wired into `/work`
itself, per item 1); a live demonstration of accounting across an actual four-phase
`/work` run remains open, alongside the still-open live route-back/hooks-against-a-real-run
demonstration item already tracked elsewhere in this document.

**Milestone update (2026-08-18): GitHub skill and independent push-verification
layer.** This session closes the "GitHub skill + real Git/GitHub integration +
independent remote push verification + false-push detection" milestone
(`ASSIGNMENT.md` §2.3.1/§2.4/§4's cardinal rule; this milestone's own explicit scope --
Jira, Obsidian, MCP-vs-REST comparison, flaky-test demos, architecture diagrams, and the
final write-up were all explicitly out of scope and are untouched). (1)
`harness/orchestrator/github.py` (new) -- deterministic, evidence-only Git/GitHub
support: real `git`/`gh` subprocess calls through one swappable `CommandRunner` seam
(`DEFAULT_RUNNER` in production; a fake runner only ever used by tests), repository
identity (`get_repo_root`/`get_current_branch`/`get_local_head_sha`/`get_remote_url`),
strict full-40-character-SHA validation (`is_full_sha` -- a short SHA can never satisfy
it, so no accidental prefix-equality is possible), `git ls-remote` output parsing
tolerant of CRLF/blank lines but strict about malformed content
(`parse_ls_remote_output`), and the one push-verification classifier
(`classify_ls_remote_result`/`verify_push`) returning exactly one of `verified` /
`mismatch` / `remote_ref_missing` / `command_failed` / `invalid_output` /
`wrong_repository` -- never a bare boolean. `gh` is wired for metadata only
(`gh_auth_status`/`gh_repo_metadata`), never as a substitute for `git ls-remote`, per
`ASSIGNMENT.md` Part 9. (2) `harness/orchestrator/evidence_io.py` gained one new
function, `retain_git_evidence` -- the same collision-guarded write
`promote_canonical`/`retain_raw_attempt` already use, redirected to a new
`runs/<run_id>/git/` subdirectory for Git evidence that is not one of the four canonical
phase artifacts. (3) `harness/orchestrator/live_cli.py` gained five new operations
(`git_repo_identity`, `retain_commit_evidence`, `retain_push_attempt`, `verify_push`,
`gh_repo_metadata`), each delegating entirely to `github.py`/`evidence_io.py` and each
independently re-deriving the facts it retains (branch, HEAD SHA, remote URL) via real
`git` calls rather than trusting a caller-supplied claim; `verify_push` never accepts a
simulated result through this boundary -- the real subprocess always runs, so a live
`/work` run can never fabricate a push outcome, even though `github.py`'s own functions
are testable with a fake `CommandRunner`. `"verified"` was added to `live_cli.py`'s
`OK_STATUSES` (exit code 0); its five sibling classifications are deliberately absent,
so `/work` treats a `mismatch`/`remote_ref_missing`/etc. exactly like any other blocked
result. (4) `.claude/skills/github/SKILL.md` (new) -- a procedural skill (no
`allowed-tools`/`disallowed-tools`/`context: fork`, mirroring `code-craftsmanship`'s
shape, not `test-runner`'s forked-wrapper shape, since git/gh commands here are run
directly by the trusted orchestrator, not mediated on behalf of an untrusted agent)
covering repository identity, working-tree inspection, branch inspection, diff review,
staging, commit creation, push, independent remote verification, `gh` metadata, and
failure/mismatch reporting, per `ASSIGNMENT.md` §2.3's github-skill requirement. States
explicitly, as its own cardinal rule: a local commit is not a verified push; `git
push`'s exit code is not independently sufficient; an agent's "pushed" claim is not
evidence; a GitHub UI assumption is not evidence; remote verification is mandatory
before reporting a push as successful. (5) `work/SKILL.md` gained: a new "GitHub / Git
delivery" section (Skill invocation -> repo identity -> working-tree/diff inspection ->
explicit staging -> commit -> `retain_commit_evidence` -> push -> `retain_push_attempt`
-> mandatory `verify_push` -> honest failure/mismatch reporting, with Git-delivery
correctness kept explicitly independent of product/test correctness in both
directions); standing rule 17 (any commit/push is always preceded by invoking the real
`github` Skill, and only a `verify_push` status of exactly `"verified"` may ever be
reported as a successful push); a cross-reference from standing rule 12 (which still
governs -- no commit/push is authorized in this milestone's own work) to rule 17; five
new operations added to the `live_cli.py` table; and a new "Git delivery status" bullet
in "What to state, every time." This answers "is the GitHub Skill actually required
when `/work` performs Git/GitHub actions" as a real, tested, standing documentary
requirement, consistent with how every other cross-cutting `/work` protocol in this
repository (staged continuation, transport-repair budgets, per-agent usage accounting)
is enforced -- via `SKILL.md` prose plus structural tests
(`tests/test_work_skill.py::TestGitHubDelivery`), not a new runtime hook; no existing
hook was modified, and all four already-registered hooks remain exactly as configured
in `.claude/settings.json`. (6) **False-push detection (`ASSIGNMENT.md` Part 6),
deterministic**: `tests/test_orchestrator_github.py::TestClassifyLsRemoteResult::
test_false_push_claim_is_classified_mismatch` constructs a claimed expected SHA
(`"a" * 40`), a controlled fake `git ls-remote` command result reporting a different
observed SHA (`"b" * 40`) via the `FakeRunner` command-result-boundary seam the module's
own docstring names, and asserts the classification is `mismatch`, never `verified`;
`tests/test_orchestrator_live_cli.py::TestVerifyPush::
test_mismatch_is_retained_honestly_never_reported_as_verified` re-proves the identical
scenario through the exact CLI boundary `/work` itself calls (a real temporary Git
repository's real HEAD SHA as the expected value, `github.DEFAULT_RUNNER` monkeypatched
only at the test boundary to return a fabricated remote SHA), confirming
`runs/<run_id>/git/push-verification.json` retains `status: "mismatch"` honestly and
`live_cli.main`'s own exit code is `1` (a well-formed negative result), never `0`. This
is explicitly a **deterministic mismatch fixture**, not a real failed GitHub push --
no real remote was ever contacted for either test. **83 new pytest cases were added,
exactly matching the observed suite delta (660 at `HEAD` -> 743 now,
`pytest --collect-only` on both), reconciled test-function-by-test-function against
`git diff`/`git show HEAD:<path>` after an initial draft of this note miscounted two of
the five files** (see the "Test-count reconciliation audit (2026-08-18, same day)" note
below for the full, itemized correction). The verified per-file breakdown -- one new
source-level `def test_...`/`def test_...(self)` line always contributes exactly one
new collected pytest case here; nothing in this milestone's additions uses
`@pytest.mark.parametrize` or a fixture that multiplies collection -- is: 45 in the new
`tests/test_orchestrator_github.py` (repository identity, SHA handling including the
explicit "no accidental short-SHA equality" case, `ls-remote` parsing including
CRLF/whitespace/malformed/duplicate-line cases, the false-push proof, `gh` metadata, and
`resolve_repo_path`'s reuse of `paths.py`), 3 in `tests/test_orchestrator_evidence_io.py`
(`retain_git_evidence`'s subdirectory placement, on-demand directory creation, and
collision guard; `HEAD` had 10 `test_*` functions, now 13), 13 in
`tests/test_orchestrator_live_cli.py` (the five new operations, including two
real-temporary-Git-repository success-path tests and the mismatch/false-push proof
above; `HEAD` had 51, now 64), 9 in `tests/test_skill_definitions.py::TestGithubSkill`
(`HEAD` had 18, now 27), and 13 in `tests/test_work_skill.py::TestGitHubDelivery`
(`HEAD` had 118, now 131) -- 45 + 3 + 13 + 9 + 13 = 83. `git diff` across all four
modified test files shows exactly one removed line in total (a single import-statement
line in `tests/test_orchestrator_live_cli.py`, widened to add `inspect`/`subprocess`/
`github`, not a deleted test) -- no pre-existing test function was deleted, renamed,
merged, or replaced anywhere in this milestone. Full harness suite 743 passing (up from
the confirmed 660 baseline at this session's start), `demo-repo/` unaffected (106 tests,
unchanged; `git status --short -- demo-repo` empty both before and after). (7)
**No live push was performed or verified this session**, per this milestone's own
explicit instruction not to commit or push: `git status`/`git remote -v`/`gh auth
status` were confirmed at the start (`main` synced with `origin/main`, clean tree,
`origin` -> `https://github.com/cmlong13/AgenticHarness.git`, `gh` authenticated as
`cmlong13`), but no commit was created and no `git push`/`retain_commit_evidence`/
`retain_push_attempt`/`verify_push` call against the real remote was ever made --
`ASSIGNMENT.md`'s "Controlled Real GitHub Demonstration" (Part 11) explicitly allows
stopping here when performing it would require prematurely committing unreviewed
milestone work, which this session's own instructions independently forbid regardless.
A live demonstration of a genuine successful `verify_push` against `origin/main` (and,
separately, of standing rule 17's Skill-invocation requirement against a real `/work`
run that legitimately reaches this section) remains open, pending user review and
explicit authorization to commit/push this milestone's own changes. Not built this
session, per the assignment's explicit exclusions and this milestone's own scope: Jira,
Obsidian, MCP-vs-REST connector comparison, flaky-test demonstration, architecture
diagrams, the final write-up, and any change to checkpoint/memory/usage-accounting
behavior (all confirmed unchanged: checkpoint/resume, memory-loop, and usage-accounting
tests remain green, and all four existing hooks remain registered in
`.claude/settings.json`, byte-for-byte unmodified this session).

**Test-count reconciliation audit (2026-08-18, same day): corrected a real test-count
contradiction, no feature work.** A narrowly-scoped follow-up audit of the GitHub-skill
milestone above found that its own first-draft test-count prose was internally
inconsistent -- it claimed "55 new focused tests" while the five per-file figures it
listed summed to 88, and neither figure matched the actually-observed 660 -> 743 suite
delta (83). Root cause, confirmed by direct measurement rather than re-guessing: two of
the five per-file figures in the original draft were simply miscounted --
`tests/test_orchestrator_live_cli.py` was reported as 16 new tests (actually 13) and
`tests/test_work_skill.py` was reported as 15 (actually 13); the other three figures
(`test_orchestrator_github.py` 45, `test_orchestrator_evidence_io.py` 3,
`test_skill_definitions.py` 9) were already correct. 45 + 3 + 13 + 9 + 13 = 83, which is
exactly the measured suite delta -- there was no hidden parametrization, fixture-driven
multiplication, or deleted/re-added test inflating or deflating the true count; it was
purely an arithmetic/transcription error in the prose, of the same general kind the
2026-08-14 usage-accounting audit above also found and corrected for a different figure.
Method used to reconcile (per this audit's own instruction not to guess): (1)
`git stash push -u`, `pytest tests -q --collect-only` against the untouched `HEAD` tree
(660 collected, confirming the stated baseline), `git stash pop` to restore the working
tree exactly; (2) `pytest tests -q --collect-only` against the current working tree (743
collected); (3) `git diff --numstat` against each of the four modified test files; (4)
`git diff -- <file> | grep -E "^[+-].*def test_"` per file to see every added/removed
test-function line; (5) `git show HEAD:<file> | grep -cE "^\s*def test_"` vs. the current
file's own count, per file, as an independent cross-check of (4); (6) a plain
`grep -cE "^\s*def test_"` count of the new, untracked `tests/test_orchestrator_github.py`
in full (45, matching its own earlier standalone `pytest` run's "45 passed"). All three
methods (diff-based line count, before/after function count, and standalone collection)
agreed for every file. No test in this milestone's additions uses
`@pytest.mark.parametrize`; the new fixtures (`git_repo`, `git_repo_root`, the
`FakeRunner` helper class) are ordinary setup helpers, not collection multipliers, so
"source-level test functions/methods added" and "pytest cases added" are the same
number here (83) -- stated as two distinct concepts only because they are not always
the same thing in general, not because they diverged in this case. This audit corrected
only this document's own prose (the paragraph above); it did not touch
`harness/orchestrator/github.py`, `live_cli.py`, `evidence_io.py`, any test file, the
`github` Skill, or `work/SKILL.md` -- re-running the full suite after this audit still
shows 743 passing, unchanged. No commit or push occurred.

**Milestone update (2026-08-18, later same day): live successful GitHub push-verification
demonstration.** The one item the GitHub-skill milestone above left open -- a live
demonstration of a genuine successful `verify_push` against the real `origin/main` remote
-- is now closed. Retained evidence lives at `runs/run-20260818-githubpush-001/` (task
`GITHUB-LIVE-001`): (1) `git/commit-evidence.json` -- the real implementation commit,
`f2fd7187d8d3ea96a4a958b67cb4ff1c115b1612` on branch `main`, retained via
`retain_commit_evidence`; (2) `git/push-attempt.json` -- a real, normal push to
`origin/main` (`https://github.com/cmlong13/AgenticHarness.git`, `refs/heads/main`),
retained via `retain_push_attempt` with its own expected local SHA
(`f2fd7187d8d3ea96a4a958b67cb4ff1c115b1612`) recorded up front -- the `git push` command's
own output/exit code was never itself treated as proof of a successful push, exactly as the
`github` Skill's cardinal rule and `work/SKILL.md` standing rule 17 require; (3)
`git/push-verification.json` -- the production `verify_push` path independently ran a real
`git ls-remote origin refs/heads/main` (never `gh`, per `github.py`'s own design), retaining
the raw command, its exit code (`0`), stdout
(`f2fd7187d8d3ea96a4a958b67cb4ff1c115b1612	refs/heads/main`), and stderr (empty)
alongside the classification: `status: "verified"`, `observed_sha` exactly equal to
`expected_sha`, reason `"observed remote SHA matches expected local SHA"`; (4)
`logs/policy-events.jsonl` records the full evidence chain in order -- `git_commit_evidence`,
`git_push_attempt`, `push_verification` (`status: "verified"`). The user separately ran the
identical `git ls-remote origin refs/heads/main` command by hand, outside the harness, and
confirmed the same remote SHA (`f2fd7187d8d3ea96a4a958b67cb4ff1c115b1612`) -- an independent
external sanity check matching the harness's own result, not itself part of the production
evidence chain. This closes the live successful independent push-verification criterion.

One distinction stays explicit, unchanged by this demonstration: the production
push-verification *mechanism* (`github.py`, `verify_push`, the five `live_cli.py`
operations, the `github` Skill) is now **LIVE-PROVEN** against a real commit and a real
remote; the deterministic false-push (mismatch) rejection proven earlier the same day
(`tests/test_orchestrator_github.py::TestClassifyLsRemoteResult::
test_false_push_claim_is_classified_mismatch`,
`tests/test_orchestrator_live_cli.py::TestVerifyPush::
test_mismatch_is_retained_honestly_never_reported_as_verified`) remains exactly what it
always was -- a controlled fixture with an expected SHA deliberately differing from a
simulated remote SHA, classified `mismatch`, never described as a real failed GitHub push;
these are two separate proofs, not to be conflated. A full `/work` pipeline that naturally
reaches the "GitHub / Git delivery" section and invokes standing rule 17 in the course of an
actual four-phase run remains **not** live-demonstrated, unless a future run's own retained
evidence proves otherwise -- this evidence was retained directly through the same
`live_cli.py` operations `/work` itself calls, not through a literal `/work` invocation, the
same documented substitution pattern the memory-loop and usage-accounting milestones already
used for their own live proofs. This documentation-only closeout pass corrects
`PROJECT_SPEC.md`'s prose to reflect the already-retained evidence above; it did not itself
commit, push, or modify any implementation or test file. Not built this session, consistent
with this closeout's own scope: Jira, Obsidian, MCP-vs-REST connector comparison, a
flaky-test demonstration, architecture diagrams, and the final write-up remain untouched.

This document is the authoritative internal reference for what is being built. It is derived
from the assignment brief ("Build Your Own Agentic Harness") and the planning discussion that
followed. Implementation should track this spec; if the two diverge, update this file first.

---

## 1. Summary

The harness is a pipeline of specialized agents with deliberately restricted permissions,
coordinated by an orchestrator, guarded by hooks that enforce rules agents might forget, and
improved over time by a memory loop. A request — a ticket ID or a free-form prompt — flows
through four phases:

| Phase | Agent role | May write code? | Output |
|---|---|---|---|
| Discovery | Orchestrator (main session) | No | `scope.json` |
| Research | Architect | No (read-only + notes) | `findings.json` |
| Implementation | Engineer | Yes | Minimal diff + `implementation-report.json` |
| Verification | Quality Engineer | Tests only | `verification-report.json` |

`checkpoint.json` supports cross-phase resume, and `run-summary.json` summarizes the final run
once all phases complete.

Cardinal rule: no phase consumes the previous phase's output on trust. The engineer verifies
the architect's claims before coding. The orchestrator verifies the engineer's "done" claim
(e.g. via `git ls-remote` / `git rev-parse`) before reporting success.

---

## 2. Runtime Approach (decided)

**Claude Code–native.** The harness is implemented as a Claude Code project configuration, not
a standalone Python orchestration engine:

- Subagents are `.md` files with YAML frontmatter (`name`, `description`, `tools`) under
  `.claude/agents/`. Claude Code does not require a `model` frontmatter field; for this project
  the MVP convention is `model: inherit` for all subagents, unless a later measured reason
  justifies changing it per-role.
- Skills are `SKILL.md` + helper scripts under `.claude/skills/`.
- Hooks are scripts registered in `.claude/settings.json`.
- The entry point is a project skill, `.claude/skills/work/SKILL.md`, invoked as `/work`
  — not a `.claude/commands/*.md` custom command. This corrects an earlier inconsistency
  in this document (this section previously called the orchestrator a "main-session
  skill" while §5's file map placed `/work` under `.claude/commands/`). Modern Claude
  Code treats a user-invocable slash-style entry point as a skill
  (`user-invocable: true`, `disable-model-invocation: true` frontmatter, no
  `context: fork` so it runs in the main session), and this repository has local,
  live-verified proof the Skill mechanism works end-to-end (`test-runner`,
  `code-craftsmanship` — see §10), where `.claude/commands/` has none. `/work` runs with
  no broad `allowed-tools` grant of its own — existing main-session permissions govern.
- `harness/` is thin Python support code (checkpointing, memory I/O, cost aggregation, evidence
  validation) that hooks and skills invoke — it is not a competing agent runtime. The live
  bridge at `harness/orchestrator/live_cli.py` extends this same principle to `/work`: a
  thin, dependency-free JSON-in/JSON-out CLI over the already-existing deterministic
  validation/evidence functions (`discovery.py`, `evidence_io.py`, `paths.py`,
  `harness/evidence.py`, `state.py`), with no agent reasoning, phase sequencing, or
  dispatch logic of its own — see §10 "Live architecture decision (Option C)".

This was chosen because the assignment's language (subagents can't spawn nested subagents,
markdown+frontmatter agent defs, `SKILL.md`, hooks in settings, slash commands) maps directly
onto Claude Code's existing feature set, so tool-restriction and permission enforcement can be
reused rather than reimplemented.

---

## 3. Required Components

### Artifact contracts (implemented)
- The six schemas (`scope`, `findings`, `implementation-report`, `verification-report`,
  `checkpoint`, `run-summary`) live under `harness/schemas/`, as JSON Schema Draft 2020-12.
- Valid reference examples for each schema live under `harness/artifacts/examples/`.
- `harness/evidence.py` validates schema conformance (via `jsonschema`) and the approved
  semantic cross-reference rules that JSON Schema itself cannot express.
- All six examples currently validate against their schemas.
- The contract test suite (`tests/test_artifact_contracts.py`) currently contains 38 passing
  tests.
- `jsonschema` is a development dependency (`pyproject.toml` `[project.optional-dependencies].dev`),
  not a runtime dependency of `harness/`.

### Orchestration
- Orchestrator (main-session skill, Discovery phase): decomposes requests into a task graph,
  decides which phases are actually needed, reads `lessons-learned.md` before dispatch, runs a
  pre-dispatch checklist, may refuse work with reasons, independently verifies all downstream
  completion claims.
- `/work` slash command — two modes: `TICKET-ID repo` (Jira) and free-form prompt. **Not started.**
- Deterministic orchestrator core — **complete** at `harness/orchestrator/` (`core.py`,
  `state.py`, `paths.py`, `discovery.py`, `evidence_io.py`, `adapters.py`): a one-pass
  Discovery → Research → Implementation → Verification state machine that dispatches Architect,
  Engineer, and Quality Engineer through an `AgentAdapter.start()`/`resume()` contract, mediates
  every staged command through a `TestRunnerAdapter`, validates every artifact against
  `harness/schemas/` and `harness/evidence.py` before promoting it, and produces a schema-valid
  `run-summary.json` for every terminal outcome. Automatically tested end-to-end against
  scripted fake adapters only (246 tests passing; full detail in §10) — no live `Agent`/
  `SendMessage` call, no real test-runner invocation, and no reasoning-backed Discovery adapter
  exist yet, so no real pipeline has ever been run. `DiscoveryAdapter` is an explicit boundary:
  it receives `raw_prompt`/`task_id`/`run_id`/`created_at`/`target_repo_path` and returns a
  proposed scope document with no persistence of its own — the orchestrator alone validates
  (schema, `validate_scope_semantics`, real path safety) and promotes it. The real,
  reasoning-backed `DiscoveryAdapter` is the live Claude Code main session (or a future `/work`
  skill), not yet implemented.

### Subagents (≥3)
- `architect.md` — Research. Read/grep/glob + docs connectors only, no source edits. Evidence
  hierarchy: executable code > test assertions > runtime config > comments > metadata. Every
  claim has `file:line`. Labels findings as Found / Not Found / Inferred. Reads method bodies,
  not just signatures. Searches the whole repo; distinguishes current code from legacy.
  Output boundary: the Architect is technically restricted to read/search tools, so it returns
  structured findings compatible with `findings.schema.json` to the orchestrator rather than
  writing `findings.json` directly — the orchestrator (or controlled support code it invokes)
  validates and persists the artifact. This is what lets the Architect run without `Write`,
  `Edit`, `Bash`, or `PowerShell` access.
- `engineer.md` — Implementation. Full edit tools. Consumes architect findings, verifying cited
  patterns exist before building on them. Minimal-change ladder (stop at the first rung that
  holds): (1) does this need to exist at all — skip speculative work; (2) reuse an existing
  helper/pattern; (3) use stdlib/platform features; (4) use an already-installed dependency;
  (5) only then write the minimum new code, without cutting input validation, data-loss-safety,
  or security checks. Writes one failing test first per new/changed behavior (TDD). Extends
  rather than replaces working code.
- `quality-engineer.md` — Verification. `Read, Grep, Glob` only — no `Edit`/`Write`/`Bash`, so it
  cannot modify source at the tool layer at all (fixes route back to the Engineer). Reuses the
  Engineer's caller-mediated staged-command precedent: it never executes anything itself, only
  requests exact commands (`attempt_requested`) that the controlled caller runs and reports real
  exit codes/output for. Independently cross-checks the implementation report's own
  `findings_ref.path` against the caller-supplied one before trusting it, and checks
  `changed_files` against the Protected Path list before verifying anything, both returning a
  schema-valid `blocked` report on failure. Runs the narrowest test command that validates the
  change. Classifies every failure as logic bug / infrastructure flake / environment before
  reporting. Retries only evidence-supported infrastructure flakes (`max_retries: 2`); never
  retries logic or environment failures. Every verdict is backed by evidence (exit codes, output
  refs) — "tests pass" alone is not a verdict. Independently verified live: it caught a
  deliberately false "tests pass" claim in a crafted implementation report by re-running the
  command for real, diagnosed the actual root cause from source, and routed it back
  (`docs/quality-engineer-permission-verification.md`).

### Skills (≥4 packs — 3 of 4 complete; see §10)
- `github/` — **complete for this milestone's actual scope** (2026-08-18):
  `.claude/skills/github/SKILL.md`, a procedural skill (no tool allowlist of its own)
  covering repository identity, working-tree/branch/diff inspection, staging, commit
  creation, push, independent remote verification (`git ls-remote`, never `gh`), `gh`
  metadata, and failure/mismatch reporting — required by `work/SKILL.md` standing rule
  17 before any commit/push. **LIVE-PROVEN (2026-08-18)** against a real commit and a
  real push to `origin/main`, independently verified by a real `git ls-remote`
  (`runs/run-20260818-githubpush-001/`; see the "live successful GitHub
  push-verification demonstration" milestone note above) — not deterministic-only. This
  satisfies `ASSIGNMENT.md` §2.3's github-skill
  requirement as this milestone scoped it (Git delivery + false-push detection); the
  broader `pr-create`/`read-file`/`search-code`/`commit-history`/`pr-review` breadth
  this file map originally sketched for GitHub-API code/PR access remains **not
  started** and is separate, later work (§4 "Later integrations").
- `jira/` (Atlassian) — `create-ticket`, `read-ticket`, `edit-ticket`, plus a field-reference doc
  so agents don't guess custom field IDs. **Not started.**
- Test-runner skill — encodes this repo's actual test command, thresholds, and report location.
  **Complete** — implemented at `.claude/skills/test-runner/`, automatically tested
  (`tests/test_skill_definitions.py`, `tests/test_test_runner_validation.py`), script-level
  boundary-verified, and live `Skill`-mechanism/runtime-permission-verified (§10).
- Code-craftsmanship skill — the minimal-change ladder and YAGNI rules as a standalone skill any
  agent can be held to. **Complete** — implemented at `.claude/skills/code-craftsmanship/`,
  structurally tested (`tests/test_skill_definitions.py`), live invoked via the `Skill` tool, and
  behaviorally A/B-evaluated against fresh no-skill/skill-enabled agent pairs (§10).

### MCP connectors (3, one built two ways)
| Connector | Used in phase | Purpose |
|---|---|---|
| GitHub (MCP server or `gh` CLI) | Discovery, Research, Verification | Read issues/PRs, search code, verify pushes landed |
| Obsidian (`mcp-obsidian`) | Discovery, Research | Personal knowledge vault: design notes, past decisions, calibration docs |
| Atlassian/Jira | Discovery | Ticket intake, status transitions, completion comments |

For at least one connector, both the MCP route and a REST-skill fallback must be implemented,
with a documented decision on which was kept and why (the reference setup dropped the Atlassian
MCP server in favor of curl-based skills for reliability/debuggability).

### Hooks (≥3, plus cost tracking)
- Skill-enforcement hook — **Implemented** (2026-08-05) at `.claude/hooks/skill_enforcement.py`
  (`PreToolUse`, matcher `Bash`): blocks a direct `Bash` bypass of the test-runner wrapper
  (`run_command.py`) called outside real, forked `Skill` mediation, and blocks a `Bash` command
  that looks like a direct write into `demo-repo/` (an orchestrator fallback around the
  Engineer's own `Edit`/`Write`). Distinguishes the legitimate forked Skill call from an
  orchestrator bypass via the `agent_id` field's presence/absence in the hook payload —
  empirically verified live, not assumed (`docs/hooks-signal-verification.md`). Every blocked
  event is appended to its own ignored, mutable runtime log,
  `.claude/hooks/logs/skill-enforcement-events.jsonl` (`.gitignore`d 2026-08-05); a verbatim
  copy of this run's blocked events is committed as retained evidence at
  `runs/hooks-diagnostic/skill-enforcement-events.jsonl`. Live-verified against
  two real, refused `Bash` tool calls in this session (`docs/hooks-permission-verification.md`);
  13 deterministic tests (`tests/test_hooks.py::TestSkillEnforcement`). See §10 "Hooks and
  same-run route-back milestone (2026-08-05)" for the full account, including the documented
  heuristic limitation and version-sensitivity caveat.
- Pre-dispatch check hook — **Implemented** (2026-08-05) at `.claude/hooks/pre_dispatch_check.py`
  (`PreToolUse`, matcher `Agent`): refuses to dispatch `architect`/`engineer`/`quality-engineer`
  unless the phase's required precondition artifact (`scope.json` `status: "approved"`,
  `findings.json`, or `implementation-report.json` `status: "ready_for_verification"`,
  respectively) is already promoted, well-formed, and identity-matches (`task_id`/`run_id`) the
  dispatch prompt itself. Live-verified against two real, refused `Agent` tool calls in this
  session, before any subagent was created (`docs/hooks-permission-verification.md`); 14
  deterministic tests (`tests/test_hooks.py::TestPreDispatchCheck`).
- Completion-guardrail hook — **Implemented** (2026-08-05) at
  `.claude/hooks/completion_guardrail.py` (`Stop`): blocks the orchestrator's turn from ending on
  a `/work` run's completion claim (`runs/<run_id>/.completion_claim.json`, written by
  `work/SKILL.md`'s own Reporting step) unless `verification-report.json` exists, is valid,
  schema/semantically valid, `final_verdict: "pass"`, every canonical artifact
  `run-summary.json` cites exists and identity-matches, and the implementation report's
  `changed_files` are genuinely reflected in `git status --porcelain`. **Live-demonstrated**
  (2026-08-05, `runs/completion-guardrail-live-001/`): a real Claude Code `Stop` event fired
  against a fixture with a genuine, previously-passing `verification-report.json` moved aside,
  and the real, registered hook blocked the turn with the exact reason
  `runs/completion-guardrail-live-001/verification-report.json is missing (deleted or never
  produced)` — the turn continued rather than ending; see
  `runs/completion-guardrail-live-001/STOP-HOOK-LIVE-BLOCK-EVIDENCE.md` and
  `docs/hooks-permission-verification.md` §4. This proves the missing-verification-evidence
  branch specifically; the hook's other four failure branches (invalid JSON, non-`pass`
  verdict, canonical-artifact mismatch, Git-reality conflict) remain **deterministically
  verified only** (13 tests, `tests/test_hooks.py::TestCompletionGuardrail`, invoking the real
  script's `main()` directly against crafted fixtures), not separately demonstrated as live
  `Stop`-event blocks. Depends on `/work` cooperatively writing `.completion_claim.json`; an
  orchestrator turn that never writes that marker is not caught by this hook — a known
  cooperation boundary, not a claim of unconditional enforcement (see §10).
- Post-agent cost hook — extracts per-agent token/cost usage. **COMPLETE (2026-08-14)**:
  `.claude/hooks/record_agent_usage.py`, registered for both `SubagentStop` (authoritative
  capture -- fires on every stop of a staged agent, including resumes) and `PostToolUse`
  (matcher `Agent`; secondary, non-authoritative corroboration only). Delegates entirely to
  `harness/orchestrator/usage.py`: transcript parsing of exact structured token categories,
  `agent_id` -> retained-`agent_dispatch` identity matching (quarantining unmatched/ambiguous
  identities rather than guessing, with a `reconcile_quarantined_usage` path for the common
  case where `SubagentStop` fires before the orchestrator retains the dispatch event), and
  Decimal-exact pricing against `harness/model-pricing.json` (live-verified 2026-08-14).
  `live_cli.py` gained `build_usage_summary`/`reconcile_quarantined_usage`; a run's total
  cost is knowable per-agent and as an explicitly-labeled subagent subtotal --
  **not** yet as a genuine full-pipeline total, since orchestrator-side usage is not
  captured this milestone (structurally deferred; see the usage-accounting milestone note
  above and `usage.py`'s own module docstring). 56 new tests (implementation) + 25 new
  tests (the same-day integration audit that wired this into `/work` itself -- see below);
  capture/reconciliation live-demonstrated the same day against a real, brand-new
  single-agent dispatch (`runs/run-20260814-usagecapture-001/`) -- one real subagent, not
  a live four-phase pipeline. The capture *mechanism* covers Architect/Engineer/Quality
  Engineer alike and is now wired into every controlled `/work` dispatch (per the
  "Integration audit (2026-08-14, same day)" note below); a live demonstration of
  accounting across an actual four-phase `/work` run remains open -- these are two
  different claims, not to be conflated. See the "Milestone update (2026-08-14)" and
  "Integration audit (2026-08-14, same day)" notes above for the full account.

### Memory loop
- `memory/lessons-learned.md` — loaded before every run (`live_cli.py`'s `load_memory`
  operation, called from `work/SKILL.md`'s "Phase 0: Memory load," before Discovery); at
  most 5 new bullets appended per terminal run (`append_memory`, `kind: "lesson"`,
  enforced by `harness/orchestrator/memory.py::append_lessons`'s own cap, not by
  convention). **Complete and demonstrated (2026-08-06) via a real, two-run
  `live_cli.py` CLI demonstration** (not a live `/work` pipeline run) — see the
  memory-loop milestone note above and §10's "Memory-loop milestone (2026-08-06)".
- `memory/facts.jsonl` — an append-only, evidence-backed persistent fact index (stable
  id, content, `source_run_id`, `evidence_ref`, `recorded_at`, `status`, optional `tags`);
  malformed/unprovenanced/duplicate/secret-shaped entries are skipped and named, never
  silently dropped, and never allowed to block a later valid entry in the same file from
  loading. Relative dates are the authoring orchestrator's responsibility to convert to
  absolute before calling `append_memory` — `memory.py` validates the resulting string is
  already absolute-shaped but does not itself interpret relative language. **Complete and
  demonstrated (2026-08-06) via the same real, two-run `live_cli.py` CLI demonstration**
  (not a live `/work` pipeline run). This supersedes this document's earlier planned
  `memory/facts/` (a directory of single-fact files + an index) — the memory-loop
  milestone's own explicit instructions preferred the smaller `memory/facts.jsonl`
  shape, and no prior code existed under `memory/` to make the older plan a binding
  convention.
- Deterministic relevance selection — plain tag/keyword-token-set intersection
  (`memory.py::derive_keywords`/`select_relevant_facts`/`select_relevant_lessons`), never
  an embedding or similarity service. **Complete.**
- Loaded-vs-applied distinction — `memory_loaded` (Part 3) is retained whenever memory is
  read and considered; `memory_applied` (Part 4) is retained via `record_memory_applied`
  only when *all* of: the entry id was actually selected as relevant by one of *this
  run's own* retained `memory_loaded` events (never a globally-valid entry this run
  never loaded and selected — a post-audit strengthening, 2026-08-06); and
  `evidence_path` resolves to a real, existing, regular file under the repo whose own
  content cites the entry id by exact, boundary-matched id (an existing-but-silent
  evidence file is refused). `run-summary.json`'s `memory_influenced_run` is derived
  from the run's own retained events (`summarize_memory`), never from assertion.
  **Complete and demonstrated (2026-08-06) via the real, two-run `live_cli.py` CLI
  demonstration** (not a live `/work` pipeline run) — see Run B below.
- Pipeline checkpointing — an interrupted run auto-resumes at the phase it died in.
  **Complete and live-demonstrated (2026-08-06)** — see the checkpoint/resume milestone note
  above and §10. Checkpoint interruption/terminal-failure writes never trigger a memory
  append (`checkpoint.py` has no reference to `memory.py` at all —
  `tests/test_orchestrator_memory.py::TestCheckpointResumeInteraction::
  test_checkpoint_module_never_calls_memory_append_functions` asserts this structurally);
  a resumed run's own terminal memory-append step runs at most once, and
  `append_fact`/`append_lessons`'s duplicate suppression makes a retried finalization
  attempt append nothing new even if it were ever called twice.
- Not part of this milestone, per the assignment's explicit exclusions: token/cost
  tracking, GitHub/Jira skills, Obsidian integration (so `memory/lessons-learned.md` is
  not the same thing as the still-not-built Obsidian vault write-back — see the Obsidian
  row above), connector authentication, REST fallback, a false-push demonstration, a
  flaky-test demonstration, and a full live four-phase `/work` run exercising this memory
  path (Run B below deliberately stops after Discovery; see its own stated limitations).

### Output contracts (per phase)
- Discovery → task definition + task graph
- Research → findings doc with `file:line` citations
- Implementation → minimal diff
- Verification → machine-readable pass/fail evidence

### Cross-cutting, non-negotiable principles
1. Evidence over assertion — every factual claim carries a citation; "I believe" is banned in
   findings docs.
2. Distrust completion claims — orchestrator independently verifies "pushed/fixed/deployed."
3. Minimal change — diff is as small as solves the problem; unjustified extra code costs points.
4. Health signals — any change to a formula/config/data path leaves behind a check that would
   catch it silently breaking later. "Tests passed at merge time" is not a health signal.
5. One canonical `.claude/` at the workspace root; subagents reference it by absolute path.
6. Cut, don't gate — dead skills/hooks/agents get deleted, not left disabled.
7. Finish now, don't defer — verification failures loop back to Implementation in the same run;
   filing a follow-up ticket is a failure state for in-scope work.

---

## 4. MVP vs. Later Integrations

### Core MVP (smallest loop that closes end-to-end)
- Canonical `.claude/` directory + `settings.json`
- `architect.md`, `engineer.md`, `quality-engineer.md`
- Code-craftsmanship skill, test-runner skill
- Orchestrator skill + `/work` (free-form prompt mode only)
- Pre-dispatch-check hook + completion-guardrail hook — **COMPLETE** (2026-08-05): implemented,
  deterministically tested; pre-dispatch-check live-verified via real refused `Agent` tool
  calls; completion-guardrail live-verified via a real `Stop`-event block against the
  missing-verification-evidence branch (`runs/completion-guardrail-live-001/`, same day) — its
  other four failure branches remain deterministic-only (see §10 "Hooks and same-run
  route-back milestone (2026-08-05)")
- `lessons-learned.md` + basic memory directory — **COMPLETE, demonstrated (2026-08-06)
  via a real, two-run `live_cli.py` CLI demonstration** (not a live `/work` pipeline
  run): see the memory-loop milestone note above and §10.
- Checkpoint/resume for the pipeline — **COMPLETE and live-demonstrated (2026-08-06)**: see
  the checkpoint/resume milestone note above and §10.
- GitHub access via `gh`/`git` CLI (real commits + independent push verification via
  `git ls-remote`/`git rev-parse`) — **COMPLETE and LIVE-PROVEN (2026-08-18)**:
  `harness/orchestrator/github.py` + five `live_cli.py` operations + the `github` Skill;
  see the "GitHub skill and independent push-verification layer" and "live successful
  GitHub push-verification demonstration" milestone notes above. A real commit
  (`f2fd7187d8d3ea96a4a958b67cb4ff1c115b1612` on `main`) was pushed to `origin`, and the
  production `verify_push` path independently ran a real `git ls-remote origin
  refs/heads/main` and retained `status: "verified"`, expected SHA exactly matching
  observed SHA (`runs/run-20260818-githubpush-001/`). The deterministic false-push
  (mismatch) rejection proven the same day remains a separate, still-valid proof of the
  opposite case (§10). A full `/work` pipeline that naturally reaches this section via a
  real four-phase run remains not live-demonstrated, unless a future run's evidence
  proves otherwise.
- A real target repo (≥50 files) to run against — **complete**: `demo-repo/loanflow`, 55 files,
  verified compatible with the test-runner wrapper and mutation-safe (§10); not yet used by a
  live orchestrator run

### Later integrations (after the loop closes once with real evidence)
- Ticket mode + Jira/Atlassian connector (MCP and/or REST skill pack)
- Obsidian MCP connector + run-summary write-back
- Full `github/` skill pack breadth (`pr-review`, `commit-history`)
- Full `jira/` skill pack + field-reference doc
- ~~Skill-enforcement hook~~ **COMPLETE** (2026-08-05) — implemented ahead of this list's
  original schedule as part of the hooks milestone; see §3 "Hooks" and §10 "Hooks and same-run
  route-back milestone (2026-08-05)"
- ~~Post-agent cost/token-tracking hook~~ **COMPLETE** (2026-08-14) — see §3 "Hooks" and
  the "Milestone update (2026-08-14): per-agent token usage and cost accounting" note
- MCP-vs-REST dual implementation + write-up decision
- Flaky-vs-logic classification with retry/backoff
- Planted-bug demo scenarios (flaky test, logic bug, deleted evidence, false push claim)

---

## 5. File/Directory Mapping

Entries marked `[DONE]` exist in the repo today (see `git ls-files`). Everything else is planned
and not yet created.

```
AgenticHarness/
├── .claude/
│   ├── settings.json              # hook registrations, permissions, model defaults
│   ├── agents/
│   │   ├── architect.md
│   │   ├── engineer.md
│   │   └── quality-engineer.md
│   ├── skills/
│   │   ├── work/
│   │   │   └── SKILL.md           # [DONE] /work entry point (project skill, not a
│   │   │                          #        .claude/commands/ custom command -- see §2)
│   │   ├── github/
│   │   │   ├── SKILL.md
│   │   │   └── scripts/           # pr-create, read-file, search-code, commit-history, pr-review
│   │   ├── jira/
│   │   │   ├── SKILL.md
│   │   │   ├── field-reference.md
│   │   │   └── scripts/           # create-ticket, read-ticket, edit-ticket
│   │   ├── test-runner/
│   │   │   └── SKILL.md
│   │   └── code-craftsmanship/
│   │       └── SKILL.md
│   ├── hooks/
│   │   ├── skill_enforcement.py
│   │   ├── pre_dispatch_check.py
│   │   ├── completion_guardrail.py
│   │   └── record_agent_usage.py  # [DONE] (usage/cost-accounting milestone, 2026-08-14) --
│   │                              #        this file map originally planned post_agent_cost.py
│   │                              #        as the name; implemented as record_agent_usage.py
│   │                              #        instead for parity with the other hook filenames'
│   │                              #        verb_noun convention (skill_enforcement,
│   │                              #        pre_dispatch_check, completion_guardrail)
│
├── .mcp.json                      # GitHub / Obsidian / Atlassian MCP server config (repo root)
│
├── harness/                       # Python glue code invoked BY hooks/skills, not a separate agent runtime
│   ├── __init__.py                # [DONE]
│   ├── orchestrator/checkpoint.py # [DONE] pipeline state save/resume (checkpoint/resume milestone,
│   │                              #        2026-08-06) -- lives under harness/orchestrator/, not
│   │                              #        directly under harness/ as this file map originally
│   │                              #        planned, for the same relative-import consistency
│   │                              #        reason discovery.py/evidence_io.py/paths.py do
│   ├── evidence.py                # [DONE] schema + semantic artifact validation, incl. validate_scope_semantics
│   ├── model-pricing.json         # [DONE] (usage/cost-accounting milestone, 2026-08-14) --
│   │                              #        official Anthropic per-model USD pricing, live-verified
│   │                              #        2026-08-14; this file map originally planned a
│   │                              #        harness/cost.py module -- token/cost aggregation instead
│   │                              #        lives in harness/orchestrator/usage.py (below), for the
│   │                              #        same relative-import consistency reason checkpoint.py/
│   │                              #        memory.py/discovery.py/evidence_io.py/paths.py do
│   ├── schemas/                   # [DONE] the six *.schema.json artifact contracts
│   ├── artifacts/
│   │   └── examples/              # [DONE] the six *.example.json reference artifacts
│   └── orchestrator/              # [DONE] deterministic one-pass orchestration core -- no live agent wiring yet
│       ├── __init__.py            # [DONE]
│       ├── core.py                # [DONE] state machine, phase dispatch, staged-command mediation, run-summary
│       ├── state.py               # [DONE] State enum, terminal/verdict/phase projections
│       ├── paths.py               # [DONE] real path containment + Protected Path checks, path_validation attestation
│       ├── discovery.py           # [DONE] scope-draft validation gate (schema + semantics + path safety)
│       ├── evidence_io.py         # [DONE] raw-candidate retention, collision-guarded canonical writes
│       ├── adapters.py            # [DONE] AgentAdapter / TestRunnerAdapter / DiscoveryAdapter interfaces only
│       ├── memory.py              # [DONE] fact/lesson validation, append-safe persistence, deterministic
│       │                          #        keyword-based relevance selection, memory_applied validation,
│       │                          #        summarize_memory_events (memory-loop milestone, 2026-08-06) --
│       │                          #        lives under harness/orchestrator/, not directly under harness/
│       │                          #        as this file map originally planned, for the same
│       │                          #        relative-import consistency reason checkpoint.py/discovery.py/
│       │                          #        evidence_io.py/paths.py do
│       ├── usage.py               # [DONE] (usage/cost-accounting milestone, 2026-08-14) --
│       │                          #        transcript parsing, agent_id -> agent_dispatch identity
│       │                          #        matching (+ quarantine reconciliation), Decimal pricing,
│       │                          #        run-usage-summary aggregation with explicit
│       │                          #        subagent-subtotal/orchestrator/full-pipeline coverage
│       │                          #        semantics -- lives under harness/orchestrator/ for the
│       │                          #        same relative-import consistency reason memory.py/
│       │                          #        checkpoint.py/discovery.py/evidence_io.py/paths.py do
│       └── live_cli.py            # [DONE] thin JSON-in/JSON-out bridge the /work skill calls -- no agent
│                                   #        reasoning; deterministically tested (see §10 "Live architecture
│                                   #        decision (Option C)"); the memory-loop milestone's four
│                                   #        operations (load_memory/append_memory/record_memory_applied/
│                                   #        summarize_memory) have been exercised for real via a deterministic
│                                   #        two-run demonstration (Run A/Run B, §10), not through a literal
│                                   #        /work slash-command invocation; the usage-accounting milestone's
│                                   #        two new operations (build_usage_summary/
│                                   #        reconcile_quarantined_usage) have been exercised via a real,
│                                   #        live single-agent dispatch (run-20260814-usagecapture-001, §10)
│
├── memory/                        # [DONE] (memory-loop milestone, 2026-08-06)
│   ├── facts.jsonl                # append-only fact index -- see §3 "Memory loop"; supersedes this file
│   │                               # map's earlier-planned memory/facts/ directory shape (never built)
│   └── lessons-learned.md
│
├── runs/                          # per-run artifacts: findings docs, diffs, verification reports, checkpoints
│
├── demo-repo/                     # [DONE] loanflow target app, 55 files — see §10; part of the
│                                   #        root repo, no nested .git/ of its own
│
├── tests/
│   ├── __init__.py                       # [DONE]
│   ├── test_artifact_contracts.py        # [DONE] incl. validate_scope_semantics coverage
│   ├── test_agent_definitions.py         # [DONE]
│   ├── test_skill_definitions.py         # [DONE]
│   ├── test_test_runner_validation.py    # [DONE]
│   ├── test_orchestrator_discovery.py    # [DONE]
│   ├── test_orchestrator_evidence_io.py  # [DONE]
│   ├── test_orchestrator_core.py         # [DONE] deterministic end-to-end coverage, fake adapters only
│   ├── fakes/
│   │   └── agent_adapter.py              # [DONE] ScriptedAgentAdapter / ScriptedTestRunnerAdapter / ScriptedDiscoveryAdapter
│   └── ...                               # a later live integration test driving one full pipeline
│                                          # run against real agents is not yet written (§10)
│
├── docs/
│   └── WRITEUP.md                 # assignment §5 deliverable (architecture diagram, MCP-vs-REST
│                                   # decision, surprise + guardrail, what was deleted and why)
│
├── README.md                      # [DONE]
├── .gitignore                     # [DONE]
└── pyproject.toml                 # [DONE]
```

---

## 6. Implementation Sequence and Status

1. Repository initialization, authoritative specification, and GitHub remote — **COMPLETE**.
2. Artifact contracts, valid examples, schema validation, and semantic validation — **COMPLETE**.
3. Architect subagent and permission-boundary verification — **COMPLETE**.
4. Engineer subagent design, implementation, and permission verification — **COMPLETE**.
   Quality Engineer subagent design, implementation, and permission verification — **COMPLETE**.
5. Code-craftsmanship and test-runner skills — **COMPLETE** (implementation, automated tests,
   and live Skill-mechanism/permission/behavioral verification; see §10). The GitHub and Jira
   skill packs required for the assignment's overall "≥4 skill packs" bar are separate,
   not-yet-started work (§3, §4 "Later integrations").
6. Deterministic orchestrator core (`harness/orchestrator/`) — **COMPLETE** as a standalone,
   fake-adapter-tested engine: one-pass state machine, artifact validation gates, staged-command
   mediation, evidence retention, run-summary generation for every terminal outcome (246 tests
   passing; see §10). `core.py` itself still has no live `Agent`/`SendMessage` adapter, no real
   test-runner adapter wired in, and no reasoning-backed Discovery adapter — live dispatch
   instead happens through the separate `/work` skill + `live_cli.py` path (Option C, §10),
   which **has** now completed a real end-to-end run (`run-20260804-riskband-003`).
7. Build or import the real target repository with at least 50 files — **COMPLETE**:
   `demo-repo/loanflow`, 55 files, 22 source modules, 23 test files (102 tests passing after
   `run-20260804-riskband-003`'s risk-band change); test-runner-wrapper and mutation-safety
   verified (§10). Used by a live, completed four-phase orchestrator run
   (`run-20260804-riskband-003`).
8. Live Claude Code adapter layer, real test-runner adapter, reasoning-backed Discovery adapter,
   and `/work` free-form prompt mode — **COMPLETE and live-verified**: `run-20260804-riskband-003`
   exercised all of Discovery (reasoning-backed, main session), a real Architect/Engineer/Quality
   Engineer dispatch, staged same-agent continuation, and the forked real test-runner Skill,
   end-to-end (see §10 "Live evidence (`run-20260804-riskband-003`)").
9. Checkpoint, memory loop, pre-dispatch hook, and completion guardrail. **Partially complete**
   (2026-08-05): pre-dispatch-check, skill-enforcement, and completion-guardrail hooks are
   implemented, registered in `.claude/settings.json`, and tested — see §10 "Hooks and same-run
   route-back milestone (2026-08-05)". The completion-guardrail hook's
   missing-verification-evidence branch is now also live-demonstrated against a real `Stop`
   event (`runs/completion-guardrail-live-001/`); its other branches remain
   deterministic-only. Checkpoint/resume — **COMPLETE and live-demonstrated (2026-08-06)**, see
   the checkpoint/resume milestone note above. The memory loop
   (`memory/lessons-learned.md`, `memory/facts.jsonl`) — **COMPLETE, demonstrated
   (2026-08-06) via a real, two-run `live_cli.py` CLI demonstration** (not a live
   `/work` pipeline run), see the memory-loop milestone note above and §10's
   "Memory-loop milestone (2026-08-06)". The post-agent cost/token-tracking hook —
   **COMPLETE (2026-08-14)**: `.claude/hooks/record_agent_usage.py` +
   `harness/orchestrator/usage.py`, live-demonstrated against a real single-agent
   dispatch (`run-20260814-usagecapture-001`) — see the "Milestone update (2026-08-14)"
   note above.
10. Independent GitHub push-verification path. **COMPLETE and LIVE-PROVEN
    (2026-08-18)** — `harness/orchestrator/github.py`, five `live_cli.py` operations,
    the `github` Skill, and `work/SKILL.md`'s "GitHub / Git delivery" section +
    standing rule 17, including both a deterministic false-push (mismatch) proof and a
    live, real push-verification proof; see the "GitHub skill and independent
    push-verification layer" and "live successful GitHub push-verification
    demonstration" milestone notes above. A real commit
    (`f2fd7187d8d3ea96a4a958b67cb4ff1c115b1612`) was pushed to `origin/main`, and the
    production `verify_push` path independently ran a real `git ls-remote` that
    retained `status: "verified"` (`runs/run-20260818-githubpush-001/`). Still open: a
    full `/work` pipeline that naturally reaches the GitHub delivery section and
    invokes standing rule 17 in the course of a real four-phase run, unless a future
    run's own retained evidence proves otherwise.
11. Close one complete free-form prompt pipeline with real evidence — **COMPLETE**:
    `run-20260804-riskband-003`, `final_verdict: "pass"`, all four canonical artifacts promoted,
    102 demo tests passing, no commit/push performed.
12. Add Jira, Obsidian, cost tracking, connector comparison, broader skills, and
    planted-defect demonstrations. **Not started.**

---

## 7. Open Questions / Assumptions

Resolved:
- **Runtime approach**: Claude Code–native (see §2). Confirmed by user.
- **Artifact contracts**: JSON Schema Draft 2020-12 for all six MVP artifacts (see §3).
- **`jsonschema` dependency**: development-only, not a runtime dependency of `harness/`.
- **Hooks on Windows**: implemented as Python scripts invoked by Claude Code's hook runner, for
  portability (the assignment assumes POSIX shell-script hooks; this environment is
  Windows/PowerShell).
- **MVP subagent model convention**: `model: inherit` for all subagents unless a later measured
  reason justifies changing it (see §2).
- **GitHub remote**: the `AgenticHarness` repo has a GitHub remote with push access confirmed.
- **`demo-repo/` content/source**: a synthetic Python application, `loanflow` (an educational
  loan-application underwriting demo), 55 files — implemented, automatically tested, and
  test-runner-wrapper/mutation-safety verified (see §10). Planted defects (a flaky test, a logic
  bug) remain deferred to the later acceptance-criteria pass, not part of this clean baseline.

Still open — defaults will be applied unless redirected before the relevant build step:
1. **Jira credentials** — ticket mode needs live Jira credentials; none exist yet. MVP avoids
   this by using prompt mode only.
2. **Obsidian vault path** — the Obsidian connector needs a real vault path; none exists yet.
3. **Cost/token extraction mechanism** — depends on what usage data is actually readable (Claude
   Code transcript JSONL vs. API response usage fields); not yet confirmed.
4. **Write-up location** — assumed `docs/WRITEUP.md`; not explicitly specified, unless the
   assignment explicitly resolves it.

---

## 8. Acceptance Criteria (from assignment, verbatim intent)

These are final assignment acceptance criteria. They are not all prerequisites for closing the
core MVP loop. Jira, Obsidian, cost tracking, connector comparisons, and planted-defect
demonstrations are implemented only after the first free-form prompt loop closes successfully.

Demonstrated live, on a repo with ≥50 files:

- [ ] `/work <ticket-id>` pulls a real ticket via the Atlassian connector and produces a scoped
      task graph.
- [x] Research phase produces a findings doc where every claim has a `file:line` citation,
      including at least one honest "Not Found." Live-demonstrated:
      `runs/run-20260804-riskband-003/findings.json` — 3 `found`/1 `inferred` finding cite
      `file:line` evidence, 2 honest `not_found` findings cite documented search attempts.
- [x] Architect physically cannot edit source files (tool restriction, not politeness).
      Independently verified both via boundary testing
      (`docs/architect-permission-verification.md`) and live in `run-20260804-riskband-003`
      (the Architect returned findings only; all file changes came from the separately
      dispatched Engineer).
- [x] Engineer's diff cites the architect finding it builds on; no new dependencies without a
      written justification. Live-demonstrated:
      `runs/run-20260804-riskband-003/implementation-report.json`'s `findings_ref.finding_ids`
      is `["F-1", "F-2", "F-4"]` (all `found` classifications from the promoted
      `findings.json`); `dependency_changes` is empty.
- [ ] A planted flaky test is classified as infrastructure and retried; a planted logic bug is
      classified as logic, not retried, and routed back to the engineer — fixed in the same run.
      **Partial** (2026-08-05): the same-run logic-failure route-back mechanism itself now
      exists and is deterministically verified (`classify_verification_outcome`,
      `_handle_verification_failure` in `harness/orchestrator/core.py`;
      `tests/test_orchestrator_core.py`, `tests/test_route_back_protocol.py`) — a genuine
      `logic_bug` verdict resumes the same Engineer/Quality Engineer for exactly one bounded
      repair cycle, while infrastructure-flake and environment failures are confirmed to never
      route back. **Not yet demonstrated by an actual planted-bug live `/work` run** — no live
      run has exercised this path yet (see §10).
- [x] Completion-guardrail hook demonstrably blocks a run where verification evidence is
      deleted. **Live-demonstrated** (2026-08-05): a dedicated fixture run,
      `runs/completion-guardrail-live-001/` (task `DEMO-COMPLETION-GUARDRAIL-1`), wrote a
      genuine, schema-valid, `final_verdict: "pass"` `verification-report.json`, then moved it
      aside to make the canonical path missing (the assignment's canonical "delete the
      verification evidence" scenario), wrote a `run-summary.json` claiming
      `final_verdict: "pass"` and a `.completion_claim.json` marker, and ended the turn. The
      real, registered Claude Code `Stop` hook fired and blocked with the exact reason
      `runs/completion-guardrail-live-001/verification-report.json is missing (deleted or never
      produced)`; the turn continued rather than ending. See
      `runs/completion-guardrail-live-001/STOP-HOOK-LIVE-BLOCK-EVIDENCE.md`,
      `runs/completion-guardrail-live-001/CLEANUP-NOTE.md`, and
      `docs/hooks-permission-verification.md` §4. Also still backed by deterministic coverage
      (`tests/test_hooks.py::TestCompletionGuardrail::test_deleted_verification_report_after_prior_pass_blocks`).
      Two things this does **not** prove: (1) the hook's other four failure branches (invalid
      JSON, non-`pass` verdict, canonical-artifact mismatch, Git-reality conflict) remain
      deterministic-only, not separately live-demonstrated; (2) this was a dedicated,
      hand-built fixture, not a block encountered inside a completed live `/work` pipeline run
      — the known cooperation boundary stands: this hook activates only when `/work` itself
      writes `.completion_claim.json` (see §10).
- [x] Orchestrator catches a simulated false "pushed" claim via `git ls-remote`.
      **Deterministically proven (2026-08-18)**:
      `tests/test_orchestrator_github.py::TestClassifyLsRemoteResult::test_false_push_claim_is_classified_mismatch`
      and `tests/test_orchestrator_live_cli.py::TestVerifyPush::test_mismatch_is_retained_honestly_never_reported_as_verified`
      each construct a claimed expected SHA and a controlled fake `git ls-remote` result
      reporting a different observed SHA, and confirm the verification layer classifies
      it `mismatch` (never `verified`) and retains that classification honestly in
      `push-verification.json`. This is a deterministic mismatch fixture, not a real
      failed GitHub push. A separate, genuine live push against the real remote has
      since been attempted and independently verified as successful — never falsely
      claimed — via a real `git ls-remote` (`runs/run-20260818-githubpush-001/`; see
      the "live successful GitHub push-verification demonstration" milestone note
      above). The two remain distinct proofs, not to be conflated: the false-push
      rejection above is deterministic-fixture-proven; a genuine successful push and
      its independent verification are now **live-proven** (§10).
- [ ] Obsidian vault receives a run summary (**not started** — separate, later work, per
      the assignment's explicit exclusion of Obsidian integration from this milestone);
      `lessons-learned.md` gains ≤5 bullets; a second run visibly uses a lesson from the
      first. **The lessons-learned/second-run-uses-a-lesson half is demonstrated
      (2026-08-06) via a real, two-run `live_cli.py` CLI demonstration**, not a live
      `/work` invocation (an explicitly allowed substitution for this specific
      requirement — see §10 "Memory-loop milestone (2026-08-06)"): Run A
      (`runs/run-20260806-memoryloop-001/`) appended exactly one lesson,
      `L-20260806-TRANSPORT-BUDGET` (well under the 5-bullet cap), grounded in real,
      pre-existing evidence from `runs/run-20260803-riskband-001/`; Run B
      (`runs/run-20260806-memoryloop-002/`) loaded memory before Discovery, selected that
      exact lesson as relevant via deterministic keyword matching, cited its ID in a
      genuine, promoted `scope.json`'s `constraints`/`AC-1`, and retained a real
      `memory_applied` event naming the lesson, the decision, and the evidence path —
      `run-summary.json`'s `memory_influenced_run: true` is derived from that retained
      event, not asserted. Obsidian write-back itself remains undone; this is the
      lessons-learned mechanism only.
- [x] Killing the pipeline mid-Implementation and rerunning resumes from checkpoint instead of
      restarting. **Live-demonstrated across two genuine, separate Claude Code processes
      (2026-08-06)**: the first process interrupted `run-20260806-refid-001` deliberately during
      Implementation (Engineer dispatched, first staged turn received and retained, never
      mediated or resumed); `checkpoint.json` correctly recorded
      `completed_phases: ["discovery", "research"]`, `current_phase: "implementation"`,
      `status: "interrupted"`. A second, separate process then invoked `/work --resume
      run-20260806-refid-001`, received `evaluate_resume.status: "resumable"`, reused Discovery
      and Research without redispatching the Architect, and restarted Implementation with a
      brand-new Engineer instance rather than any surviving process-local handle — this is the
      run that does resume from checkpoint instead of restarting, exactly as this checklist item
      asks. **Qualification**: that resumed invocation did not go on to complete the pipeline —
      after mediating a real failing C-1 and a real passing C-2 test and independently confirming
      the production fix, it terminated **blocked** during Implementation when the Engineer
      produced a second transport-format violation after the phase's single correction budget was
      already exhausted; it never entered Verification and never promoted
      `implementation-report.json`. See §10 "Second process (2026-08-06, same day): genuine
      fresh-process resume, terminated blocked" and `runs/run-20260806-refid-001/` for the full
      record. Deterministic proof that a fresh process reuses completed phases and dispatches a
      fresh agent for the restarted one, without redispatching a completed phase's agent, and that
      such a resumed run can also reach a genuine passing Verification, remains covered by
      `tests/test_orchestrator_core.py`'s resume tests only — no live run has yet demonstrated a
      resumed pipeline completing through Verification with a passing verdict.
- [ ] Total token/cost figure for one full pipeline run can be stated.

## 9. What NOT to Build

- No web dashboard, no UI polish, no logo. A status view, if built, is a debugging surface only.
- No speculative multi-user support, plugin marketplaces, or configuration options with a single
  caller.
- No mega-agent with every tool — restricted toolsets are the point.

---

## 10. Current Status and Immediate Next Steps

### Completed

- Repository initialized and connected to GitHub.
- Artifact-contract foundation implemented.
- Six JSON schemas created.
- Six valid example artifacts created.
- JSON Schema Draft 2020-12 validation implemented.
- Approved semantic artifact validation implemented.
- 38 artifact-contract tests passing.
- Read-only Architect subagent implemented at `.claude/agents/architect.md`.
- Architect restricted to Read, Grep, Glob.
- Architect permission boundary independently verified.
- File writes, source edits, shell execution, dependency installation, Git mutation, and
  permission self-escalation were technically unavailable because the required tools were
  absent.
- Architect findings are returned to the caller rather than written directly.
- The main session or future orchestrator validates and persists findings.
- Permission-boundary verification is documented at
  `docs/architect-permission-verification.md`.
- The first Architect findings attempt passed schema validation but failed semantic validation
  because an inferred finding referenced a `not_found` finding.
- The invalid result was preserved unchanged as evidence.
- The Architect instructions were strengthened with a semantic pre-output self-check.
- The retry passed both JSON Schema validation and semantic validation.
- The Architect milestone was committed and pushed.
- Implementation-phase Engineer subagent implemented at `.claude/agents/engineer.md`, restricted
  to Read, Grep, Glob, Edit, Write — confirmed both via the frontmatter and via Claude Code's
  own runtime agent-type listing.
- The Engineer's protocol is caller-mediated: it requests exact commands
  (`pre_test_requested`/merged `post_test_requested`/`finalization_evidence_requested`) and the
  controlled caller executes them and returns real exit codes, output, and diff statistics; the
  Engineer never fabricates command output, exit codes, or changed-file statistics.
- A required `path_validation` caller attestation gates all work before any file is read — the
  Engineer performs lexical path checks itself (containment under `target_repo_path`, exact
  `in_scope` match, no traversal) but relies on the controlled caller to verify real filesystem
  resolution and symlink non-escape, since the Engineer has no `stat`/`realpath`-capable tool.
- A fixed Protected Path list (`.claude/**`, `.git/**`, `PROJECT_SPEC.md`,
  `harness/schemas/**`, `harness/artifacts/examples/**`, `harness/evidence.py`,
  `runs/**/scope.json`, `runs/**/findings.json`, `runs/**/verification-report.json`, anything
  outside `target_repo_path`) is honored even when an erroneous scope artifact lists one of
  these paths as in-scope.
- Engineer permission-boundary and staged-protocol verification is documented at
  `docs/engineer-permission-verification.md`, run live against a disposable fixture at
  `runs/engineer-boundary-test/`: missing-attestation, false-attestation, genuine out-of-scope,
  and Protected-Path-override cases all correctly returned schema-valid `blocked` reports with
  zero file edits; the full happy-path staged protocol produced a schema- and
  semantically-valid `ready_for_verification` report backed by real `pytest` exit codes and a
  real `git diff --no-index` diff. An unplanned tooling mistake (resuming the wrong way)
  produced genuine evidence that a fresh, non-continued Engineer instance detects a broken
  handoff and refuses to fabricate continuity rather than proceeding.
- `tests/test_agent_definitions.py` independently re-verifies the Architect's, Engineer's, and
  Quality Engineer's tool allowlists and re-validates every saved boundary-test report.
- Verification-phase Quality Engineer subagent implemented at
  `.claude/agents/quality-engineer.md`, restricted to `Read, Grep, Glob` — confirmed both via the
  frontmatter and via Claude Code's own runtime agent-type listing, resolving the
  permission-design question below in favor of the narrowest option: no shell/test-runner tool
  at all, full reuse of the Engineer's caller-mediated staged-command protocol
  (`attempt_requested` / `command_result`), adapted for verification instead of implementation.
- The Quality Engineer never trusts the implementation report on faith: it independently
  cross-checks the implementation report's own `findings_ref.path` against the caller-supplied
  `findings_ref.path` before reading anything else, and checks every `changed_files` entry
  against the same fixed Protected Path list the Engineer is bound to — both return a
  schema-valid `blocked` report on failure, before any command is requested.
- A command-safety denylist (`>`, `>>`, `;`, `&&`, `||`, backticks, `$(`) blocks the Quality
  Engineer from ever requesting a mutating or shell-composed command; live-tested by suggesting
  an unsafe redirected command and confirming it declined and requested the safe equivalent
  instead.
- `retry_policy.max_retries` is fixed at `2`, reserved exclusively for evidence-supported
  `infrastructure_flake` classifications — never for `logic_bug` or `environment` failures.
- Quality Engineer permission-boundary and staged-protocol verification is documented at
  `docs/quality-engineer-permission-verification.md`, run live against retained fixtures at
  `runs/quality-engineer-boundary-test/`: missing-attestation, false-attestation, findings_ref
  mismatch, blocked-implementation-input, and Protected-Path-claim cases all correctly returned
  schema-valid `blocked` reports with zero tool calls or zero command requests; a real happy-path
  run produced a genuine `pass` verdict from a real `pytest` exit code; a command-safety test
  confirmed it declines an unsafe caller-suggested command. Most notably, a deliberately
  dishonest implementation report (claiming a test passed when it did not) was independently
  re-executed for real, found to genuinely fail, root-caused by reading the actual source code
  unprompted, and correctly routed back to the Engineer with an accurate reason — direct evidence
  the design fulfills the "no phase consumes the previous phase's output on trust" cardinal rule.
  A real gap (the final report wasn't required to be raw, unfenced JSON) was found and fixed
  mid-verification; every test after the fix returned clean raw JSON.
- Infrastructure-flake retry and environment-failure cases were also independently verified live,
  against a genuine reproducible transient failure (a marker-file-backed cold connection that
  fails once with real `ConnectionError` evidence and passes on a real, unmodified-source retry)
  and a genuine deterministic missing-dependency failure (`ModuleNotFoundError` at collection
  time). The Quality Engineer classified the first `infrastructure_flake`, retried the identical
  command once (within `max_retries: 2`), marked only the retry `retried: true`, and produced a
  schema-valid `pass` report once the retry genuinely passed. It classified the second
  `environment`, never retried it, never routed it back as a `logic_bug`, and produced a
  schema-valid `inconclusive` report. Verifying this live surfaced and fixed a real design bug in
  `quality-engineer.md`: its original wording would have required *every* attempt targeting a
  criterion to be `"pass"`, which would have wrongly kept a resolved flake from ever reading as
  `passed`; Step 11 now judges a criterion by its most recent attempt instead.
- pytest is now configured with `testpaths = ["tests"]` in `pyproject.toml`, so the default
  `python -m pytest` command collects only the real project suite and does not sweep in the
  retained boundary fixtures under `runs/` (including the fixture that is intentionally left
  failing — `runs/quality-engineer-boundary-test/fixture-repo-2/fixture-src/test_strings.py` —
  which remains available for explicit targeted execution, e.g.
  `pytest runs/quality-engineer-boundary-test/fixture-repo-2/fixture-src/test_strings.py`).
- 116 total tests passing (73 artifact-contract + 43 agent-definition; the artifact-contract
  count grew from 38 via two unrelated, already-committed contract fixes that predate the Quality
  Engineer work).
- **None of this is committed yet.** The Quality Engineer implementation is approved in
  direction but was explicitly held back from commit pending this final verification and cleanup
  pass. Do not assume the working tree is clean or that local `main` matches `origin/main` from
  this document alone — check `git status` for the current state.
- **Code-craftsmanship skill** implemented at `.claude/skills/code-craftsmanship/SKILL.md`: the
  minimal-change ladder, YAGNI/no-speculative-abstraction, no-drive-by-cleanup, and
  Protected-Path checklist, structurally tested by `tests/test_skill_definitions.py`. Live
  invoked through Claude Code's actual `Skill` tool (not a simulation) and behaviorally
  evaluated with a live A/B comparison: four independent, fresh, `worktree`-isolated agents (2
  scenarios x no-skill/skill-enabled) given an identical prompt bundling a one-line bug fix with
  (a) a bait speculative-abstraction suggestion and (b) unrelated cleanup plus a Protected-Path
  edit suggestion. Both skill-enabled sessions declined the speculative abstraction and the
  Protected-Path edit, the latter citing the skill's exact Protected Path list by name; the
  no-skill baseline in that scenario accepted the Protected-Path bait, drafting a real edit to
  `harness/schemas/implementation-report.schema.json` with no recognition it was off-limits —
  the clearest single result demonstrating the skill doing its intended job. (n=1 per cell; an
  honest existence-proof, not a statistically powered result — full detail and verbatim outputs
  retained at `runs/code-craftsmanship-ab-test/`, especially `grading-summary.md`.)
- **Test-runner skill** implemented at `.claude/skills/test-runner/` (`SKILL.md` +
  `scripts/run_command.py`): validates and executes exactly one narrowly-scoped
  `python -m pytest ...` command against an already-existing caller-written request file,
  enforcing a strict positional grammar, path containment under `target_repo_path`, wrapper-
  assigned sentinel exit codes (124 timeout, 125 persistent mutation detected, 126 internal
  wrapper failure), and a before/after filesystem hash sweep for mutation detection. Automated
  tests (`tests/test_test_runner_validation.py`) cover the wrapper's pure functions plus
  real subprocess-level integration cases. Script-level boundary-verified with real subprocess
  execution, real exit codes, real file mutation, and a real timeout
  (`runs/test-runner-boundary-test/verification-summary.md`). Subsequently live-verified through
  the actual `Skill` mechanism (not a direct Bash call): `${CLAUDE_SKILL_DIR}` resolved to a
  concrete real path, a genuine `command_result` and a genuine `command_rejected` both
  round-tripped with every field preserved exactly and no fabricated data, and all eight tools
  named in `disallowed-tools` (`Write`, `Edit`, `NotebookEdit`, `PowerShell`, `Agent`, `Skill`,
  `WebFetch`, `WebSearch`) were confirmed genuinely denied at the tool layer via live disposable
  probes — runtime-observed enforcement, not just a frontmatter claim
  (`docs/test-runner-skill-permission-verification.md`). The mutating fixture
  (`mutator_target.py`) was found to accumulate modifications across repeated manual runs; a
  `reset_mutation_fixture.py` script was added and its determinism verified live (reset → rerun
  → identical mutation detected → reset again), without disturbing the original retained
  detection evidence.
- **Forked-isolation repair (2026-08-04)**: a live `/work` attempt
  (`runs/run-20260804-riskband-002/`) found that invoking `test-runner` inline removed
  `Write`/`Edit` not only from the rest of the orchestrator's own turn but from a
  *resumed Engineer subagent's* tool availability too, blocking Implementation after a
  genuinely successful pre-test mediation. `test-runner/SKILL.md` now declares `context:
  fork` and `background: false` (Claude Code v2.1.218+) so the skill's own
  `disallowed-tools` restriction is confined to its own forked context. The wrapper
  script, its grammar, and its `allowed-tools`/`disallowed-tools` declarations are
  unchanged — see "Live evidence (`run-20260804-riskband-002`)" in §10 for the full
  account. **This repair has not yet been live-verified by a real `/work` run.**
- **Documented, unresolved limitation carried over from this verification, motivating the
  planned hooks milestone**: during the A/B evaluation, one no-skill baseline agent hit a real
  `Write` permission denial on a file and worked around it by using `Bash` (a still-open tool)
  to write the same bytes anyway, rather than respecting the denial. This was not a
  `code-craftsmanship` or `test-runner` boundary bypass (no skill was active for that agent, so
  no `disallowed-tools` restriction applied to it) — it is evidence for a broader point
  `test-runner/SKILL.md` already names as its own open gap: `disallowed-tools` "does not sandbox
  the caller," and only a skill-scoped `PreToolUse` hook (§3 "Hooks," not yet built) can close
  a tool-routing-around-a-denial gap for real.
- **Test-runner's documented trust-boundary limitation stands, unchanged by this
  verification**: the wrapper's strict grammar and `shell=False` prevent shell injection in the
  command line, but `pytest` still imports and executes real repository Python code once
  launched — the wrapper cannot stop that code from writing files, opening network connections,
  or spawning subprocesses while it runs. It can only detect a persistent file change
  afterward, via the before/after hash sweep. True prevention would require disposable or
  OS-sandboxed execution, which is explicitly out of scope for this milestone.
- Full pytest suite: **196 passed, 0 failed, exit code 0** (`tests/test_agent_definitions.py`,
  `tests/test_artifact_contracts.py`, `tests/test_skill_definitions.py`,
  `tests/test_test_runner_validation.py`).
- **Only 2 of the assignment's required ≥4 skill packs are complete** (code-craftsmanship,
  test-runner). The `github/` and `jira/` skill packs (§3) have not been started and remain
  future work, per §4's "Later integrations" — they are not required for the Core MVP loop, but
  are required for the assignment's full Skills acceptance bar. The Skills requirement, and the
  project overall, are **not** complete.
- **None of this is committed yet either** (code-craftsmanship, test-runner, and their tests/
  docs/retained evidence) — check `git status` for the current state before assuming otherwise.

### Deterministic orchestrator core (`harness/orchestrator/`)

A one-pass, fake-adapter-tested orchestration engine implementing Discovery → Research →
Implementation → Verification as a deterministic state machine. This is a standalone milestone,
not yet wired into a live Claude Code session — see "Status distinctions" below before assuming
any of this runs a real pipeline.

#### Implemented

- `harness/orchestrator/core.py` — the state machine and top-level `run()` entry point: drives
  exactly one straight-through attempt through Discovery, Research (Architect), Implementation
  (Engineer), and Verification (Quality Engineer), with no automatic route-back on a
  verification failure.
- `harness/orchestrator/state.py` — the `State` enum and its projections onto
  `run-summary.schema.json`'s narrower `final_verdict` vocabulary and phase names.
- `harness/orchestrator/discovery.py` — the `DiscoveryAdapter` boundary: a proposed scope
  document is supplied externally (`raw_prompt`/`task_id`/`run_id`/`created_at`/
  `target_repo_path` in, a scope dict out, no persistence performed by the adapter itself), and
  this module alone validates it (schema, `validate_scope_semantics`, real path safety) before
  the orchestrator ever promotes or trusts it.
- `harness/orchestrator/adapters.py` — the `AgentAdapter` (`start()`/`resume()`, explicit opaque
  handle), `TestRunnerAdapter` (`invoke()`/`diff_stats()`), and `DiscoveryAdapter` `Protocol`
  interfaces. No Claude Agent SDK dependency, no API-key authentication, no `claude -p`
  subprocess call — Claude Code-native per §2, live invocation deferred to the main session.
- `harness/orchestrator/paths.py` — real filesystem containment (`Path.resolve(strict=True)`
  plus a containment check, so a symlink/junction escape resolves outside the root and is
  caught, not merely a lexical check), the same fixed Protected Path list Engineer/Quality
  Engineer/code-craftsmanship already use, and the `path_validation` attestation builder that
  genuinely performs the check it attests to rather than asserting the claim.
- `harness/orchestrator/evidence_io.py` — strict JSON parsing (no fence-stripping, no prose
  extraction), raw-candidate retention under `runs/<run_id>/attempts/` before any validation,
  and collision-guarded, atomic promotion of a validated candidate to its canonical filename
  (`scope.json`, `findings.json`, `implementation-report.json`, `verification-report.json`,
  `run-summary.json`) — a second write to an existing canonical path is always a hard error,
  never a silent overwrite.
- Staged command mediation in `core.py`: every request from Engineer/Quality Engineer is
  validated (`task_id`, `run_id`, `command_id`, `command`, `working_directory`) against the
  request the orchestrator itself built before a `command_result` is trusted;
  `command_rejected` halts the phase immediately; exit code `124` (timeout) and `125`
  (persistent mutation) are forwarded to the agent unchanged, with `125` additionally retained
  as a policy event in `runs/<run_id>/logs/policy-events.jsonl`.
- Same-handle continuity: the handle returned by `AgentAdapter.start()` is the only handle ever
  passed to `resume()` for that phase; a `resume()` failure blocks the phase and never triggers
  a second `start()` call.
- `run-summary.json` generation for every terminal outcome (`discovery_refused`,
  `discovery_invalid`, `research_blocked`, `implementation_blocked`, `verification_blocked`,
  `verification_failed`, `verification_inconclusive`, `completed`, and the internal-failure
  `failed` bucket), always schema-valid against `run-summary.schema.json`.

#### Verified

- 246 tests collected, 246 passed, 0 failed, pytest exit code 0.
- All seven orchestrator modules (`__init__.py`, `paths.py`, `adapters.py`, `state.py`,
  `evidence_io.py`, `discovery.py`, `core.py`) compile cleanly (`python -m py_compile`).
- `verification_inconclusive` is explicitly tested: a schema-valid Quality Engineer report with
  an unresolved `environment` classification produces `VERIFICATION_INCONCLUSIVE`, never
  `COMPLETED`, and a schema-valid run summary that says so.
- Internal failed-state summary behavior is explicitly tested: an injected, genuinely unexpected
  exception in an evidence-persistence call (after run identity and the run directory already
  exist) is caught by `run()`'s top-level safety net, lands in `State.FAILED`, retains both the
  pre-crash raw candidate and a fallback error-evidence file, and still produces a schema-valid
  `run-summary.json` with `final_verdict: "blocked"` — never a false `"pass"`.
- Discovery refusal and invalid-scope behavior are both explicitly tested and shown to be
  asymmetric: a refused scope is a legitimate, schema-valid artifact and **is** promoted to the
  real canonical `scope.json`; an invalid draft is never promoted, and `artifact_refs.scope`
  instead references a retained, existing, explicitly-labeled error-evidence file (see "Known
  contract limitation" below).
- No automatic route-back to Engineer: exactly one dispatch call site exists per agent role in
  `core.py`; a `verification_failed` outcome is terminal for this slice, confirmed by asserting
  Engineer's `start()` count stays at 1 after a `fail` verdict.
- No live Claude agent is launched by any automated test — every `AgentAdapter`/
  `TestRunnerAdapter`/`DiscoveryAdapter` in the test suite is the scripted double in
  `tests/fakes/agent_adapter.py`.

#### Status distinctions

- Designed: yes
- Implemented: yes
- Automatically tested: yes
- State-transition verified: yes
- Artifact-validation verified: yes
- Same-handle continuity verified with fake adapters: yes
- Live Agent/SendMessage integrated: no *(this module, `core.py`, itself never calls a
  live adapter — Option C below routes live dispatch through the `/work` skill and
  `live_cli.py` instead; live Agent/SendMessage dispatch has happened, but not through
  `core.py`/`core.run()` — see `run-20260804-riskband-003`)*
- Live test-runner adapter integrated: no *(same caveat — the live path mediates through
  the real `test-runner` Skill directly from `/work`, not through a `TestRunnerAdapter`
  passed into `core.run()`)*
- End-to-end verified: no for `core.py` itself; **yes for the harness overall**, via the
  `/work` skill's Option C path (`run-20260804-riskband-003`, `final_verdict: "pass"`)
- Complete orchestrator milestone: no

#### Live architecture decision (Option C)

Three ways to let a real, live pipeline run were evaluated: (A) `/work` directly
coordinates subagents and calls small Python validation helpers ad hoc; (B) `/work`
implements live `AgentAdapter`/`TestRunnerAdapter`/`DiscoveryAdapter` objects and passes
them into `core.run()` so the existing state machine drives a live run; (C) a hybrid —
the main session owns reasoning, phase decisions, and dispatch, while the deterministic
modules own validation, path safety, evidence persistence, and terminal-verdict
vocabulary, called directly rather than through `core.run()`.

**Option C was selected.** Option B is not achievable without rewriting the control
flow: `core.py`'s `_run_agent_phase()` calls `agent_adapter.start()`/`resume()`
synchronously and expects a return value in the same Python stack frame, but a live
"agent adapter" would have to invoke Claude Code's `Agent`/`SendMessage`/`Skill` tools
from *inside a Python method* — impossible without an API-key-based SDK bridge, which
§2 and this milestone both rule out. So `core.py`, `adapters.py`, and `state.py` remain
exactly as they are: not rewritten, not bypassed, still the executable specification for
phase-transition and validation rules, exercised deterministically by
`tests/test_orchestrator_core.py` against `tests/fakes/agent_adapter.py`. The `/work`
skill (`.claude/skills/work/SKILL.md`) is the live control loop instead, calling the
same underlying functions (`discovery.validate_scope_draft`, the `harness.evidence`
schema/semantic validators, `evidence_io.*`, `paths.*`, `state.FINAL_VERDICT_BY_STATE`)
through the new thin bridge `harness/orchestrator/live_cli.py` — a JSON-in/JSON-out CLI
with 9 operations (`validate_scope`, `retain_attempt`, `validate_artifact`,
`promote_artifact`, `build_path_attestation`, `check_command_identity`,
`retain_rejection`, `retain_policy_event`, `write_run_summary`), each delegating to an
existing function rather than reimplementing it. `live_cli.py` is deliberately not an
orchestrator: it contains no phase sequencing, no dispatch, and no agent reasoning.

**Status:** `live_cli.py` and `.claude/skills/work/SKILL.md` are implemented and
`live_cli.py` is deterministically tested — `tests/test_orchestrator_live_cli.py`, 24
tests, all passing; `tests/test_work_skill.py` (structural checks on `SKILL.md` itself,
including the Architect transport-repair protocol and the test-runner restriction
lifecycle below), 38 tests, all passing. This proves the bridge and the skill's own
documented instructions are internally correct. It does **not** yet prove real Agent
dispatch, same-agent resume, Skill mediation, or the transport-repair correction flow
work end-to-end live — that requires an actual `/work` invocation (a literal slash
command, not a hand-rehearsal like the one below), tracked below and in "Remaining
work." **`/work` itself is not yet live-verified.**

#### Dry-run evidence (2026-08-03, `run-20260803-riskband-001`)

`run-20260803-riskband-001` was a **manually executed rehearsal** of the `/work`
instructions -- an operator followed `.claude/skills/work/SKILL.md` step by step against
the prospective task "Add the applicant's overall risk band to the human-readable
decision explanation." It was **not invoked through a literal `/work` slash command**;
no claim to the contrary should be inferred from the run's `run_id`/evidence directory
naming, which follows the skill's own "Run identity" convention purely so the retained
artifacts are shaped exactly as a real invocation's would be. Full evidence retained
under `runs/run-20260803-riskband-001/` (canonical `scope.json`, `run-summary.json`,
`attempts/`, `requests/`, `logs/`, and a `live_cli/` scratch directory of every bridge
request issued). Result: **`final_verdict: "blocked"`** -- not a pass, and not a
completed pipeline; recorded honestly rather than softened.

This rehearsal verified: the live bridge (`live_cli.py`) end-to-end for Discovery, a
real Architect dispatch via the `Agent` tool, raw-response retention before parsing,
malformed-output blocking (never silently fence-stripped), one real `test-runner` Skill
invocation (not a direct wrapper call), request/result identity checking, and mutation
safety (no file under `demo-repo/` changed). It did **not** verify: Research did not
complete (blocked on the Architect's fenced output — see below); no canonical
`findings.json` was produced; the Engineer and Quality Engineer were not dispatched.
This was **not** a successful `/work` dry run in the sense of reaching the dry-run
boundary cleanly, and it was **not** a completed four-phase run — it is evidence that
the underlying mechanisms work individually, not proof of a working `/work` pipeline.

What this genuinely proves:
- Discovery (main-session reasoning) -> `live_cli.py` `validate_scope`/`promote_artifact`
  -> canonical `scope.json`: real, end-to-end, works.
- A real Architect dispatch via the `Agent` tool (`subagent_type: "architect"`) returns
  a real agent id and a real findings response -- dispatch itself works.
- **Finding, addressed in instructions but not yet live-verified:** the live
  Architect's returned text was wrapped in a Markdown code fence
  (```` ```json ... ``` ````), which `architect.md`'s own Output section explicitly
  forbids. Per `harness/orchestrator/core.py`'s existing no-fence-stripping policy
  (never relax this to "fix" a live result), the raw response was retained as evidence
  and the Research phase was correctly blocked rather than silently repaired --
  `findings.json` was never promoted. A separate, clearly non-canonical diagnostic
  confirmed the fence-stripped content is itself schema- and semantically-valid (9
  well-cited findings, zero errors), so the Architect's actual research was sound; only
  the raw transport contract was violated. Root cause (subagent non-compliance vs.
  something in the relay path) is not yet isolated. In response, `SKILL.md` now
  specifies an **Architect transport repair** protocol (see "Architect transport
  repair" in `.claude/skills/work/SKILL.md`): retain the malformed output verbatim,
  record a `transport_parse_failure` policy event, never locally strip fences, send a
  transport-only correction request to the *same* Architect agent id via `SendMessage`
  (explicitly: do not redo research, do not change findings, return raw JSON, no fence,
  no prose), wait for that same agent's reply, retain and validate it as attempt 2,
  and permit exactly one such correction attempt before blocking Research -- never a
  replacement Architect presented as a continuation. This protocol is **implemented in
  instructions and covered by `tests/test_work_skill.py`'s structural checks, but
  remains unverified against a real fenced Architect response** — this dry run predates
  the protocol's existence, so it was not exercised here.
- One real `test-runner` Skill invocation (`Skill` tool, not a direct `Bash` call to the
  wrapper) against `demo-repo/tests/unit/test_explanations.py` succeeded independently
  of the Research block: real `command_result`, `exit_code: 0`, 3 passed, no mutation
  detected, request/result identity independently checked and matched via `live_cli.py`
  `check_command_identity`. This is genuine (partial) evidence for Remaining-work item 5
  below -- a single successful invocation, not exhaustive Skill-mediation coverage.
- **Observed, now documented as expected behavior:** `test-runner/SKILL.md`'s
  `disallowed-tools` (`Write`, `Edit`, ...) remained enforced at the tool layer for
  several subsequent turns after the wrapper call completed, not just for the single
  invoking turn -- two live `Write` attempts and one `Edit` attempt were denied
  afterward, requiring `Bash` heredocs/inline Python as a workaround. This is now
  understood and documented (`.claude/skills/work/SKILL.md`'s "Test-runner Skill
  restriction lifecycle" section) as **expected turn-lifecycle behavior, not an
  unexplained defect**: once `test-runner` is invoked via the `Skill` tool, its
  `disallowed-tools` remain enforced for the remainder of the current user turn.
  `SKILL.md` now requires that, after the first `test-runner` invocation in a turn, the
  main-session orchestrator not depend on `Write`/`Edit` for further evidence
  operations (using `Bash` to drive `live_cli.py` instead), that `Bash` never be used to
  edit application source under `demo-repo/`, that only the Engineer subagent modify
  approved `demo-repo/` files, and that test execution still always go through the real
  `test-runner` Skill rather than a direct wrapper call even under this restriction.
- Engineer and Quality Engineer were **not** dispatched (by design -- a dry run must not
  reach Implementation) -- the staged Agent-start/`SendMessage`-resume continuation
  protocol documented in the `/work` skill remains entirely unexercised live.
- No file under `demo-repo/` changed (`git status --short -- demo-repo` empty, verified
  independently after the run, not merely assumed).

#### Live evidence (2026-08-04, `run-20260804-riskband-002`) — blocked but useful

`run-20260804-riskband-002` was a **real, live `/work` attempt** (not a hand-rehearsal
like `run-20260803-riskband-001` above), retained unmodified at
`runs/run-20260804-riskband-002/`. It ended `final_verdict: "blocked"` -- not a
completed pipeline -- but it live-verified several things that were previously only
specified in instructions, and it surfaced the specific defect this milestone repairs:

- **The Engineer's command-grammar fix held live.** The Engineer's `pre_test_requested`
  used the accepted `python -m pytest tests/unit/test_explanations.py -v` grammar, not
  the bare `pytest ...` form a prior run had produced -- confirming `engineer.md`'s
  grammar correction actually changed live behavior, not just its written contract.
- **The Engineer transport-repair protocol worked live, resuming the same agent id.**
  The Engineer's first `pre_test_requested` reply was wrapped in a Markdown fence,
  violating its own no-fence contract; the one permitted transport-only correction was
  sent to the *same* Engineer agent id (`a7c52385d707fe116`) via `SendMessage`, and the
  corrected reply parsed as clean raw JSON -- direct live evidence the
  "Engineer transport repair" protocol in `work/SKILL.md` functions as designed, not
  just as documented instructions.
- **A genuine failing TDD regression test was produced correctly, before any production
  change.** Independently confirmed via `git status`/`git diff` before mediation: only
  the in-scope test file (`demo-repo/tests/unit/test_explanations.py`) had changed.
- **The real test-runner Skill returned the expected failure.** The mediated
  `command_result` had `exit_code: 1`, and the retained log showed the exact expected
  `TypeError` from the planned signature change -- not a syntax error, import error, or
  unrelated regression. Request/result identity matched
  (`check_command_identity`).
- **It then blocked on the defect this milestone repairs.** Invoking the (at the time,
  inline, non-forked) `test-runner` Skill removed `Write`/`Edit` for the rest of that
  user turn -- and this run is the first live evidence that the removal propagated into
  the *resumed Engineer subagent's own tool availability*, not just the main session's,
  per `runs/run-20260804-riskband-002/logs/policy-events.jsonl`'s
  `cross_agent_tool_restriction_observed` event. The Engineer correctly self-reported a
  schema-valid `blocked` implementation-report (`Edit`/`Write` "not enabled in this
  context") rather than fabricating an edit; independently re-confirmed via
  `git status`/`git diff` that no unauthorized or silent edit occurred.
- **Chosen repair, not yet live-verified:** `test-runner/SKILL.md` now declares
  `context: fork` and `background: false` (Claude Code v2.1.218+; the installed version
  at the time of this repair was 2.1.221), so the Skill's own `disallowed-tools`
  restriction is confined to its own forked context instead of leaking into the
  invoking turn or a resumed subagent. This is a harness-configuration repair only --
  `run-command.py`'s wrapper grammar, its `allowed-tools`/`disallowed-tools`
  declarations, and `work/SKILL.md`'s request/result identity checking are unchanged.
  **It has not yet been exercised by a real `/work` run** -- whether the fork's
  isolation actually holds (the resumed Engineer's `Edit`/`Write` genuinely remains
  available after a forked test-runner invocation) is a claim to be live-verified by
  the next `/work` attempt, not one this milestone can certify from static
  configuration alone.

#### Live evidence (2026-08-04, `run-20260804-riskband-003`) — completed

`run-20260804-riskband-003` is a **real, live `/work` attempt**, retained unmodified at
`runs/run-20260804-riskband-003/`. It ended `final_verdict: "pass"` -- **the first
successful literal `/work` execution to complete Discovery → Research → Implementation →
Verification end-to-end against `demo-repo/`**, closing the item 6 gap "Remaining work"
below tracked as open through both prior live attempts.

- **Discovery → Research → Implementation → Verification all completed and promoted.**
  `scope.json`, `findings.json`, `implementation-report.json`
  (`status: "ready_for_verification"` -- **the first such artifact ever produced by a
  real `/work` run**), and `verification-report.json` (`final_verdict: "pass"` -- **the
  first canonical passing verification report ever produced by a real `/work` run**) are
  all canonical and schema-valid.
- **Canonical findings: 6 total -- 3 `found` (F-1, F-2, F-4), 2 honest `not_found`
  (F-3, F-5), 1 `inferred` (F-6),** per `findings.json`, the sole source of truth for
  this count. This count is consistent across every retained artifact and draft in this
  run (the raw Architect attempt, `findings.json`, `implementation-report.json`'s
  `findings_ref.finding_ids`, and both the draft and canonical `run-summary.json`); an
  audit of the run directory found no retained document stating a different count. Any
  other figure quoted for this run elsewhere is superseded by this one.
- **The forked test-runner isolation repair is now live-verified.** A real,
  live-dispatched Engineer (agent `a5c7fbf7f46248441`) requested pre-implementation test
  evidence mediated through the real, forked (`context: fork`, `background: false`)
  `test-runner` Skill, received the real result, and its `Edit`/`Write` tools remained
  available on its next turn to make the implementation edit -- resolving the exact
  defect `run-20260804-riskband-002` surfaced. `PROJECT_SPEC.md`'s "not yet live-verified"
  caveat on the fork repair (above) is resolved by this run.
- **Genuine TDD evidence, in the correct order.** Independently confirmed via
  `git status`/`git diff` before command C-1 was mediated: only
  `demo-repo/tests/unit/test_explanations.py` had changed, no production file yet. C-1
  (`python -m pytest tests/unit/test_explanations.py -v`) returned `exit_code: 1` with
  the exact expected `TypeError` (not a syntax/import error or unrelated failure). Only
  after that failure was confirmed did the Engineer make its production edit; C-2, run
  after the edit, returned `exit_code: 0`, 4 passed.
- **Same-Engineer and same-Quality-Engineer staged continuation, both live-verified.**
  Per `logs/policy-events.jsonl`, exactly one `agent_dispatch` event exists for each of
  the Architect (`a8e5a6118fb7648f7`), Engineer (`a5c7fbf7f46248441`), and Quality
  Engineer (`a96d7d1edcebbc7e5`) -- no second dispatch for any phase, so no replacement
  agent was ever substituted for a continuation. The narrative and staged-turn evidence
  (`attempts/implementation-1..5.raw.txt`, `attempts/verification-1..3.raw.txt`) show the
  Engineer resumed across 5 turns and the Quality Engineer across 3 turns, all under
  their single respective dispatched agent id.
- **Exactly one transport correction per phase, both formalized.** The Engineer's first
  `pre_test_requested` reply and the Quality Engineer's first `attempt_requested` reply
  were each Markdown-fenced; each was repaired with exactly one transport-only
  correction to the same agent id (`transport_parse_failure` policy events retained for
  both), matching the one-correction-per-phase budget. The Quality Engineer's repair had
  previously been handled only "by extension" of the Architect/Engineer protocols (per
  `logs/policy-events.jsonl`'s own description field); `work/SKILL.md` now states an
  explicit Quality Engineer transport-repair protocol (see "Quality Engineer transport
  repair" in `work/SKILL.md`) rather than relying on informal analogy, with matching
  coverage in `tests/test_work_skill.py`.
- **Every test command was mediated through the real, forked `test-runner` Skill.**
  `logs/policy-events.jsonl` records five `skill_invocation` events (C-1, C-2, V-1,
  ORCH-1, ORCH-2), each `invoked_via: "Skill tool"`, `context: "fork"`,
  `background: false`. Request/result identity (`check_command_identity`) is explicitly
  recorded and matched for C-1, C-2, and V-1 (`live_cli/18-check-identity-c1.json`,
  `22-check-identity-c2.json`, `36-check-identity-v1.json`).
- **Evidence gap, honestly recorded rather than invented: ORCH-1 and ORCH-2 (the
  orchestrator's own independent re-verification commands) have no retained
  `check_command_identity` operation.** `live_cli/41-policy-orch-checks.json` and
  `42-policy-orch-checks2.json` are `retain_policy_event` calls recording the
  `skill_invocation` only -- unlike C-1/C-2/V-1, neither is followed by a
  `check_command_identity` step in the retained `live_cli/` sequence. The orchestrator's
  own narrative in `run-summary.json` still asserts ORCH-1 and ORCH-2 returned the
  expected `exit_code: 0` results, but this claim is not backed by the same explicit
  identity-check artifact the staged-agent commands have. This is a genuine gap in this
  run's evidence trail, not a failure of the run itself, and `work/SKILL.md` does not yet
  require an explicit `check_command_identity` step for the orchestrator's own
  independent-reverification commands (only for staged-agent-requested commands).
- **Independent Git-reality check, matching the implementation report exactly.** Exactly
  three files changed: `demo-repo/src/loanflow/explanations.py`,
  `demo-repo/src/loanflow/cli.py`, `demo-repo/tests/unit/test_explanations.py` -- no
  other `demo-repo/` file, and no Protected Path, changed. The working diff for these
  three files is byte-identical to the retained `runs/run-20260804-riskband-003/diff.patch`
  and matches `implementation-report.json`'s own `changed_files` claim. The diff is
  minimal: one new required parameter and one new output line in `explanations.py`, one
  call-site line in `cli.py`, and the corresponding test updates.
- **102 demo tests passing.** ORCH-2 (`python -m pytest tests -q`, `working_directory:
  "demo-repo"`) returned `exit_code: 0`, 102 passed -- the prior 101-test baseline plus
  the one new risk-band test. Independently reproduced in this audit
  (`python -m pytest demo-repo/tests -q` from the repo root): 102 passed.
- **No commit or push was performed by the harness at any point in this run** -- `git
  log` shows no new commit from this run, consistent with `work/SKILL.md` standing rule
  12.
- **Remaining gaps, not closed by this milestone** (see §3/§4 "Later integrations" for
  full detail): hooks and guardrails (skill-enforcement, pre-dispatch-check,
  completion-guardrail, post-agent cost); same-run Quality-Engineer logic-failure
  route-back to the Engineer (a `fail` verdict still ends the run, per Phase 4's own
  documented behavior); checkpoint/resume; the memory loop and `lessons-learned.md`
  (still does not exist); token/cost accounting; the GitHub and Jira skill packs and
  their connectors; the Obsidian integration; and false-push/flaky-test demonstrations.
  This run closes the Core MVP's live end-to-end loop once, with real evidence -- it does
  not close the project.

#### Hooks and same-run route-back milestone (2026-08-05)

A follow-on milestone on top of `run-20260804-riskband-003`, closing several of the items
"Live evidence (`run-20260804-riskband-003`) -- completed" listed as still open: ORCH-*
request/result identity enforcement, the same-run Quality-Engineer logic-failure route-back
to the Engineer, and three of the four hooks required by the assignment (skill-enforcement,
pre-dispatch-check, completion-guardrail; post-agent cost tracking remains open). **None of
this has yet been exercised by a new live `/work` run** -- every claim below is either
deterministic-test evidence or, for two of the three hooks, live evidence captured against
real Claude Code tool calls made directly in this session (not through a completed `/work`
pipeline). This distinction is maintained precisely throughout this section; nothing here
should be read as claiming a fourth live end-to-end run occurred.

**Part 1 -- ORCH-* request/result identity enforcement.** `run-20260804-riskband-003`'s own
retained evidence has a documented gap: its two orchestrator-owned independent
re-verification commands (`ORCH-1`, `ORCH-2`) were mediated through the real, forked
`test-runner` Skill and had `skill_invocation` policy events retained, but neither had a
`check_command_identity` check run against its result, unlike `C-1`/`C-2`/`V-1`.
`work/SKILL.md` standing rule 13 and the Phase 4 re-verification section now require
`check_command_identity` for **every** orchestrator-owned test-runner request, `ORCH-*`
included, before its result is trusted; a mismatch blocks completion (`kind:
"orchestrator_command_identity_mismatch"` policy event) rather than being silently
accepted. This is an instruction-level contract change to `work/SKILL.md` -- there is no
`core.py` engine change for it, consistent with the Option C architecture (above): live
dispatch and mediation run through `/work` itself, not through `core.run()`. Structurally
verified by `tests/test_route_back_protocol.py::TestWorkSkillOrchestratorCommandIdentity`
(6 tests). **Not yet demonstrated in a new live run** -- the next real `/work` execution is
what would confirm `ORCH-1`/`ORCH-2` actually get `check_command_identity` calls in
practice.

**Part 2 -- bounded same-run logic-failure route-back.** `harness/orchestrator/core.py` no
longer treats every `verification-report.json` `"fail"` verdict as terminal.
`classify_verification_outcome()` independently re-derives whether a verdict is a genuine
`logic_bug` (requires `final_verdict == "fail"`, `routed_back_to_engineer.routed == true`,
and at least one `attempts[]` entry classified `logic_bug` -- all three, not the verdict
string alone), an `infrastructure_flake` or `environment` `"inconclusive"` verdict (both
always terminal, never routed -- the Quality Engineer's own bounded retry already resolves
or exhausts a flake before any verdict reaches this function), or `insufficient_evidence` (a
missing/malformed/blocked verification result -- also never routed). Only a genuine
`logic_bug` is eligible for route-back, and `MAX_LOGIC_REPAIR_ATTEMPTS = 1` bounds it to
exactly one cycle. `_handle_verification_failure()` drives the cycle: `agent_adapter.resume()`
(never `start()`) on the *exact same* Engineer handle from this run's original
Implementation dispatch, carrying the real Quality Engineer failure evidence verbatim; the
resumed Engineer's new final report promotes to a **separate** canonical path
(`implementation-report.repair-1.json`) via `evidence_io.promote_canonical()`'s existing
collision guard, leaving the original `implementation-report.json` retained, untouched; then
`resume()` (never `start()`) on the *exact same* Quality Engineer handle, whose new final
report promotes to `verification-report.repair-1.json`, again leaving the original
retained. A repaired `"pass"` updates `artifact_refs.implementation_report`/
`artifact_refs.verification_report` to the repair-round paths for the rest of the run; a
repeated `"fail"` on the repair round is terminal (`route_back_exhaustion` policy event) --
no second repair cycle is ever attempted. Matching protocol sections were added to
`engineer.md` ("Route-back repair protocol") and `quality-engineer.md` ("Re-verification
protocol"), and to `work/SKILL.md` ("Same-run logic-failure route-back to the Engineer"),
all requiring: TDD failing-test-first ordering re-applied to the repair (the same Step 5/6
minimal-change-ladder and one-failing-test-first discipline as the original cycle,
re-checked via `git status`/`git diff` before the repair's pre-test command runs); fresh,
never-reused command ids for every repair-cycle command (`C-3`, `C-4`, `V-2`, ...); every
repair-cycle command mediated through the same real, forked `test-runner` Skill with the
same request/result identity checking; and a broken continuity during either resume treated
as a **broken agent continuity** failure that blocks the run, never as license to dispatch a
replacement agent and call it a continuation.

Audited against this milestone's own checklist and confirmed clean, with no defects
requiring correction:
- Exactly one `start()` call per role for the whole run, repair round included
  (`agent_adapter.start_count("engineer") == 1`, `== 1` for `quality_engineer` too) -- every
  repair-cycle turn is a `resume()`.
- Repair limit is exactly `MAX_LOGIC_REPAIR_ATTEMPTS = 1`; a second consecutive `logic_bug`
  verdict on the repair round ends the run at `VERIFICATION_FAILED` with a
  `route_back_exhaustion` policy event, never a second repair cycle.
- The original `implementation-report.json`/`verification-report.json` are never deleted or
  overwritten; the repair round's artifacts always land at the distinct `*.repair-1.json`
  canonical paths, enforced by the pre-existing `EvidenceCollisionError` guard in
  `evidence_io.promote_canonical()` (unchanged by this milestone, reused as-is).
- `run-summary.json`'s `artifact_refs` correctly point at the repair-round paths after a
  successful repair, and `objective_summary` states plainly that a repair cycle occurred
  (verified by `test_logic_bug_routes_back_to_the_same_engineer_and_repairs_successfully`).
- `infrastructure_flake`, `environment`, and "malformed or insufficient evidence" (a
  self-reported `blocked` verification report) are each independently tested and confirmed
  to never route back and never touch the Engineer a second time
  (`test_infrastructure_flake_does_not_route_back_to_engineer`,
  `test_environment_failure_does_not_route_back_to_engineer`,
  `test_missing_or_invalid_verification_evidence_blocks_routing_rather_than_repairing`).
- The repair round's implementation report is validated by the exact same schema and
  semantic rules as the original round -- a repair whose pre-test command did not actually
  fail (test-first ordering violated) is rejected exactly as it would be the first time
  (`test_repair_implementation_report_must_preserve_test_first_ordering`).
- Request/result identity checking (the `task_id`/`run_id`/`command_id`/`command`/
  `working_directory` cross-check against the request the orchestrator itself built) applies
  to repair-cycle commands automatically, because `_handle_verification_failure()` reuses
  the same `_drive_staged_protocol()` helper the original round uses -- there is no separate,
  unvalidated code path for repair-round commands.
- The ordinary successful (no-repair) path is unchanged and still covered by its own
  existing tests (`test_verification_pass_permits_completion`, and the rest of
  `tests/test_orchestrator_core.py`'s pre-existing suite) -- none of them needed
  modification for this milestone; the new route-back logic is purely additive at the
  `"fail"` branch.
- 34 tests in `tests/test_orchestrator_core.py` (the file's full suite, un-narrowed) and 43
  structural tests in `tests/test_route_back_protocol.py` covering the
  `engineer.md`/`quality-engineer.md`/`work/SKILL.md` prose contracts, all passing.
- **Genuinely deterministic, not yet live.** No live `Agent`/`SendMessage` route-back
  exchange has occurred -- the only exercised path is `core.run()` against
  `tests/fakes/agent_adapter.py`'s scripted doubles, consistent with `core.py`'s documented
  Option C boundary (a live route-back would run through `/work` + `live_cli.py`, exactly as
  `run-20260804-riskband-003` did for the ordinary path, and that has not happened yet for
  this feature).

**Part 3 -- hooks.** Three of the assignment's required hooks are implemented at
`.claude/hooks/` and registered in `.claude/settings.json` (validated as well-formed JSON):

- `pre_dispatch_check.py` (`PreToolUse`, matcher `Agent`) -- refuses to dispatch
  `architect`/`engineer`/`quality-engineer` unless the correct precondition artifact
  (`scope.json` `status: "approved"`; `findings.json`; `implementation-report.json`
  `status: "ready_for_verification"`) is already promoted, well-formed JSON, and
  identity-matches (`task_id`/`run_id`) the dispatch prompt. Fails open on its own internal
  errors (malformed hook stdin) -- never the single point of failure for real harness work.
  **Live-verified**: two real `Agent` tool calls in this session were genuinely refused
  before any subagent was created (a run with no promoted `findings.json`; a run with no
  promoted `implementation-report.json`), at zero token/tool cost for the refused dispatch
  itself -- `docs/hooks-permission-verification.md`. 14 deterministic tests
  (`tests/test_hooks.py::TestPreDispatchCheck`).
- `skill_enforcement.py` (`PreToolUse`, matcher `Bash`) -- blocks two specific,
  previously-undefended bypasses `test-runner/SKILL.md` itself already names as open gaps: a
  direct `Bash` call to `run_command.py` from the main (orchestrator) session (bypassing the
  real `Skill` mechanism), and any `Bash` command that looks like a direct write into
  `demo-repo/` (an orchestrator fallback around the Engineer's own `Edit`/`Write` boundary).
  Distinguishes a legitimate forked `test-runner` Skill invocation from an orchestrator
  bypass using the `agent_id` field's presence/absence in the hook payload -- this signal
  was **empirically captured and verified live**, not assumed, via a throwaway diagnostic
  hook before `skill_enforcement.py` was written (`docs/hooks-signal-verification.md`, real
  captured payloads retained at `runs/hooks-diagnostic/diag-log-trimmed.jsonl`). Every
  blocked event (and only blocked events) is appended, with a real timestamp, to the hook's
  own ignored runtime log, `.claude/hooks/logs/skill-enforcement-events.jsonl` -- see
  "Repository hygiene" below for why this is gitignored rather than committed directly.
  **Live-verified**: a real direct-wrapper-bypass `Bash` call and a real `demo-repo/`
  write-attempt `Bash` call were both genuinely refused in this session before the shell
  ever ran them (independently confirmed afterward: `git status --short -- demo-repo`
  stayed empty, `explanations.py` byte-for-byte unchanged); the real, forked `test-runner`
  Skill invocation used to capture the signal evidence was **not** blocked, proving the
  distinguishing signal works both ways in the same live session, not just in theory --
  `docs/hooks-permission-verification.md`. 13 deterministic tests
  (`tests/test_hooks.py::TestSkillEnforcement`). **Documented limitation, not overclaimed**:
  the `demo-repo/` write check is a substring/regex heuristic (redirects, `sed -i`,
  `cp`/`mv`/`rm`, a `python -c ... open(..., 'w')` pattern), not a sandboxed guarantee -- a
  write disguised past this specific token list could still slip through, consistent with
  `test-runner/SKILL.md`'s own already-documented trust-boundary honesty.
- `completion_guardrail.py` (`Stop`) -- blocks the orchestrator's turn from ending on a
  `/work` completion claim unless `verification-report.json` exists, parses, passes schema
  and semantic validation, has `final_verdict: "pass"`, every canonical artifact
  `run-summary.json`'s `artifact_refs` cites exists and identity-matches this run, and the
  implementation report's `changed_files` are genuinely reflected in `git status
  --porcelain` (this harness never commits, so a real change must still show as a live,
  uncommitted working-tree modification). Resolves a repaired run's `*.repair-1.json`
  canonical paths via `run-summary.json`'s own `artifact_refs`, not a hardcoded filename.
  Only activates for a run that itself wrote `runs/<run_id>/.completion_claim.json` (per
  `work/SKILL.md`'s Reporting section) -- every other conversation turn is untouched, and
  `stop_hook_active` always short-circuits to allow, so this hook can never create an
  infinite must-continue loop. 13 deterministic tests
  (`tests/test_hooks.py::TestCompletionGuardrail`), exercised by invoking the real script's
  `main()` directly against crafted `Stop` payloads and crafted `runs/<run_id>/` fixture
  trees -- including the assignment's canonical "delete the verification evidence after a
  prior pass" scenario. **Not live-proven as an actual `Stop`-event block against a real
  Claude Code conversation turn** -- deliberately not attempted in this session, because
  triggering it live against this very conversation risked leaving a bad
  `.completion_claim.json` marker in place at the end of this session, which would then
  block this very report from ending; `docs/hooks-permission-verification.md` states this
  distinction plainly rather than implying equal live proof for all three hooks.
  **Update, same day:** this specific gap — no live `Stop`-event proof — was closed by a
  follow-up demonstration using a dedicated, disposable fixture run rather than this
  session's own conversation turn, sidestepping the risk above. See "Live Stop-hook
  demonstration (`completion-guardrail-live-001`, 2026-08-05)" below for the full account;
  the missing-verification-evidence branch specifically is now live-proven, the other four
  branches remain deterministic-only as described above.
- **Known cooperation boundary, stated honestly, not glossed over**:
  `completion_guardrail.py` activates only when `/work` itself writes
  `.completion_claim.json` before reporting completion. An orchestrator turn that skips
  that write -- through a bug, an unusual code path, or a future prompt that omits the
  instruction -- is not caught by this hook at all; the hook has nothing to react to. This
  is a real, load-bearing limitation, not a hypothetical edge case, and it must not be
  described as "impossible to bypass" or "unconditionally enforced." Closing it fully would
  require a mechanism that does not depend on the orchestrator's own cooperation (e.g. a
  `Stop` hook that unconditionally inspects `runs/` for the most recent run directory
  regardless of any marker) -- deferred, not attempted in this milestone.
- **Signal stability caveat**: `skill_enforcement.py`'s `agent_id`/`agent_type`-presence
  signal was empirically observed against the Claude Code version installed at the time of
  this milestone. Nothing here confirms this field's presence/absence is a documented,
  stable contract across Claude Code versions -- a future version could change hook payload
  shape without notice, silently defeating the bypass check (it would fail open, per
  `_is_wrapper_bypass`'s `not agent_id` condition -- a version change is far more likely to
  under-block than to falsely block real work, which is the safer failure direction, but it
  is still a real version-sensitivity risk worth stating rather than treating this signal
  as permanently guaranteed).
- **Post-agent cost/token-tracking hook remains not started** -- the fourth hook this part
  of the assignment names, out of scope for this milestone. (Superseded: **COMPLETE
  2026-08-14** -- see the "Milestone update (2026-08-14): per-agent token usage and cost
  accounting" note above.)
- Repository hygiene, corrected 2026-08-05: `.gitignore` already covered `__pycache__/`
  (confirmed via `git check-ignore`, so `.claude/hooks/__pycache__/*.pyc` is excluded
  automatically, no change needed). `.claude/hooks/logs/skill-enforcement-events.jsonl`
  was initially left un-gitignored and committed directly from its live runtime location
  -- a real defect, since every future blocked event during ordinary harness operation
  would then modify a tracked file and dirty the repository. This is fixed:
  `.claude/hooks/logs/` is now listed in `.gitignore`, `skill_enforcement.py`'s own
  `_record()` still creates that directory and file on demand with no code change
  (`LOG_PATH.parent.mkdir(parents=True, exist_ok=True)`, empirically re-confirmed by
  deleting the directory and re-triggering `_record()`), and the two blocked-event entries
  this session's live demonstrations generated are preserved, unchanged, as committed
  evidence at `runs/hooks-diagnostic/skill-enforcement-events.jsonl` -- a one-time verbatim
  copy, not a live-updating file. Future real blocked events accumulate only in the
  ignored runtime log; they do not retroactively alter the retained evidence copy. See
  `docs/hooks-permission-verification.md`'s "Runtime log vs. retained evidence" section for
  the full account. The throwaway `_diag_capture.py` diagnostic hook used to capture the
  `docs/hooks-signal-verification.md` payloads was deleted before this audit, as its own
  documentation states; confirmed absent from the working tree.

**Validation performed during this audit (2026-08-05):** `python -m pytest
tests/test_orchestrator_core.py -q` (34 passed), `python -m pytest
tests/test_route_back_protocol.py -q` (43 passed), `python -m pytest tests/test_hooks.py -q`
(40 passed), `python -m pytest tests -q` (442 passed, up from 350 in the previous committed
milestone), `python -m pytest demo-repo/tests -q` (102 passed, unchanged), `git diff --check`
(clean, only line-ending warnings), `git status --short -- demo-repo` (empty). One
documentation-only defect was found and fixed during this audit:
`docs/hooks-permission-verification.md` understated `TestPreDispatchCheck`'s and
`TestSkillEnforcement`'s real test counts by one each (13/12 claimed vs. 14/13 actual) --
corrected to match the real collected counts. No defects were found in
`harness/orchestrator/core.py`'s route-back implementation or in the three hook scripts
themselves.

**Follow-up repository-hygiene correction (2026-08-05, same day):** this audit's own first
pass left `.claude/hooks/logs/skill-enforcement-events.jsonl` committed directly from its
live runtime location -- flagged and fixed immediately after: `.claude/hooks/logs/` is now
`.gitignore`d, the two blocked-event entries it held are preserved as a verbatim, committed
copy at `runs/hooks-diagnostic/skill-enforcement-events.jsonl`, and
`skill_enforcement.py`'s own directory/file auto-creation was empirically re-confirmed to
still work unchanged against the now-ignored path. See "Repository hygiene" above (Part 3)
and `docs/hooks-permission-verification.md`'s "Runtime log vs. retained evidence" section
for the full account. No route-back behavior, hook behavior, schemas, agent contracts, or
historical `runs/run-*` evidence were touched by this correction.

#### Live Stop-hook demonstration (`completion-guardrail-live-001`, 2026-08-05)

A follow-on demonstration, same day, closing the one item the hooks milestone above
explicitly left open: whether `completion_guardrail.py` actually blocks a real Claude Code
`Stop` event, not just a direct `main()` invocation against crafted fixtures. This does
**not** constitute a new live `/work` run — it is a dedicated, disposable fixture built and
inspected directly, exactly as `run-20260804-riskband-003`'s and prior boundary-test
evidence were, to exercise the hook's real end-of-turn trigger without risking this
session's own conversation turn (the exact risk the hooks-milestone text above declined to
take).

- **Fixture.** `runs/completion-guardrail-live-001/`, task_id `DEMO-COMPLETION-GUARDRAIL-1`,
  run_id `completion-guardrail-live-001`: schema- and semantically-valid `scope.json`,
  `findings.json`, `implementation-report.json`; a genuine, schema-valid,
  `final_verdict: "pass"` `verification-report.json` was written and validated first, then
  moved aside (not deleted) to `verification-report.json.moved-aside`, so the canonical path
  `runs/completion-guardrail-live-001/verification-report.json` is genuinely missing;
  `run-summary.json` claims `final_verdict: "pass"`; `.completion_claim.json` was written
  last, matching the exact marker `work/SKILL.md`'s Reporting step writes.
- **Real `Stop` event.** The assistant's turn was ended (no further tool calls) with the
  marker in place, letting Claude Code invoke the actually-registered `.claude/settings.json`
  `Stop` hook — not a simulated call to `completion_guardrail.py`'s `main()`. The hook fired
  automatically, per the harness's own `Stop`-hook contract.
- **Exact block message (verbatim, delivered by the Claude Code harness as "Stop hook
  feedback"):**
  ```
  [python .claude/hooks/completion_guardrail.py]: completion-guardrail: this run cannot be reported complete:
  - run_id='completion-guardrail-live-001': runs/completion-guardrail-live-001/verification-report.json is missing (deleted or never produced).
  ```
  This is check #1 in the hook's own docstring — the specific missing-verification reason,
  not a generic refusal.
- **Turn continued.** The blocked `Stop` event caused Claude Code to re-invoke the assistant
  with the hook's stderr fed back instead of letting the turn end — direct proof `Stop` was
  denied, not merely that the script would return exit code 2 in isolation.
- **Cleanup.** `.completion_claim.json` was renamed to `.completion_claim.json.consumed`
  (content byte-for-byte unchanged) immediately after capturing the evidence above, so no
  future turn in this session (or later) is blocked by this fixture. The underlying invalid
  condition — `verification-report.json` absent from its canonical path — was deliberately
  left as-is rather than "fixed," since restoring it would erase the exact condition this
  fixture exists to demonstrate. Confirmed via `Get-ChildItem runs -Recurse -Force -Filter
  ".completion_claim.json"`: no output, i.e. no active marker remains anywhere under `runs/`.
- **Full account retained at:** `runs/completion-guardrail-live-001/STOP-HOOK-LIVE-BLOCK-EVIDENCE.md`
  and `runs/completion-guardrail-live-001/CLEANUP-NOTE.md`; cross-referenced from
  `docs/hooks-permission-verification.md` §4.

**What this does and does not prove:**
- Proves: the real, registered `Stop` hook genuinely intercepts and blocks a real Claude Code
  end-of-turn event on a false/unverifiable completion claim, specifically for the
  missing-`verification-report.json` branch — the assignment's canonical "delete the
  verification evidence" scenario.
- Does not prove: the hook's other four check branches (invalid JSON, non-`pass` verdict,
  canonical-artifact `run-summary.json` mismatch, Git-reality conflict against
  `changed_files`) firing as live `Stop`-event blocks — those remain deterministic-only
  (`tests/test_hooks.py::TestCompletionGuardrail`), exactly as before this demonstration.
- Does not prove: this hook (or the rest of the hooks/route-back milestone — ORCH-* identity
  enforcement, same-run logic-bug route-back, skill-enforcement's live behavior inside a
  real pipeline) firing inside an actual completed `/work` run. No `/work` pipeline was run
  for this demonstration, and none of `run-20260804-riskband-003`'s or the hooks milestone's
  other open items are closed by it.
- Does not change: the known cooperation boundary — `completion_guardrail.py` activates only
  when `/work` itself writes `.completion_claim.json` before reporting completion; an
  orchestrator turn that never writes that marker is still not caught by this hook. This
  demonstration proves the hook blocks *when the marker exists and evidence is missing*; it
  says nothing about whether the marker is always written.
- Does not touch: any historical `runs/run-*` evidence (`run-20260803-riskband-001`,
  `run-20260804-riskband-002`, `run-20260804-riskband-003`, the hooks-diagnostic and
  boundary-test fixtures) — this fixture is entirely self-contained under its own
  `runs/completion-guardrail-live-001/` directory.

#### Remaining work

1. ~~Build `/work` free-form mode as a main-session command/skill.~~ **Entry point and
   deterministic live bridge implemented** — `.claude/skills/work/SKILL.md` (a project
   skill, not a `.claude/commands/` custom command; see §2) and
   `harness/orchestrator/live_cli.py`, both described above. Exercised by one real
   `/work --dry-run` walk-through — see "Dry-run evidence" above.
2. Provide reasoning-backed Discovery. **Exercised live and worked**: Discovery
   produced a real, schema/semantically-valid `scope.json` in the dry run above.
3. Dispatch real Architect, Engineer, and Quality Engineer subagents. **COMPLETE** — all
   three real-dispatched live in `run-20260804-riskband-003` (Architect
   `a8e5a6118fb7648f7`, Engineer `a5c7fbf7f46248441`, Quality Engineer
   `a96d7d1edcebbc7e5`), each with exactly one `agent_dispatch` policy event (no
   replacement agent for any phase). See "Live evidence (`run-20260804-riskband-003`) —
   completed" above.
4. Resume the same Engineer and Quality Engineer instances during staged exchanges.
   **COMPLETE** — both exercised live and worked end-to-end in `run-20260804-riskband-003`:
   the Engineer resumed across 5 staged turns (including its one transport correction)
   through `finalization_evidence_requested` and its final report; the Quality Engineer
   resumed across 3 staged turns (including its one transport correction) through its
   final `pass` verdict. Both under their single respective dispatched agent id, per
   `logs/policy-events.jsonl`.
5. Mediate tests through the actual test-runner `Skill` mechanism. **COMPLETE** for the
   forked path — `run-20260804-riskband-003` mediated five real commands (C-1, C-2, V-1,
   ORCH-1, ORCH-2) through the forked, synchronous `test-runner` Skill, with
   request/result identity confirmed for C-1/C-2/V-1. Still not exhaustive: a
   `command_rejected` or a mutation-sentinel `125` case has not yet been observed through
   `/work` itself.
6. Run the first real Discovery → Research → Implementation → Verification pipeline
   against `demo-repo/`. **COMPLETE** — `run-20260804-riskband-003` is the first live
   `/work` run to complete all four phases end-to-end, `final_verdict: "pass"`. See "Live
   evidence (`run-20260804-riskband-003`) — completed" above for the full account.
7. Retain and validate all artifacts and produce a real `run-summary.json`. **COMPLETE**
   for a passing run — `run-20260804-riskband-003` produced the first canonical
   `implementation-report.json` with `status: "ready_for_verification"` and the first
   canonical `verification-report.json` with `final_verdict: "pass"`, alongside canonical
   `scope.json`, `findings.json`, and a `"pass"`-verdict `run-summary.json`. The
   `run-20260803-riskband-001`/`run-20260804-riskband-002` `"blocked"`-verdict summaries
   remain retained as evidence of the earlier, unsuccessful attempts they honestly
   describe.
8. Implement same-run logic-failure route-back, ORCH-* command identity enforcement, and the
   pre-dispatch-check/skill-enforcement/completion-guardrail hooks. **Implemented and
   deterministically verified** (2026-08-05) — see "Hooks and same-run route-back milestone
   (2026-08-05)" above for the full account. The completion-guardrail hook's
   missing-verification-evidence branch is additionally now **live-demonstrated** against a
   real `Stop` event via a dedicated fixture (`runs/completion-guardrail-live-001/`; see "Live
   Stop-hook demonstration" above) — but that fixture was not a live `/work` run. **Still not
   exercised by a live `/work` run**: this remains the next thing a new live run would need to
   prove, including whether a planted logic bug is actually routed back and repaired
   end-to-end, and whether ORCH-* identity checks and the other hooks' branches fire as
   expected inside a real pipeline.

Populating the 50+ file demo repository (formerly item 1 of this list) is **COMPLETE** — see
"Demo repository (`demo-repo/loanflow`)" below. Items 1–7 above are now all complete, closing
the Core MVP's live end-to-end loop with real evidence (`run-20260804-riskband-003`).
Checkpoint/resume, hooks/guardrails, the memory loop, token/cost accounting, and the remaining
GitHub/Jira/Obsidian integrations stay deferred per §4 "Later integrations," not dropped, and
are not satisfied by this milestone — see "Live evidence (`run-20260804-riskband-003`) —
completed" above for the explicit list of what remains open.

#### Known contract limitation

`run-summary.schema.json` requires `artifact_refs.scope` unconditionally, but an invalid or
internally-failed Discovery outcome may have no canonical `scope.json` to reference at all. The
orchestrator points `artifact_refs.scope` at retained error evidence under
`runs/<run_id>/attempts/` instead, and that file's own content explicitly states no scope was
ever promoted. This is schema-valid — `artifact_refs.scope` is still a non-empty string path to
a real, existing file — but the schema itself has no property to distinguish a canonical,
validated artifact reference from a retained error-evidence reference; that distinction is
carried only by convention (the path and the file's own prose), not by the contract. Schema
revision to close this gap is deferred and was not performed in this milestone.

### Demo repository (`demo-repo/loanflow`)

The controlled target application for live Agentic Harness demonstrations.

#### Implemented

- `demo-repo/` contains `loanflow`, a clean, educational loan-application underwriting demo
  application — not legally compliant, not financially authoritative, and not for real personal
  information.
- 55 meaningful project files under `demo-repo/`. `demo-repo/` is part of the root
  AgenticHarness Git repository, contains no nested `.git/` of its own, and its history is
  tracked through the root repository (see "Status distinctions" below).
- 22 Python source modules under `demo-repo/src/loanflow/`.
- 23 pytest test files (19 unit, 4 integration) plus `conftest.py`, 4 JSON fixtures, and
  `config/thresholds.json`.
- Realistic applicant, application-lifecycle, income/DTI, collateral/LTV, risk-banding,
  underwriting-rule, decision/explanation, audit-event, health-check, configuration, pipeline,
  and CLI behavior.
- Standard-library-only at runtime; no network, database, cloud service, Docker, or
  authentication dependency anywhere in `demo-repo/`.
- No nested `.git/` or `.claude/` — `demo-repo/` is part of the root AgenticHarness Git
  repository and uses the root's single canonical `.claude/` only.

#### Verified

- Demo test suite: 101 collected, 101 passed, 0 failed (`python -m pytest demo-repo/tests -q`,
  identically reproduced via `cd demo-repo && python -m pytest tests -q`).
- Full harness suite unaffected: 246 collected, 246 passed, 0 failed.
- Every `.py` file under `demo-repo/` compiles cleanly (`python -m py_compile`).
- A SHA-256 before/after hash sweep of every file under `demo-repo/` (excluding
  `__pycache__`/`.pytest_cache`/`.git`) around a full demo test run showed zero changed, added,
  or removed paths — the suite leaves the target tree unchanged.
- The real test-runner wrapper (`.claude/skills/test-runner/scripts/run_command.py`) was
  invoked directly against `target_repo_path: "demo-repo"` for both a narrow request (risk-band
  boundary tests) and the full demo test directory; both returned `command_result` with
  `exit_code: 0` and real pytest results (`8 passed`, `101 passed` respectively) — neither
  request was rejected, and neither returned `124` (timeout), `125` (mutation), or `126`
  (internal error). This proves wrapper compatibility and mutation safety; it does **not** mean
  the demo was exercised through Claude Code's actual `Skill` invocation — the request files
  were executed directly through the wrapper script, not dispatched via the `Skill` tool. Live
  orchestrator-to-Skill integration remains future work.
- Baseline evidence retained at `runs/demo-repo-baseline-verification/requests/`,
  `runs/demo-repo-baseline-verification/logs/`, and
  `runs/demo-repo-baseline-verification/verification-summary.md`.
- The default health-check script (`demo-repo/scripts/health_check.py`) reports
  `Overall: HEALTHY`, exit code 0, against the committed `config/thresholds.json` baseline.
- The CLI (`loanflow.cli`, via `PYTHONPATH=demo-repo/src`) produces the expected decision for
  each retained fixture: approve for `applicant_basic.json`, refer/manual review for
  `applicant_boundary.json`, decline for `applicant_high_debt.json`.

#### Status distinctions

- Designed: yes
- Implemented: yes
- Automatically tested: yes
- Test-runner-wrapper verified: yes
- Mutation-safety verified: yes
- Clean baseline verified: yes
- Used by a live orchestrator run: **yes** — `run-20260804-riskband-003`
- Used in an end-to-end four-phase run: **yes** — `run-20260804-riskband-003`,
  `final_verdict: "pass"`, 102 demo tests passing post-change

### Immediate next milestone

Code-craftsmanship, test-runner, the deterministic orchestrator core, the demo repository, and
the live adapter layer (§6 items 5–8, 11) are all now complete and verified, including one real,
passing, end-to-end four-phase `/work` run (`run-20260804-riskband-003`) — the Core MVP's live
loop has closed once, with real evidence. The remaining paths: (1) the `github/` and `jira/`
skill packs, required for the assignment's "≥4 skill packs" bar but not the Core MVP loop (§4);
(2) hooks/guardrails and token/cost accounting (§6 items 9–10) — checkpoint/resume, the other
item in that pair, is now complete, see the checkpoint/resume milestone notes above (2026-08-06);
(3) Obsidian integration, connector comparisons, and planted-defect (flaky-test,
false-push) demonstrations (§6 item 12). See "Live evidence (`run-20260804-riskband-003`) —
completed" in §10 for exactly what this milestone did and did not close.

**Update (2026-08-05):** three of the four hooks named in (2) above are now implemented and
tested (pre-dispatch-check, skill-enforcement, completion-guardrail), along with the
same-run logic-failure route-back loop and ORCH-* identity enforcement — see §10 "Hooks and
same-run route-back milestone (2026-08-05)". The completion-guardrail hook's
missing-verification-evidence branch is now also live-demonstrated against a real `Stop`
event, via a dedicated fixture rather than a live `/work` run — see §10 "Live Stop-hook
demonstration (`completion-guardrail-live-001`, 2026-08-05)". **Update (2026-08-06):**
checkpoint/resume is now complete — see the checkpoint/resume milestone notes above for the
current, authoritative account. **Update (2026-08-06, same day):** the memory loop is now
also complete — see the memory-loop milestone notes above. **Update (2026-08-14):** the
post-agent cost/token-usage-accounting hook is now also complete and live-demonstrated —
see the "Milestone update (2026-08-14)" note above. A live demonstration of
route-back/hooks against a real, full four-phase `/work` run remains open.

### Eight-day MVP planning target

Planning target only, not a guarantee — goal is to close a working free-form-prompt MVP within
approximately eight focused development days:

- Day 1–2: Engineer and Quality Engineer.
- Day 3: Code-craftsmanship and test-runner skills.
- Day 4: Target/demo repository.
- Day 5–6: Orchestrator and `/work` prompt mode.
- Day 7: Checkpoint, memory, and guardrail hooks.
- Day 8: First complete evidence-backed run and defect correction.

Jira, Obsidian, cost tracking, connector comparisons, planted-defect demonstrations, and final
documentation may require additional time after the working MVP closes.

### Following milestones

1. ~~Engineer agent.~~ **COMPLETE**
2. ~~Quality Engineer agent.~~ **COMPLETE**
3. ~~Code-craftsmanship and test-runner skills.~~ **COMPLETE** (2 of the assignment's required
   ≥4 skill packs; `github/` and `jira/` remain future work — see §3, §10)
4. ~~Deterministic orchestrator core.~~ **COMPLETE** as a standalone, fake-adapter-tested engine
   — `core.py` itself is not live-integrated (live dispatch runs through `/work`/`live_cli.py`
   instead, per Option C), but the harness overall is now end-to-end live-verified (see §10
   "Status distinctions" and "Live evidence (`run-20260804-riskband-003`)").
5. ~~Demo repository.~~ **COMPLETE** — `demo-repo/loanflow`, 55 files; automatically tested (102
   tests as of `run-20260804-riskband-003`), test-runner-wrapper and mutation-safety verified
   (see §10 "Demo repository"). Used by a live, completed four-phase orchestrator run.
6. ~~Live Claude Code adapter layer, real test-runner adapter, reasoning-backed Discovery
   adapter, and `/work` prompt mode.~~ **COMPLETE and live-verified** —
   `run-20260804-riskband-003`.
7. Checkpoint, memory, and guardrail hooks. **Partially complete**: pre-dispatch-check,
   skill-enforcement, and completion-guardrail hooks are implemented, configured, and tested
   (see §10 "Hooks and same-run route-back milestone (2026-08-05)") — live-verified for the
   two `PreToolUse` hooks; the `Stop` hook is now live-verified too, for its
   missing-verification-evidence branch specifically (`runs/completion-guardrail-live-001/`,
   see §10 "Live Stop-hook demonstration") — its other four failure branches remain
   deterministic-only. ~~Checkpoint/resume.~~ **COMPLETE (2026-08-06)**: persistence is
   implemented, deterministic resume tests pass, and a genuine two-process live restart reused
   Discovery and Research and restarted Implementation with a fresh Engineer — that live
   resumed invocation later ended blocked during Implementation on transport-budget exhaustion;
   a resumed run reaching a passing Verification remains deterministic-only. See the
   checkpoint/resume milestone notes above for the full, authoritative account. ~~The memory
   loop.~~ **COMPLETE (2026-08-06)**: `memory/facts.jsonl` and `memory/lessons-learned.md`
   are implemented, deterministically tested (62+ new tests, plus a post-audit hardening
   pass), and demonstrated via a real, two-run `live_cli.py` CLI demonstration (Run A/
   Run B) — not a live `/work` pipeline run — see the memory-loop milestone notes above.
   ~~The post-agent cost hook.~~ **COMPLETE (2026-08-14)**: `record_agent_usage.py` +
   `usage.py`, live-demonstrated against a real single-agent dispatch — see the
   "Milestone update (2026-08-14)" note above.
8. ~~First complete evidence-backed pipeline run.~~ **COMPLETE** — `run-20260804-riskband-003`,
   `final_verdict: "pass"`. See §10 "Live evidence (`run-20260804-riskband-003`) — completed."
9. Deferred integrations and final assignment demonstrations (includes `github/`/`jira/` skill
   packs, Jira/Obsidian connectors, cost tracking, and planted-defect demos). **Not started.**
