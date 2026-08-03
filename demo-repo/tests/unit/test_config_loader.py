from __future__ import annotations

import json

import pytest

from loanflow.config_loader import load_thresholds
from loanflow.errors import ConfigError

_VALID_PAYLOAD = {
    "dti_low_max": 0.36,
    "dti_medium_max": 0.43,
    "ltv_low_max": 0.80,
    "ltv_medium_max": 0.95,
    "max_dti_hard_fail": 0.50,
    "max_ltv_hard_fail": 1.00,
    "min_credit_score": 580,
    "effective_date": "2026-01-01",
}


def test_load_valid_thresholds_file(tmp_path) -> None:
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps(_VALID_PAYLOAD), encoding="utf-8")
    thresholds = load_thresholds(config_file)
    assert thresholds.min_credit_score == 580


def test_load_missing_file_raises_config_error(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_thresholds(tmp_path / "does-not-exist.json")


def test_load_malformed_json_raises_config_error(tmp_path) -> None:
    config_file = tmp_path / "thresholds.json"
    config_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_thresholds(config_file)


def test_load_file_missing_required_field_raises_config_error(tmp_path) -> None:
    payload = dict(_VALID_PAYLOAD)
    del payload["min_credit_score"]
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_thresholds(config_file)
