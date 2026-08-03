from __future__ import annotations

import pytest

from loanflow.applicant_validation import validate_reference_id
from loanflow.errors import ValidationError


def test_valid_reference_id_is_returned_unchanged() -> None:
    assert validate_reference_id("AP-000123") == "AP-000123"


@pytest.mark.parametrize("bad_id", ["AP000123", "ap-000123", "AP-12", ""])
def test_invalid_reference_id_formats_are_rejected(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        validate_reference_id(bad_id)
