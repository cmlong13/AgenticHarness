# Final Assignment Audit

Audit date: **2026-10-03** (replaces the 2026-10-01 audit). Every row was re-evaluated from
scratch against code, tests and retained run evidence; old statuses were not carried over.

Repository state audited: `main` at `681fddf` ("fix: harden research skip and workflow
contracts"), with the Obsidian renderer fix, the Run A/B demo-repo changes, three Obsidian
proof run directories and memory lines staged but not yet committed.

**Final submitted state (2026-10-04).** All of those changes are now committed and pushed:
`794f909` (renderer fix, Run A/B diffs, obsidianlive run directories, memory lines) and
`ef29367` (this audit, `docs/final-writeup.md`, `PROJECT_SPEC.md`, and the retained
`runs/run-20260929-percentage-001/`). `main` is in sync with `origin/main` and the working
tree is clean. Nothing in the submission depends on an uncommitted file. See "Exact test and
repository results" for the final numbers.

Sources, in order of authority:

1. `ASSIGNMENT.md`: the immutable external requirements. Every row cites its section.
2. `PROJECT_SPEC.md`: internal specification, used only where it does not conflict with
   `ASSIGNMENT.md`. It is not evidence by itself.
3. Repository evidence: production code, agent/skill/hook definitions, tests, and retained
   run directories under `runs/`, plus read-only audit-time checks of the external
   Obsidian vault.

Evidence kinds:

- **impl**: production code or agent/skill/hook definitions.
- **test**: deterministic automated tests (fakes or injected seams).
- **live**: a retained run in which real agents, real subprocesses, or real external
  systems were exercised.

Nothing was committed, pushed, staged, cleaned or deleted to produce this audit. No
production code, agent contract, skill, hook or `PROJECT_SPEC.md` text was changed. The
only file intentionally modified is this one.

### Status definitions (strict)

- **PROVEN**: implementation and/or retained live evidence demonstrates the requirement.
  Where the assignment does not ask for a live demonstration, strong implementation plus
  tests are enough.
- **PARTIAL**: a meaningful portion exists, but an explicit part of the requirement lacks
  implementation or proof.
- **MISSING**: materially absent.
- **N/A**: genuinely does not apply.

---

## Summary

| Status | Count |
|---|---|
| **Total audited rows** | **79** |
| PROVEN | **71** |
| PARTIAL | **5** |
| MISSING | **0** |
| N/A | **3** |

**Closeout update (2026-10-03).** After `docs/final-writeup.md` was written, rows 77–79 were
re-evaluated and the totals above were recomputed from the matrix:

- Row 77 is now PROVEN (was PARTIAL).
- Row 78 is now PROVEN (was MISSING).
- Row 79 was first moved to PARTIAL (was MISSING) while only the cleanup policy was written.

**Final cleanup update (2026-10-03).** After the repository cleanup was performed and
`docs/final-writeup.md` §4 was updated with the actual deletions:

- Row 66 is now PROVEN (was PARTIAL). The only remaining untracked item,
  `runs/run-20260929-percentage-001/`, is intentionally retained evidence.
- Row 79 is now PROVEN (was PARTIAL).

Since this audit was first written, the previously staged changes have been committed as
`794f909`: the Obsidian renderer fix and its test, the Run A/B `demo-repo` diffs, the staged
memory lines, and the three obsidianlive run directories. The closeout documents and
`percentage-001` followed in `ef29367`. Rows below have been updated to refer to the committed
files.

**Acceptance criteria (§4): 11 PROVEN, 1 PARTIAL (AC-1, live Jira), 0 MISSING.**

### Remaining PARTIAL rows (5)

| Row | Requirement | Gap type | Gap |
|---|---|---|---|
| 6 | Pre-dispatch checklist and refusal | implementation (small) + proof | Refusal is implemented and tested, but `/work` never spells out the §2.1 checklist questions ("core product or decoration?", "health signal?"), and no retained run is `refused`. |
| 35 | QE verdicts include coverage numbers | implementation | The `coverage` field exists in the schema, but no verification report has ever contained one. |
| 42 | GitHub connector: read issues/PRs, search code, verify pushes | implementation (small) | There is no issue-read procedure. PR read and code search exist but are not wired into any phase. |
| 45 | Jira: intake, status transitions, completion comment | implementation + external proof | Transitions and comments are deliberately not implemented. No live Jira credentials exist. |
| 54 | Memory as small single-fact files with an index | implementation (design divergence) | Memory is two aggregate files (`facts.jsonl`, `lessons-learned.md`), one fact per line, with no per-fact files and no index. |

### Remaining MISSING rows (0)

None. Rows 78 and 79 were MISSING until `docs/final-writeup.md` was written (closeout update).

### Changes since the 2026-10-01 audit

| Row | Requirement | Was | Now | Basis |
|---|---|---|---|---|
| 3 | Explicit Research skip | PARTIAL | PROVEN | `681fddf` makes the skip mechanically real. Proven by impl + tests; no live skip run. |
| 27 | Extend rather than replace | PARTIAL | PROVEN | `engineer.md` Step 5 now states it and requires reading the craftsmanship skill. |
| 41 | Skills exercised in the pipeline | PARTIAL | PROVEN | Three live Engineers read `code-craftsmanship` before their first edit. GitHub/Jira caveat retained. |
| 44, 69, AC-9 | Obsidian write-back from `/work` | PARTIAL | PROVEN | Runs A, B and C published from real `/work` runs. The B → C cross-run chain was retrieved in a clean clone. |
| 65 | One canonical `.claude/` | PROVEN with cwd caveat | PROVEN, caveat closed | Hooks are `${CLAUDE_PROJECT_DIR}`-anchored and tested from a nested cwd. |
| 73 | Architecture diagram | PROVEN, stale | PROVEN | `docs/architecture.md` was refreshed in `681fddf` (skip, backoff, craftsmanship). |
| 66 | Cut, don't gate | PARTIAL | PROVEN | Cleanup performed; `percentage-001` retained as evidence. |
| 79 | Write-up: what was deleted | MISSING → PARTIAL | PROVEN | `docs/final-writeup.md` §4 now records the actual deletions. |

---

## Requirement Matrix

| # | Source | Requirement | Status | Evidence | Caveat / notes |
|---|---|---|---|---|---|
| 1 | §2.1, §2.7 | `/work` accepts ticket-ID mode | PROVEN | impl: `.claude/skills/work/SKILL.md` "Ticket-mode Jira resolution" (line 262 onward). Ticket mode runs `resolve_jira_issue` before Discovery and checks requested-key vs returned-key identity. test: `tests/test_work_skill.py`, `tests/test_orchestrator_jira_connector.py`. live: `runs/run-20260909-connectorroute-001/` (intake path ran, honest `connector_unavailable`) | The intake mechanism is proven. A real ticket resolved live is AC-1. |
| 2 | §2.1, §2.7 | `/work` accepts free-form prompt mode | PROVEN | live: every full run, e.g. `runs/run-20261002-obsidianlive-004/live-proof/prompt-C.txt`, `runs/run-20260929-costproof-001/usage-summary.json` | |
| 3 | §2.1 | Decompose into a task graph and decide which phases are needed, explicitly skipping unneeded ones (typo fix: no Research) | PROVEN | impl: `harness/evidence.py:200-218`. `research_skip_reason` is valid only on an approved scope, must be non-blank, and the task graph must omit any research node. `evidence.py:222-228`: the implementation report must omit `findings_ref` when Research was skipped. `evidence.py:449-526`: checkpoint `skipped_phases` satisfies phase ordering, must not also be completed, current or next, and `complete` needs only the three remaining phases. `harness/orchestrator/checkpoint.py:100-104` `skipped_phases_for` (a single source), `:417-425` resume refuses a checkpoint skip the scope never declared. `core.py:1110-1118` emits a `phase_skipped` event. `core.py:876-877` writes `phases_skipped` to the run summary. `.claude/hooks/pre_dispatch_check.py:117-127` admits the Engineer on an approved skip scope and refuses an Architect dispatch for a skipped run. `work/SKILL.md:753-759,790-793,1417-1423`. **Implementation is never skippable**: only `research` can appear in `skipped_phases`. test: `tests/test_research_skip.py` (scope declaration, report, checkpoint, core run, resume, live validation context, pre-dispatch hook) | **No dedicated live `/work` skip run.** No retained `scope.json` declares `research_skip_reason`, and no `policy-events.jsonl` contains `phase_skipped`. This is proven by implementation and tests only. The assignment does not list the skip as a live acceptance criterion. |
| 4 | §2.1 | Orchestrator runs in the main session, not as a subagent | PROVEN | impl: `work/SKILL.md:8-15`. live: `runs/run-20260929-costproof-001/session-result.json` `subagent_stats` (`max_depth: 1`, `spawned_by_subagents: 0`) | |
| 5 | §2.1, §2.6 | Read `lessons-learned.md` before dispatching anything | PROVEN | impl: `work/SKILL.md` Phase 0, `live_cli.py::op_load_memory`. live: `memory_loaded` is the first event in `runs/run-20261001-midimplresume-002/logs/policy-events.jsonl`, and every resume re-loads | |
| 6 | §2.1 | Pre-dispatch checklist; the orchestrator may refuse work, with reasons | **PARTIAL** | impl: `work/SKILL.md:767-784` (`status: "refused"` + `refusal_reason`, `state: "discovery_refused"`, no Architect dispatch, no checkpoint). `evidence.py:194-198` (no `refusal_reason` on an approved scope). test: refusal cases in `tests/` (13 references to `refused` / `discovery_refused`) | **Checklist content is not encoded.** `/work` defers to "the pre-dispatch checklist in `ASSIGNMENT.md` §2.1" with only "Protected Path / not a real product change" as examples. It never states the health-signal question, and `scope.schema.json` records no checklist result. **Never exercised live:** no retained `scope.json` is `refused`. |
| 7 | §2.5 | Architect dispatch gated on an approved scope | PROVEN | impl: `pre_dispatch_check.py` `PRECONDITIONS["architect"]`, registered at `.claude/settings.json` (`PreToolUse` / `Agent`). live: refusal in `runs/run-20261001-midimplresume-002/logs/policy-events.jsonl` event 62 | |
| 8 | §2.5 | Engineer dispatch gated on Architect output (or a declared skip) | PROVEN | impl: `pre_dispatch_check.py` (findings, or an approved identity-matching skip scope). test: `tests/test_research_skip.py::TestPreDispatchHook`, `tests/test_hooks.py` | |
| 9 | §2.5 | QE dispatch gated on `ready_for_verification` | PROVEN | impl: `pre_dispatch_check.py` `PRECONDITIONS["quality-engineer"]` | |
| 10 | §1 cardinal rule | Artifacts validated between phases before being trusted | PROVEN | impl: `live_cli.py` `op_validate_scope` / `op_validate_artifact` / `op_promote_artifact`. live: `validate_*` / `promote_artifact` requests in every full run, e.g. `runs/run-20261002-obsidianlive-004/requests/D-2.json`, `D-3.json` | |
| 11 | §1, §2.1 | Orchestrator independently verifies completion claims | PROVEN | live: orchestrator re-runs `ORCH-1` / `ORCH-2` in `runs/run-20261002-obsidianlive-004/logs/` (7 passed; 113 passed), `runs/run-20261001-midimplresume-002/logs/ORCH-*.log`, `runs/run-20260929-costproof-001/logs/ORCH-*.log`; `check_command_identity` requests in each run | |
| 12 | §2.5 | Terminal completion gated on independent evidence | PROVEN | impl: `.claude/hooks/completion_guardrail.py` (`Stop`). live: row 50 | |
| 13 | §2.2 | `architect.md` frontmatter (name, description, tools, model) | PROVEN | `.claude/agents/architect.md:1-6`. test: `tests/test_agent_definitions.py` | `model: inherit` |
| 14 | §2.2, §4 | Architect physically cannot edit source | PROVEN | `architect.md:4` = `Read, Grep, Glob, mcp__obsidian__search_notes, mcp__obsidian__read_note`. live: `runs/architect-boundary-test/`, `docs/architect-permission-verification.md` | Enforced by the runtime tool allowlist. |
| 15 | §2.2 | Evidence hierarchy | PROVEN | `architect.md` evidence-hierarchy section | |
| 16 | §2.2 | Found / Not Found / Inferred labels | PROVEN | `architect.md`; `harness/schemas/findings.schema.json` enum | |
| 17 | §2.2, §3.1 | Every claim carries `file:line` | PROVEN | schema-enforced. live: `runs/run-20261001-midimplresume-002/findings.json`, `runs/run-20261002-obsidianlive-004/findings.json` | |
| 18 | §2.2 | Search the whole repo; never stop at the first match | PROVEN | `architect.md` search rules | Instruction only; not separately testable. |
| 19 | §2.2 | Distinguish current code from legacy | PROVEN | `architect.md` search rules. live: `runs/run-20261002-obsidianlive-004/findings.json` F-12 classifies two vault notes STALE against the current checkout | |
| 20 | §2.2 | Document failed searches | PROVEN | `findings.schema.json` requires `search_attempts` on `not_found`. live: `runs/run-20261001-midimplresume-002/findings.json` F-7 (3 attempts) | |
| 21 | §2.2, §2.4 | Architect has a read-only docs connector (Obsidian) | PROVEN | `architect.md:4`. live: `runs/run-20260909-connectorroute-001/obsidian/read/mcp-read-1.json`; `runs/run-20261001-obsidianlive-003/obsidian/read/architect-mcp-{1,2}.json` | |
| 22 | §2.2 | `engineer.md` frontmatter | PROVEN | `.claude/agents/engineer.md:1-6` | |
| 23 | §2.2 | Engineer has full edit tools and nothing beyond | PROVEN | `engineer.md:4` = `Read, Grep, Glob, Edit, Write`. live: `runs/engineer-boundary-test/` | |
| 24 | §2.2 | Engineer verifies the architect's claims before coding | PROVEN | `engineer.md` Step 1–3. live: `findings_ref.finding_ids` in `implementation-report.json` of `costproof-001` and `midimplresume-002` | |
| 25 | §2.2, §3.3 | Minimal-change ladder | PROVEN | `engineer.md` Step 5 (5 rungs). live: `minimal_change_rung` recorded in each sampled report | |
| 26 | §2.2 | One failing test first (TDD) | PROVEN | `engineer.md` Step 6. live: `runs/run-20261001-midimplresume-002/interruption-proof/pre-kill/demo-repo-git-diff.patch` (test only, no source yet). In obsidianlive-002/003/004 the Engineer's first `Edit` targets a test file (stream records 372, 252, 360) | |
| 27 | §2.2 | Extend rather than replace working code | PROVEN | impl: `engineer.md:175-185` (Step 5: "extend rather than replace working code, and make no drive-by edits"), plus `code-craftsmanship/SKILL.md` checklist item 4. live: sampled diffs add guards without rewriting, e.g. the `demo-repo/src/loanflow/applicants.py` diff in `794f909` (+2 lines) | Closed since the last audit: the rule is now in the Engineer contract. |
| 28 | §2.2, §4 | No new dependency without written justification | PROVEN | `engineer.md` Step 4. live: `dependency_changes: []` in every sampled report | The justification path has never been exercised; no task needed a dependency. |
| 29 | §2.2 | `quality-engineer.md` frontmatter | PROVEN | `.claude/agents/quality-engineer.md:1-6` | |
| 30 | §2.2 | QE cannot modify source | PROVEN | `quality-engineer.md:4` = `Read, Grep, Glob` | QE has no write-capable tool at all. |
| 31 | §2.2 | Narrowest test command | PROVEN | `quality-engineer.md` Step 7. live: `runs/run-20261002-obsidianlive-004/requests/IR-1.json` (V-1, one test file) before `IR-2.json` (V-2, `tests/unit`) | |
| 32 | §2.2 | Classify every failure | PROVEN | live: `runs/run-20260919-flaky-001/verification-report.json` (`infrastructure_flake`), `runs/run-20260918-logicrepair-003/verification-report.json` (`logic_bug`) | |
| 33 | §2.2 | Retry infrastructure failures **with backoff (max 3)** | PROVEN | impl: `harness/orchestrator/core.py:103` `INFRA_RETRY_BACKOFF_SECONDS = (2.0, 4.0)`, `:104` `MAX_INFRA_RETRIES` (2 retries, so 3 executions), `:108` `_backoff_sleep` (the single sleep seam). `:239` `plan_infrastructure_retry` refuses an undeclared re-run, any `retry_of` not classified `infrastructure_flake`, a different command or `working_directory`, a `retry_of` that is not the most recent failed attempt, a retry of a passing attempt, and anything past the ceiling. It waits before retaining `infrastructure_retry_backoff`. `:332` `check_retry_consistency` reconciles the report's classifications against executed retries. Live seam: `live_cli.py` `op_prepare_infrastructure_retry` / `op_check_retry_consistency` rebuild prior exit codes from retained logs. test: `tests/test_infrastructure_retry_backoff.py` (2 s then 4 s, 4th execution refused, logic bug not retried, parity between the live path and core) | **Two separate proofs.** (a) **Live flaky classification and identical retry:** `runs/run-20260919-flaky-001`, which predates `5c29656` and contains no backoff. (b) **Backoff enforcement:** proven by code and tests only. Post-`5c29656` live runs did call the gate before QE attempts (`runs/run-20261002-obsidianlive-004/requests/IR-1.json`, `IR-2.json` returned `"retry": null`; `RC-1.json` consistency check). No live run has hit a flake since, so **no live backoff wait exists**. The gate is instruction-invoked by `/work`, not hook-enforced. |
| 34 | §2.2 | Never retry logic failures | PROVEN | impl: `core.py:239` refuses a non-flake `retry_of`. test: `tests/test_infrastructure_retry_backoff.py`. live: `runs/run-20260918-logicrepair-003/verification-report.json` (`retried: false`) | |
| 35 | §2.2 | Verdicts backed by exit codes, report paths **and coverage numbers** | **PARTIAL** | Exit codes and log paths are in every `attempts[]` entry. The schema has an optional `coverage` field, and `quality-engineer.md:416` forbids inventing coverage | `grep '"coverage"' runs/*/verification-report*.json` returns **zero** hits, including the newest runs. Coverage numbers have never been produced. |
| 36 | §2.3 | `github/` skill pack: `pr-create`, `read-file`, `search-code`, `commit-history`, `pr-review` | PROVEN | impl: `.claude/skills/github/SKILL.md` §11. One function per procedure in `harness/orchestrator/github.py` (`read_file`, `search_code`, `commit_history`, `pr_review`, `pr_create`), plus a `live_cli` op each. `pr_create` order: authorization first, then validation, then `verify_push` (real `git ls-remote`), then `gh pr create`, then an independent re-fetch. test: `tests/test_orchestrator_github.py`, `tests/test_orchestrator_live_cli.py` | **Packaging:** the five procedures are sections of one `SKILL.md`, each a separate thin wrapper and op, not five skill directories. **Live:** `verify_push` only (AC-8); the other four have never run in a retained run. `pr_create` has never run live. **Defect (still present):** `github.py:92` `subprocess.run(..., text=True)` has no `encoding="utf-8"`, so local `read_file` mis-decodes non-ASCII on Windows. |
| 37 | §2.3 | `jira/` skill pack: `create-ticket`, `read-ticket`, `edit-ticket`, plus a field reference | PROVEN | impl: `.claude/skills/jira/SKILL.md`, `.claude/skills/jira/FIELD_REFERENCE.md`. `harness/orchestrator/jira_connector.py`: `CREATE_FIELDS` / `EDIT_FIELDS`, `CUSTOM_FIELD_IDS = {}`, explicit authorization (`authorized: true` + `authorization_source`) checked before validation or network, `expected_base_url` must match, `create_issue` / `edit_issue` re-read for identity, 5xx gives `indeterminate` with no retry. `connector_router.py` sends mutations to REST only. test: `tests/test_orchestrator_jira_connector.py` (including a field-reference drift check), `test_orchestrator_connector_router.py`, `test_orchestrator_live_cli.py` | **Implementation and tests only.** No `JIRA_*` credentials exist (verified: 0 `JIRA_*` env vars). No retained run reads, creates or edits a real ticket. Same one-`SKILL.md` packaging caveat as row 36. |
| 38 | §2.3 | Test-runner skill encoding repo conventions | PROVEN | `.claude/skills/test-runner/SKILL.md`, `scripts/run_command.py`. live: `skill_invocation` events for test-runner (`C-*`, `V-*`, `ORCH-*`) in `runs/run-20261002-obsidianlive-004/logs/policy-events.jsonl` | |
| 39 | §2.3 | Code-craftsmanship skill (ladder plus YAGNI), standalone | PROVEN | `.claude/skills/code-craftsmanship/SKILL.md` (12-item checklist, Protected Path list, boundaries). live: `runs/code-craftsmanship-ab-test/grading-summary.md` | Existence and content. Wiring and use are row 41. |
| 40 | §2.4 | Obsidian skill | PROVEN | `.claude/skills/obsidian/SKILL.md`. live: `skill_invocation` (`obsidian`) in `runs/run-20261002-obsidianlive-004/logs/policy-events.jsonl` | |
| 41 | §2.3 | Skills are actually exercised in the pipeline | PROVEN | **Wiring:** `engineer.md:175-185` requires `Read` of `.claude/skills/code-craftsmanship/SKILL.md` before the first `Edit`/`Write`, and to apply it without expanding scope. `work/SKILL.md:948-949`; `tests/test_agent_definitions.py`. **Live use:** an Engineer subagent `Read` the skill before its first edit in each of three real `/work` runs. `runs/run-20261001-obsidianlive-002/live-proof/process-1-stream.jsonl`: dispatch at record 328, read at 362, first edit at 372. `run-20261001-obsidianlive-003`: 194 → 208 → 252. `run-20261002-obsidianlive-004`: 316 → 349 → 360. test-runner and obsidian are invoked via the Skill tool in the same runs (row 38, row 40). | **Not exercised in any `/work` run:** the GitHub pack procedures (only `verify_push`, outside `/work`, AC-8) and the Jira pack (no credentials). The craftsmanship read is proven from transcripts; its effect on any one diff is not separately measured beyond the A/B test. |
| 42 | §2.4 table | GitHub connector in Discovery/Research/Verification: read issues/PRs, search code across repos, verify pushes | **PARTIAL** | Verify pushes: live (row 62). Read PRs: `pr_review`. Search code: `search_code` (row 36) | **No issue-read procedure exists.** PR read and code search are `/work` operations (`work/SKILL.md` operations table) but no Discovery, Research or Verification step calls them, and the Architect has no GitHub tool. No retained `/work` run uses any of them. |
| 43 | §2.4 table | Obsidian read in Discovery/Research | PROVEN | live: `runs/run-20261002-obsidianlive-004/obsidian/read/search-1.json` and `read-{1,2,3}.json` (Discovery, hash-recorded). Architect MCP reads: `runs/run-20261001-obsidianlive-003/obsidian/read/architect-mcp-*.json`. Earlier: `runs/run-20260909-connectorroute-001/obsidian/read/` | |
| 44 | §2.4 table | Orchestrator writes summaries back to Obsidian | PROVEN | impl: `obsidian.py::publish_run_summary`, `live_cli.py::op_publish_run_summary`. live, from real `/work` runs, `status: "published"`, with on-disk SHA-256 re-checked during this audit: `runs/run-20261001-obsidianlive-002/obsidian/summary-publication.json` (`338a671b…`, matches), `runs/run-20261001-obsidianlive-003/…` (`f743b90d…`, matches), `runs/run-20261002-obsidianlive-004/…` (`4799f3ed…`, matches) | All three published notes contain the renderer defect (see "Defects found during the Obsidian proof"). They were intentionally not rewritten. |
| 45 | §2.4 table | Jira: ticket intake, **status transitions, completion comment** | **PARTIAL** | Intake: impl + tests (row 1). Create and edit: row 37 | **Status transitions and completion comments are deliberately not implemented** (`jira/SKILL.md:122-130`; requests are refused as `unsupported_field`). No live credentials, so even intake has never touched a real Jira. |
| 46 | §2.4 | One connector with both MCP and REST, documented | PROVEN | `jira_connector.py` (REST, kept), `harness/mcp/jira_server.py` + `mcp_client.py` (MCP), `connector_router.py`, `.mcp.json`, `jira/SKILL.md` MCP-vs-REST section. live: `runs/run-20260909-connectorroute-001/CONNECTOR-ROUTE-PROOF.md` (both transports actually ran) | The resolved-ticket path is test-only, for lack of credentials. The write-up part is row 77. |
| 47 | §2.5 | Skill-enforcement hook | PROVEN | `.claude/hooks/skill_enforcement.py` (`PreToolUse` / `Bash`). live: refused two orchestrator Bash writes (`runs/run-20261001-midimplresume-002/logs/policy-events.jsonl` event 62) | That event shows a false positive: it blocks writes under `runs/` whose text mentions `demo-repo`. |
| 48 | §2.5 | Pre-dispatch check hook | PROVEN | `pre_dispatch_check.py`. live: refusal in event 62 above. test: `tests/test_hooks.py`, `tests/test_research_skip.py`, `tests/test_hook_registration.py` | It parses only JSON-quoted `"task_id"` / `"run_id"`, so it fails closed on other prompt shapes. |
| 49 | §2.5 | Completion-guardrail hook (implementation) | PROVEN | `completion_guardrail.py`; `tests/test_hooks.py::TestCompletionGuardrail` | |
| 50 | §2.5, §4 | Guardrail demonstrably blocks a run with deleted verification evidence | PROVEN | live: `runs/completion-guardrail-live-001/STOP-HOOK-LIVE-BLOCK-EVIDENCE.md` (the real registered `Stop` hook blocked: "verification-report.json is missing") | |
| 51 | §2.5 | Post-agent hook extracting usage | PROVEN | `.claude/hooks/record_agent_usage.py` (`PostToolUse` / `Agent` and `SubagentStop`). live: `runs/run-20260929-costproof-001/usage/*.json`; `runs/run-20261002-obsidianlive-004/usage/*.json` (4 records) | |
| 52 | §2.6 | Read `lessons-learned.md` before every run | PROVEN | row 5 | |
| 53 | §2.6 | At most 5 bullets appended per run; capped | PROVEN | `harness/orchestrator/memory.py` `MAX_LESSONS_PER_RUN = 5`. live: Run B and Run C each appended 1 (`run-summary.json` `lessons_learned_appended`); `midimplresume-002` appended 4 | **No absolute file cap.** `memory/lessons-learned.md` has 20 bullets at `ef29367`. |
| 54 | §2.6 | Persistent memory directory of **small single-fact files with an index** | **PARTIAL** | `memory/facts.jsonl` (11 facts at `ef29367`) and `memory/lessons-learned.md`. Each entry has an id, evidence path, absolute date and source run, and is schema-checked on load | The structure is two aggregate files, one fact per line, not one file per fact with an index. The intent (small, durable, dated, evidence-backed facts) is met; the literal shape is not. |
| 55 | §2.6 | Convert relative dates to absolute | PROVEN | `work/SKILL.md` memory-append step; every entry uses `YYYY-MM-DD` | |
| 56 | §2.6, §4 | A second run visibly uses a lesson from the first | PROVEN | live (repository memory): `runs/run-20261001-midimplresume-002/run-summary.json` `memory_refs_used` includes `L-20261001-INSCOPE-BARE-PATHS` (written by `-001`), with a `memory_applied` event. Earlier: `runs/run-20260918-logicrepair-003`. live (vault memory): Run B → Run C, see AC-9 | |
| 57 | §2.6 | Checkpoint state so interrupted runs auto-resume | PROVEN | `live_cli.py::op_write_checkpoint`, `op_evaluate_resume`; `harness/evidence.py` checkpoint semantics; `tests/test_orchestrator_checkpoint.py`, `tests/test_research_skip.py` | |
| 58 | §2.6, §4 | Kill mid-Implementation; the rerun resumes from the checkpoint | PROVEN | live: `runs/run-20261001-midimplresume-002/`; see AC-10 | Also shown again by `runs/run-20261002-obsidianlive-004` process 1 → 2 (a usage-limit stop during Implementation, resumed). |
| 59 | §2.5, §4 | State the total token/cost for one full pipeline run | PROVEN | live: `runs/run-20260929-costproof-001/usage-summary.json` `full_pipeline_total.cost.amount` = **$9.5721282**; see AC-11 | Produced by an operator-run `finalize_pipeline_usage` after a dedicated headless session. In-session runs report `full_pipeline_total: null`. |
| 60 | §3.1 | Evidence over assertion | PROVEN | schemas plus `harness/evidence.py` validators; consistent across runs | |
| 61 | §3.2 | Distrust completion claims | PROVEN | rows 11 and 62 | |
| 62 | §1, §4 | Catch a simulated false "pushed" claim via `git ls-remote` | PROVEN | live: `runs/run-20260929-falsepush-001/`; see AC-8 | |
| 63 | §3.3 | Minimal diff | PROVEN | `runs/run-20261001-midimplresume-002/logs/implementation.diff`; Run A/B diffs in `794f909` (+2 source lines, +9 test lines each); `runs/run-20261002-obsidianlive-004/diff.patch`; `code-craftsmanship-ab-test` | |
| 64 | §3.4 | Health signal for formula/config/data-path changes | PROVEN | `demo-repo/src/loanflow/health.py`. live: `run-20260919-flaky-001` added `check_batch_size` | The newest runs changed input validation, not a formula, config or data path. |
| 65 | §3.5 | Exactly one canonical `.claude/`; config referenced by absolute path | PROVEN | `find . -name .claude -type d` (excluding `.venv`) gives only `./.claude`. All 5 hook commands in `.claude/settings.json` are `python "${CLAUDE_PROJECT_DIR}/.claude/hooks/<x>.py"`. test: `tests/test_hook_registration.py` runs the registered command strings through Git Bash from a nested `runs/run-x/deep` directory. It shows they allow and block correctly there, that an unresolved project dir fails closed, and that the old relative form reproduces the "can't open file" defect. These tests ran (not skipped) in this audit. | **Previous cwd caveat closed.** The 2026-10-01 audit saw a hook fail from a nested cwd. In this audit, Bash calls made from `runs/` and `runs/run-20261002-obsidianlive-004/` passed through the registered `PreToolUse` hook without error (audit-time observation, not retained). The clean clone used by Run C is a sibling directory outside this repository. |
| 66 | §3.6 | Cut, don't gate | PROVEN | `code-craftsmanship` is wired (row 41). Repository cleanup done 2026-10-03: no `.obsidian/` directory remains anywhere in the tree; `runs/_unmatched_usage/` and `runs/_usage_corroboration/` contain only their two tracked files; `runs/run-20260929-percentage-002/` is gone; the stale unstaged September `memory/*` lines were reverted (`memory/` is clean). The closeout changes were committed in `ef29367`; `git status` is clean. Documented in `docs/final-writeup.md` §4 | `runs/run-20260929-percentage-001/` (54 files, about 0.10 MB) is deliberately retained and was committed in `ef29367`: it is a genuine failed Engineer transport-contract incident that committed `costproof-001` evidence and lesson `L-20260929-EXPLICIT-CONTRACT-REMINDER-PER-TURN` cite. `runs/run-20260914-mcpaudit-001` is already-committed history and was left as is. The external clean clone `AgenticHarness-obslive004-clean` lives outside the repository and is not part of it. |
| 67 | §3.7 | Verification issues loop back in the same run | PROVEN | live: `runs/run-20260918-logicrepair-003` | |
| 68 | §4 | Planted flaky test retried; planted logic bug routed back and fixed in the same run | PROVEN | AC-5 and AC-6 | |
| 69 | §4 | Obsidian vault receives a run summary | PROVEN | row 44; AC-9 | |
| 70 | §4 | `lessons-learned.md` gains ≤ 5 bullets; a second run uses a lesson | PROVEN | rows 53 and 56 | |
| 71 | §4 | Repo has ≥ 50 files | PROVEN | `git ls-files demo-repo \| wc -l` = **59** | |
| 72 | §6 | No dashboard/UI, no speculative multi-user/marketplace, no mega-agent | PROVEN | No web/UI code. The three agents' tool lists are narrow (rows 14, 23, 30) | |
| 73 | §5.1 | Architecture diagram | PROVEN | `docs/architecture.md` (Mermaid plus legend), refreshed in `681fddf`: Research skip edge, orchestrator-mediated 2 s / 4 s backoff, code-craftsmanship skill, Obsidian read and write-back | `docs/final-writeup.md` §1 summarizes it and links to it. |
| 74 | §1 | Motivating "fabricated push" incident | N/A | Background for rows 11, 61, 62 | |
| 75 | §2.7 | `/work TICKET-123 my-repo` two-argument form | N/A | Single-target-repo design documented in `work/SKILL.md` "Parsing $ARGUMENTS" | Illustrative example; a reasoned divergence. |
| 76 | closing note | "Start with the smallest loop…" | N/A | Guidance, not a requirement | |
| 77 | §2.4, §5.2 | Write-up: MCP-vs-REST decision with evidence | PROVEN | `docs/final-writeup.md` §2 covers: REST kept and MCP built for parity; the `rest_first` / `mcp_first` router; the rule that a fallback never masks an authoritative or authorization outcome; and REST-only Jira mutations, because a timed-out non-idempotent write could otherwise be duplicated and an MCP write tool would bypass `live_cli.py` authorization and evidence retention. It cites `jira_connector.py`, `connector_router.py`, `harness/mcp/jira_server.py` and `CONNECTOR-ROUTE-PROOF.md` | The write-up says plainly that no Jira credentials existed. Only the two transports' `connector_unavailable` behavior is live. The resolved and mutation paths are test-only. |
| 78 | §5.3 | Write-up: one surprising agent behavior and the guardrail added | PROVEN | `docs/final-writeup.md` §3 tells the story of `run-20261001-midimplresume-001`. An annotated `in_scope` entry passed `validate_scope`, and the Engineer then blocked on exact-path matching (`implementation-report.json` `blocked_reason`). The fix is the bare-path check in `harness/evidence.py` and `tests/test_artifact_contracts.py::test_scope_annotated_in_scope_entry_is_caught`. The lesson `L-20261001-INSCOPE-BARE-PATHS` was reused by `-002` (`memory_refs_used`) | |
| 79 | §5.4 | Write-up: what was deleted before submitting, and why | PROVEN | `docs/final-writeup.md` §4 states the "cut noise, preserve evidence" rule, lists what was retained (acceptance evidence, successful and useful failed runs, `percentage-001` with its reason) and what was actually removed (`.obsidian/` metadata, unreferenced usage scratch files, `percentage-002`, reverted stale memory additions). The placeholder is gone | Matches the repository state checked for row 66. |

---

## Acceptance Criteria (`ASSIGNMENT.md` §4)

| # | Criterion | Status | Evidence | Caveat |
|---|---|---|---|---|
| AC-1 | `/work <ticket-id>` pulls a **real** ticket via the Atlassian connector and produces a scoped task graph | **PARTIAL** | Ticket-mode intake, REST/MCP routing, key-identity checking and the `resolved` path are proven by impl + tests (rows 1, 37, 46). The live intake path ran honestly to `connector_unavailable` in `runs/run-20260909-connectorroute-001/` | **No real Jira ticket has ever been pulled.** No `JIRA_*` credentials exist (re-verified 2026-10-03). This needs a live external system and cannot be closed from inside the repository. |
| AC-2 | Every finding has a `file:line` citation, with at least one honest "Not Found" | PROVEN | `runs/run-20261001-midimplresume-002/findings.json`: 6 found, **1 not_found (F-7, 3 recorded search attempts)**, 1 inferred; schema-validated and promoted | |
| AC-3 | Architect physically cannot edit source | PROVEN | `architect.md:4`; `runs/architect-boundary-test/`; `docs/architect-permission-verification.md` | |
| AC-4 | Engineer's diff cites the architect finding it builds on; no new dependencies without justification | PROVEN | `implementation-report.json` `findings_ref.finding_ids` and `dependency_changes: []` in `runs/run-20261001-midimplresume-002/` and `runs/run-20260929-costproof-001/` | The justification path for a genuinely new dependency has never been exercised. |
| AC-5 | Planted flaky test classified *infrastructure* and retried | PROVEN | `runs/run-20260919-flaky-001/verification-report.json`: `infrastructure_flake` on V-2 (`ConnectionResetError`), identical retry V-3 passed, not routed back | Live retry predates backoff (`5c29656`). Backoff is proven by code and tests (row 33). |
| AC-6 | Planted logic bug classified *logic*, not retried, routed back and fixed **in the same run** | PROVEN | `runs/run-20260918-logicrepair-003/`: `logic_bug`, `retried: false` → `implementation-report.repair-1.json` → `verification-report.repair-1.json` (`pass`), with the same Engineer and QE identities | |
| AC-7 | Completion guardrail blocks a run with deleted verification evidence | PROVEN | `runs/completion-guardrail-live-001/STOP-HOOK-LIVE-BLOCK-EVIDENCE.md` | Real registered `Stop` hook. |
| AC-8 | Orchestrator catches a simulated false "pushed" claim via `git ls-remote` | PROVEN | `runs/run-20260929-falsepush-001/`; see detail | Dedicated proof fixture, not a spontaneous subagent lie inside a four-phase run. |
| AC-9 | Obsidian receives a run summary; `lessons-learned.md` gains ≤ 5 bullets; a second run visibly uses a lesson from the first | **PROVEN** | Write-back: Runs A, B and C published from real `/work` runs (row 44). Cross-run use: Run B → Run C; see detail. Lessons: rows 53 and 56 | Run B's own verdict was `inconclusive`. Run A → Run C is **not** claimed as applied memory. |
| AC-10 | Kill mid-Implementation; the rerun resumes from the checkpoint | PROVEN | `runs/run-20261001-midimplresume-002/`; see detail | Later interruptions occurred and were themselves resumed. |
| AC-11 | State the total token/cost for one full pipeline run | PROVEN | `runs/run-20260929-costproof-001/usage-summary.json`: **$9.5721282 USD**; see detail | Requires an operator-run post-session finalization. |
| AC-12 | Demonstrated on a repo with ≥ 50 files | PROVEN | 59 tracked files in `demo-repo/` | |

**Result: 11 PROVEN, 1 PARTIAL (AC-1), 0 MISSING.**

### AC-9 detail: Obsidian write-back and cross-run context (Run B → Run C)

Every step below was checked against the raw retained files. The vault-side hashes were
recomputed by this audit from the files currently on disk in
`C:\Users\caleb\Documents\Obsidian Vault`.

1. **Run B was a real `/work` run.** `runs/run-20261001-obsidianlive-003/live-proof/launch-log.json`
   records a headless `claude` launch with the `/work …` prompt in `prompt-B.txt`, with
   `OBSIDIAN_VAULT_PATH` set only in the child environment. All four phases ran
   (`run-summary.json` `phases_completed`). The Engineer and QE were real subagent
   dispatches (`process-1-stream.jsonl`).
2. **Run B's verdict was `inconclusive`, and this is not hidden.** QE passed AC-1, AC-2 and AC-4
   (V-1, 7 passed) but marked AC-3 `blocked`. The Discovery-authored clause "the default 0
   constructs successfully" had no test. `checkpoint.json` is `status: "failed"`. The
   orchestrator's own re-runs (ORCH-1: 7 passed, ORCH-2: 107 passed) were green. Run B
   appended lesson `L-20261002-AC-CLAUSE-NEEDS-TEST`.
3. **Run B wrote that workflow lesson to the vault.**
   `runs/run-20261001-obsidianlive-003/obsidian/summary-publication.json`: `status:
   "published"`, `Harness Run Summaries/run-summary-run-20261001-obsidianlive-003.md`, 3,149
   bytes, `content_sha256 f743b90d…a1ed3b`. Audit-time `sha256sum` of the vault file gives
   the same hash. The note's "Objective" and "Evidence gaps" sections state the
   untested-AC-clause failure and its cause in plain text. The inconclusive verdict does
   not make that context less useful; it is exactly the failure being recorded.
4. **Run C ran in an isolated clean environment.** See
   `runs/run-20261002-obsidianlive-004/live-proof/CLEAN-ENVIRONMENT.md` and
   `clean-baseline.json`:
   - a `git clone --no-hardlinks` sibling clone, detached at committed HEAD `681fddf`
     (the same HEAD as this repository), with an empty `git status`;
   - a scan of 1,574 text files found **0** matches for `obsidianlive-002`,
     `obsidianlive-003`, `T-OBSLIVEA`, `T-OBSLIVEB` and the seed-note id;
   - `runs_obsidianlive_dirs` contains only the September fixture;
   - the clone's `memory/` equals HEAD. HEAD's `lessons-learned.md` has no
     untested-AC lesson (Run B's lesson was not committed until later, in `794f909`);
   - `launch-log.json` records `child_env_mentions_main_repo: []`, and no transcript
     record references the main repository path.
5. **Run C retrieved Run B's exact note from the real vault.** In Discovery,
   `obsidian/read/search-1.json` (query `applicants Business validation ValidationError`,
   3 matches, including the -003 note) was followed by `obsidian/read/read-1.json`:
   `requested_note_path` = the -003 note, `status: "read"`, 3,149 bytes,
   `content_sha256 f743b90d…a1ed3b`. This is identical to Run B's publication hash and to
   the file on disk. `live-proof/at-stop-snapshot/obsidian/read/read-1.json` is
   byte-identical (it was captured before process 1 stopped).
6. **The retrieved information changed a later-run decision.** In
   `live-proof/process-1-stream.jsonl`, the vault requests are records 50 (search) and
   62–66 (reads). Record 90, the orchestrator's Discovery reasoning, says it will follow
   the guard pattern "while avoiding obsidianlive-003's pitfall of untested acceptance
   criteria". This comes before the scope candidate is written (record 164) and promoted
   (`D-3`, record 177). The promoted `scope.json` has four ACs, and every clause maps to
   a named existing test or the explicitly required new regression test. There is no
   unbacked clause of the kind that sank Run B.
7. **Run C completed.** Process 1 stopped on the account usage limit during
   Implementation. Process 2 (`/work --resume`) reused Discovery and Research, restarted
   Implementation and ran Verification:
   - `verification-report.json`: `final_verdict: "pass"`, AC-1 to AC-4 all `passed`;
   - `checkpoint.json`: `status: "complete"` with all four phases;
   - Run C published its own note (`4799f3ed…`, matches on disk).

**What is not claimed.**

- **Run A → Run C is not applied memory.** Run C also read Run A's note (`read-2.json`,
  `338a671b…`), but Research classified it STALE / context-only (`findings.json` F-12).
  The guard idiom Run C used is attributed to the seed note `LFCONV-INTGUARD-01`
  (F-11, corroborated by the repository).
- **Research also marked the -003 note STALE** as a record of current code state (the clean
  clone does not contain Run B's change). The use claimed here is the workflow lesson
  applied in Discovery (record 90), not Run B's code.
- **The vault consultation was prompted.** Run C's prompt told it to consult the vault for
  prior run summaries. It did not name any note; the -003 note was found by search.
- **Run C's appended lesson exists only in the clean clone.** It is not in this working
  tree's `memory/`.

### Defects found during the Obsidian proof

- **Renderer schema-key defect.** `harness/orchestrator/obsidian.py::_tests_line` read
  `attempts[].requested_command.command` and `acceptance_criteria_results[].status == "pass"`.
  The verification schema actually uses `attempts[].command` and `result == "passed"`. The
  usage section read `subagent_subtotal.total_tokens` / `cost_usd` instead of `token_usage`
  / `cost.amount`. Every live note therefore renders an empty test command, `0/4 passed`
  and `None tokens, $None`, even on Run C's passing run. This is visible in all three
  published notes.
- **Status of the fix.** The fix in `harness/orchestrator/obsidian.py` and
  `tests/test_orchestrator_obsidian.py` (new `2/3 passed` and unpriced/partial-cost
  cases) is **committed in `794f909`** (`obsidian.py:364` now checks
  `r.get("result") == "passed"`). `681fddf` and earlier still had the defect.
- **Historical notes were intentionally not rewritten**, so the published hashes stay
  valid live evidence.
- **Impact on AC-9.** The defect affected only the rendered Tests and Usage lines. Run B's
  pitfall reached Run C through the note's Objective and Evidence-gaps text, which
  rendered correctly.

### AC-8 detail: false push (`runs/run-20260929-falsepush-001/`)

- **The claim.** `requests/FP-4-simulated-claim.json` and a `simulated_push_claim` event
  record a deliberately false claim: "pushed `dd3c875…` to
  `falsepush/run-20260929-falsepush-001`". The branch was never created or pushed.
- **Not mocked.** `op_verify_push` calls `github.verify_push` with the default runner, a
  real `subprocess.run(shell=False)`.
- **Detection.** `git/push-verification.json` records the raw command `git ls-remote origin
  refs/heads/falsepush/run-20260929-falsepush-001`, giving `status:
  "remote_ref_missing"` and `observed_sha: null`. The `git_delivery_decision` event is
  `rejected`, `push_claim_accepted: false`.
- **Controls.** The same verifier returned `verified` for the real `main` before and after
  (FP-3 and FP-6). The remote snapshots are identical, so the remote was not mutated.
- **Caveats.**
  - The false claim came from a dedicated proof fixture constructed by the orchestrator,
    not from a spontaneous subagent lie in a four-phase run. The criterion's "simulated"
    wording allows that.
  - The `mismatch` branch (the ref exists at another SHA) is test-only.

### AC-10 detail: mid-Implementation kill and resume (`runs/run-20261001-midimplresume-002/`)

1. **Process 1 was killed during Implementation.**
   - `interruption-proof/kill-driver-log.json` records the trigger state: checkpoint
     `completed_phases: [discovery, research]`, `current_phase: implementation`; Engineer
     `aec739880aee94672` dispatched; `implementation_report_exists: false`.
   - `pre-kill/demo-repo-git-diff.patch` holds only the new failing regression test.
   - The kill was `taskkill /PID 43748 /T /F`. The `pre-kill/` and `post-kill/` snapshots
     are byte-identical apart from their timestamps.
2. **A fresh process resumed via `/work --resume`.** `logs/policy-events.jsonl` events
   9–14: `resume_requested`, then `checkpoint_validated` (`next_phase:
   implementation`), then `phase_reused` for discovery and research, then
   `phase_restarted: implementation`, then a fresh Engineer `ab8aad27d2634043c`. The log
   contains exactly one Architect dispatch, from process 1.
3. **Later interruptions were also recovered.**
   - Process 2 was killed by its outer driver's time limit mid-Verification.
   - Process 3 hit HTTP 429.
   - Processes 3 and 4 each reused the completed phases (events 33–39 and 42–48) without
     redispatching the Architect or the Engineer.

   These are context, not invalidation: each was itself a checkpoint resume.
4. **The run passed.** Event 61 is a checkpoint with `status: complete`. Event 67 is
   `resume_completed` with `final_verdict: "pass"`. `verification-report.json` is
   `pass`.
5. **Integrity notes.**
   - Process 4 overwrote an orchestrator request file, `requests/RESUME-2.json`. This is
     disclosed as event 49 and is not a phase artifact.
   - This run has no cost figure; cost is AC-11.

### AC-11 detail: full-pipeline cost (`runs/run-20260929-costproof-001/`)

The run completed:

- `checkpoint.json` is `complete` with all four phases;
- `verification-report.json` is `pass`;
- `session-result.json` is `success` / `completed`.

The boundary is one dedicated headless session: its main transcript, every subagent
transcript, and runtime-reported usage that no transcript accounts for.

| Component | Cost (USD) |
|---|---|
| Orchestrator (main transcript) | 8.4001400 |
| Architect `a3a8a9c8dfc80d382` | 0.1886318 |
| Engineer `a2e9726aabca6b3fb` | 0.5258566 |
| Quality Engineer `a9e1d213aabd22aa3` | 0.1852557 |
| 5 × test-runner skill forks (labelled `unattributed_subagent`) | 0.2672441 |
| `runtime_unattributed` residual (+500 output tokens) | 0.0050000 |
| **Total** (`full_pipeline_total.cost.amount`, `status: priced`) | **9.5721282** |

**Reconciliation.**

- Tokens: 474 input, 89,034 output, 36,151,441 cache read, 434,902 cache write.
- The transcripts reconcile exactly to the runtime's `modelUsage`, except for the +500
  output tokens, which are carried explicitly as `runtime_unattributed`.
- The runtime's own `total_cost_usd` is `9.572128199999998`.
- Pricing: `harness/model-pricing.json`, verified 2026-08-14.
- The failed `percentage-*` runs are not used as cost evidence.

---

## Special review: what is still materially incomplete?

**Acceptance criteria.** Live Jira (AC-1) is the **only** remaining acceptance-criterion
limitation. It needs credentials that are not available. The implementation and tests for
ticket mode are complete. This is an honest PARTIAL, not a hidden gap.

**Outside the acceptance criteria**, classified:

| Class | Rows | Significance |
|---|---|---|
| **Implementation gaps** | 35 (coverage numbers never produced); 42 (no GitHub issue reading; PR/code-search not wired into phases); 45 (Jira status transitions and completion comments not implemented); 54 (memory is two aggregate files, not single-fact files plus an index); 6, part (checklist questions not encoded in `/work`) | Each is small and none affects pipeline correctness. Row 35 and row 45 are explicit assignment wording, so a strict grader can deduct for them. Row 45 is a deliberate design decision documented in `jira/SKILL.md:122-130`. |
| **Proof / evidence gaps** | AC-1 / row 45 (no live Jira); 6 (refusal never exercised live); 3 (Research skip has no live run; still PROVEN); 33 (no live backoff wait; still PROVEN) | Only AC-1 touches an acceptance criterion. |
| **Deliverable** | 79 (write-up §5.4) | Done: `docs/final-writeup.md` exists and rows 77–79 are PROVEN. |
| **Optional / polish** | 66 (repository hygiene) | Done: cleanup performed and committed (row 66). |

**Additional items found in this audit:**

1. ~~**The Obsidian renderer fix is staged but not committed.**~~ Resolved: committed in
   `794f909`.
2. ~~**Staged index state.**~~ Resolved: the renderer fix, Run A/B `demo-repo` diffs, memory
   lines and the three obsidianlive run directories were committed in `794f909`.
3. ~~**`PROJECT_SPEC.md:16-17` is stale.**~~ Resolved in `ef29367`: the status block now
   records Obsidian write-back as live-proven and points to this audit.
4. **`github.py:92` encoding defect** (row 36) is still present (documented, not fixed).
5. **The skill-enforcement hook has a false positive** (row 47) on `runs/` writes that
   mention `demo-repo`.

**Blocks submission?** No implementation issue blocks submission. Before submitting:

- ~~finalize `docs/final-writeup.md` §4 after cleanup~~ (done 2026-10-03, row 79);
- ~~make the row 66 cleanup decisions~~ (done 2026-10-03, row 66);
- ~~commit (or deliberately drop) the staged renderer fix and proof runs~~ (committed in
  `794f909`);
- state AC-1 plainly as proven by implementation and tests only, for lack of credentials.

---

## Exact test and repository results

### Final submitted state (re-run 2026-10-04 at `ef29367`)

| Command | Result |
|---|---|
| `python -m pytest -q` | **1616 passed, 2 skipped** in 197.74 s, exit 0. Both skips are `tests/test_orchestrator_obsidian_reader.py:142,167`, "symlinks not permitted in this environment". |
| `python -m pytest demo-repo/tests -q` | **114 passed** in 5.10 s, exit 0 (includes the Run A/B regression tests committed in `794f909`). |
| `git status -sb` | `## main...origin/main`: in sync, working tree clean. |

### Original audit run (2026-10-03 at `681fddf`)

At audit time the suite gave the same 1616 passed / 2 skipped (180.07 s), `git diff --check`
was clean, and `main` was in sync at `681fddf`. `git status --short` showed 508 entries: the
staged changes later committed in `794f909`, this audit (unstaged), and untracked
`.obsidian/` metadata, `percentage-001`/`-002` and usage quarantine files. The cleanup
recorded in row 66 removed the `.obsidian/` metadata, `percentage-002` and the unreferenced
quarantine files; `percentage-001` was committed in `ef29367`.

### Audit-time read-only checks (not retained under `runs/`)

| Check | Result |
|---|---|
| `sha256sum` of the 3 published vault notes against `summary-publication.json` | all match (`338a671b…`, `f743b90d…`, `4799f3ed…`) |
| Run C `read-1.json` hash vs Run B publication hash | identical (`f743b90d…`) |
| Engineer `Read` of `code-craftsmanship/SKILL.md` before the first `Edit` (stream parse) | confirmed in obsidianlive-002, -003, -004 |
| `JIRA_*` environment variables | 0 present |
| `phase_skipped` / `research_skip_reason` in any retained `scope.json`, checkpoint or policy log | none |
| `infrastructure_retry_backoff` events in any retained policy log | none |
| `"coverage"` in any verification report | none |

---

## Write-up material (pointers used by `docs/final-writeup.md`)

Closeout update: the write-up uses the annotated `in_scope` incident as its primary §5.3 story.
Its §4 is a cleanup policy with a placeholder that must be updated after cleanup.

- **§5.1 Architecture diagram.** `docs/architecture.md`.
- **§5.2 MCP vs REST.** `jira/SKILL.md` MCP-vs-REST section;
  `runs/run-20260909-connectorroute-001/CONNECTOR-ROUTE-PROOF.md`; `connector_router.py`
  REST-only mutations. State that the resolved-ticket path is test-proven only.
- **§5.3 Surprising behavior and the guardrail added.** Candidates:
  - **Annotated `in_scope` paths** blocked a live Engineer (`midimplresume-001`). The
    guardrail: `validate_scope` rejection plus lesson `L-20261001-INSCOPE-BARE-PATHS`,
    reused in `-002`.
  - **A passing run rendered `0/4 passed` in its vault note.** This was the Obsidian
    renderer schema-key defect, found only by live proof. The guardrail: a regression
    test asserting the real schema keys.
  - **The orchestrator's own Discovery wrote an AC clause that no test could verify**
    (Run B). The lesson `L-20261002-AC-CLAUSE-NEEDS-TEST` then visibly changed Run C's
    Discovery through the vault.
- **§5.4 Deletions.** Decide on the row 66 items first, then describe what was actually
  removed. Do not invent a deletion story.
