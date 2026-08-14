#!/usr/bin/env python3
"""SubagentStop / PostToolUse(Agent) hook -- captures real per-agent token
usage evidence. Registered in .claude/settings.json.

This is an EVIDENCE-CAPTURE hook, not a guardrail: it never blocks anything
and always exits 0, regardless of what it finds or any internal error --
usage accounting must never be the thing standing between the harness and
doing real work (same "fail open on our own errors" posture
pre_dispatch_check.py documents for its own internal-error path, applied
here to every path, not just internal errors, since this hook has no
legitimate reason to ever refuse a tool call).

Per the retained diagnostic evidence (docs/usage-hook-signal-verification.md,
runs/hooks-diagnostic/usage-hook-diagnostic-log.jsonl) and
harness/orchestrator/usage.py's own module docstring:

- `SubagentStop` (no matcher) is the PRIMARY, authoritative capture path --
  it fires on every stop of a staged agent, including every SendMessage
  resume, unlike PostToolUse:Agent which fires once per Agent dispatch only.
  Delegates entirely to usage.capture_subagent_usage, which parses the real
  subagent transcript, identity-matches agent_id against retained
  agent_dispatch policy events, prices it, and writes/overwrites the
  authoritative per-agent usage record.
- `PostToolUse` (matcher "Agent") is a SECONDARY, non-authoritative
  corroboration signal -- its own structured tool_response.usage is real,
  but would silently miss every resumed turn if relied on as the source of
  truth. Delegates to usage.record_post_tool_use_corroboration, which
  retains it as supplementary evidence only, never merged into or used to
  compute the authoritative per-agent record.

All actual parsing/identity/pricing/persistence logic lives in
harness/orchestrator/usage.py -- this script is a thin dispatcher over the
real hook JSON payload, mirroring the existing hooks' own
thin-script-over-shared-module convention.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# .claude/hooks/record_agent_usage.py -> parents[0]=hooks, [1]=.claude, [2]=repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from harness.orchestrator import usage  # noqa: E402


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # malformed hook input -- fail open, never block on our own error

    if not isinstance(payload, dict):
        return 0

    event = payload.get("hook_event_name")
    try:
        if event == "SubagentStop":
            usage.capture_subagent_usage(payload, repo_root=REPO_ROOT)
        elif event == "PostToolUse" and payload.get("tool_name") == "Agent":
            usage.record_post_tool_use_corroboration(payload, repo_root=REPO_ROOT)
        # Any other event/matcher combination is not this hook's concern --
        # the registration in settings.json should already prevent this, but
        # never act on an unexpected event rather than assuming.
    except Exception:
        # A usage-capture failure is never allowed to be mistaken for a
        # blocking guardrail failure -- swallow and fail open. (A future
        # debugging aid could log this to .claude/hooks/logs/, matching
        # skill_enforcement.py's own convention, but is not required for
        # this milestone's evidence-capture contract.)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
