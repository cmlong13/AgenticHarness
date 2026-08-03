"""Exercises the CLI against the real tests/fixtures/*.json payloads.

Read-only: the CLI only reads application/config files, so pointing it at
the retained fixtures never mutates them.
"""
from __future__ import annotations

from pathlib import Path

from loanflow.cli import main

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_cli_decides_the_basic_fixture_as_approve(capsys) -> None:
    exit_code = main(["decide", str(_FIXTURES / "applicant_basic.json")])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "approved" in captured.out.lower()


def test_cli_decides_the_high_debt_fixture_as_decline(capsys) -> None:
    exit_code = main(["decide", str(_FIXTURES / "applicant_high_debt.json")])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "declined" in captured.out.lower()


def test_cli_accepts_an_explicit_thresholds_config(capsys) -> None:
    exit_code = main([
        "decide", str(_FIXTURES / "applicant_basic.json"),
        "--config", str(_FIXTURES / "thresholds_test.json"),
    ])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "approved" in captured.out.lower()


def test_cli_decides_the_boundary_fixture_as_refer(capsys) -> None:
    exit_code = main(["decide", str(_FIXTURES / "applicant_boundary.json")])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "manual review" in captured.out.lower()
