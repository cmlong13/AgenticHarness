from __future__ import annotations

import pytest

from loanflow.applicants import Applicant, Business
from loanflow.errors import ValidationError


def test_valid_applicant_constructs() -> None:
    applicant = Applicant(applicant_id="AP-000123", full_name="Jordan Rivera", years_employed=5)
    assert applicant.full_name == "Jordan Rivera"


def test_applicant_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        Applicant(applicant_id="AP-000123", full_name="   ", years_employed=0)


def test_applicant_rejects_negative_years_employed() -> None:
    with pytest.raises(ValidationError):
        Applicant(applicant_id="AP-000123", full_name="Jordan Rivera", years_employed=-1)


def test_applicant_rejects_non_integer_and_bool_years_employed() -> None:
    with pytest.raises(ValidationError):
        Applicant(applicant_id="AP-1", full_name="A", years_employed=2.5)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Applicant(applicant_id="AP-1", full_name="A", years_employed=True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Applicant(applicant_id="AP-1", full_name="A", years_employed=False)  # type: ignore[arg-type]


def test_valid_business_constructs() -> None:
    business = Business(business_id="BZ-000456", legal_name="Rivera Bakery LLC", years_in_operation=3)
    assert business.legal_name == "Rivera Bakery LLC"


def test_business_rejects_empty_legal_name() -> None:
    with pytest.raises(ValidationError):
        Business(business_id="BZ-000456", legal_name="", years_in_operation=0)


def test_business_rejects_negative_years_in_operation() -> None:
    with pytest.raises(ValidationError):
        Business(business_id="BZ-000456", legal_name="Rivera Bakery LLC", years_in_operation=-2)
