from __future__ import annotations

import pytest

from loanflow.applications import ApplicationStatus, LoanApplication
from loanflow.errors import TransitionError, ValidationError


def _build_application(applicant, sample_collateral, sample_income, sample_debt, requested_amount=None):
    from loanflow.primitives import Money

    return LoanApplication(
        application_id="LN-0001",
        applicant=applicant,
        requested_amount=requested_amount or Money.from_dollars(250_000),
        collateral=sample_collateral,
        income_sources=sample_income,
        debt_obligations=sample_debt,
    )


def test_valid_application_starts_in_draft(sample_applicant, sample_collateral, sample_income, sample_debt) -> None:
    application = _build_application(sample_applicant, sample_collateral, sample_income, sample_debt)
    assert application.status is ApplicationStatus.DRAFT


def test_application_rejects_empty_application_id(sample_applicant, sample_collateral, sample_income, sample_debt) -> None:
    from loanflow.primitives import Money

    with pytest.raises(ValidationError):
        LoanApplication(
            application_id="   ",
            applicant=sample_applicant,
            requested_amount=Money.from_dollars(100_000),
            collateral=sample_collateral,
            income_sources=sample_income,
            debt_obligations=sample_debt,
        )


def test_application_rejects_non_positive_requested_amount(
    sample_applicant, sample_collateral, sample_income, sample_debt
) -> None:
    from loanflow.primitives import Money

    with pytest.raises(ValidationError):
        _build_application(
            sample_applicant, sample_collateral, sample_income, sample_debt, requested_amount=Money(0)
        )


def test_full_lifecycle_transition_sequence_succeeds(
    sample_applicant, sample_collateral, sample_income, sample_debt
) -> None:
    application = _build_application(sample_applicant, sample_collateral, sample_income, sample_debt)
    application.advance_to(ApplicationStatus.SUBMITTED)
    application.advance_to(ApplicationStatus.UNDER_REVIEW)
    application.advance_to(ApplicationStatus.DECISIONED)
    assert application.status is ApplicationStatus.DECISIONED


def test_skipping_a_lifecycle_step_is_rejected(
    sample_applicant, sample_collateral, sample_income, sample_debt
) -> None:
    application = _build_application(sample_applicant, sample_collateral, sample_income, sample_debt)
    with pytest.raises(TransitionError):
        application.advance_to(ApplicationStatus.DECISIONED)


def test_decisioned_is_a_terminal_status(sample_applicant, sample_collateral, sample_income, sample_debt) -> None:
    application = _build_application(sample_applicant, sample_collateral, sample_income, sample_debt)
    application.advance_to(ApplicationStatus.SUBMITTED)
    application.advance_to(ApplicationStatus.UNDER_REVIEW)
    application.advance_to(ApplicationStatus.DECISIONED)
    with pytest.raises(TransitionError):
        application.advance_to(ApplicationStatus.SUBMITTED)
