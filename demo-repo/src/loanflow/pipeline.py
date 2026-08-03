"""Wires the full applicant -> risk -> decision -> audit pipeline together.

This is the single place that defines the composition order. Both the CLI
and the integration tests call this instead of re-deriving the wiring
themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from loanflow.applicants import Applicant
from loanflow.audit import AuditEvent, AuditLog, EventType
from loanflow.collateral import Collateral
from loanflow.config import Thresholds
from loanflow.decisions import Decision, recommend
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Clock, Money
from loanflow.risk_profile import RiskProfile, build_risk_profile
from loanflow.underwriting_engine import UnderwritingEngine


@dataclass(frozen=True)
class PipelineResult:
    risk_profile: RiskProfile
    decision: Decision


def run_pipeline(
    *,
    applicant: Applicant,
    application_id: str,
    requested_amount: Money,
    collateral: Collateral,
    income_sources: List[IncomeSource],
    debt_obligations: List[DebtObligation],
    credit_score: int,
    thresholds: Thresholds,
    clock: Clock,
    audit_log: AuditLog,
    engine: Optional[UnderwritingEngine] = None,
) -> PipelineResult:
    engine = engine or UnderwritingEngine()

    audit_log.append(AuditEvent(
        EventType.APPLICATION_SUBMITTED,
        application_id,
        f"application submitted for applicant {applicant.applicant_id}",
        clock.now(),
    ))

    profile = build_risk_profile(
        income_sources=income_sources,
        debt_obligations=debt_obligations,
        loan_amount=requested_amount,
        collateral=collateral,
        credit_score=credit_score,
        thresholds=thresholds,
    )
    rule_results = engine.evaluate(profile, thresholds)
    decision = recommend(rule_results)

    audit_log.append(AuditEvent(
        EventType.DECISION_MADE,
        application_id,
        f"decision {decision.outcome.value} for applicant {applicant.applicant_id}",
        clock.now(),
    ))

    return PipelineResult(risk_profile=profile, decision=decision)
