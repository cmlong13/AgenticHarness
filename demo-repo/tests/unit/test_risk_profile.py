from __future__ import annotations

from loanflow.collateral import Collateral
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money
from loanflow.risk_bands import Band, CreditBucket
from loanflow.risk_profile import build_risk_profile


def test_low_risk_inputs_produce_low_overall_band(default_thresholds) -> None:
    profile = build_risk_profile(
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(10_000))],
        debt_obligations=[DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(500))],
        loan_amount=Money.from_dollars(150_000),
        collateral=Collateral(description="home", appraised_value=Money.from_dollars(300_000)),
        credit_score=780,
        thresholds=default_thresholds,
    )
    assert profile.dti_band is Band.LOW
    assert profile.ltv_band is Band.LOW
    assert profile.credit_bucket is CreditBucket.EXCELLENT
    assert profile.overall_band is Band.LOW


def test_high_dti_alone_drives_overall_band_to_high(default_thresholds) -> None:
    profile = build_risk_profile(
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(2_000))],
        debt_obligations=[DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(1_800))],
        loan_amount=Money.from_dollars(50_000),
        collateral=Collateral(description="home", appraised_value=Money.from_dollars(300_000)),
        credit_score=780,
        thresholds=default_thresholds,
    )
    assert profile.dti_band is Band.HIGH
    assert profile.overall_band is Band.HIGH


def test_poor_credit_alone_drives_overall_band_to_high(default_thresholds) -> None:
    profile = build_risk_profile(
        income_sources=[IncomeSource(label="salary", monthly_amount=Money.from_dollars(10_000))],
        debt_obligations=[DebtObligation(label="auto_loan", monthly_payment=Money.from_dollars(500))],
        loan_amount=Money.from_dollars(150_000),
        collateral=Collateral(description="home", appraised_value=Money.from_dollars(300_000)),
        credit_score=500,
        thresholds=default_thresholds,
    )
    assert profile.credit_bucket is CreditBucket.POOR
    assert profile.overall_band is Band.HIGH
