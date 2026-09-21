"""Post-hoc health checks: decision-distribution skew and config staleness."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import List

from loanflow.config import Thresholds
from loanflow.decisions import Decision, DecisionOutcome
from loanflow.primitives import Clock

_MAX_DECLINE_SHARE = 0.90
_MAX_CONFIG_AGE_DAYS = 180
_MAX_BATCH_SIZE = 1000


@dataclass(frozen=True)
class HealthResult:
    check_name: str
    healthy: bool
    detail: str


def check_decision_distribution(decisions: List[Decision]) -> HealthResult:
    if not decisions:
        return HealthResult("decision_distribution", True, "no decisions to evaluate")
    decline_share = sum(1 for d in decisions if d.outcome is DecisionOutcome.DECLINE) / len(decisions)
    if decline_share > _MAX_DECLINE_SHARE:
        return HealthResult(
            "decision_distribution",
            False,
            f"{decline_share:.0%} of {len(decisions)} decisions were declines, above the "
            f"{_MAX_DECLINE_SHARE:.0%} sanity threshold",
        )
    return HealthResult("decision_distribution", True, f"{decline_share:.0%} decline share is within range")


def check_config_freshness(thresholds: Thresholds, clock: Clock) -> HealthResult:
    age = clock.today() - thresholds.effective_date
    if age > timedelta(days=_MAX_CONFIG_AGE_DAYS):
        return HealthResult(
            "config_freshness",
            False,
            f"threshold configuration is {age.days} days old, above the "
            f"{_MAX_CONFIG_AGE_DAYS}-day freshness limit",
        )
    return HealthResult("config_freshness", True, f"threshold configuration is {age.days} days old")


def check_batch_size(decisions: List[Decision]) -> HealthResult:
    batch_size = len(decisions)
    if batch_size > _MAX_BATCH_SIZE:
        return HealthResult(
            "batch_size",
            False,
            f"batch of {batch_size} decisions exceeds the {_MAX_BATCH_SIZE}-decision sanity "
            f"threshold (e.g. a data-pipeline duplication bug)",
        )
    return HealthResult(
        "batch_size", True, f"batch of {batch_size} decisions is within the {_MAX_BATCH_SIZE}-decision sanity threshold"
    )


@dataclass(frozen=True)
class HealthReport:
    results: tuple

    @property
    def healthy(self) -> bool:
        return all(r.healthy for r in self.results)

    def summary(self) -> str:
        lines = [f"Overall: {'HEALTHY' if self.healthy else 'UNHEALTHY'}"]
        for result in self.results:
            marker = "OK" if result.healthy else "FAIL"
            lines.append(f"  [{marker}] {result.check_name}: {result.detail}")
        return "\n".join(lines)


def build_health_report(decisions: List[Decision], thresholds: Thresholds, clock: Clock) -> HealthReport:
    return HealthReport((
        check_decision_distribution(decisions),
        check_config_freshness(thresholds, clock),
        check_batch_size(decisions),
    ))
