"""Adapter interfaces the orchestrator core depends on.

None of these is a concrete implementation of a live Claude Code call. The
Claude Agent SDK exists and is a real option in principle, but this project
intentionally does not adopt it in this milestone: PROJECT_SPEC.md selects a
Claude Code-native, main-session architecture with thin Python support code
(harness/) invoked by that session, not a competing standalone agent
runtime. No Agent SDK dependency, API-key authentication, or `claude -p`
subprocess is used anywhere in this package. A live AgentAdapter -- one that
actually calls the Agent/SendMessage/Skill tools -- can only be supplied by
the Claude Code session itself and does not exist in this repository yet;
only the interface and deterministic fakes exist in this slice.
"""
from __future__ import annotations

from typing import Protocol


class UnresumableHandleError(Exception):
    """Raised (or left to propagate) when an AgentAdapter.resume() call cannot resume the given
    handle. The orchestrator must treat this as an immediate phase block, never as license to
    call start() again and pretend continuity was preserved -- see
    runs/engineer-boundary-test/continuity-broken-handoff-transcript.md for why that distinction
    is load-bearing rather than theoretical."""


class AgentAdapter(Protocol):
    def start(self, agent_type: str, payload: dict) -> tuple[str, str]:
        """Dispatch a fresh agent instance. Returns (opaque_handle, raw_text_response)."""
        ...

    def resume(self, handle: str, message: dict) -> str:
        """Resume the exact instance behind `handle` with `message`. Returns raw_text_response.
        Must raise (UnresumableHandleError or otherwise) rather than silently substituting a
        fresh instance when `handle` can no longer be resumed."""
        ...


class TestRunnerAdapter(Protocol):
    def invoke(self, request: dict) -> dict:
        """Run one command through the test-runner skill's wrapper. Returns the wrapper's own
        command_result or command_rejected dict, unchanged."""
        ...

    def diff_stats(self, changed_files_known: list[dict]) -> list[dict]:
        """Return real {path, change_type, lines_added, lines_removed} entries for exactly the
        changed_files_known set, for the Engineer's finalization_evidence exchange. This is real
        external evidence (a diff) the orchestrator has no way to compute on its own in this
        slice -- never fabricated by orchestrator code itself."""
        ...


class DiscoveryAdapter(Protocol):
    def propose_scope(
        self, *, raw_prompt: str, task_id: str, run_id: str, created_at: str, target_repo_path: str
    ) -> dict:
        """Propose a scope.json-shaped document from a free-form prompt. Performs no persistence
        -- the orchestrator validates and promotes it. The real, reasoning-backed implementation
        is the live Claude Code main session (or a future /work skill); only deterministic fakes
        exist in this repository today."""
        ...
