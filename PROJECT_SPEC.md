# Project Spec: Agentic Coding Harness

Status: **Approved understanding — no functionality implemented yet.**

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
| Discovery | Orchestrator (main session) | No | Scoped task definition + task graph |
| Research | Architect | No (read-only + notes) | Evidence-based findings doc, `file:line` citations |
| Implementation | Engineer | Yes | Minimal diff extending existing patterns |
| Verification | Quality Engineer | Tests only | Machine-readable pass/fail evidence |

Cardinal rule: no phase consumes the previous phase's output on trust. The engineer verifies
the architect's claims before coding. The orchestrator verifies the engineer's "done" claim
(e.g. via `git ls-remote` / `git rev-parse`) before reporting success.

---

## 2. Runtime Approach (decided)

**Claude Code–native.** The harness is implemented as a Claude Code project configuration, not
a standalone Python orchestration engine:

- Subagents are `.md` files with YAML frontmatter (`name`, `description`, `tools`, `model`)
  under `.claude/agents/`.
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
│   └── mcp.json                   # GitHub / Obsidian / Atlassian MCP server config
│
├── harness/                       # Python glue code invoked BY hooks/skills, not a separate agent runtime
│   ├── checkpoint.py              # pipeline state save/resume
│   ├── memory.py                  # lessons-learned.md + memory-dir read/append (cap enforcement)
│   ├── task_graph.py              # Discovery output data model
│   ├── evidence.py                # citation/verdict format validation
│   └── cost.py                    # token/cost aggregation across a run
│
├── memory/
│   ├── lessons-learned.md
│   └── facts/                     # single-fact files + index.md
│
├── runs/                          # per-run artifacts: findings docs, diffs, verification reports, checkpoints
│
├── demo-repo/                     # target repo for live demos — real code + git history (≥50 files)
│
├── tests/                         # unit tests for harness/*, integration test driving one full pipeline run
│
├── docs/
│   └── WRITEUP.md                 # assignment §5 deliverable (architecture diagram, MCP-vs-REST
│                                   # decision, surprise + guardrail, what was deleted and why)
│
├── README.md
├── .gitignore
└── pyproject.toml
```

---

## 6. Dependency Order

1. Repo foundations — `git init` this project; populate/`git init` `demo-repo/` with real
   content; finalize `.claude/settings.json` as the single canonical config.
2. Subagent contracts — `architect.md`, `engineer.md`, `quality-engineer.md`.
3. Skills the subagents depend on — code-craftsmanship, test-runner.
4. Orchestrator + `/work` (prompt mode only).
5. `harness/` support code — `checkpoint.py`, `memory.py`.
6. Memory loop wiring — `lessons-learned.md` + facts dir, read-before/append-after behavior.
7. Guardrail hooks — pre-dispatch check, completion-guardrail.
8. GitHub verification path — `git`/`gh` CLI wrapper, used both as a skill and by the
   orchestrator's independent-verification step.
9. **Close the loop**: run one trivial free-form task through all four phases against
   `demo-repo` with real evidence at each gate.
10. Only after closure: Jira connector + ticket mode, Obsidian connector, skill-enforcement
    hook, cost-tracking hook, full skill-pack breadth, MCP-vs-REST dual build, flaky/logic
    classification refinement, planted-bug demo scenarios.

---

## 7. Open Questions / Assumptions

Resolved:
- **Runtime approach**: Claude Code–native (see §2). Confirmed by user.

Still open — defaults will be applied unless redirected before the relevant build step:
1. **`demo-repo/` content** — currently empty; needs to become a real ≥50-file repo with git
   history and intentionally planted defects (a flaky test, a logic bug) for the acceptance
   criteria demos. No source repo specified yet — default plan is to generate a synthetic one.
2. **External service access** — ticket mode needs live Jira credentials; the Obsidian
   connector needs a real vault path; GitHub push-verification needs a real or realistic local
   remote. None exist yet. MVP avoids this by using prompt mode + local git only.
3. **Hooks on Windows** — the assignment assumes POSIX shell-script hooks; this environment is
   Windows/PowerShell. Default: implement hooks as Python scripts invoked by Claude Code's hook
   runner, for portability.
4. **Model assignment per agent** — frontmatter requires a `model` field per subagent; no
   per-role model choice has been made yet.
5. **Cost/token tracking mechanics** — depends on what usage data is actually readable (Claude
   Code transcript JSONL vs. API response usage fields); not yet confirmed.
6. **Write-up location** — assumed `docs/WRITEUP.md`; not explicitly specified.

---

## 8. Acceptance Criteria (from assignment, verbatim intent)

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

## 10. Next Steps (not yet started)

1. Repo foundations: `git init` this project; populate `demo-repo/` with a real ≥50-file
   codebase and git history.
2. Define the three subagents and their two prerequisite skills, plus `.claude/settings.json`.
3. Close the smallest end-to-end loop (orchestrator + `/work` prompt mode, two hooks, one
   trivial task) before adding Jira, Obsidian, MCP, or cost tracking.
