"""Human-readable explanation text for a Decision."""
from __future__ import annotations

from loanflow.decisions import Decision, DecisionOutcome
from loanflow.risk_bands import Band
from loanflow.underwriting_rules import RuleOutcome

_OUTCOME_HEADLINE = {
    DecisionOutcome.APPROVE: "Application approved.",
    DecisionOutcome.REFER: "Application referred for manual review.",
    DecisionOutcome.DECLINE: "Application declined.",
}

_MARKER_BY_OUTCOME = {
    RuleOutcome.PASS: "OK",
    RuleOutcome.BORDERLINE: "BORDERLINE",
    RuleOutcome.HARD_FAIL: "FAIL",
}


def explain(decision: Decision, overall_risk_band: Band) -> str:
    lines = [_OUTCOME_HEADLINE[decision.outcome], f"Overall risk band: {overall_risk_band.value}"]
    for result in decision.rule_results:
        marker = _MARKER_BY_OUTCOME[result.outcome]
        lines.append(f"  [{marker}] {result.rule_name}: {result.reason}")
    return "\n".join(lines)
