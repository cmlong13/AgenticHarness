from __future__ import annotations

from loanflow.decisions import DecisionOutcome, recommend
from loanflow.underwriting_rules import RuleOutcome, RuleResult


def _result(outcome: RuleOutcome) -> RuleResult:
    return RuleResult("some_rule", outcome, "test reason")


def test_all_passing_rules_recommends_approve() -> None:
    decision = recommend([_result(RuleOutcome.PASS), _result(RuleOutcome.PASS)])
    assert decision.outcome is DecisionOutcome.APPROVE


def test_a_borderline_rule_recommends_refer() -> None:
    decision = recommend([_result(RuleOutcome.PASS), _result(RuleOutcome.BORDERLINE)])
    assert decision.outcome is DecisionOutcome.REFER


def test_a_hard_fail_rule_recommends_decline() -> None:
    decision = recommend([_result(RuleOutcome.PASS), _result(RuleOutcome.HARD_FAIL)])
    assert decision.outcome is DecisionOutcome.DECLINE


def test_hard_fail_outranks_a_simultaneous_borderline() -> None:
    decision = recommend([_result(RuleOutcome.BORDERLINE), _result(RuleOutcome.HARD_FAIL)])
    assert decision.outcome is DecisionOutcome.DECLINE
