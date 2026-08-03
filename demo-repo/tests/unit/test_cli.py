from __future__ import annotations

import json

from loanflow.cli import main

_VALID_APPLICATION = {
    "application_id": "LN-0001",
    "applicant": {"applicant_id": "AP-000123", "full_name": "Jordan Rivera", "years_employed": 5},
    "collateral": {"description": "single-family home", "appraised_value": 300000},
    "income_sources": [{"label": "salary", "monthly_amount": 10000}],
    "debt_obligations": [{"label": "auto_loan", "monthly_payment": 500}],
    "requested_amount": 150000,
    "credit_score": 780,
}


def test_decide_command_prints_explanation_and_returns_zero(tmp_path, capsys) -> None:
    application_file = tmp_path / "application.json"
    application_file.write_text(json.dumps(_VALID_APPLICATION), encoding="utf-8")

    exit_code = main(["decide", str(application_file)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "approved" in captured.out.lower()


def test_decide_command_with_missing_file_returns_one(tmp_path, capsys) -> None:
    exit_code = main(["decide", str(tmp_path / "does-not-exist.json")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error" in captured.err.lower()


def test_decide_command_with_malformed_json_returns_one(tmp_path, capsys) -> None:
    application_file = tmp_path / "application.json"
    application_file.write_text("{not valid json", encoding="utf-8")
    exit_code = main(["decide", str(application_file)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error" in captured.err.lower()


def test_decide_command_with_custom_config(tmp_path, capsys) -> None:
    application_file = tmp_path / "application.json"
    application_file.write_text(json.dumps(_VALID_APPLICATION), encoding="utf-8")
    config_file = tmp_path / "thresholds.json"
    config_file.write_text(json.dumps({
        "dti_low_max": 0.36, "dti_medium_max": 0.43, "ltv_low_max": 0.80, "ltv_medium_max": 0.95,
        "max_dti_hard_fail": 0.50, "max_ltv_hard_fail": 1.00, "min_credit_score": 580,
        "effective_date": "2026-01-01",
    }), encoding="utf-8")

    exit_code = main(["decide", str(application_file), "--config", str(config_file)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "approved" in captured.out.lower()
