"""Fixture module for Quality Engineer environment-failure verification.

Deliberately imports a dependency that is never installed in this environment, so the
resulting failure is a genuine missing-dependency (environment) problem, not application logic.
"""

import qe_fixture_nonexistent_external_dependency


def report_value():
    return qe_fixture_nonexistent_external_dependency.value
