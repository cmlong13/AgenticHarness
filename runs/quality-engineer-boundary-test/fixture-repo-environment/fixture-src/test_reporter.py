"""Fixture test for Quality Engineer environment-failure verification."""

from reporter import report_value


def test_report_value_is_one():
    assert report_value() == 1
