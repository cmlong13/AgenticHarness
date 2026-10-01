from __future__ import annotations

import pytest

from loanflow.errors import ValidationError
from loanflow.risk_bands import Band, CreditBucket, classify_credit_score, classify_dti, classify_ltv
from loanflow.primitives import Percentage


def test_dti_at_low_max_boundary_is_low(default_thresholds) -> None:
    assert classify_dti(default_thresholds.dti_low_max, default_thresholds) is Band.LOW


def test_dti_just_above_low_max_is_medium(default_thresholds) -> None:
    just_above = Percentage(default_thresholds.dti_low_max.basis_points + 1)
    assert classify_dti(just_above, default_thresholds) is Band.MEDIUM


def test_dti_at_medium_max_boundary_is_medium(default_thresholds) -> None:
    assert classify_dti(default_thresholds.dti_medium_max, default_thresholds) is Band.MEDIUM


def test_dti_just_above_medium_max_is_high(default_thresholds) -> None:
    just_above = Percentage(default_thresholds.dti_medium_max.basis_points + 1)
    assert classify_dti(just_above, default_thresholds) is Band.HIGH


def test_ltv_at_low_max_boundary_is_low(default_thresholds) -> None:
    assert classify_ltv(default_thresholds.ltv_low_max, default_thresholds) is Band.LOW


def test_ltv_just_above_medium_max_is_high(default_thresholds) -> None:
    just_above = Percentage(default_thresholds.ltv_medium_max.basis_points + 1)
    assert classify_ltv(just_above, default_thresholds) is Band.HIGH


def test_credit_bucket_boundaries() -> None:
    boundary_cases = {
        579: CreditBucket.POOR, 580: CreditBucket.FAIR,
        669: CreditBucket.FAIR, 670: CreditBucket.GOOD,
        739: CreditBucket.GOOD, 740: CreditBucket.EXCELLENT,
    }
    for score, expected in boundary_cases.items():
        assert classify_credit_score(score) is expected


def test_credit_score_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        classify_credit_score(299)
    with pytest.raises(ValidationError):
        classify_credit_score(851)


def test_credit_score_non_integer_and_bool_is_rejected() -> None:
    with pytest.raises(ValidationError):
        classify_credit_score(700.5)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        classify_credit_score(700.0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        classify_credit_score(True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        classify_credit_score(False)  # type: ignore[arg-type]
