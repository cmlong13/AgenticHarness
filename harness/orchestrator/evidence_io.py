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


def atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` via a temp file + os.replace, allowing overwrite of an
    already-existing file -- unlike _write_new's collision guard below, this is for
    evidence that is legitimately rewritten across a run's lifetime (checkpoint.json is
    the one user of this today). os.replace is atomic on both POSIX and Windows, so an
    interruption between the temp-file write and the replace can never leave a partially
    written checkpoint appearing valid at the canonical path -- the canonical path either
    still holds the previous, fully-written version or the new, fully-written version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_bytes(data)
    try:
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise EvidenceCollisionError(f"refusing to overwrite existing evidence at {path}")
    atomic_write(path, data)


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


def retain_git_evidence(run_directory: Path, filename: str, doc: dict) -> Path:
    """Collision-guarded write for Git/GitHub evidence documents (commit-evidence.json,
    push-attempt.json, push-verification.json -- see harness/orchestrator/github.py),
    retained under runs/<run_id>/git/ rather than the run root since these are not one of
    the four canonical phase artifacts CANONICAL_FILENAMES enumerates. Shares
    promote_canonical's collision guard: never silently overwrites already-retained Git
    evidence -- a caller needing a second, independent record (e.g. a repair-round retry)
    must pass a distinctly named filename, mirroring the implementation-report.repair-1.json
    convention core.py's route-back path already establishes."""
    path = run_directory / "git" / filename
    _write_new(path, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    return path


def retain_obsidian_evidence(run_directory: Path, filename: str, doc: dict) -> Path:
    """Collision-guarded write for Obsidian publication evidence
    (summary-publication.json -- see harness/orchestrator/obsidian.py), retained under
    runs/<run_id>/obsidian/ for the same reason retain_git_evidence uses
    runs/<run_id>/git/ and retain_jira_evidence uses runs/<run_id>/jira/: an Obsidian
    run-summary publication is an external-delivery claim, kept structurally distinct
    from the four canonical phase artifacts CANONICAL_FILENAMES enumerates and from
    pipeline/Git/Jira status. Shares promote_canonical's collision guard -- never
    silently overwrites an already-retained publication record; a legitimate re-publish
    passes a distinctly named filename (e.g. summary-publication.retry-1.json), mirroring
    the implementation-report.repair-1.json convention."""
    path = run_directory / "obsidian" / filename
    _write_new(path, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    return path


def retain_obsidian_read_evidence(run_directory: Path, filename: str, doc: dict) -> Path:
    """Collision-guarded write for Obsidian vault READ evidence (search-<n>.json,
    read-<n>.json -- see harness/orchestrator/obsidian_reader.py), retained under
    runs/<run_id>/obsidian/read/ -- a subdirectory deliberately distinct from
    runs/<run_id>/obsidian/summary-publication.json (the WRITE-BACK / publication
    evidence retain_obsidian_evidence produces). A read consultation during
    Discovery/Research and a run-summary publication are two different claims about
    the same connector and must never share an evidence file. Shares
    promote_canonical's collision guard -- never silently overwrites an
    already-retained read record; a caller needing a second record passes a
    distinctly numbered filename (search-2.json, read-2.json, ...)."""
    path = run_directory / "obsidian" / "read" / filename
    _write_new(path, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    return path


def retain_connector_routing_evidence(run_directory: Path, connector: str, filename: str, doc: dict) -> Path:
    """Collision-guarded write for dual-route connector-selection evidence (see
    harness/orchestrator/connector_router.py -- the ASSIGNMENT.md §2.4 "implement both
    the MCP route and a REST-skill fallback" requirement). Retained under
    runs/<run_id>/<connector>/routing/ -- deliberately a subdirectory of the connector's
    own evidence area (runs/<run_id>/jira/) and structurally distinct from that
    connector's ordinary result evidence (jira/issue-resolution.json): a routing record
    answers "which transport was tried, which succeeded, and why," not "what did the
    connector say about this object." Shares promote_canonical's collision guard -- a
    caller needing a second record passes a distinctly named filename (route-2.json)."""
    path = run_directory / connector / "routing" / filename
    _write_new(path, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    return path


def retain_jira_evidence(run_directory: Path, filename: str, doc: dict) -> Path:
    """Collision-guarded write for Jira evidence documents (issue-resolution.json -- see
    harness/orchestrator/jira_connector.py), retained under runs/<run_id>/jira/ for the
    same reason retain_git_evidence uses runs/<run_id>/git/: this is not one of the four
    canonical phase artifacts CANONICAL_FILENAMES enumerates, and ticket-mode resolution
    happens before Discovery even runs, so no run-phase directory is a natural home for
    it. Shares promote_canonical's collision guard -- never silently overwrites an
    already-retained Jira resolution."""
    path = run_directory / "jira" / filename
    _write_new(path, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    return path
