"""Checkpoint persistence and resume-precondition evaluation.

Owns the one canonical checkpoint per run, `runs/<run_id>/checkpoint.json`, used by both
harness/orchestrator/core.py's deterministic, fake-adapter-tested `run()`/`resume()` (for
automated tests) and harness/orchestrator/live_cli.py's `write_checkpoint`/
`evaluate_resume` operations (for the real live `/work` skill). Per PROJECT_SPEC.md's
Option C, this module is the Python side of checkpointing: it never reasons about what
to do next, only whether a checkpoint is well-formed, current, and safe to build on.

Deliberately never persists an agent handle. A handle is only ever valid inside the live
Claude Code process that created it; carrying one across a checkpoint would invite
exactly the fabricated-continuation failure mode ASSIGNMENT.md's cardinal rule forbids.
A fresh process resuming an incomplete phase always dispatches a brand-new agent
instance, never a "resumed" old handle -- see core.py's resume().
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from harness.evidence import (
    PHASE_ORDER,
    load_json,
    validate_against_schema,
    validate_checkpoint_semantics,
    validate_findings_semantics,
    validate_implementation_report_semantics,
    validate_scope_semantics,
    validate_verification_report_semantics,
)

from . import evidence_io, paths

_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "harness" / "schemas"
CHECKPOINT_FILENAME = "checkpoint.json"

# Maps a non-discovery checkpoint phase to the schema basename used to revalidate its
# canonical artifact. "discovery" is handled separately in _revalidate_artifact below
# (its schema basename is "scope", and its semantic validator takes no extra context).
_ARTIFACT_SCHEMA_NAME = {
    "discovery": "scope",
    "research": "findings",
    "implementation": "implementation-report",
    "verification": "verification-report",
}


class CheckpointError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


def checkpoint_path(run_directory: Path) -> Path:
    return run_directory / CHECKPOINT_FILENAME


def _load_schema() -> dict:
    return load_json(_SCHEMAS_DIR / "checkpoint.schema.json")


def _load_artifact_schema(name: str) -> dict:
    return load_json(_SCHEMAS_DIR / f"{name}.schema.json")


def _write(run_directory: Path, doc: dict) -> Path:
    """Schema- and semantically-validates `doc` and refuses to write anything that fails
    either check -- a checkpoint the writer itself would reject as malformed or
    inconsistent must never reach disk in the first place. Then atomically replaces
    runs/<run_id>/checkpoint.json (evidence_io.atomic_write: temp file + os.replace), so
    an interruption mid-write can never leave a partially written file that later reads
    as valid JSON. Retains a checkpoint_written policy event on success."""
    schema_errors = validate_against_schema(doc, _load_schema(), artifact="checkpoint")
    if schema_errors:
        raise CheckpointError(
            "refusing to write invalid checkpoint: " + "; ".join(str(e) for e in schema_errors), "invalid_checkpoint"
        )
    semantic_errors = validate_checkpoint_semantics(doc)
    if semantic_errors:
        raise CheckpointError(
            "refusing to write invalid checkpoint: " + "; ".join(str(e) for e in semantic_errors), "invalid_checkpoint"
        )

    path = checkpoint_path(run_directory)
    evidence_io.atomic_write(path, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    evidence_io.retain_policy_event(
        run_directory,
        "checkpoint_written",
        {"status": doc["status"], "completed_phases": doc["completed_phases"], "current_phase": doc["current_phase"]},
    )
    return path


def _remaining_after(completed_phases: list[str]) -> list[str]:
    return [phase for phase in PHASE_ORDER if phase not in completed_phases]


def record_phase_progress(
    run_directory: Path,
    *,
    task_id: str,
    run_id: str,
    target_repo_path: str,
    updated_at: str,
    completed_phases: list[str],
    artifact_refs: dict[str, str],
) -> Path:
    """Called after a phase's canonical artifact has been retained, schema-validated,
    semantically validated, and promoted, and the run is continuing -- status
    "in_progress". `artifact_refs` is phase-keyed ("discovery"/"research"/
    "implementation"/"verification"), distinct from run-summary.json's artifact-name
    keys ("scope"/"findings"/...); callers holding the latter must remap first."""
    remaining = _remaining_after(completed_phases)
    if not remaining:
        raise CheckpointError("record_phase_progress called with all phases already completed", "invalid_call")
    current_phase = remaining[0]
    next_phase = remaining[1] if len(remaining) > 1 else None
    doc = {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "target_repo_path": target_repo_path,
        "updated_at": updated_at,
        "completed_phases": list(completed_phases),
        "current_phase": current_phase,
        "next_phase": next_phase,
        "artifact_refs": dict(artifact_refs),
        "blockers": [],
        "next_resume_action": f"dispatch the {current_phase} phase",
        "status": "in_progress",
    }
    return _write(run_directory, doc)


def record_completion(
    run_directory: Path,
    *,
    task_id: str,
    run_id: str,
    target_repo_path: str,
    updated_at: str,
    artifact_refs: dict[str, str],
) -> Path:
    """Called once Verification passes and every phase is genuinely complete."""
    doc = {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "target_repo_path": target_repo_path,
        "updated_at": updated_at,
        "completed_phases": list(PHASE_ORDER),
        "current_phase": None,
        "next_phase": None,
        "artifact_refs": dict(artifact_refs),
        "blockers": [],
        "next_resume_action": None,
        "status": "complete",
    }
    return _write(run_directory, doc)


def record_interruption(
    run_directory: Path,
    *,
    task_id: str,
    run_id: str,
    target_repo_path: str,
    updated_at: str,
    completed_phases: list[str],
    artifact_refs: dict[str, str],
    note: str = "",
) -> Path:
    """Called for a deliberate, evidence-backed pause before an in-progress phase
    finishes -- status "interrupted", distinct from "in_progress" (a phase mid-flight
    that simply hasn't finished yet) and from "failed" (a genuine terminal error).
    `note`, if given, is retained as a blocker entry naming the current phase, purely as
    an honest record of why the run paused -- it is not itself a failure."""
    remaining = _remaining_after(completed_phases)
    if not remaining:
        raise CheckpointError("record_interruption called with all phases already completed", "invalid_call")
    current_phase = remaining[0]
    next_phase = remaining[1] if len(remaining) > 1 else None
    doc = {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "target_repo_path": target_repo_path,
        "updated_at": updated_at,
        "completed_phases": list(completed_phases),
        "current_phase": current_phase,
        "next_phase": next_phase,
        "artifact_refs": dict(artifact_refs),
        "blockers": [{"description": note, "phase": current_phase}] if note else [],
        "next_resume_action": f"resume and dispatch the {current_phase} phase",
        "status": "interrupted",
    }
    return _write(run_directory, doc)


def record_terminal_failure(
    run_directory: Path,
    *,
    task_id: str,
    run_id: str,
    target_repo_path: str,
    updated_at: str,
    completed_phases: list[str],
    artifact_refs: dict[str, str],
    reason: str,
) -> Path:
    """Called for any terminal outcome other than a genuine pass -- status "failed".
    Overwrites a previously written "in_progress"/"interrupted" checkpoint for this run
    so a later resume attempt sees the true, current terminal state rather than a stale
    "in progress" record that would otherwise look resumable."""
    remaining = _remaining_after(completed_phases)
    current_phase = remaining[0] if remaining else PHASE_ORDER[-1]
    next_phase = remaining[1] if len(remaining) > 1 else None
    doc = {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "target_repo_path": target_repo_path,
        "updated_at": updated_at,
        "completed_phases": list(completed_phases),
        "current_phase": current_phase,
        "next_phase": next_phase,
        "artifact_refs": dict(artifact_refs),
        "blockers": [{"description": reason, "phase": current_phase}],
        "next_resume_action": f"none: run terminated during {current_phase} ({reason}); not resumable",
        "status": "failed",
    }
    return _write(run_directory, doc)


@dataclass
class ResumeDecision:
    """The outcome of evaluate_resume(). `resumable=False` always carries a `code`
    (e.g. "no_checkpoint", "run_id_mismatch", "terminal_complete", "terminal_failed",
    "target_repo_path_invalid", "missing_artifact", "invalid_artifact",
    "artifact_identity_mismatch") and a human-readable `reason`. `resumable=True` carries
    everything a caller needs to continue the run without re-deriving anything:
    `completed_phases` (to reuse), `next_phase` (the one incomplete phase to restart),
    `artifact_refs` (phase-keyed paths), and `docs` (the already-loaded, already-
    revalidated canonical document for every completed phase, keyed the same way)."""

    resumable: bool
    code: str
    reason: str
    checkpoint: dict | None = None
    task_id: str | None = None
    target_repo_path: str | None = None
    completed_phases: list[str] = field(default_factory=list)
    next_phase: str | None = None
    artifact_refs: dict[str, str] = field(default_factory=dict)
    docs: dict[str, dict] = field(default_factory=dict)


def _revalidate_artifact(phase: str, doc: dict, docs_so_far: dict[str, dict]) -> list[str]:
    schema_name = _ARTIFACT_SCHEMA_NAME[phase]
    schema_errors = validate_against_schema(doc, _load_artifact_schema(schema_name), artifact=schema_name)
    if schema_errors:
        return [str(e) for e in schema_errors]

    if phase == "discovery":
        return [str(e) for e in validate_scope_semantics(doc)]
    if phase == "research":
        return [str(e) for e in validate_findings_semantics(doc)]
    if phase == "implementation":
        findings_doc = docs_so_far.get("research", {})
        return [str(e) for e in validate_implementation_report_semantics(doc, findings_doc)]
    if phase == "verification":
        scope_doc = docs_so_far.get("discovery", {})
        return [str(e) for e in validate_verification_report_semantics(doc, scope_doc)]
    return [f"unrecognized phase {phase!r}"]  # unreachable given PHASE_ORDER-bounded callers


def evaluate_resume(run_directory: Path, *, requested_run_id: str, repo_root: Path) -> ResumeDecision:
    """Side-effect-free (reads only): implements every ASSIGNMENT-milestone Part 3
    resume precondition in order --

    1. checkpoint.json exists
    2. it is valid JSON, schema-valid, and semantically valid
    3. its run_id matches the requested run_id
    4. it is not a terminal ("complete"/"failed") run
    5. its target_repo_path re-validates against the real filesystem (existence,
       containment, symlink-escape, Protected Path)
    6. every artifact_refs entry for every completed phase exists on disk
    7. every one of those artifacts is schema- and semantically revalidated (not trusted
       from the checkpoint's own claim), including the cross-artifact context each
       phase's semantic validator needs (findings for implementation, scope for
       verification) -- which also means predecessor relationships (research needs
       valid scope; implementation needs valid scope+findings; verification needs valid
       scope+findings+implementation-report) are enforced structurally, not just by the
       completed_phases-prefix rule validate_checkpoint_semantics already checks
    8. each artifact's own task_id/run_id must match the checkpoint's -- an artifact that
       claims a different run is evidence of a false completed-phase claim, not proof of
       one

    Callers (core.py's resume(), live_cli.py's evaluate_resume operation) are
    responsible for retaining resume_requested/checkpoint_validated/resume_refused
    policy events -- this function only decides, it never writes evidence itself.
    """
    # Independent of what checkpoint.json says: a run whose run-summary.json already
    # exists has already reached SOME terminal conclusion (pass, fail, blocked, or
    # inconclusive) via evidence_io.write_run_summary -- refuse resume outright rather
    # than trust checkpoint.json's status field alone. This also covers same-run
    # logic-repair-loop outcomes (ASSIGNMENT.md's same-run route-back), whose nested
    # revisits of Implementation/Verification checkpoint.json's simple linear phase
    # model does not attempt to represent (see core.py's _finalize).
    summary_path = run_directory / "run-summary.json"
    if summary_path.exists():
        return ResumeDecision(
            False, "run_already_terminal", f"run-summary.json already exists at {summary_path}; run already concluded"
        )

    path = checkpoint_path(run_directory)
    if not path.exists():
        return ResumeDecision(False, "no_checkpoint", f"no checkpoint found at {path}")

    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return ResumeDecision(False, "malformed_checkpoint", f"checkpoint.json is not valid JSON: {exc}")
    if not isinstance(doc, dict):
        return ResumeDecision(False, "malformed_checkpoint", "checkpoint.json does not contain a JSON object")

    schema_errors = validate_against_schema(doc, _load_schema(), artifact="checkpoint")
    if schema_errors:
        return ResumeDecision(False, "invalid_checkpoint", "; ".join(str(e) for e in schema_errors))
    semantic_errors = validate_checkpoint_semantics(doc)
    if semantic_errors:
        return ResumeDecision(False, "invalid_checkpoint", "; ".join(str(e) for e in semantic_errors))

    if doc.get("run_id") != requested_run_id:
        return ResumeDecision(
            False,
            "run_id_mismatch",
            f"checkpoint run_id {doc.get('run_id')!r} does not match requested run_id {requested_run_id!r}",
        )

    status = doc.get("status")
    if status in ("complete", "failed"):
        return ResumeDecision(
            False,
            f"terminal_{status}",
            f"run {requested_run_id!r} is terminal (status={status!r}); refusing to resume",
            checkpoint=doc,
            task_id=doc.get("task_id"),
            completed_phases=list(doc.get("completed_phases", [])),
        )

    target_repo_path = doc.get("target_repo_path")
    try:
        paths.validate_target_repo_path(target_repo_path, repo_root=repo_root)
    except paths.PathSafetyError as exc:
        return ResumeDecision(False, "target_repo_path_invalid", f"target_repo_path re-check failed: {exc.message}")

    task_id = doc.get("task_id")
    completed_phases = list(doc.get("completed_phases", []))
    artifact_refs = dict(doc.get("artifact_refs", {}))

    docs: dict[str, dict] = {}
    for phase in completed_phases:
        rel = artifact_refs.get(phase)
        if not rel:
            return ResumeDecision(False, "missing_artifact", f"completed phase {phase!r} has no artifact_refs entry")
        artifact_path = repo_root / rel
        if not artifact_path.exists():
            return ResumeDecision(False, "missing_artifact", f"{phase}: referenced artifact {rel!r} does not exist")
        try:
            artifact_doc = load_json(artifact_path)
        except (OSError, json.JSONDecodeError) as exc:
            return ResumeDecision(False, "invalid_artifact", f"{phase}: {rel!r} is not valid JSON: {exc}")
        if not isinstance(artifact_doc, dict):
            return ResumeDecision(False, "invalid_artifact", f"{phase}: {rel!r} does not contain a JSON object")

        if artifact_doc.get("task_id") != task_id or artifact_doc.get("run_id") != requested_run_id:
            return ResumeDecision(
                False,
                "artifact_identity_mismatch",
                f"{phase}: {rel!r} task_id/run_id does not match this checkpoint's identity",
            )

        errors = _revalidate_artifact(phase, artifact_doc, docs)
        if errors:
            return ResumeDecision(False, "invalid_artifact", f"{phase}: {rel!r} failed revalidation: " + "; ".join(errors))

        docs[phase] = artifact_doc

    # Deliberately re-derived from the revalidated completed_phases, not read from
    # doc["next_phase"] -- this function never trusts the checkpoint's own claims about
    # what to do next any more than it trusts its claims about what is already done. See
    # checkpoint.schema.json's $comment for why this also means next_phase is not part
    # of validate_checkpoint_semantics's independently-enforced rule set.
    next_phase = next((phase for phase in PHASE_ORDER if phase not in completed_phases), None)

    return ResumeDecision(
        True,
        "ok",
        "checkpoint is valid and resumable",
        checkpoint=doc,
        task_id=task_id,
        target_repo_path=target_repo_path,
        completed_phases=completed_phases,
        next_phase=next_phase,
        artifact_refs=artifact_refs,
        docs=docs,
    )
