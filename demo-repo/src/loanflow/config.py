"""Underwriting threshold configuration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from loanflow.errors import ConfigError
from loanflow.primitives import Percentage


@dataclass(frozen=True)
class Thresholds:
    dti_low_max: Percentage
    dti_medium_max: Percentage
    ltv_low_max: Percentage
    ltv_medium_max: Percentage
    max_dti_hard_fail: Percentage
    max_ltv_hard_fail: Percentage
    min_credit_score: int
    effective_date: date

    def __post_init__(self) -> None:
        if self.dti_low_max.basis_points > self.dti_medium_max.basis_points:
            raise ConfigError("dti_low_max must not exceed dti_medium_max")
        if self.ltv_low_max.basis_points > self.ltv_medium_max.basis_points:
            raise ConfigError("ltv_low_max must not exceed ltv_medium_max")
        if not (300 <= self.min_credit_score <= 850):
            raise ConfigError("min_credit_score must be between 300 and 850")


DEFAULT_THRESHOLDS = Thresholds(
    dti_low_max=Percentage.from_ratio(0.36),
    dti_medium_max=Percentage.from_ratio(0.43),
    ltv_low_max=Percentage.from_ratio(0.80),
    ltv_medium_max=Percentage.from_ratio(0.95),
    max_dti_hard_fail=Percentage.from_ratio(0.50),
    max_ltv_hard_fail=Percentage.from_ratio(1.00),
    min_credit_score=580,
    effective_date=date(2026, 1, 1),
)
