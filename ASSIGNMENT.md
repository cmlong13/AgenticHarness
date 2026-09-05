

# Assignment: Build Your Own Agentic Harness

**Audience:** Junior engineer / CS student comfortable with git, a terminal, and one programming language.

**Deliverable:** A working agentic coding harness (built on Claude Code or an equivalent agent runtime) that executes a full **Discovery → Research → Implementation → Verification** pipeline on a real repository, plus a short demo write-up.

This assignment is modeled on a production harness that runs daily. Everything asked of you below exists in that reference setup and has survived real-world failure modes — the criteria aren't academic.

---

## 1. The Core Idea

An agentic harness is not "a chatbot with tools." It is a **pipeline of specialized agents with deliberately restricted permissions**, coordinated by an orchestrator, with **hooks that enforce rules the agents might forget** and a **memory loop that makes the system smarter over time**.

Your harness must implement four phases. Each phase has a distinct agent role, a distinct toolset, and a distinct output contract:

| Phase | Agent role | May write code? | Output |
|---|---|---|---|
| **Discovery** | Orchestrator (main session) | No | Scoped task definition + task graph |
| **Research** | Architect | No (read-only + notes) | Evidence-based findings doc with `file:line` citations |
| **Implementation** | Engineer | Yes | Minimal diff extending existing patterns |
| **Verification** | Quality Engineer | Tests only | Machine-readable pass/fail evidence |

**The cardinal rule:** no phase may consume the previous phase's output on trust. The engineer verifies the architect's claims before coding. The orchestrator verifies the engineer's "done" claim before reporting success. (Real incident that motivates this: a subagent once *fabricated* a "pushed and fixed" report; the orchestrator now runs `git ls-remote` / `git rev-parse` / live queries before believing any completion claim. Build that skepticism in from day one.)

---

## 2. Required Components

### 2.1 Orchestrator (Discovery phase)

The orchestrator runs in your **main session** — it is a skill/instruction set, *not* a subagent, because subagents typically cannot spawn nested subagents. Its jobs:

