"""Currency and percentage value types, and an injectable clock.

Money is stored as integer cents so currency math never touches binary
floats. Percentage is stored as integer basis points (1/100 of a percent)
for the same reason. Anything in loanflow that needs "the current time"
takes a Clock instead of calling datetime.now() directly, so every
calculation stays deterministic under test.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, Union

from loanflow.errors import ValidationError


@dataclass(frozen=True, order=True)
class Money:
    cents: int

    def __post_init__(self) -> None:
        if isinstance(self.cents, bool) or not isinstance(self.cents, int):
            raise ValidationError("Money.cents must be an integer number of cents")

    @classmethod
    def from_dollars(cls, dollars: Union[float, int, str]) -> "Money":
        value = Decimal(str(dollars)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return cls(int(value * 100))

    def __add__(self, other: "Money") -> "Money":
        return Money(self.cents + other.cents)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.cents - other.cents)

    def is_negative(self) -> bool:
        return self.cents < 0

    def as_dollars(self) -> str:
        sign = "-" if self.cents < 0 else ""
        whole, frac = divmod(abs(self.cents), 100)
        return f"{sign}{whole}.{frac:02d}"


@dataclass(frozen=True, order=True)
class Percentage:
    """A ratio expressed in basis points (1/100 of a percent)."""

    basis_points: int

    def __post_init__(self) -> None:
        if self.basis_points < 0:
            raise ValidationError("Percentage.basis_points must not be negative")

    @classmethod
    def from_ratio(cls, ratio: Union[float, str]) -> "Percentage":
        value = (Decimal(str(ratio)) * 10_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return cls(int(value))

    def as_percent_string(self) -> str:
        whole, frac = divmod(self.basis_points, 100)
        return f"{whole}.{frac:02d}%"


class Clock(Protocol):
    def today(self) -> date: ...

    def now(self) -> datetime: ...


class SystemClock:
    def today(self) -> date:
        return datetime.now(timezone.utc).date()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FixedClock:
    fixed_now: datetime

    def today(self) -> date:
        return self.fixed_now.date()

    def now(self) -> datetime:
        return self.fixed_now
