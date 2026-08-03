"""Underwriting rule definitions.

Each rule inspects a RiskProfile against Thresholds and returns a
RuleResult: pass, borderline (refer), or hard-fail (decline). Adding a new
rule means adding one more function of this same shape to
UNDERWRITING_RULES -- see demo-repo/README.md for a worked example of that
minimal-diff pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Tuple

from loanflow.config import Thresholds
from loanflow.risk_bands import Band, CreditBucket
from loanflow.risk_profile import RiskProfile


class RuleOutcome(str, Enum):
    PASS = "pass"
    BORDERLINE = "borderline"
    HARD_FAIL = "hard_fail"


@dataclass(frozen=True)
class RuleResult:
    rule_name: str
    outcome: RuleOutcome
    reason: str


def max_dti_rule(profile: RiskProfile, thresholds: Thresholds) -> RuleResult:
    if profile.dti.basis_points > thresholds.max_dti_hard_fail.basis_points:
        return RuleResult(
            "max_dti_rule",
            RuleOutcome.HARD_FAIL,
            f"DTI {profile.dti.as_percent_string()} exceeds the hard-fail limit "
            f"{thresholds.max_dti_hard_fail.as_percent_string()}",
        )
    if profile.dti_band is Band.HIGH:
        return RuleResult(
            "max_dti_rule", RuleOutcome.BORDERLINE, f"DTI {profile.dti.as_percent_string()} is in the high band"
        )
    return RuleResult("max_dti_rule", RuleOutcome.PASS, f"DTI {profile.dti.as_percent_string()} is acceptable")


def max_ltv_rule(profile: RiskProfile, thresholds: Thresholds) -> RuleResult:
    if profile.ltv.basis_points > thresholds.max_ltv_hard_fail.basis_points:
        return RuleResult(
            "max_ltv_rule",
            RuleOutcome.HARD_FAIL,
            f"LTV {profile.ltv.as_percent_string()} exceeds the hard-fail limit "
            f"{thresholds.max_ltv_hard_fail.as_percent_string()}",
        )
    if profile.ltv_band is Band.HIGH:
        return RuleResult(
            "max_ltv_rule", RuleOutcome.BORDERLINE, f"LTV {profile.ltv.as_percent_string()} is in the high band"
        )
    return RuleResult("max_ltv_rule", RuleOutcome.PASS, f"LTV {profile.ltv.as_percent_string()} is acceptable")


def min_credit_score_rule(profile: RiskProfile, thresholds: Thresholds) -> RuleResult:
    if profile.credit_score < thresholds.min_credit_score:
        return RuleResult(
            "min_credit_score_rule",
            RuleOutcome.HARD_FAIL,
            f"credit score {profile.credit_score} is below the minimum {thresholds.min_credit_score}",
        )
    if profile.credit_bucket is CreditBucket.FAIR:
        return RuleResult(
            "min_credit_score_rule",
            RuleOutcome.BORDERLINE,
            f"credit score {profile.credit_score} is in the fair bucket",
        )
    return RuleResult(
        "min_credit_score_rule", RuleOutcome.PASS, f"credit score {profile.credit_score} is acceptable"
    )


UNDERWRITING_RULES: Tuple[Callable[[RiskProfile, Thresholds], RuleResult], ...] = (
    max_dti_rule,
    max_ltv_rule,
    min_credit_score_rule,
)
