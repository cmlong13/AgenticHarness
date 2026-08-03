"""In-memory application storage.

Nothing here writes to disk -- this repository is deliberately memory-only
so that running the test suite never mutates the target repository tree.
"""
from __future__ import annotations

from typing import Dict, List

from loanflow.applications import LoanApplication
from loanflow.errors import RepositoryError


class ApplicationRepository:
    def __init__(self) -> None:
        self._applications: Dict[str, LoanApplication] = {}

    def add(self, application: LoanApplication) -> None:
        if application.application_id in self._applications:
            raise RepositoryError(
                f"application {application.application_id!r} already exists in the repository"
            )
        self._applications[application.application_id] = application

    def get(self, application_id: str) -> LoanApplication:
        try:
            return self._applications[application_id]
        except KeyError as exc:
            raise RepositoryError(f"application {application_id!r} was not found") from exc

    def all(self) -> List[LoanApplication]:
        return list(self._applications.values())

    def __len__(self) -> int:
        return len(self._applications)
