from __future__ import annotations

from loanflow.decisions import Decision, DecisionOutcome
from loanflow.explanations import explain
from loanflow.underwriting_rules import RuleOutcome, RuleResult


def test_explain_approve_mentions_approval_and_each_rule() -> None:
    decision = Decision(DecisionOutcome.APPROVE, (RuleResult("max_dti_rule", RuleOutcome.PASS, "DTI is fine"),))
    text = explain(decision)
    assert "approved" in text.lower()
    assert "max_dti_rule" in text
    assert "[OK]" in text


def test_explain_refer_mentions_manual_review() -> None:
    decision = Decision(
        DecisionOutcome.REFER, (RuleResult("max_dti_rule", RuleOutcome.BORDERLINE, "DTI is borderline"),)
    )
    text = explain(decision)
    assert "manual review" in text.lower()
    assert "[BORDERLINE]" in text


def test_explain_decline_mentions_decline_and_the_failing_rule() -> None:
    decision = Decision(
        DecisionOutcome.DECLINE, (RuleResult("max_ltv_rule", RuleOutcome.HARD_FAIL, "LTV too high"),)
    )
    text = explain(decision)
    assert "declined" in text.lower()
    assert "[FAIL]" in text
    assert "LTV too high" in text