- Accept work in two modes: a **ticket ID** (pulled from your Atlassian/Jira connector) or a **free-form prompt**.
- Decompose the request into a task graph and decide which phases are needed (a typo fix doesn't need a full research phase — say so explicitly and skip it).
- Read a `lessons-learned.md` file **before** dispatching anything (see §2.6).
- Run a **pre-dispatch checklist** and push back on the ticket itself if it fails — e.g. "does this change the core product, or is it decoration?", "does it leave behind a health signal that would catch a silent regression?" Your orchestrator must be allowed to refuse work, with reasons.
- Verify all downstream completion claims with independent commands before declaring success.

### 2.2 Subagent definitions (one markdown file each)

Define at least three subagents as markdown files with YAML frontmatter (`name`, `description`, `tools`, `model`). The `tools` list is your permission model — **restricting tools is the whole point**:

**`architect.md` (Research phase).** Read/grep/glob + your docs connectors. No file edits to source. Must follow an evidence hierarchy — executable code > test assertions > runtime config > comments > metadata — and these rules, verbatim or adapted:

- Never claim something exists without finding it first.
- Document search attempts, including the ones that found nothing ("Found" / "Not Found" / "Inferred" are three distinct labels).
- Read method bodies, not just signatures.
- Every claim carries a `file:line` reference.
- Search the whole repo (or all repos); never stop at the first match; distinguish current code from legacy.

**`engineer.md` (Implementation phase).** Full edit tools. Must consume the architect's findings and obey a **minimal-change ladder** — stop at the first rung that holds:

1. Does this need to exist at all? Speculative need → skip it, say so in one line.
2. Already in the codebase? Reuse the existing helper/pattern.
3. Stdlib or platform feature covers it? Use it.
4. An already-installed dependency solves it? Use it. Never add a new dependency for what a few lines can do.
5. Only then: the minimum code that works — without simplifying away input validation, error handling that prevents data loss, or security checks.

Plus: one failing test first for each new/changed behavior (TDD), extend rather than replace working code, and verify a cited pattern actually exists at its `file:line` before building on it.

**`quality-engineer.md` (Verification phase).** Test tools only; **forbidden from modifying source** (fixes get delegated back to the engineer). Rules:

- Run the *narrowest* test command that validates the change, not the whole suite.
- Classify every failure (logic bug / infrastructure flake / environment) before reporting it.
- Retry infrastructure failures with backoff (max 3); never retry logic failures.
- Every verdict is backed by evidence: exit codes, report paths, coverage numbers. "Tests pass" without an exit code is not a verdict.

### 2.3 Custom skills (your plugin layer)

Skills are reusable instruction packages (a `SKILL.md` plus helper scripts) that agents invoke for repeatable procedures. Build **at least four**:

1. **`github/` skill pack** — small, single-purpose skills: `pr-create`, `read-file`, `search-code`, `commit-history`, `pr-review`. Prefer thin `curl`/`gh` wrappers over one mega-skill; small skills compose and fail legibly.
2. **`jira/` (Atlassian) skill pack** — `create-ticket`, `read-ticket`, `edit-ticket`, plus a field-reference doc so agents don't guess custom field IDs.
3. **A test-runner skill** for your chosen stack (e.g. Playwright or pytest) encoding *your repo's* conventions: which command, which thresholds, where reports land.
4. **A code-craftsmanship skill** — your minimal-change ladder and YAGNI rules as a standalone skill so any agent can be held to it.

### 2.4 MCP connectors (your context layer)

Wire up MCP servers for the three external systems, and know what each phase uses them for:

| Connector | Used in phase | Purpose |
|---|---|---|
| **GitHub** (official MCP server or `gh` CLI) | Discovery, Research, Verification | Read issues/PRs, search code across repos, verify pushes actually landed |
| **Obsidian** (e.g. `mcp-obsidian` against your vault) | Discovery, Research | Your personal knowledge vault: design notes, past decisions, calibration docs. The architect reads it; the orchestrator writes summaries back to it |
| **Atlassian/Jira** | Discovery | Ticket intake, status transitions, posting the completion comment |

**Hard-won caveat to design around:** MCP connectors are not automatically the right tool. The reference setup *abandoned* the Atlassian MCP server in favor of curl-based skills because raw REST calls were more reliable and debuggable. Requirement: for at least one connector, implement **both** the MCP route and a REST-skill fallback, and document in your write-up which one you kept and why.

### 2.5 Hooks (your guardrail layer)

Agents forget instructions; hooks don't. Implement **at least three** lifecycle hooks (shell scripts registered in your settings):

1. **Skill-enforcement hook** — blocks or warns when an agent performs an operation "by hand" that a skill exists for (e.g. raw Jira API call bypassing the jira skill).
2. **Pre-dispatch check hook** — runs before a subagent is spawned; validates required state exists (findings doc present before the engineer starts, etc.).
3. **Completion-guardrail hook** — runs at end of workflow; blocks "done" if verification evidence is missing.

Also track per-agent token/cost metrics (a post-agent hook that extracts usage) so you can answer "what did this pipeline run cost?"

### 2.6 Memory loop (what makes it a *system*)

- Maintain `lessons-learned.md`: the orchestrator reads it before every run and appends at most **5 bullets** after each run. Cap it — an unbounded lessons file becomes noise nobody reads.
- Maintain a persistent memory directory of small single-fact files with an index, for durable facts: environment quirks ("AWS env vars corrupted, use profile X"), workflow corrections ("always pull `release` before dispatching"), project state. Convert relative dates to absolute when saving.
- Checkpoint pipeline state so an interrupted run **auto-resumes** at the phase it died in, rather than restarting.

### 2.7 Entry point

A single slash command (e.g. `/work`) that accepts both modes:

```
/work TICKET-123 my-repo        # ticket mode: pulls scope from Jira
/work Fix the flaky login test  # prompt mode: free-form
```

It loads the orchestrator skill, runs Discovery, and drives the remaining phases via subagent dispatch.

---

## 3. Non-Negotiable Principles (graded hardest)

1. **Evidence over assertion.** Every factual claim any agent makes carries a citation (`file:line`, URL, command output). "I believe" is banned vocabulary in findings docs.
2. **Distrust completion claims.** The orchestrator independently verifies "pushed/fixed/deployed" with its own commands. Assume subagents can hallucinate success.
3. **Minimal change.** The diff is as small as solves the problem. Grading will diff your agent's output against the obvious lazy solution; unjustified extra code costs points.
4. **Health signals.** Any change to a formula, config value, or data path must leave behind a check that would catch it silently breaking later (a CI sanity test, a distribution check, a freshness timestamp). "The tests passed at merge time" is not a health signal. (Motivating incident: a scoring model's output was silently clamped to a floor value for 30+ days because a units bug had no distribution check watching it.)
5. **One canonical config directory.** Exactly one `.claude/` (or equivalent) directory at your workspace root. Subagents reference it by absolute path. Duplicated config directories in subfolders/worktrees are the #1 source of "why is my agent ignoring my rules" bugs.
6. **Cut, don't gate.** Dead skills, unused hooks, half-built agents: delete them and trust git history. Your final submission is graded partly on what it *doesn't* contain.
7. **Finish now, don't defer.** When verification surfaces an issue, the pipeline loops back to Implementation in the same run. "Filed a follow-up ticket" is a failure state for anything in scope.

---

## 4. Acceptance Criteria (checklist)

Your harness passes when you can demonstrate, live, on a repo with ≥ 50 files:

- [ ] `/work <ticket-id>` pulls a real ticket via the Atlassian connector and produces a scoped task graph.
- [ ] Research phase produces a findings doc where **every** claim has a `file:line` citation, including at least one honest "Not Found."
- [ ] Architect physically cannot edit source files (tool restriction, not politeness).
- [ ] Engineer's diff cites the architect finding it builds on; no new dependencies without a written justification.
- [ ] A deliberately planted flaky test is classified as *infrastructure* and retried; a deliberately planted logic bug is classified as *logic*, not retried, and routed back to the engineer — who fixes it **in the same run**.
- [ ] Completion-guardrail hook demonstrably blocks a run where you delete the verification evidence.
- [ ] Orchestrator catches a simulated false "pushed" claim (report a push without pushing; show detection via `git ls-remote`).
- [ ] Obsidian vault receives a run summary; `lessons-learned.md` gains ≤ 5 bullets; a second run visibly uses a lesson from the first.
- [ ] Kill the pipeline mid-Implementation; rerun resumes from checkpoint instead of restarting.
- [ ] You can state the total token/cost figure for one full pipeline run.

## 5. Write-Up (1–2 pages)

1. Architecture diagram: agents, skills, hooks, connectors, and data flow between phases.
2. MCP-vs-REST decision for the connector you built both ways (§2.4), with evidence.
3. One thing an agent did that surprised you, and the guardrail you added in response.
4. What you deleted before submitting, and why (§3.6).

## 6. What NOT to Build

- No web dashboard, no UI polish, no logo. If you build a status view, it must be a *debugging* surface (which phase, which agent, what evidence) — diagnostics before decoration.
- No speculative "multi-user support," plugin marketplaces, or configuration options with one caller. YAGNI is graded.
- No mega-agent with every tool. If one agent can do everything, you built a chatbot, not a harness.

---

*Start with the smallest loop that closes end-to-end — one trivial ticket flowing through all four phases with real evidence at each gate — then deepen each phase. Loop closure beats breadth, every time.*
</content>
