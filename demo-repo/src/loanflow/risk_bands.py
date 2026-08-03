"""Boundary classification of DTI, LTV, and credit signals into risk bands.

These are pure threshold-comparison functions -- the natural, isolated
location for a future boundary-value defect demonstration (see
demo-repo/README.md, "Future demonstration readiness").
"""
from __future__ import annotations

from enum import Enum

from loanflow.config import Thresholds
from loanflow.errors import ValidationError
from loanflow.primitives import Percentage


class Band(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def classify_dti(dti: Percentage, thresholds: Thresholds) -> Band:
    if dti.basis_points <= thresholds.dti_low_max.basis_points:
        return Band.LOW
    if dti.basis_points <= thresholds.dti_medium_max.basis_points:
        return Band.MEDIUM
    return Band.HIGH


def classify_ltv(ltv: Percentage, thresholds: Thresholds) -> Band:
    if ltv.basis_points <= thresholds.ltv_low_max.basis_points:
        return Band.LOW
    if ltv.basis_points <= thresholds.ltv_medium_max.basis_points:
        return Band.MEDIUM
    return Band.HIGH


class CreditBucket(str, Enum):
    POOR = "poor"
    FAIR = "fair"
    GOOD = "good"
    EXCELLENT = "excellent"


def classify_credit_score(score: int) -> CreditBucket:
    if not (300 <= score <= 850):
        raise ValidationError("credit score must be between 300 and 850")
    if score < 580:
        return CreditBucket.POOR
    if score < 670:
        return CreditBucket.FAIR
    if score < 740:
        return CreditBucket.GOOD
    return CreditBucket.EXCELLENT
