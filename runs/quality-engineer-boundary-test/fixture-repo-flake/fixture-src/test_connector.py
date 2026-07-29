"""Fixture test for Quality Engineer infrastructure-flake retry verification."""

from connector import attempt_connection


def test_attempt_connection_succeeds_once_warm():
    assert attempt_connection() == "connected"
