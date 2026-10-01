from __future__ import annotations

from datetime import datetime, timezone

import pytest

from loanflow.errors import ValidationError
from loanflow.primitives import FixedClock, Money, Percentage


def test_money_from_dollars_converts_to_cents() -> None:
    assert Money.from_dollars(19.99) == Money(1999)
    assert Money.from_dollars("100") == Money(10_000)


def test_money_add_and_subtract() -> None:
    assert Money(500) + Money(250) == Money(750)
    assert Money(500) - Money(250) == Money(250)


def test_money_rejects_non_integer_cents() -> None:
    with pytest.raises(ValidationError):
        Money(1.5)  # type: ignore[arg-type]


def test_money_is_negative() -> None:
    assert Money(-100).is_negative()
    assert not Money(0).is_negative()


def test_money_as_dollars_formats_with_sign() -> None:
    assert Money(1999).as_dollars() == "19.99"
    assert Money(-500).as_dollars() == "-5.00"


def test_percentage_from_ratio_and_display() -> None:
    pct = Percentage.from_ratio(0.365)
    assert pct.basis_points == 3650
    assert pct.as_percent_string() == "36.50%"


def test_percentage_rejects_negative_basis_points() -> None:
    with pytest.raises(ValidationError):
        Percentage(-1)


def test_percentage_rejects_non_integer_and_bool_basis_points() -> None:
    with pytest.raises(ValidationError):
        Percentage(1.5)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Percentage(True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Percentage(False)  # type: ignore[arg-type]


def test_fixed_clock_returns_the_same_instant_every_call() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert clock.now() == clock.now()
    assert clock.today() == clock.now().date()
