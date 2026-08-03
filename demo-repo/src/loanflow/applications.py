"""Loan application aggregate and its status lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple

from loanflow.applicants import Applicant
from loanflow.collateral import Collateral
from loanflow.errors import TransitionError, ValidationError
from loanflow.income import DebtObligation, IncomeSource
from loanflow.primitives import Money


class ApplicationStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    DECISIONED = "decisioned"


_ALLOWED_TRANSITIONS: Dict[ApplicationStatus, Tuple[ApplicationStatus, ...]] = {
    ApplicationStatus.DRAFT: (ApplicationStatus.SUBMITTED,),
    ApplicationStatus.SUBMITTED: (ApplicationStatus.UNDER_REVIEW,),
    ApplicationStatus.UNDER_REVIEW: (ApplicationStatus.DECISIONED,),
    ApplicationStatus.DECISIONED: (),
}


@dataclass
class LoanApplication:
    application_id: str
    applicant: Applicant
    requested_amount: Money
    collateral: Collateral
    income_sources: List[IncomeSource]
    debt_obligations: List[DebtObligation]
    status: ApplicationStatus = ApplicationStatus.DRAFT

    def __post_init__(self) -> None:
        if not self.application_id.strip():
            raise ValidationError("LoanApplication.application_id must not be empty")
        if self.requested_amount.cents <= 0:
            raise ValidationError("LoanApplication.requested_amount must be positive")

    def advance_to(self, new_status: ApplicationStatus) -> None:
        allowed = _ALLOWED_TRANSITIONS[self.status]
        if new_status not in allowed:
            raise TransitionError(
                f"cannot transition application {self.application_id!r} from "
                f"{self.status.value!r} to {new_status.value!r}"
            )
        self.status = new_status
