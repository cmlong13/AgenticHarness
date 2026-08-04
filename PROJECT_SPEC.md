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

### Skills (≥4 packs — 2 of 4 complete; see §10)
- `github/` — small single-purpose skills: `pr-create`, `read-file`, `search-code`,
  `commit-history`, `pr-review`. Thin `curl`/`gh` wrappers, not one mega-skill. **Not started.**
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
- Skill-enforcement hook — blocks/warns when an agent bypasses a skill that exists for the
  operation (e.g. raw Jira API call instead of the jira skill).
- Pre-dispatch check hook — runs before a subagent is spawned; validates required state exists
  (e.g. findings doc present before the engineer starts).
- Completion-guardrail hook — runs at end of workflow; blocks "done" if verification evidence is
  missing.
- Post-agent cost hook — extracts per-agent token/cost usage so a full pipeline run's total cost
  is knowable.

### Memory loop
- `lessons-learned.md` — read before every run; at most 5 bullets appended after each run.
- Persistent memory directory — small single-fact files + an index, for durable facts
  (environment quirks, workflow corrections, project state). Relative dates converted to
  absolute when saved.
- Pipeline checkpointing — an interrupted run auto-resumes at the phase it died in.

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
- Pre-dispatch-check hook + completion-guardrail hook
- `lessons-learned.md` + basic memory directory
- Checkpoint/resume for the pipeline
- GitHub access via `gh`/`git` CLI (real commits + independent push verification via
  `git ls-remote`/`git rev-parse`)
- A real target repo (≥50 files) to run against — **complete**: `demo-repo/loanflow`, 55 files,
  verified compatible with the test-runner wrapper and mutation-safe (§10); not yet used by a
  live orchestrator run

### Later integrations (after the loop closes once with real evidence)
- Ticket mode + Jira/Atlassian connector (MCP and/or REST skill pack)
- Obsidian MCP connector + run-summary write-back
- Full `github/` skill pack breadth (`pr-review`, `commit-history`)
- Full `jira/` skill pack + field-reference doc
- Skill-enforcement hook
- Post-agent cost/token-tracking hook
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
│   │   └── post_agent_cost.py
│
├── .mcp.json                      # GitHub / Obsidian / Atlassian MCP server config (repo root)
│
├── harness/                       # Python glue code invoked BY hooks/skills, not a separate agent runtime
│   ├── __init__.py                # [DONE]
│   ├── checkpoint.py              # pipeline state save/resume -- not yet built (§10 "Remaining work")
│   ├── memory.py                  # lessons-learned.md + memory-dir read/append (cap enforcement)
│   ├── evidence.py                # [DONE] schema + semantic artifact validation, incl. validate_scope_semantics
│   ├── cost.py                    # token/cost aggregation across a run
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
│       └── live_cli.py            # [DONE] thin JSON-in/JSON-out bridge the /work skill calls -- no agent
│                                   #        reasoning; deterministically tested (see §10 "Live architecture
│                                   #        decision (Option C)"), not yet exercised by a live /work run
│
├── memory/
│   ├── lessons-learned.md
│   └── facts/                     # single-fact files + index.md
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
9. Checkpoint, memory loop, pre-dispatch hook, and completion guardrail. **Not started** — see
   §4 "Later integrations."
10. Independent GitHub push-verification path. **Not started** — no commit/push has been
    performed by the harness in any live run to date (by design; see `work/SKILL.md` standing
    rule 12).
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
- [ ] Completion-guardrail hook demonstrably blocks a run where verification evidence is
      deleted.
- [ ] Orchestrator catches a simulated false "pushed" claim via `git ls-remote`.
- [ ] Obsidian vault receives a run summary; `lessons-learned.md` gains ≤5 bullets; a second run
      visibly uses a lesson from the first.
- [ ] Killing the pipeline mid-Implementation and rerunning resumes from checkpoint instead of
      restarting.
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
(2) hooks/guardrails, checkpoint/resume, the memory loop, and token/cost accounting (§6 items
9–10); (3) Obsidian integration, connector comparisons, and planted-defect (flaky-test,
false-push) demonstrations (§6 item 12). See "Live evidence (`run-20260804-riskband-003`) —
completed" in §10 for exactly what this milestone did and did not close.

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
7. Checkpoint, memory, and guardrail hooks. **Not started.**
8. ~~First complete evidence-backed pipeline run.~~ **COMPLETE** — `run-20260804-riskband-003`,
   `final_verdict: "pass"`. See §10 "Live evidence (`run-20260804-riskband-003`) — completed."
9. Deferred integrations and final assignment demonstrations (includes `github/`/`jira/` skill
   packs, Jira/Obsidian connectors, cost tracking, and planted-defect demos). **Not started.**
