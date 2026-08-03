"""Runs the registered underwriting rules against a risk profile."""
from __future__ import annotations

from typing import Callable, List, Tuple

from loanflow.config import Thresholds
from loanflow.risk_profile import RiskProfile
from loanflow.underwriting_rules import UNDERWRITING_RULES, RuleResult


class UnderwritingEngine:
    def __init__(
        self, rules: Tuple[Callable[[RiskProfile, Thresholds], RuleResult], ...] = UNDERWRITING_RULES
    ) -> None:
        self._rules = rules

    def evaluate(self, profile: RiskProfile, thresholds: Thresholds) -> List[RuleResult]:
        return [rule(profile, thresholds) for rule in self._rules]
