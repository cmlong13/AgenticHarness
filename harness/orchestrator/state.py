"""One-pass MVP orchestration states.

No automatic route-back to Engineer and no resume support this slice --
every run either reaches a single terminal outcome or blocks along the way.
IMPLEMENTATION_WAITING_FOR_COMMAND and VERIFICATION_WAITING_FOR_ATTEMPT are
transient states a staged protocol passes through internally; a RunResult
never rests in one of them.
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    INITIALIZED = "initialized"
    DISCOVERY_RUNNING = "discovery_running"
    DISCOVERY_REFUSED = "discovery_refused"
    DISCOVERY_INVALID = "discovery_invalid"
    RESEARCH_RUNNING = "research_running"
    RESEARCH_BLOCKED = "research_blocked"
    IMPLEMENTATION_RUNNING = "implementation_running"
    IMPLEMENTATION_WAITING_FOR_COMMAND = "implementation_waiting_for_command"
    IMPLEMENTATION_BLOCKED = "implementation_blocked"
    VERIFICATION_RUNNING = "verification_running"
    VERIFICATION_WAITING_FOR_ATTEMPT = "verification_waiting_for_attempt"
    VERIFICATION_BLOCKED = "verification_blocked"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_INCONCLUSIVE = "verification_inconclusive"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATES = frozenset(
    {
        State.DISCOVERY_REFUSED,
        State.DISCOVERY_INVALID,
        State.RESEARCH_BLOCKED,
        State.IMPLEMENTATION_BLOCKED,
        State.VERIFICATION_BLOCKED,
        State.VERIFICATION_FAILED,
        State.VERIFICATION_INCONCLUSIVE,
        State.COMPLETED,
        State.FAILED,
    }
)

# run-summary.schema.json's final_verdict enum has only 4 values (pass/fail/blocked/
# inconclusive) -- every richer internal state below is projected onto the closest of these.
# See the final report for the "refused"/"invalid" representational gap this creates.
FINAL_VERDICT_BY_STATE: dict[State, str] = {
    State.DISCOVERY_REFUSED: "blocked",
    State.DISCOVERY_INVALID: "blocked",
    State.RESEARCH_BLOCKED: "blocked",
    State.IMPLEMENTATION_BLOCKED: "blocked",
    State.VERIFICATION_BLOCKED: "blocked",
    State.VERIFICATION_FAILED: "fail",
    State.VERIFICATION_INCONCLUSIVE: "inconclusive",
    State.COMPLETED: "pass",
    State.FAILED: "blocked",
}

PHASE_BY_STATE: dict[State, str | None] = {
    State.DISCOVERY_REFUSED: "discovery",
    State.DISCOVERY_INVALID: "discovery",
    State.RESEARCH_BLOCKED: "research",
    State.IMPLEMENTATION_BLOCKED: "implementation",
    State.VERIFICATION_BLOCKED: "verification",
    State.VERIFICATION_FAILED: "verification",
    State.VERIFICATION_INCONCLUSIVE: "verification",
    State.COMPLETED: "verification",
    State.FAILED: None,
}
