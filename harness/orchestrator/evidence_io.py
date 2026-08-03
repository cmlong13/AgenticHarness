"""Deterministic run-directory evidence persistence.

Every artifact is retained as a raw candidate before validation, and only a
validated candidate is ever promoted to a canonical filename. Nothing here
overwrites a file that already exists -- a second write to an existing path
is always a hard error, mirroring the test-runner skill's own
"evidence_collision" policy for its command logs. This is a single-writer,
one-pass MVP: no implementation-history replacement behavior is implemented
yet (a routed-back second attempt at a canonical artifact is out of scope
for this slice).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

CANONICAL_FILENAMES = {
    "discovery": "scope.json",
    "research": "findings.json",
    "implementation": "implementation-report.json",
    "verification": "verification-report.json",
}


class EvidenceCollisionError(Exception):
    pass


def run_dir(repo_root: Path, run_id: str) -> Path:
    return repo_root / "runs" / run_id


def ensure_run_dirs(run_directory: Path) -> None:
    run_directory.mkdir(parents=True, exist_ok=True)
    (run_directory / "attempts").mkdir(exist_ok=True)
    (run_directory / "requests").mkdir(exist_ok=True)
    (run_directory / "logs").mkdir(exist_ok=True)


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise EvidenceCollisionError(f"refusing to overwrite existing evidence at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_bytes(data)
    try:
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def retain_raw_attempt(run_directory: Path, phase: str, attempt_n: int, raw_text: str) -> Path:
    path = run_directory / "attempts" / f"{phase}-{attempt_n}.raw.txt"
    _write_new(path, raw_text.encode("utf-8"))
    return path


def retain_request(run_directory: Path, request: dict) -> Path:
    command_id = request["command_id"]
    path = run_directory / "requests" / f"{command_id}.json"
    _write_new(path, (json.dumps(request, indent=2) + "\n").encode("utf-8"))
    return path


def retain_rejection(run_directory: Path, rejection: dict) -> Path:
    command_id = rejection.get("command_id") or "unknown"
    path = run_directory / "logs" / f"{command_id}.rejected.json"
    _write_new(path, (json.dumps(rejection, indent=2) + "\n").encode("utf-8"))
    return path


def retain_policy_event(run_directory: Path, kind: str, payload: dict) -> Path:
    """Append-only log, never collision-checked -- multiple policy events (e.g. repeated
    mutation-sentinel 125 results) are expected to accumulate across a run."""
    path = run_directory / "logs" / "policy-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"kind": kind, **payload}) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return path


def promote_canonical(run_directory: Path, filename: str, doc: dict) -> Path:
    path = run_directory / filename
    _write_new(path, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    return path


def write_run_summary(run_directory: Path, doc: dict) -> Path:
    return promote_canonical(run_directory, "run-summary.json", doc)
