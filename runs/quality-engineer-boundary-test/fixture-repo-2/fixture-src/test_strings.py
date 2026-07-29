"""Fixture tests for strings.py -- clamp() boundary coverage."""

from strings import clamp


def test_clamp_within_range():
    assert clamp(5, 0, 10) == 5


def test_clamp_upper_bound():
    assert clamp(15, 0, 10) == 10
