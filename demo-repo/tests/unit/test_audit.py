from __future__ import annotations

import json

from loanflow.audit import AuditEvent, AuditLog, EventType


def test_append_then_events_returns_events_in_order(frozen_clock) -> None:
    log = AuditLog()
    log.append(AuditEvent(EventType.APPLICATION_SUBMITTED, "LN-0001", "submitted", frozen_clock.now()))
    log.append(AuditEvent(EventType.DECISION_MADE, "LN-0001", "decision approve", frozen_clock.now()))
    events = log.events()
    assert len(log) == 2
    assert [e.event_type for e in events] == [EventType.APPLICATION_SUBMITTED, EventType.DECISION_MADE]


def test_events_returns_a_copy_not_the_internal_list(frozen_clock) -> None:
    log = AuditLog()
    log.append(AuditEvent(EventType.APPLICATION_SUBMITTED, "LN-0001", "submitted", frozen_clock.now()))
    events = log.events()
    events.clear()
    assert len(log) == 1


def test_export_json_writes_every_event_to_the_given_path(frozen_clock, tmp_path) -> None:
    log = AuditLog()
    log.append(AuditEvent(EventType.APPLICATION_SUBMITTED, "LN-0001", "submitted", frozen_clock.now()))
    log.append(AuditEvent(EventType.DECISION_MADE, "LN-0001", "decision approve", frozen_clock.now()))

    export_path = tmp_path / "audit-export.json"
    log.export_json(export_path)

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert len(payload) == 2
    assert payload[0]["event_type"] == "application_submitted"
    assert payload[1]["application_id"] == "LN-0001"
