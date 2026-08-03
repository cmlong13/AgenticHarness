"""Format validation for synthetic applicant/business reference identifiers.

Kept separate from applicants.py: identifier-format policy changes
independently of the applicant data shape itself, and this is where a
future format change would land without touching the dataclasses.
"""
from __future__ import annotations

import re

from loanflow.errors import ValidationError

_REFERENCE_ID_RE = re.compile(r"^[A-Z]{2}-\d{6}$")


def validate_reference_id(reference_id: str) -> str:
    """Validate a synthetic applicant/business reference ID, e.g. 'AP-000123'.

    This is a synthetic demo identifier format only -- never a real
    government-issued identifier.
    """
    if not _REFERENCE_ID_RE.match(reference_id):
        raise ValidationError(
            f"reference_id {reference_id!r} does not match the required format 'XX-NNNNNN'"
        )
    return reference_id
