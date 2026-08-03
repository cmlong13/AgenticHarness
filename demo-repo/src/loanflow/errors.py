"""Exception hierarchy for loanflow.

Educational demonstration code only -- not legally compliant, not
financially authoritative, and not intended to process real personal
information.
"""
from __future__ import annotations


class LoanflowError(Exception):
    """Base class for all loanflow errors."""


class ValidationError(LoanflowError):
    """Raised when input data fails a domain validation rule."""


class ConfigError(LoanflowError):
    """Raised when threshold/configuration data is missing or invalid."""


class RepositoryError(LoanflowError):
    """Raised for application-repository storage failures (duplicate/missing key)."""


class TransitionError(LoanflowError):
    """Raised when a loan application status transition is not allowed."""
