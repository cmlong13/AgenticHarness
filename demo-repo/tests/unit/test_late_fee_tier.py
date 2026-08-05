"""Unit tests for loanflow.late_fee_tier.classify_late_fee_tier.

Repair pass (task T-LATEFEE2, run run-20260805-latefee-005, repair_attempt 1):
Verification genuinely failed AC-2 -- demo-repo/tests/unit/test_late_fee_tier_regression.py
(a pre-existing, out-of-scope fixture, the binding regression bar) requires
day 30 to classify as SEVERE, one day earlier than the first pass's literal
reading of demo-repo/docs/late-fee-tier-policy.md. This file's boundary
table is updated here to match that binding regression bar: grace 0-5
inclusive, standard 6-29 inclusive, severe 30 and above.
"""
from __future__ import annotations

import pytest

from loanflow.errors import ValidationError
from loanflow.late_fee_tier import classify_late_fee_tier


def test_late_fee_tier_boundaries() -> None:
    boundary_cases = {
        0: "grace",
        5: "grace",
        6: "standard",
        29: "standard",
        30: "severe",
        31: "severe",
    }
    for days_past_due, expected in boundary_cases.items():
        assert classify_late_fee_tier(days_past_due) == expected


def test_negative_days_past_due_is_rejected() -> None:
    with pytest.raises(ValidationError):
        classify_late_fee_tier(-1)
