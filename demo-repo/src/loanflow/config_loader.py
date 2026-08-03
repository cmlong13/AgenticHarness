"""Load and validate Thresholds from a JSON configuration file.

Read-only: this module never writes anything.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Union

from loanflow.config import Thresholds
from loanflow.errors import ConfigError
from loanflow.primitives import Percentage

_REQUIRED_FIELDS = {
    "dti_low_max", "dti_medium_max", "ltv_low_max", "ltv_medium_max",
    "max_dti_hard_fail", "max_ltv_hard_fail", "min_credit_score", "effective_date",
}


def load_thresholds(path: Union[str, Path]) -> Thresholds:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"threshold configuration file not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"threshold configuration file is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("threshold configuration file must contain a JSON object")

    missing = _REQUIRED_FIELDS - raw.keys()
    if missing:
        raise ConfigError(f"threshold configuration is missing field(s): {sorted(missing)}")

    try:
        return Thresholds(
            dti_low_max=Percentage.from_ratio(raw["dti_low_max"]),
            dti_medium_max=Percentage.from_ratio(raw["dti_medium_max"]),
            ltv_low_max=Percentage.from_ratio(raw["ltv_low_max"]),
            ltv_medium_max=Percentage.from_ratio(raw["ltv_medium_max"]),
            max_dti_hard_fail=Percentage.from_ratio(raw["max_dti_hard_fail"]),
            max_ltv_hard_fail=Percentage.from_ratio(raw["max_ltv_hard_fail"]),
            min_credit_score=int(raw["min_credit_score"]),
            effective_date=date.fromisoformat(raw["effective_date"]),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"threshold configuration has an invalid value: {exc}") from exc
