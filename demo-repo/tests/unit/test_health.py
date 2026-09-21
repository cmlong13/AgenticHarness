from __future__ import annotations

from datetime import datetime, timezone

from loanflow.decisions import Decision, DecisionOutcome
from loanflow.health import (
    build_health_report,
    check_batch_size,
    check_config_freshness,
    check_decision_distribution,
)
from loanflow.primitives import FixedClock


def _decision(outcome: DecisionOutcome) -> Decision:
    return Decision(outcome, ())


def test_balanced_decision_distribution_is_healthy() -> None:
    decisions = [_decision(DecisionOutcome.APPROVE), _decision(DecisionOutcome.DECLINE),
                 _decision(DecisionOutcome.REFER), _decision(DecisionOutcome.APPROVE)]
    result = check_decision_distribution(decisions)
    assert result.healthy


def test_all_declines_is_unhealthy() -> None:
    decisions = [_decision(DecisionOutcome.DECLINE) for _ in range(10)]
    result = check_decision_distribution(decisions)
    assert not result.healthy
    assert "decline" in result.detail.lower()


def test_empty_decision_batch_is_healthy() -> None:
    result = check_decision_distribution([])
    assert result.healthy


def test_oversized_decision_batch_is_unhealthy() -> None:
    decisions = [_decision(DecisionOutcome.APPROVE) for _ in range(1001)]
    result = check_batch_size(decisions)
    assert not result.healthy
    assert "1001" in result.detail


def test_normal_decision_batch_size_is_healthy() -> None:
    decisions = [_decision(DecisionOutcome.APPROVE) for _ in range(4)]
    result = check_batch_size(decisions)
    assert result.healthy


def test_fresh_configuration_is_healthy(default_thresholds) -> None:
    clock = FixedClock(datetime(2026, 2, 1, tzinfo=timezone.utc))
    result = check_config_freshness(default_thresholds, clock)
    assert result.healthy


def test_stale_configuration_is_unhealthy(default_thresholds) -> None:
    clock = FixedClock(datetime(2027, 6, 1, tzinfo=timezone.utc))
    result = check_config_freshness(default_thresholds, clock)
    assert not result.healthy
    assert "days old" in result.detail


def test_build_health_report_combines_all_checks(default_thresholds) -> None:
    clock = FixedClock(datetime(2026, 2, 1, tzinfo=timezone.utc))
    report = build_health_report([_decision(DecisionOutcome.APPROVE)], default_thresholds, clock)
    assert len(report.results) == 3
    assert report.healthy
    assert "HEALTHY" in report.summary()
