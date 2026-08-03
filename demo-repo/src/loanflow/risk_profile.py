"""Aggregate risk profile combining DTI band, LTV band, and credit bucket."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from loanflow.collateral import Collateral, calculate_ltv
from loanflow.config import Thresholds
from loanflow.dti import calculate_dti
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money, Percentage
from loanflow.risk_bands import Band, CreditBucket, classify_credit_score, classify_dti, classify_ltv

_BAND_WEIGHT = {Band.LOW: 0, Band.MEDIUM: 1, Band.HIGH: 2}
_BUCKET_WEIGHT = {
    CreditBucket.EXCELLENT: 0,
    CreditBucket.GOOD: 0,
    CreditBucket.FAIR: 1,
    CreditBucket.POOR: 2,
}
_WEIGHT_TO_BAND = {0: Band.LOW, 1: Band.MEDIUM, 2: Band.HIGH}


@dataclass(frozen=True)
class RiskProfile:
    dti: Percentage
    dti_band: Band
    ltv: Percentage
    ltv_band: Band
    credit_score: int
    credit_bucket: CreditBucket
    overall_band: Band


def build_risk_profile(
    *,
    income_sources: List[IncomeSource],
    debt_obligations: List[DebtObligation],
    loan_amount: Money,
    collateral: Collateral,
    credit_score: int,
    thresholds: Thresholds,
) -> RiskProfile:
    dti = calculate_dti(income_sources, debt_obligations)
    ltv = calculate_ltv(loan_amount, collateral)
    dti_band = classify_dti(dti, thresholds)
    ltv_band = classify_ltv(ltv, thresholds)
    credit_bucket = classify_credit_score(credit_score)
    combined_weight = max(
        _BAND_WEIGHT[dti_band], _BAND_WEIGHT[ltv_band], _BUCKET_WEIGHT[credit_bucket]
    )
    return RiskProfile(
        dti=dti,
        dti_band=dti_band,
        ltv=ltv,
        ltv_band=ltv_band,
        credit_score=credit_score,
        credit_bucket=credit_bucket,
        overall_band=_WEIGHT_TO_BAND[combined_weight],
    )
