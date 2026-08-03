from __future__ import annotations

from loanflow.applicants import Applicant
from loanflow.audit import AuditLog
from loanflow.collateral import Collateral
from loanflow.decisions import DecisionOutcome
from loanflow.income import DebtObligation, IncomeSource
from loanflow.pipeline import run_pipeline
from loanflow.primitives import Money
from loanflow.underwriting_rules import RuleOutcome


def test_high_debt_low_credit_applicant_is_declined(default_thresholds, frozen_clock) -> None:
    applicant = Applicant(applicant_id="AP-100002", full_name="Sam Okafor", years_employed=2)
    collateral = Collateral(description="condo", appraised_value=Money.from_dollars(250_000))
    audit_log = AuditLog()

    result = run_pipeline(
        applicant=applicant,
        application_id="LN-1002",
        requested_amount=Money.from_dollars(240_000),
        collateral=collateral,
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(4_000))],
        debt_obligations=[
            DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(900)),
            DebtObligation(label="credit_card", monthly_payment=Money.from_dollars(1_300)),
        ],
        credit_score=560,
        thresholds=default_thresholds,
        clock=frozen_clock,
        audit_log=audit_log,
    )

    assert result.decision.outcome is DecisionOutcome.DECLINE
    assert any(r.outcome is RuleOutcome.HARD_FAIL for r in result.decision.rule_results)
