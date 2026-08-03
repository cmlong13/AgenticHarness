"""Collateral valuation and loan-to-value calculation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from loanflow.errors import ValidationError
from loanflow.primitives import Money, Percentage


@dataclass(frozen=True)
class Collateral:
    description: str
    appraised_value: Money

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValidationError("Collateral.description must not be empty")
        if self.appraised_value.cents <= 0:
            raise ValidationError("Collateral.appraised_value must be positive")


def calculate_ltv(loan_amount: Money, collateral: Collateral) -> Percentage:
    """Loan-to-value ratio: loan amount / appraised collateral value."""
    if loan_amount.is_negative():
        raise ValidationError("loan_amount must not be negative")
    ratio = (Decimal(loan_amount.cents) / Decimal(collateral.appraised_value.cents)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return Percentage(int(ratio * 10_000))
