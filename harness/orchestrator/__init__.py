"""Deterministic orchestration engine for the Discovery -> Research ->
Implementation -> Verification pipeline.

Everything in this package is pure, testable Python with no dependency on a
live Claude Code session. Real Agent/SendMessage/Skill invocation is kept
behind the adapter interfaces in adapters.py; only deterministic fakes back
those interfaces today (see tests/fakes/agent_adapter.py).
"""
