from __future__ import annotations

import pytest

from loanflow.collateral import Collateral, calculate_ltv
from loanflow.errors import ValidationError
from loanflow.primitives import Money


def test_valid_collateral_constructs() -> None:
    collateral = Collateral(description="condo", appraised_value=Money.from_dollars(200_000))
    assert collateral.appraised_value == Money(20_000_000)


def test_collateral_rejects_non_positive_appraised_value() -> None:
    with pytest.raises(ValidationError):
        Collateral(description="condo", appraised_value=Money(0))


def test_calculate_ltv_normal_case() -> None:
    collateral = Collateral(description="condo", appraised_value=Money.from_dollars(200_000))
    ltv = calculate_ltv(Money.from_dollars(160_000), collateral)
    assert ltv.as_percent_string() == "80.00%"


def test_calculate_ltv_at_full_value_is_one_hundred_percent() -> None:
    collateral = Collateral(description="condo", appraised_value=Money.from_dollars(200_000))
    ltv = calculate_ltv(Money.from_dollars(200_000), collateral)
    assert ltv.as_percent_string() == "100.00%"


def test_calculate_ltv_rejects_negative_loan_amount() -> None:
    collateral = Collateral(description="condo", appraised_value=Money.from_dollars(200_000))
    with pytest.raises(ValidationError):
        calculate_ltv(Money(-1), collateral)
