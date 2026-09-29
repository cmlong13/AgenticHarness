#!/usr/bin/env python3
"""PreToolUse hook, matcher "Bash" -- blocks and records the two Skill-mediation bypasses
`test-runner/SKILL.md` itself already names as a known, un-enforced gap ("This does not
sandbox the caller... a skill-scoped PreToolUse hook could enforce this for real"), plus
raw Jira REST calls that bypass the jira skill (check 3 below).

Registered in .claude/settings.json. Every blocked or flagged attempt is appended to
.claude/hooks/logs/skill-enforcement-events.jsonl before this script exits, so "record"
(the assignment's "block or record") always happens even on the paths this script blocks
outright -- there is always a retained trail, never a silent allow or a silent block.

## Signal this relies on (empirically verified live, not assumed)

Per docs/hooks-signal-verification.md: a `Bash` call made by the main (orchestrator)
session carries no `agent_id`/`agent_type` field in the hook payload; a `Bash` call made
from inside a forked subagent context (which is what `test-runner/SKILL.md`'s own
`context: fork` invocation actually is) does. Architect/Engineer/Quality Engineer hold no
`Bash` tool at all (see their frontmatter), so the only two contexts that can ever produce
a `Bash` PreToolUse event in this harness are the main orchestrator session (no agent_id)
and a forked Skill's own execution (agent_id present). This is therefore a real,
repository-specific enforcement signal, not a guess -- see the doc above for the raw
captured payloads this conclusion is based on.

## What is blocked

1. **Direct execution of run_command.py outside the real test-runner Skill flow / an
   attempt to bypass required Skill mediation** -- a `Bash` command that invokes
   `run_command.py`, made from the main session (`agent_id` absent from the hook payload).
   The legitimate path is always: the main session calls the `Skill` tool (a different
   tool entirely, not matched by this hook), which forks its own execution context and
   *that* fork's `Bash` call (agent_id present) is what actually runs the wrapper --
   never blocked.
2. **Bash-based edits to application source under demo-repo/ as an orchestrator
   fallback** -- a `Bash` command naming a `demo-repo/` path together with a write
   indicator (`>`, `>>`, `sed -i`, `tee`, `cp`, `mv`, `rm`, or a `python -c`/`python3 -c`
   one-liner that calls `open(` in a write mode). Blocked unconditionally, regardless of
   `agent_id` -- only the Engineer subagent's own `Edit`/`Write` tool may ever modify
   `demo-repo/` source, per `work/SKILL.md` standing rule and `engineer.md`'s Protected
   Path boundary. This is a heuristic, substring-based check (documented, not exhaustive)
   -- it cannot see through arbitrary obfuscation, matching this repository's existing,
   already-documented trust-boundary honesty (see test-runner/SKILL.md's own "Repository
   test-code trust boundary" section).

3. **Raw Jira REST calls bypassing the jira skill** (ASSIGNMENT.md §2.5's own example) --
   a `Bash` command that names a Jira REST path (`/rest/api/2/`, `/rest/api/3/`,
   `/rest/api/latest/`) together with an HTTP client in command position (`curl`, `wget`,
   `http`, `Invoke-RestMethod`/`irm`, `Invoke-WebRequest`/`iwr`) or a `python -c "..."`
   one-liner using `urllib`/`requests`/`httpx`. A command that merely mentions a client
   name (a grep, a doc edit) is not matched. Blocked unconditionally: every
   Jira read and write goes through `live_cli.py`'s `read_ticket` / `resolve_jira_issue` /
   `create_ticket` / `edit_ticket` operations, which enforce authorization and retain
   evidence. Same substring heuristic, same documented limits, as check 2.

Read-only demo-repo/ commands the orchestrator is explicitly required to run (`git status
--short -- demo-repo`, `git diff --stat -- demo-repo`, `cat`/`Read`-equivalents) are never
matched by the write-indicator check above and are always allowed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = REPO_ROOT / ".claude" / "hooks" / "logs" / "skill-enforcement-events.jsonl"

_WRAPPER_MARKER = "run_command.py"

# `(?<!\d)` excludes `2>&1`-style stderr redirection (digit immediately before `>`), which
# is not a write to a new path -- only a bare `>`/`>>` counts as an actual write indicator.
_REDIRECT_RE = re.compile(r"(?<!\d)>>?(?!=)")
_OTHER_WRITE_INDICATORS = ("sed -i", "tee ", "cp ", "mv ", "rm ", "rm -")
_PYTHON_WRITE_RE = re.compile(r"python3?\s+-c.*open\([^)]*['\"]([wa][b+]?)['\"]", re.IGNORECASE)
_JIRA_REST_PATH_RE = re.compile(r"/rest/api/(?:2|3|latest)/", re.IGNORECASE)
# An HTTP client in command position (line start, or after ; & | ( or $( ), or a
# `python -c "..."` one-liner using an HTTP library. Matching the client name anywhere
# would also block commands that merely *mention* curl next to a Jira path (docs, greps).
_HTTP_CLIENT_CMD_RE = re.compile(
    r"(?:^|[;&|(]|\$\()\s*(?:curl|wget|http|https|invoke-restmethod|invoke-webrequest|irm|iwr)(?:\.exe)?\b",
    re.IGNORECASE | re.MULTILINE,
)
_PYTHON_HTTP_RE = re.compile(r"python3?\s+-c\s+[\"'].*(?:urllib|requests\.|httpx)", re.IGNORECASE | re.DOTALL)


def _record(kind: str, command: str, reason: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "command": command,
        "reason": reason,
    }
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _is_wrapper_bypass(command: str, agent_id: str | None) -> bool:
    return _WRAPPER_MARKER in command and not agent_id


def _looks_like_demo_repo_write(command: str) -> bool:
    if "demo-repo" not in command:
        return False
    if _REDIRECT_RE.search(command):
        return True
    if any(token in command for token in _OTHER_WRITE_INDICATORS):
        return True
    if _PYTHON_WRITE_RE.search(command):
        return True
    return False


def _looks_like_raw_jira_call(command: str) -> bool:
    if not _JIRA_REST_PATH_RE.search(command):
        return False
    return bool(_HTTP_CLIENT_CMD_RE.search(command) or _PYTHON_HTTP_RE.search(command))


def _block(kind: str, command: str, reason: str) -> int:
    _record(kind, command, reason)
    sys.stderr.write(reason + "\n")
    return 2


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # malformed hook input -- fail open, never block on our own error

    if payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command", ""))
    agent_id = payload.get("agent_id")

    if _is_wrapper_bypass(command, agent_id):
        return _block(
            "skill_mediation_bypass",
            command,
            "skill-enforcement: direct Bash execution of run_command.py outside the real "
            "test-runner Skill flow is blocked. Invoke the Skill tool (skill: \"test-runner\") "
            "instead -- never call the wrapper script directly from the orchestrator session.",
        )

    if _looks_like_demo_repo_write(command):
        return _block(
            "orchestrator_source_edit_fallback",
            command,
            "skill-enforcement: a Bash command appears to write into demo-repo/ directly. "
            "Only the Engineer subagent's own Edit/Write tool may modify approved demo-repo/ "
            "files -- Bash must never be used as an orchestrator fallback to edit application "
            "source.",
        )

    if _looks_like_raw_jira_call(command):
        return _block(
            "raw_jira_api_bypass",
            command,
            "skill-enforcement: a Bash command appears to call the Jira REST API directly. "
            "Invoke the jira Skill and use live_cli.py's read_ticket / resolve_jira_issue / "
            "create_ticket / edit_ticket operations instead -- they enforce authorization and "
            "retain evidence.",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
