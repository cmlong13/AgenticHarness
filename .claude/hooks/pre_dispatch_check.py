#!/usr/bin/env python3
"""PreToolUse hook, matcher "Agent" -- blocks a controlled-harness subagent dispatch
that is missing its required, promoted, identity-matching precondition artifact.

Registered in .claude/settings.json. Claude Code invokes this script as a subprocess for
every `Agent` tool call, passing one JSON object on stdin (session_id, cwd, hook_event_name,
tool_name, tool_input, and -- confirmed empirically, see docs/hooks-signal-verification.md
-- agent_id/agent_type when the *calling* context is itself a forked subagent). Exit code 2
blocks the tool call and returns this script's stderr text to the caller as the reason;
exit code 0 allows it. This script never exits nonzero for its own internal errors (a
malformed hook payload, an unreadable file) -- those fail open, since a broken hook must
never be the only thing standing between the harness and doing real work; only a positively
identified, known-bad dispatch is blocked.

Preconditions enforced (only for this harness's own three subagent types -- any other
`subagent_type` is out of scope and always allowed):
- "engineer" requires a promoted, schema-shaped runs/<run_id>/findings.json whose own
  task_id/run_id match the ones the dispatch prompt itself names.
- "quality-engineer" requires a promoted, schema-shaped runs/<run_id>/implementation-report.json
  whose status is "ready_for_verification" (a status the Quality Engineer's own contract
  requires before it can verify anything) and whose task_id/run_id match.
- "architect" requires a promoted runs/<run_id>/scope.json with status "approved" and
  matching task_id/run_id -- Research must not start before Discovery has actually
  produced and approved a scope.
- Every one of the three above additionally requires that the dispatch prompt itself name
  a run_id/task_id at all -- a controlled dispatch always includes these per
  architect.md/engineer.md/quality-engineer.md's own documented Input contracts, so their
  total absence is itself a sign this is not a real controlled dispatch.

This is intentionally a cheap, structural check (file exists, parses as JSON, has the
right status/identity fields) -- not a full JSON-Schema re-validation. Full schema and
semantic validation already happens at promotion time via harness/orchestrator/live_cli.py
before any artifact is ever written to these paths; this hook's job is a fast dispatch-time
guard, not a second copy of that validation.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# .claude/hooks/pre_dispatch_check.py -> parents[0]=hooks, [1]=.claude, [2]=repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Precondition artifact required before each controlled subagent type may be dispatched,
# and the status value(s) that make it usable by the next phase. `None` means any status
# on a promoted artifact is acceptable (Discovery's own "refused" scope should never reach
# an Architect dispatch either, so "approved" is required there too).
PRECONDITIONS = {
    "architect": ("scope.json", {"approved"}),
    "engineer": ("findings.json", None),
    "quality-engineer": ("implementation-report.json", {"ready_for_verification"}),
}

_RUN_ID_RE = re.compile(r'"run_id"\s*:\s*"([^"]+)"')
_TASK_ID_RE = re.compile(r'"task_id"\s*:\s*"([^"]+)"')


def _normalize(subagent_type: str) -> str:
    return subagent_type.strip().replace("_", "-").lower()


def _extract(pattern: re.Pattern, prompt: str) -> str | None:
    match = pattern.search(prompt)
    return match.group(1) if match else None


def _block(reason: str) -> int:
    sys.stderr.write(reason + "\n")
    return 2


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # malformed hook input -- fail open, never block on our own error

    if payload.get("tool_name") != "Agent":
        return 0  # matcher should already guarantee this, but never act on the wrong tool

    tool_input = payload.get("tool_input") or {}
    subagent_type = _normalize(str(tool_input.get("subagent_type", "")))
    if subagent_type not in PRECONDITIONS:
        return 0  # not one of this harness's controlled dispatches -- not this hook's concern

    prompt = str(tool_input.get("prompt", ""))
    run_id = _extract(_RUN_ID_RE, prompt)
    task_id = _extract(_TASK_ID_RE, prompt)
    if not run_id or not task_id:
        return _block(
            f"pre-dispatch check: refusing to dispatch subagent_type={subagent_type!r} -- "
            "its prompt does not name a run_id/task_id, so no promoted precondition artifact "
            "can be identified or checked. A controlled dispatch always echoes these."
        )

    filename, allowed_statuses = PRECONDITIONS[subagent_type]
    artifact_path = REPO_ROOT / "runs" / run_id / filename

    if not artifact_path.is_file():
        return _block(
            f"pre-dispatch check: refusing to dispatch subagent_type={subagent_type!r} for "
            f"run_id={run_id!r} -- required precondition artifact {filename} does not exist "
            f"at {artifact_path}. It must be validated and promoted before this dispatch."
        )

    try:
        doc = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _block(
            f"pre-dispatch check: refusing to dispatch subagent_type={subagent_type!r} -- "
            f"{artifact_path} exists but is not valid JSON ({exc}). A promoted artifact must "
            "always be well-formed."
        )

    if not isinstance(doc, dict):
        return _block(
            f"pre-dispatch check: refusing to dispatch subagent_type={subagent_type!r} -- "
            f"{artifact_path} does not contain a JSON object."
        )

    if doc.get("task_id") != task_id or doc.get("run_id") != run_id:
        return _block(
            f"pre-dispatch check: refusing to dispatch subagent_type={subagent_type!r} -- "
            f"{artifact_path}'s own task_id/run_id ({doc.get('task_id')!r}/{doc.get('run_id')!r}) "
            f"does not match the dispatch prompt's ({task_id!r}/{run_id!r}). Task/run identity "
            "must match the promoted artifact exactly."
        )

    if allowed_statuses is not None and doc.get("status") not in allowed_statuses:
        return _block(
            f"pre-dispatch check: refusing to dispatch subagent_type={subagent_type!r} -- "
            f"{artifact_path}'s status is {doc.get('status')!r}, not one of {sorted(allowed_statuses)}. "
            "The precondition artifact does not authorize this dispatch yet."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
