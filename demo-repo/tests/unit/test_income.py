from __future__ import annotations

import pytest

from loanflow.errors import ValidationError
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money


def test_valid_income_source_constructs() -> None:
    source = IncomeSource(label="salary", monthly_amount=Money.from_dollars(5000))
    assert source.monthly_amount == Money(500_000)


def test_income_source_rejects_empty_label() -> None:
    with pytest.raises(ValidationError):
        IncomeSource(label="  ", monthly_amount=Money.from_dollars(100))


def test_income_source_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        IncomeSource(label="salary", monthly_amount=Money(-1))


def test_income_source_allows_zero_amount() -> None:
    source = IncomeSource(label="salary", monthly_amount=Money(0))
    assert source.monthly_amount == Money(0)


def test_valid_debt_obligation_constructs() -> None:
    debt = DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(400))
    assert debt.monthly_payment == Money(40_000)


def test_debt_obligation_rejects_negative_payment() -> None:
    with pytest.raises(ValidationError):
        DebtObligation(label="auto_loan", monthly_payment=Money(-1))
