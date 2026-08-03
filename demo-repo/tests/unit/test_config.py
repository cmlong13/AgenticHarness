from __future__ import annotations

from datetime import date

import pytest

from loanflow.config import DEFAULT_THRESHOLDS, Thresholds
from loanflow.errors import ConfigError
from loanflow.primitives import Percentage


def _thresholds(**overrides):
    base = dict(
        dti_low_max=Percentage.from_ratio(0.36),
        dti_medium_max=Percentage.from_ratio(0.43),
        ltv_low_max=Percentage.from_ratio(0.80),
        ltv_medium_max=Percentage.from_ratio(0.95),
        max_dti_hard_fail=Percentage.from_ratio(0.50),
        max_ltv_hard_fail=Percentage.from_ratio(1.00),
        min_credit_score=580,
        effective_date=date(2026, 1, 1),
    )
    base.update(overrides)
    return Thresholds(**base)


def test_default_thresholds_are_valid() -> None:
    assert DEFAULT_THRESHOLDS.min_credit_score == 580


def test_thresholds_rejects_inverted_dti_bounds() -> None:
    with pytest.raises(ConfigError):
        _thresholds(dti_low_max=Percentage.from_ratio(0.50), dti_medium_max=Percentage.from_ratio(0.40))


def test_thresholds_rejects_inverted_ltv_bounds() -> None:
    with pytest.raises(ConfigError):
        _thresholds(ltv_low_max=Percentage.from_ratio(0.99), ltv_medium_max=Percentage.from_ratio(0.90))


def test_thresholds_rejects_out_of_range_credit_score() -> None:
    with pytest.raises(ConfigError):
        _thresholds(min_credit_score=200)
