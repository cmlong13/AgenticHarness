"""Shared pytest fixtures for the loanflow demo test suite."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from loanflow.applicants import Applicant
from loanflow.collateral import Collateral
from loanflow.config import DEFAULT_THRESHOLDS, Thresholds
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import FixedClock, Money


@pytest.fixture
def frozen_clock() -> FixedClock:
    return FixedClock(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def default_thresholds() -> Thresholds:
    return DEFAULT_THRESHOLDS


@pytest.fixture
def sample_applicant() -> Applicant:
    return Applicant(applicant_id="AP-000123", full_name="Jordan Rivera", years_employed=5)


@pytest.fixture
def sample_income() -> list[IncomeSource]:
    return [IncomeSource(label="salary", monthly_amount=Money.from_dollars(6000))]


@pytest.fixture
def sample_debt() -> list[DebtObligation]:
    return [DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(400))]


@pytest.fixture
def sample_collateral() -> Collateral:
    return Collateral(description="single-family home", appraised_value=Money.from_dollars(300_000))
