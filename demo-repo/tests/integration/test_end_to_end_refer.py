from __future__ import annotations

from loanflow.applicants import Applicant
from loanflow.audit import AuditLog
from loanflow.collateral import Collateral
from loanflow.decisions import DecisionOutcome
from loanflow.income import DebtObligation, IncomeSource
from loanflow.pipeline import run_pipeline
from loanflow.primitives import Money
from loanflow.underwriting_rules import RuleOutcome


def test_borderline_dti_applicant_is_referred_for_manual_review(default_thresholds, frozen_clock) -> None:
    applicant = Applicant(applicant_id="AP-100003", full_name="Alex Chen", years_employed=4)
    collateral = Collateral(description="townhouse", appraised_value=Money.from_dollars(300_000))
    audit_log = AuditLog()

    result = run_pipeline(
        applicant=applicant,
        application_id="LN-1003",
        requested_amount=Money.from_dollars(150_000),
        collateral=collateral,
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(10_000))],
        debt_obligations=[DebtObligation(label="student_loan", monthly_payment=Money.from_dollars(4_400))],
        credit_score=780,
        thresholds=default_thresholds,
        clock=frozen_clock,
        audit_log=audit_log,
    )

    assert result.decision.outcome is DecisionOutcome.REFER
    assert not any(r.outcome is RuleOutcome.HARD_FAIL for r in result.decision.rule_results)
    assert any(r.outcome is RuleOutcome.BORDERLINE for r in result.decision.rule_results)
