"""Turns underwriting rule results into a final approve/refer/decline decision."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple

from loanflow.underwriting_rules import RuleOutcome, RuleResult


class DecisionOutcome(str, Enum):
    APPROVE = "approve"
    REFER = "refer"
    DECLINE = "decline"


@dataclass(frozen=True)
class Decision:
    outcome: DecisionOutcome
    rule_results: Tuple[RuleResult, ...]


def recommend(rule_results: List[RuleResult]) -> Decision:
    if any(r.outcome is RuleOutcome.HARD_FAIL for r in rule_results):
        return Decision(DecisionOutcome.DECLINE, tuple(rule_results))
    if any(r.outcome is RuleOutcome.BORDERLINE for r in rule_results):
        return Decision(DecisionOutcome.REFER, tuple(rule_results))
    return Decision(DecisionOutcome.APPROVE, tuple(rule_results))
