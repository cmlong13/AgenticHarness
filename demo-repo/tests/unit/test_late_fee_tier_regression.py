"""Planted demonstration regression fixture -- task T-LATEFEE / T-LATEFEE2, runs
run-20260805-latefee-004 (closed 'blocked' on an unrelated transport-repair budget
issue before Verification was ever reached) and run-20260805-latefee-005 (the
continuation attempt) -- see PROJECT_SPEC.md / ASSIGNMENT.md's same-run
logic-repair-loop acceptance criterion.

This file pre-dates any Engineer implementation for this task. It is part of this
run's controlled, disclosed fixture, not something the Engineer authored -- it is
listed `out_of_scope` in this run's scope.json and must not be edited by the
Engineer. It defines this task's binding regression bar for the SEVERE tier
boundary, which is deliberately one day earlier than the literal wording of
demo-repo/docs/late-fee-tier-policy.md (see that document's own notice).

Collection will fail with an ImportError until `loanflow.late_fee_tier` exists --
that is the expected pre-implementation state, not an error to work around.
"""
from __future__ import annotations

from loanflow.late_fee_tier import classify_late_fee_tier


def test_day_30_is_severe_not_standard():
    """The SEVERE tier must include day 30 itself, per this task's actual required
    behavior -- not day 31, as docs/late-fee-tier-policy.md's literal wording states
    in isolation. See that document's own notice for why it deliberately differs
    from this regression check."""
    assert classify_late_fee_tier(30) == "severe"


def test_day_29_is_still_standard():
    assert classify_late_fee_tier(29) == "standard"
