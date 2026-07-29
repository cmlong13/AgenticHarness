"""Fixture module for Quality Engineer infrastructure-flake retry verification.

Simulates a cold external connection that needs one warm-up attempt before it succeeds --
a real, reproducible transient failure driven by on-disk state, not by test logic.
"""

import os

_MARKER = os.path.join(os.path.dirname(__file__), ".connection_warm")


def is_connection_warm():
    return os.path.exists(_MARKER)


def attempt_connection():
    if not is_connection_warm():
        with open(_MARKER, "w", encoding="utf-8") as fh:
            fh.write("warm")
        raise ConnectionError("transient: connection refused, remote endpoint not yet warm (simulated cold start)")
    return "connected"
