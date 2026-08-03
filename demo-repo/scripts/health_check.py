#!/usr/bin/env python3
"""Manual convenience script: run the health checks against a small batch
of sample decisions and the configured thresholds, and print a report.

Not part of the automated test suite -- run manually from the repo root:
    .venv/Scripts/python.exe demo-repo/scripts/health_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loanflow.config import DEFAULT_THRESHOLDS  # noqa: E402
from loanflow.config_loader import load_thresholds  # noqa: E402
from loanflow.decisions import Decision, DecisionOutcome  # noqa: E402
from loanflow.health import build_health_report  # noqa: E402
from loanflow.primitives import SystemClock  # noqa: E402


def main() -> int:
    config_path = Path(__file__).resolve().parents[1] / "config" / "thresholds.json"
    thresholds = load_thresholds(config_path) if config_path.exists() else DEFAULT_THRESHOLDS

    sample_decisions = [
        Decision(DecisionOutcome.APPROVE, ()),
        Decision(DecisionOutcome.APPROVE, ()),
        Decision(DecisionOutcome.REFER, ()),
        Decision(DecisionOutcome.DECLINE, ()),
    ]
    report = build_health_report(sample_decisions, thresholds, SystemClock())
    print(report.summary())
    return 0 if report.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
