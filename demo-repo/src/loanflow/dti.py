"""Debt-to-income ratio calculation."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import List

from loanflow.errors import ValidationError
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money, Percentage


def total_monthly_income(sources: List[IncomeSource]) -> Money:
    total = Money(0)
    for source in sources:
        total = total + source.monthly_amount
    return total


def total_monthly_debt(obligations: List[DebtObligation]) -> Money:
    total = Money(0)
    for obligation in obligations:
        total = total + obligation.monthly_payment
    return total


def calculate_dti(income_sources: List[IncomeSource], debt_obligations: List[DebtObligation]) -> Percentage:
    """Debt-to-income ratio: total monthly debt payments / total monthly income.

    Raises ValidationError when there is no income to divide by -- a DTI
    ratio is undefined in that case, not "infinite" or "zero".
    """
    income = total_monthly_income(income_sources)
    debt = total_monthly_debt(debt_obligations)
    if income.cents <= 0:
        raise ValidationError("cannot calculate DTI with zero or negative total monthly income")
    ratio = (Decimal(debt.cents) / Decimal(income.cents)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return Percentage(int(ratio * 10_000))
