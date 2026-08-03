from __future__ import annotations

import pytest

from loanflow.applications import LoanApplication
from loanflow.errors import RepositoryError
from loanflow.primitives import Money
from loanflow.repository import ApplicationRepository


def _application(application_id, applicant, sample_collateral, sample_income, sample_debt):
    return LoanApplication(
        application_id=application_id,
        applicant=applicant,
        requested_amount=Money.from_dollars(100_000),
        collateral=sample_collateral,
        income_sources=sample_income,
        debt_obligations=sample_debt,
    )


def test_add_then_get_returns_the_same_application(sample_applicant, sample_collateral, sample_income, sample_debt) -> None:
    repo = ApplicationRepository()
    application = _application("LN-0001", sample_applicant, sample_collateral, sample_income, sample_debt)
    repo.add(application)
    assert repo.get("LN-0001") is application


def test_add_duplicate_application_id_raises(sample_applicant, sample_collateral, sample_income, sample_debt) -> None:
    repo = ApplicationRepository()
    application = _application("LN-0001", sample_applicant, sample_collateral, sample_income, sample_debt)
    repo.add(application)
    with pytest.raises(RepositoryError):
        repo.add(application)


def test_get_missing_application_id_raises(sample_applicant, sample_collateral, sample_income, sample_debt) -> None:
    repo = ApplicationRepository()
    with pytest.raises(RepositoryError):
        repo.get("does-not-exist")


def test_all_returns_every_added_application(sample_applicant, sample_collateral, sample_income, sample_debt) -> None:
    repo = ApplicationRepository()
    repo.add(_application("LN-0001", sample_applicant, sample_collateral, sample_income, sample_debt))
    repo.add(_application("LN-0002", sample_applicant, sample_collateral, sample_income, sample_debt))
    assert {app.application_id for app in repo.all()} == {"LN-0001", "LN-0002"}


def test_len_reflects_number_of_stored_applications(
    sample_applicant, sample_collateral, sample_income, sample_debt
) -> None:
    repo = ApplicationRepository()
    assert len(repo) == 0
    repo.add(_application("LN-0001", sample_applicant, sample_collateral, sample_income, sample_debt))
    assert len(repo) == 1
