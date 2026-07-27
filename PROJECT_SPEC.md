# Project Spec: Agentic Coding Harness

Status: **Implementation in progress — artifact contracts, validation, and the read-only Architect
subagent (with independently verified permission boundary) are complete; Engineer and Quality
Engineer subagents not yet implemented.**

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
- The entry point is a slash command (`/work`) under `.claude/commands/`.
- `harness/` is thin Python support code (checkpointing, memory I/O, cost aggregation, evidence
  validation) that hooks and skills invoke — it is not a competing agent runtime.

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
- `/work` slash command — two modes: `TICKET-ID repo` (Jira) and free-form prompt.

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
- `quality-engineer.md` — Verification. Test tools only; cannot modify source (fixes route back
  to the engineer). Runs the narrowest test command that validates the change. Classifies every
  failure as logic bug / infrastructure flake / environment before reporting. Retries
  infrastructure failures with backoff (max 3); never retries logic failures. Every verdict is
  backed by evidence (exit codes, report paths, coverage numbers) — "tests pass" alone is not a
  verdict.

### Skills (≥4 packs)
- `github/` — small single-purpose skills: `pr-create`, `read-file`, `search-code`,
  `commit-history`, `pr-review`. Thin `curl`/`gh` wrappers, not one mega-skill.
- `jira/` (Atlassian) — `create-ticket`, `read-ticket`, `edit-ticket`, plus a field-reference doc
  so agents don't guess custom field IDs.
- Test-runner skill — encodes this repo's actual test command, thresholds, and report location.
- Code-craftsmanship skill — the minimal-change ladder and YAGNI rules as a standalone skill any
  agent can be held to.

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
- A real target repo (≥50 files) to run against

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
│   ├── commands/
│   │   └── work.md                # /work entry point
│   ├── agents/
│   │   ├── architect.md
│   │   ├── engineer.md
│   │   └── quality-engineer.md
│   ├── skills/
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
│   ├── checkpoint.py              # pipeline state save/resume
│   ├── memory.py                  # lessons-learned.md + memory-dir read/append (cap enforcement)
│   ├── task_graph.py              # Discovery output data model
│   ├── evidence.py                # [DONE] schema + semantic artifact validation
│   ├── cost.py                    # token/cost aggregation across a run
│   ├── schemas/                   # [DONE] the six *.schema.json artifact contracts
│   └── artifacts/
│       └── examples/              # [DONE] the six *.example.json reference artifacts
│
├── memory/
│   ├── lessons-learned.md
│   └── facts/                     # single-fact files + index.md
│
├── runs/                          # per-run artifacts: findings docs, diffs, verification reports, checkpoints
│
├── demo-repo/                     # target repo for live demos — real code + git history (≥50 files)
│
├── tests/
│   ├── __init__.py                       # [DONE]
│   ├── test_artifact_contracts.py        # [DONE] 38 passing tests
│   └── ...                               # unit tests for harness/*, integration test driving
│                                          # one full pipeline run
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
4. Engineer and Quality Engineer subagent design, implementation, and permission
   verification — **NEXT**.
5. Code-craftsmanship and test-runner skills.
6. Build or import the real target repository with at least 50 files.
7. Orchestrator skill and `/work` free-form prompt mode.
8. Checkpoint, memory loop, pre-dispatch hook, and completion guardrail.
9. Independent GitHub push-verification path.
10. Close one complete free-form prompt pipeline with real evidence.
11. Add Jira, Obsidian, cost tracking, connector comparison, broader skills, and
    planted-defect demonstrations.

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

Still open — defaults will be applied unless redirected before the relevant build step:
1. **`demo-repo/` content/source** — currently empty; needs to become a real ≥50-file repo with
   git history. No source repo specified yet — default plan is to generate a synthetic one.
   Planted defects (a flaky test, a logic bug) are deferred demonstrations for the later
   acceptance-criteria pass, not required in the initial demo-repo population.
