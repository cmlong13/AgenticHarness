from __future__ import annotations

from loanflow.applicants import Applicant
from loanflow.audit import AuditLog, EventType
from loanflow.collateral import Collateral
from loanflow.decisions import DecisionOutcome
from loanflow.income import DebtObligation, IncomeSource
from loanflow.pipeline import run_pipeline
from loanflow.primitives import Money


def test_strong_applicant_is_approved_and_fully_audited(default_thresholds, frozen_clock) -> None:
    applicant = Applicant(applicant_id="AP-100001", full_name="Jordan Rivera", years_employed=5)
    collateral = Collateral(description="single-family home", appraised_value=Money.from_dollars(300_000))
    audit_log = AuditLog()

    result = run_pipeline(
        applicant=applicant,
        application_id="LN-1001",
        requested_amount=Money.from_dollars(150_000),
        collateral=collateral,
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(10_000))],
        debt_obligations=[DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(500))],
        credit_score=780,
        thresholds=default_thresholds,
        clock=frozen_clock,
        audit_log=audit_log,
    )

    assert result.decision.outcome is DecisionOutcome.APPROVE
    assert result.risk_profile.overall_band.value == "low"
    assert len(audit_log) == 2
    assert [e.event_type for e in audit_log.events()] == [
        EventType.APPLICATION_SUBMITTED, EventType.DECISION_MADE
    ]
