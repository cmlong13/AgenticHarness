"""Fixture tests for calc.py."""

from calc import add, total_pages


def test_add():
    assert add(2, 3) == 5


def test_total_pages_non_exact_multiple_rounds_up():
    assert total_pages(7, 3) == 3