2. **Jira credentials** — ticket mode needs live Jira credentials; none exist yet. MVP avoids
   this by using prompt mode only.
3. **Obsidian vault path** — the Obsidian connector needs a real vault path; none exists yet.
4. **Cost/token extraction mechanism** — depends on what usage data is actually readable (Claude
   Code transcript JSONL vs. API response usage fields); not yet confirmed.
5. **Write-up location** — assumed `docs/WRITEUP.md`; not explicitly specified, unless the
   assignment explicitly resolves it.

---

## 8. Acceptance Criteria (from assignment, verbatim intent)

These are final assignment acceptance criteria. They are not all prerequisites for closing the
core MVP loop. Jira, Obsidian, cost tracking, connector comparisons, and planted-defect
demonstrations are implemented only after the first free-form prompt loop closes successfully.

Demonstrated live, on a repo with ≥50 files:

- [ ] `/work <ticket-id>` pulls a real ticket via the Atlassian connector and produces a scoped
      task graph.
- [ ] Research phase produces a findings doc where every claim has a `file:line` citation,
      including at least one honest "Not Found."
- [ ] Architect physically cannot edit source files (tool restriction, not politeness).
- [ ] Engineer's diff cites the architect finding it builds on; no new dependencies without a
      written justification.
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
- The working tree is clean and local `main` matches `origin/main`.

### Immediate next milestone

Design and implement:

- `.claude/agents/engineer.md`
- `.claude/agents/quality-engineer.md`

This milestone is design-and-boundary work only in this step; implementation happens in a
later, separate step.

#### Engineer — intended boundaries

- Performs only the Implementation phase.
- Receives `scope.json` and validated `findings.json`.
- Reopens and independently verifies Architect citations before relying on them.
- May modify implementation source and tests.
- Uses the minimal-change ladder.
- Writes a failing test before implementation for each new or changed behavior when applicable.
- Records pre-implementation failure evidence and post-implementation passing evidence.
- Records changed files, commands, exit codes, test evidence, dependency changes, and findings
  references in `implementation-report.json`.
- May report `ready_for_verification` or `blocked`, but may not declare final completion.
- Must not modify scope, research findings, verification evidence, Architect permissions,
  Quality Engineer permissions, or orchestrator configuration.
- Must not commit or push unless that capability is explicitly introduced and separately
  justified later.

#### Quality Engineer — intended boundaries

- Performs only the Verification phase.
- Receives `scope.json`, `findings.json`, `implementation-report.json`, and the implementation
  diff.
- Independently verifies acceptance criteria.
- Runs the narrowest sufficient test commands.
- Records exact commands, exit codes, output references, classifications, and criterion-level
  results in `verification-report.json`.
- May classify the final result as pass, fail, blocked, or inconclusive.
- Must not modify application source code.
- Must not modify implementation tests merely to make them pass.
- Must not install dependencies.
- Must not commit or push.
- Must route implementation defects back to the Engineer.
- Its ability to run tests may require a restricted shell or test-runner capability, so its
  permission design must distinguish executable test access from general source-edit access.

#### Permission-design requirement for the next design review

Before implementation begins, the next design review must determine:

- The exact tool allowlist for the Engineer.
- The exact tool allowlist for the Quality Engineer.
- Which restrictions are technically enforced.
- Which restrictions remain behavioral.
- Whether the Quality Engineer can receive a narrowly controlled testing capability rather than
  unrestricted shell access.
- How the Engineer can edit source while being prevented from changing protected harness
  artifacts and agent definitions.
- How implementation and verification outputs will be returned, validated, and persisted.

Do not assume Claude Code supports path-specific write restrictions without verifying it.

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

1. Engineer agent.
2. Quality Engineer agent.
3. Code-craftsmanship and test-runner skills.
4. Demo repository.
5. Orchestrator and `/work` prompt mode.
6. Checkpoint, memory, and guardrail hooks.
7. First complete evidence-backed pipeline run.
8. Deferred integrations and final assignment demonstrations.
