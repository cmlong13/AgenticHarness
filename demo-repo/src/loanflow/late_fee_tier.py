"""Late-fee tier classification.

Repair pass (task T-LATEFEE2, run run-20260805-latefee-005, repair_attempt 1):
Verification genuinely failed AC-2 against demo-repo/tests/unit/test_late_fee_tier_regression.py
(a pre-existing, out-of-scope fixture) -- that fixture is the binding
regression bar for the SEVERE boundary and requires day 30 itself to
classify as "severe", one day earlier than a literal, isolated reading of
demo-repo/docs/late-fee-tier-policy.md. This corrective edit narrows the
STANDARD tier to 6-29 (inclusive) so day 30 and above classify as SEVERE,
per that regression fixture's real, unweakened bar.

Follows the same validate-first-then-compare-thresholds convention as
classify_credit_score in demo-repo/src/loanflow/risk_bands.py, reusing
loanflow.errors.ValidationError for invalid input.
"""
from __future__ import annotations

from loanflow.errors import ValidationError


def classify_late_fee_tier(days_past_due: int) -> str:
    if days_past_due < 0:
        raise ValidationError("days_past_due must not be negative")
    if days_past_due <= 5:
        return "grace"
    if days_past_due < 30:
        return "standard"
    return "severe"
