# Hooks milestone: live signal verification (2026-08-05)

Before designing `.claude/hooks/skill_enforcement.py`, this milestone needed a real answer
to a question `test-runner/SKILL.md` itself leaves open: does a `PreToolUse` hook see enough
information to tell a legitimate, forked `test-runner` Skill invocation of
`run_command.py` apart from the orchestrator (main session) calling `run_command.py`
directly via `Bash` as a bypass? Guessing wrong here would either fail to block a real
bypass, or break the live-verified forked test-runner path
(`run-20260804-riskband-003`). This was answered empirically, live, rather than assumed.

## Method

1. A throwaway diagnostic hook (`_diag_capture.py`, deleted before commit) was registered
   in `.claude/settings.json` for `PreToolUse` on `Bash` and `Agent`, appending every raw
   hook JSON payload it received to a local log file. It never blocked anything (always
   exited 0).
2. Three calls were made and captured: (a) a plain `Bash` call from this main
   (orchestrator) session; (b) a real `Skill` tool invocation of `test-runner`
   (`context: fork`, `background: false`) against a real, already-passing demo-repo test
   file (`runs/hooks-diagnostic/requests/C-1.json`), which — per `test-runner/SKILL.md` —
   internally runs `python ".claude/skills/test-runner/scripts/run_command.py"
   --request-file ...` via its own forked `Bash` call; (c) a real `Agent` tool dispatch of
   a trivial `general-purpose` subagent, to see the `Agent` tool's own `PreToolUse` shape.
3. The captured payloads were compared field-by-field.

## Result

`session_id`, `transcript_path`, and `cwd` were **identical** across the main session and
the forked Skill's own `Bash` call — not a usable distinguishing signal on their own.

The decisive signal: **`agent_id` and `agent_type` are present in the hook payload only
for tool calls made from inside a forked/subagent context, and absent for tool calls made
directly by the main (orchestrator) session.**

Real captured payload for the main session's own `Bash` call (no `agent_id`):
```json
{
  "session_id": "8c8ee8cb-4004-4379-9d9e-7c9096be8ae5",
  "cwd": "C:\\Users\\caleb\\Documents\\Projects\\AgenticHarness",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "echo \"main-session-baseline-call\"", "description": "..."}
}
```

Real captured payload for the forked test-runner Skill's own internal `Bash` call
(same `session_id`, but `agent_id`/`agent_type` now present):
```json
{
  "session_id": "8c8ee8cb-4004-4379-9d9e-7c9096be8ae5",
  "cwd": "C:\\Users\\caleb\\Documents\\Projects\\AgenticHarness",
  "agent_id": "a3a6b67eaa9d5093b",
  "agent_type": "general-purpose",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "cd \"C:/Users/caleb/Documents/Projects/AgenticHarness\" && python \".claude/skills/test-runner/scripts/run_command.py\" --request-file \"runs/hooks-diagnostic/requests/C-1.json\"",
    "description": "Run test-runner wrapper against request file"
  }
}
```

Real captured payload for the `Agent` tool call itself, made from the main session (no
`agent_id` -- confirming the *dispatch* action itself is always visible as a main-session
event, never something a hook would mistake for happening "inside" the subagent it is
about to create):
```json
{
  "session_id": "8c8ee8cb-4004-4379-9d9e-7c9096be8ae5",
  "hook_event_name": "PreToolUse",
  "tool_name": "Agent",
  "tool_input": {
    "description": "Hook payload diagnostic probe",
    "prompt": "...",
    "subagent_type": "general-purpose",
    "run_in_background": false
  }
}
```

Note: the forked Skill context's `agent_type` reported generically as `"general-purpose"`,
not `"test-runner"` -- Claude Code does not appear to label a Skill's own fork with the
skill's name in this field. `skill_enforcement.py` therefore does not attempt to confirm
*which* skill's fork is running (it cannot, from this signal alone) -- it only distinguishes
"inside some forked/subagent context" from "the main orchestrator session," which is the
actual distinction the assignment's bypass concern is about (the orchestrator using `Bash`
directly instead of going through the `Skill` tool). No subagent in this harness other than
a Skill's own fork ever holds a `Bash` tool capable of invoking `run_command.py` (Architect,
Engineer, and Quality Engineer all lack `Bash` entirely -- see their frontmatter), so this
signal is sufficient for this repository's actual agent topology even though it would not
generalize to "which of several concurrently active forked skills" in a busier project.

## Retained evidence

The raw captured log lines (trimmed of unrelated commands) are preserved at
`runs/hooks-diagnostic/diag-log-trimmed.jsonl`. The scratch request/response
(`runs/hooks-diagnostic/requests/C-1.json`, `runs/hooks-diagnostic/logs/C-1.log`) are also
retained, showing the real forked Skill call this experiment rode along on genuinely
executed (`exit_code: 0`, 3 passed) rather than being purely synthetic.

All of the above lives under `runs/hooks-diagnostic/` -- **committed, immutable
demonstration evidence**, distinct from `skill_enforcement.py`'s own live runtime log at
`.claude/hooks/logs/skill-enforcement-events.jsonl`, which is `.gitignore`d (added
2026-08-05) because it is a mutable file the hook keeps appending to during ordinary
harness operation, not a one-time evidence snapshot. A verbatim copy of that runtime log's
contents at verification time is committed separately, as
`runs/hooks-diagnostic/skill-enforcement-events.jsonl` -- see
`docs/hooks-permission-verification.md`'s "Runtime log vs. retained evidence" section for
the full account.
