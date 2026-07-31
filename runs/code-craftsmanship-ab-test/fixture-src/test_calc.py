"""Fixture tests for calc.py (code-craftsmanship A/B evaluation)."""

from calc import add


def test_add():
    assert add(2, 3) == 5
