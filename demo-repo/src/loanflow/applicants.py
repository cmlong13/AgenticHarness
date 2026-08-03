"""Synthetic applicant and business identity records.

Educational demo only: identifiers here are synthetic reference numbers,
never real government-issued identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass

from loanflow.errors import ValidationError


@dataclass(frozen=True)
class Applicant:
    applicant_id: str
    full_name: str
    years_employed: int = 0

    def __post_init__(self) -> None:
        if not self.full_name.strip():
            raise ValidationError("Applicant.full_name must not be empty")
        if self.years_employed < 0:
            raise ValidationError("Applicant.years_employed must not be negative")


@dataclass(frozen=True)
class Business:
    business_id: str
    legal_name: str
    years_in_operation: int = 0

    def __post_init__(self) -> None:
        if not self.legal_name.strip():
            raise ValidationError("Business.legal_name must not be empty")
        if self.years_in_operation < 0:
            raise ValidationError("Business.years_in_operation must not be negative")
