"""Append-only audit event log."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Union


class EventType(str, Enum):
    APPLICATION_SUBMITTED = "application_submitted"
    DECISION_MADE = "decision_made"


@dataclass(frozen=True)
class AuditEvent:
    event_type: EventType
    application_id: str
    detail: str
    occurred_at: datetime


class AuditLog:
    def __init__(self) -> None:
        self._events: List[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def events(self) -> List[AuditEvent]:
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def export_json(self, path: Union[str, Path]) -> None:
        """Write every event as JSON to an explicit path.

        Callers (and every test that exercises this method) must pass a
        path outside the target repository -- e.g. pytest's tmp_path --
        never a path inside demo-repo/ itself. See demo-repo/README.md.
        """
        payload = [
            {
                "event_type": event.event_type.value,
                "application_id": event.application_id,
                "detail": event.detail,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in self._events
        ]
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
