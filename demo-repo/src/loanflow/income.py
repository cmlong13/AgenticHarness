"""Income and debt line items."""
from __future__ import annotations

from dataclasses import dataclass

from loanflow.errors import ValidationError
from loanflow.primitives import Money


@dataclass(frozen=True)
class IncomeSource:
    label: str
    monthly_amount: Money

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValidationError("IncomeSource.label must not be empty")
        if self.monthly_amount.is_negative():
            raise ValidationError("IncomeSource.monthly_amount must not be negative")


@dataclass(frozen=True)
class DebtObligation:
    label: str
    monthly_payment: Money

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValidationError("DebtObligation.label must not be empty")
        if self.monthly_payment.is_negative():
            raise ValidationError("DebtObligation.monthly_payment must not be negative")
