"""Fixture module for Quality Engineer boundary verification."""


def clamp(value, lo, hi):
    return max(lo, value)
