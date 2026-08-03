"""Deterministic test doubles for AgentAdapter, TestRunnerAdapter, and DiscoveryAdapter.

Pure scripted playback -- no LLM reasoning, no live Claude Code calls. These exist so the
orchestrator's own logic (state transitions, validation gates, staged-protocol mediation,
continuity handling) can be exercised deterministically. Only a live Claude Code session can
back these adapters for real; see harness/orchestrator/adapters.py.
"""
from __future__ import annotations

from harness.orchestrator.adapters import UnresumableHandleError

UNRESUMABLE = object()  # sentinel: popping this from a script raises UnresumableHandleError


class ScriptedAgentAdapter:
    """Plays back a fixed sequence of raw text responses per agent_type, in order: the first
    entry answers start(), every subsequent entry answers the next resume() call. Records every
    start()/resume() call (op, agent_type, handle) so a test can assert exactly one start() per
    phase and that every resume() used the handle start() returned."""

    def __init__(self, responses: dict[str, list], *, handles: dict[str, str] | None = None):
        self._queues = {agent_type: list(seq) for agent_type, seq in responses.items()}
        self._handle_overrides = handles or {}
        self._handle_to_agent_type: dict[str, str] = {}
        self._next_handle_n = 0
        # (op, agent_type, handle, payload_or_message) -- the 4th element lets a test inspect
        # exactly what the orchestrator sent on a given turn (e.g. that an exit_code was
        # forwarded unchanged) without needing content-aware branching in the fake itself.
        self.calls: list[tuple[str, str, str | None, dict]] = []

    def start(self, agent_type: str, payload: dict) -> tuple[str, str]:
        self._next_handle_n += 1
        handle = self._handle_overrides.get(agent_type, f"handle-{agent_type}-{self._next_handle_n}")
        if handle in self._handle_to_agent_type:
            raise AssertionError(f"handle {handle!r} already in use -- start() must never reuse a live handle")
        self._handle_to_agent_type[handle] = agent_type
        self.calls.append(("start", agent_type, handle, payload))
        return handle, self._pop(agent_type)

    def resume(self, handle: str, message: dict) -> str:
        agent_type = self._handle_to_agent_type.get(handle)
        self.calls.append(("resume", agent_type or "<unknown>", handle, message))
        if agent_type is None:
            raise UnresumableHandleError(f"handle {handle!r} is not a live, resumable agent instance")
        return self._pop(agent_type)

    def _pop(self, agent_type: str) -> str:
        queue = self._queues.get(agent_type)
        if not queue:
            raise AssertionError(f"no more scripted responses for agent_type {agent_type!r}")
        item = queue.pop(0)
        if item is UNRESUMABLE:
            raise UnresumableHandleError(f"scripted continuity loss for agent_type {agent_type!r}")
        return item

    def start_count(self, agent_type: str) -> int:
        return sum(1 for op, at, _handle, _payload in self.calls if op == "start" and at == agent_type)

    def handles_used(self, agent_type: str) -> list[str]:
        return [handle for _op, at, handle, _payload in self.calls if at == agent_type]


class ScriptedTestRunnerAdapter:
    """Returns pre-scripted command_result/command_rejected dicts keyed by command_id, and a
    pre-scripted diff_stats() result. Records every invoke() request for assertions."""

    def __init__(self, command_results: dict[str, dict], *, diff_stats: list[dict] | None = None):
        self._command_results = command_results
        self._diff_stats = diff_stats if diff_stats is not None else []
        self.invoked_requests: list[dict] = []
        self.diff_stats_calls: list[list[dict]] = []

    def invoke(self, request: dict) -> dict:
        self.invoked_requests.append(request)
        command_id = request.get("command_id")
        if command_id not in self._command_results:
            raise AssertionError(f"no scripted command_result/command_rejected for command_id {command_id!r}")
        return self._command_results[command_id]

    def diff_stats(self, changed_files_known: list[dict]) -> list[dict]:
        self.diff_stats_calls.append(changed_files_known)
        return self._diff_stats


class ScriptedDiscoveryAdapter:
    """Returns a fixed, pre-built scope draft (or raises, to simulate a Discovery adapter
    failure) regardless of the raw_prompt supplied -- Discovery's own reasoning is out of scope
    for these deterministic tests (see adapters.DiscoveryAdapter)."""

    def __init__(self, scope_draft: dict | None = None, *, raises: Exception | None = None):
        self._scope_draft = scope_draft
        self._raises = raises
        self.calls: list[dict] = []

    def propose_scope(self, *, raw_prompt, task_id, run_id, created_at, target_repo_path) -> dict:
        self.calls.append(
            {
                "raw_prompt": raw_prompt,
                "task_id": task_id,
                "run_id": run_id,
                "created_at": created_at,
                "target_repo_path": target_repo_path,
            }
        )
        if self._raises is not None:
            raise self._raises
        return self._scope_draft
