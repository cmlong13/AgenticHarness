from __future__ import annotations

import pytest

from loanflow.dti import calculate_dti
from loanflow.errors import ValidationError
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money


def _income(amount: float) -> list[IncomeSource]:
    return [IncomeSource(label="salary", monthly_amount=Money.from_dollars(amount))]


def _debt(amount: float) -> list[DebtObligation]:
    return [DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(amount))]


def test_calculate_dti_normal_case() -> None:
    dti = calculate_dti(_income(5000), _debt(1500))
    assert dti.as_percent_string() == "30.00%"


def test_calculate_dti_with_zero_debt_is_zero_percent() -> None:
    dti = calculate_dti(_income(5000), _debt(0))
    assert dti.basis_points == 0


def test_calculate_dti_at_equal_income_and_debt_is_one_hundred_percent() -> None:
    dti = calculate_dti(_income(2000), _debt(2000))
    assert dti.as_percent_string() == "100.00%"


def test_calculate_dti_raises_when_total_income_is_zero() -> None:
    with pytest.raises(ValidationError):
        calculate_dti(_income(0), _debt(500))
