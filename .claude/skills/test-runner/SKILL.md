---
name: test-runner
description: Validates and executes exactly one narrowly-scoped "python -m pytest ..."
  command, against an already-existing request file, via a fixed wrapper script — on
  behalf of the Engineer's or Quality Engineer's caller-mediated command protocol.
  Invoked only by the controlled caller/orchestrator; neither Engineer nor Quality
  Engineer holds the Skill tool.
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/run_command.py *)
disallowed-tools: Write, Edit, NotebookEdit, PowerShell, Agent, Skill, WebFetch, WebSearch
---

# What allowed-tools/disallowed-tools actually do here
`allowed-tools` pre-approves *only* the wrapper invocation pattern — no permission
prompt for calling `run_command.py`. `disallowed-tools` temporarily removes the listed
tools while this skill is active — including `Write` and `Edit`. `Bash` is deliberately
kept, since it's the only way to launch the wrapper. **This does not sandbox the
caller.** A caller that already holds general Bash permission can still make a separate,
unrelated raw Bash call outside this pattern — nothing here technically prevents that.
A skill-scoped `PreToolUse` hook could enforce "only the wrapper pattern is allowed
while this skill is active" for real; that's deferred to the planned hooks milestone
(`.claude/hooks/` doesn't exist yet), not implemented now.

# The request file must already exist — this skill never creates it
Because `Write`/`Edit` are removed for the duration of this skill's invocation, this
skill cannot create or modify its own request file. The controlled caller must, in a
separate step *before* invoking this skill (using its own, unrestricted tools), write
the request JSON to exactly:
```
runs/<run_id>/requests/<command_id>.json
```
This skill is then invoked with that existing path as its sole argument, e.g.:
```
/test-runner runs/<run_id>/requests/<command_id>.json
```
Do not attempt to write, generate, or repair the request file from within this skill's
own turn under any circumstance.

# Invocation
Run exactly:
```
python "${CLAUDE_SKILL_DIR}/scripts/run_command.py" --request-file "<the argument given to this skill invocation>"
```
Read the single JSON line printed to stdout. It is always one of `command_result` or
`command_rejected` — copy `command_result` directly into your reply to the Engineer or
Quality Engineer, unchanged.

# Escalation on command_rejected
`command_rejected` means no real command ever executed — there is no real `exit_code`.
Halt the staged protocol for this task, record the rejection to a plain evidence log
under `runs/<run_id>/`, and mark the task blocked at the orchestration layer. Never
relax validation live to work around a rejection.

# Wrapper-assigned exit_code sentinels — never the child process's own exit status
These are always delivered as a normal `command_result` (never a third message type),
so Engineer/Quality Engineer always get a reply for their outstanding request:

- **124 — timeout.** The command started but exceeded `timeout_seconds`. Matches the
  conventional `timeout(1)` exit code.
- **125 — persistent mutation detected.** The wrapper detected a persistent non-cache
  file addition, deletion, or modification under `target_repo_path` after the command
  ran — reported even if the underlying pytest run's own real exit code was `0`. The
  real pytest exit code and the exact changed paths are preserved in the retained log.
  **The controlled caller must treat `exit_code: 125` as a blocked execution-policy
  violation, not as test evidence of any kind** — never accept it as a passing or
  failing result.
- **126 — internal wrapper failure after execution may have started.** An unexpected
  error (hashing, subprocess bookkeeping, log writing) occurred after the command may
  already have run. The real outcome is unknown; treat exactly like `125` — blocked,
  never trusted as pass/fail evidence. All output captured before the failure is
  preserved in the log (or, if the log itself couldn't be written, inline in
  `output_summary`).

# Mutation detection — what it actually checks
The wrapper hashes every regular file under `target_repo_path` (excluding
`.pytest_cache/`, `__pycache__/`, `.git/`) before and after the command runs, and flags
any path whose hash changed or that was added/removed. This is a plain filesystem
comparison, not Git tracked-file status — Git is never consulted, so these are
described as "non-cache file" changes, not "tracked" files.

# Repository test-code trust boundary
Repository test code is trusted in this MVP. The strict grammar and `shell=False`
prevent shell injection in the command line, but pytest still imports and executes
repository Python code — the wrapper cannot technically stop that code from writing
files, opening network connections, or spawning subprocesses once running. It can only
detect a persistent file change afterward via the hash sweep above. True prevention
requires disposable or OS-sandboxed execution — out of scope for this milestone.
