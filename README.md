# Agentic Harness

A Claude Code–native agentic coding harness: a pipeline of specialized agents
(Discovery → Research → Implementation → Verification), each with deliberately
restricted permissions, coordinated by an orchestrator, guarded by hooks, and
improved over time by a memory loop. Full requirements and design rationale
live in [`PROJECT_SPEC.md`](./PROJECT_SPEC.md).

## Status

**Scaffold only — no functionality implemented yet.**

What exists today:
- Project directory structure (`.claude/`, `harness/`, `tests/`, `demo-repo/`)
- Packaging config (`pyproject.toml`)
- Approved project spec (`PROJECT_SPEC.md`)

What does not exist yet: subagent definitions, skills, hooks, MCP connectors,
the `/work` command, the memory loop, and `demo-repo/`'s actual content. None
of the pipeline phases can be run yet.

## Planned architecture

Four phases, each a distinct agent role with a distinct toolset:

| Phase | Agent role | May write code? | Output |
|---|---|---|---|
| Discovery | Orchestrator (main session) | No | Scoped task definition + task graph |
| Research | Architect | No (read-only + notes) | Findings doc with `file:line` citations |
| Implementation | Engineer | Yes | Minimal diff extending existing patterns |
| Verification | Quality Engineer | Tests only | Machine-readable pass/fail evidence |

Planned layout (see `PROJECT_SPEC.md` §5 for the full mapping):

- `.claude/agents/` — `architect.md`, `engineer.md`, `quality-engineer.md`
- `.claude/skills/` — `github/`, `jira/`, `test-runner/`, `code-craftsmanship/`
- `.claude/hooks/` — pre-dispatch check, completion guardrail, skill enforcement, cost tracking
- `.claude/commands/work.md` — the `/work` entry point (ticket mode + free-form prompt mode)
- `harness/` — Python support code hooks/skills call into (checkpointing, memory I/O, cost aggregation, evidence validation)
- `memory/` — `lessons-learned.md` and a fact-file memory directory
- `demo-repo/` — sample target repository the harness operates on
- `tests/` — unit and integration tests for `harness/`

The core rule driving the design: no phase consumes the previous phase's
output on trust — every completion claim is independently re-verified before
it's believed.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -e ".[dev]"
```

There is nothing runnable yet — this installs the package skeleton and dev
dependencies (`pytest`) only.

## Next implementation milestone

Establish the contracts and the first read-only agent before building orchestration:

1. Define the core artifact contracts for scope, findings, implementation, verification, checkpoints, and run summaries.

2. Create the read-only Architect subagent and enforce its file-search and citation requirements.

3. Populate demo-repo/ with a real codebase and use the Architect to research one small task with evidence.

4. Add the Engineer and Quality Engineer only after the research contract is working reliably.

5. Build the orchestrator and /work command after the three phase outputs are stable
