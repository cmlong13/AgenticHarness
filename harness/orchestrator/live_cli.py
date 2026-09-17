#!/usr/bin/env python3
"""Thin deterministic live bridge for the main-session orchestrator (the `/work` skill).

This is NOT an orchestrator and contains no agent reasoning, no phase sequencing, and
no dispatch logic. Every operation below is a direct call into an already-existing,
already-tested function in harness.evidence or harness.orchestrator.{discovery,
evidence_io,paths,state} -- schema validation, semantic validation, path-containment
rules, Protected Path matching, atomic-write behavior, canonical collision protection,
and terminal-verdict mapping are all reused from there, never reimplemented here.

It exists only to give the main Claude Code session a single, quoting-safe invocation
shape (one JSON request file in, one JSON result on stdout) instead of repeated
multiline `python -c` snippets scattered through the /work skill's instructions --
the same reason .claude/skills/test-runner/scripts/run_command.py exists as a wrapper
around subprocess execution rather than an inline Bash pipeline.

Invocation:
    python -m harness.orchestrator.live_cli --request-file "<path to a JSON file>"

The request file is a JSON object with an "operation" field selecting one of the
handlers in OPERATIONS below, plus operation-specific fields (documented on each
handler). Exactly one JSON object is always printed to stdout, even on error.

Exit code convention (distinct from the test-runner skill's own sentinel-exit-code
convention, since this is a trusted internal tool invoked directly by the main
session, not a Skill-scoped wrapper protecting against a restricted caller):
    0 -- the operation ran and produced an affirmative result (valid / match / ok /
         retained / promoted / written).
    1 -- the operation ran and produced a well-formed, deterministic NEGATIVE result
         (invalid / blocked / mismatch) -- never a Python exception, always JSON.
    2 -- a CLI-usage or internal error (bad arguments, malformed request JSON, a
         missing required field, an unreadable referenced file, an unexpected
         exception). The JSON body's "status" is always "error" at this tier.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness.evidence import (
    load_json,
    validate_against_schema,
    validate_findings_semantics,
    validate_implementation_report_semantics,
    validate_verification_report_semantics,
)

from . import (
    checkpoint,
    connector_router,
    discovery,
    evidence_io,
    github,
    jira_connector,
    memory,
    obsidian,
    obsidian_reader,
    paths,
    usage,
)
from .state import FINAL_VERDICT_BY_STATE, State

REPO_ROOT = paths.REPO_ROOT
SCHEMA_DIR = REPO_ROOT / "harness" / "schemas"

# Maps a non-discovery canonical phase to the schema basename used to validate it.
# "discovery" is deliberately absent -- its validator is discovery.validate_scope_draft,
# which loads scope.schema.json itself; see op_validate_scope / op_promote_artifact.
PHASE_SCHEMA = {
    "research": "findings",
    "implementation": "implementation-report",
    "verification": "verification-report",
}

# Each semantic validator's extra-context requirement, keyed the same way as
# PHASE_SCHEMA. "research" (findings) needs no extra context.
SEMANTIC_VALIDATORS = {
    "research": lambda doc, ctx: validate_findings_semantics(doc),
    "implementation": lambda doc, ctx: validate_implementation_report_semantics(doc, ctx["findings_doc"]),
    "verification": lambda doc, ctx: validate_verification_report_semantics(doc, ctx["scope_doc"]),
}

# response["status"] values that represent an affirmative outcome (exit code 0).
# Every other status a handler can return ("invalid", "blocked", "mismatch", "refused")
# is a well-formed negative result (exit code 1), not a CLI failure. "verified" is
# op_verify_push's own affirmative classification (harness.orchestrator.github); its five
# sibling classifications ("mismatch", "remote_ref_missing", "command_failed",
# "invalid_output", "wrong_repository") are deliberately absent here -- each is a
# well-formed negative push-verification outcome the /work skill must treat exactly like
# any other blocked result, never as evidence of a verified push. "resolved" is
# op_resolve_jira_issue's own affirmative classification (harness.orchestrator.
# jira_connector); its six sibling classifications ("not_found", "unauthorized",
# "connector_unavailable", "identity_mismatch", "invalid_issue_key", "invalid_response")
# are deliberately absent here for the identical reason -- each is a well-formed negative
# Jira-resolution outcome, never evidence that a real ticket was resolved. "published"
# is op_publish_run_summary's own affirmative classification (harness.orchestrator.
# obsidian); its five sibling classifications ("connector_unavailable",
# "invalid_destination", "destination_unavailable", "collision", "write_failed") are
# deliberately absent here for the identical reason -- each is a well-formed negative
# Obsidian-publication outcome the /work skill must report as a delivery failure,
# entirely separately from the run's own pipeline verdict, never as a published note.
# "read" (op_read_obsidian_note) and "found" (op_search_obsidian) are the two
# affirmative Obsidian-READ classifications (harness.orchestrator.obsidian_reader);
# their non-affirmative siblings ("not_found", "no_matches", "connector_unavailable",
# "invalid_destination", "destination_unavailable", "invalid_note_path",
# "invalid_query", "read_failed", "search_failed") are deliberately absent -- each is
# a well-formed negative read outcome that must be retained honestly and treated as
# "no vault evidence retrieved," never as authority. `no_matches` in particular is a
# real, honest "nothing there," not a connector failure.
# op_resolve_jira_issue_routed (the ASSIGNMENT.md §2.4 dual-route Jira connector) has its
# own four affirmative classifications from harness.orchestrator.connector_router --
# "resolved_via_rest" / "resolved_via_mcp" / "rest_failed_mcp_resolved" /
# "mcp_failed_rest_resolved" (a route genuinely returned a matching, identity-checked
# issue). Its non-affirmative siblings ("connector_unavailable", "not_found",
# "unauthorized", "identity_mismatch", "invalid_issue_key", "both_routes_failed",
# "route_disagreement") are deliberately absent -- each is a well-formed negative routing
# outcome, never evidence a real ticket was resolved.
OK_STATUSES = {
    "valid", "match", "ok", "retained", "promoted", "written", "resumable", "appended", "captured", "verified",
    "resolved", "published", "read", "found",
    "resolved_via_rest", "resolved_via_mcp", "rest_failed_mcp_resolved", "mcp_failed_rest_resolved",
}

# op_write_checkpoint's `kind` field selects which checkpoint.py record_* builder runs.
_CHECKPOINT_KINDS = {"progress", "completion", "interruption", "terminal_failure"}

# op_append_memory's `kind` field selects fact-append vs. lesson-append behavior --
# mirrors op_write_checkpoint's own kind-dispatch convention above.
_MEMORY_KINDS = {"fact", "lesson"}

_MEMORY_APPLIED_FIELDS = (
    "entry_id", "entry_type", "source_run_id", "current_run_id", "phase", "decision", "evidence_path",
)

_IDENTITY_FIELDS = ("task_id", "run_id", "command_id", "command", "working_directory")


class LiveCliUsageError(Exception):
    """A CLI-usage or malformed-request problem -- always tier 2 (exit code 2)."""


# ---------------------------------------------------------------------------
# Request parsing helpers
# ---------------------------------------------------------------------------


def _load_schema(name: str) -> dict:
    return load_json(SCHEMA_DIR / f"{name}.schema.json")


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT)).replace("\\", "/")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_str(req: dict, field: str) -> str:
    value = req.get(field)
    if not isinstance(value, str) or not value:
        raise LiveCliUsageError(f"{field!r} must be a non-empty string")
    return value


def _get_doc(req: dict, field: str) -> dict:
    """Accepts either an inline `<field>` object or a `<field>_ref` path to a JSON
    file on disk (repo-root-relative) -- callers with a large payload already saved
    to disk (e.g. a raw agent response that was parsed and re-serialized) should
    prefer `_ref` rather than re-embedding it inside another JSON document."""
    if field in req:
        value = req[field]
        if not isinstance(value, dict):
            raise LiveCliUsageError(f"{field!r} must be a JSON object")
        return value
    ref_field = f"{field}_ref"
    if ref_field in req:
        ref = req[ref_field]
        if not isinstance(ref, str) or not ref:
            raise LiveCliUsageError(f"{ref_field!r} must be a non-empty string")
        try:
            return load_json(REPO_ROOT / ref)
        except (OSError, json.JSONDecodeError) as exc:
            raise LiveCliUsageError(f"{ref_field} does not resolve to valid JSON: {exc}") from exc
    raise LiveCliUsageError(f"request missing required field {field!r} or {ref_field!r}")


def _get_text(req: dict, field: str) -> str:
    """Same inline-or-ref convention as _get_doc, but for raw (non-JSON-object) text --
    e.g. an agent's raw response, which may not even be valid JSON yet."""
    if field in req:
        value = req[field]
        if not isinstance(value, str):
            raise LiveCliUsageError(f"{field!r} must be a string")
        return value
    ref_field = f"{field}_ref"
    if ref_field in req:
        ref = req[ref_field]
        if not isinstance(ref, str) or not ref:
            raise LiveCliUsageError(f"{ref_field!r} must be a non-empty string")
        try:
            return (REPO_ROOT / ref).read_text(encoding="utf-8")
        except OSError as exc:
            raise LiveCliUsageError(f"{ref_field} does not resolve: {exc}") from exc
    raise LiveCliUsageError(f"request missing required field {field!r} or {ref_field!r}")


def _validate_phase_doc(phase: str, schema_name: str, doc: dict, context_refs: dict) -> list[str]:
    schema = _load_schema(schema_name)
    schema_errors = validate_against_schema(doc, schema, artifact=schema_name)
    if schema_errors:
        return [str(e) for e in schema_errors]

    semantic_fn = SEMANTIC_VALIDATORS[phase]
    context: dict = {}
    if phase == "implementation":
        findings_ref = context_refs.get("findings")
        if not findings_ref:
            raise LiveCliUsageError("context_refs.findings is required to validate an implementation-report")
        context["findings_doc"] = load_json(REPO_ROOT / findings_ref)
    if phase == "verification":
        scope_ref = context_refs.get("scope")
        if not scope_ref:
            raise LiveCliUsageError("context_refs.scope is required to validate a verification-report")
        context["scope_doc"] = load_json(REPO_ROOT / scope_ref)

    semantic_errors = semantic_fn(doc, context)
    return [str(e) for e in semantic_errors]


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def op_validate_scope(req: dict) -> dict:
    """1. Validate a Discovery candidate -- delegates entirely to
    discovery.validate_scope_draft (schema + semantics + path safety), never
    reimplemented here. Read-only: never writes anything."""
    candidate = _get_doc(req, "candidate")
    errors = discovery.validate_scope_draft(
        candidate,
        expected_task_id=_require_str(req, "expected_task_id"),
        expected_run_id=_require_str(req, "expected_run_id"),
        target_repo_path=_require_str(req, "target_repo_path"),
        repo_root=REPO_ROOT,
    )
    if errors:
        return {"operation": "validate_scope", "status": "invalid", "errors": errors}
    return {"operation": "validate_scope", "status": "valid"}


def op_retain_attempt(req: dict) -> dict:
    """2. Retain a raw phase attempt -- delegates to evidence_io.retain_raw_attempt.
    Must be called before any parsing/validation is trusted, exactly as
    core.py's _run_agent_phase does for every agent turn."""
    run_id = _require_str(req, "run_id")
    phase = _require_str(req, "phase")
    attempt_n = req.get("attempt_n")
    if not isinstance(attempt_n, int) or isinstance(attempt_n, bool) or attempt_n < 1:
        raise LiveCliUsageError("'attempt_n' must be a positive integer")
    raw_text = _get_text(req, "raw_text")

    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_raw_attempt(run_directory, phase, attempt_n, raw_text)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "retain_attempt", "status": "blocked", "error": str(exc)}
    return {"operation": "retain_attempt", "status": "retained", "path": _rel(path)}


def op_validate_artifact(req: dict) -> dict:
    """3. Validate a phase artifact (findings / implementation-report /
    verification-report) against its schema and phase-specific semantic
    validator. Inspect-only -- never writes anything, so it is safe to call on
    a still-unparsed candidate before deciding whether to promote it."""
    phase = _require_str(req, "phase")
    schema_name = PHASE_SCHEMA.get(phase)
    if schema_name is None:
        raise LiveCliUsageError(
            f"phase {phase!r} is not valid for validate_artifact (expected one of {sorted(PHASE_SCHEMA)})"
        )
    doc = _get_doc(req, "doc")
    errors = _validate_phase_doc(phase, schema_name, doc, req.get("context_refs", {}))
    if errors:
        return {"operation": "validate_artifact", "status": "invalid", "errors": errors}
    return {"operation": "validate_artifact", "status": "valid"}


def op_promote_artifact(req: dict) -> dict:
    """4. Promote a valid canonical artifact -- re-validates (same functions as
    op 1 / op 3, never reimplemented) immediately before writing, exactly the
    way core.py's _validate_and_promote does, so this operation can never
    promote something invalid on its own -- callers are not trusted to have
    remembered to validate first. Covers all four canonical phases
    (discovery/research/implementation/verification); the canonical filename
    is looked up from evidence_io.CANONICAL_FILENAMES, never re-derived here."""
    run_id = _require_str(req, "run_id")
    phase = _require_str(req, "phase")
    doc = _get_doc(req, "doc")

    if phase == "discovery":
        errors = discovery.validate_scope_draft(
            doc,
            expected_task_id=_require_str(req, "expected_task_id"),
            expected_run_id=_require_str(req, "expected_run_id"),
            target_repo_path=_require_str(req, "target_repo_path"),
            repo_root=REPO_ROOT,
        )
    elif phase in PHASE_SCHEMA:
        errors = _validate_phase_doc(phase, PHASE_SCHEMA[phase], doc, req.get("context_refs", {}))
    else:
        raise LiveCliUsageError(f"phase {phase!r} is not a recognized canonical phase")

    if errors:
        return {"operation": "promote_artifact", "status": "invalid", "errors": errors}

    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    filename = evidence_io.CANONICAL_FILENAMES[phase]
    try:
        path = evidence_io.promote_canonical(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "promote_artifact", "status": "blocked", "error": str(exc)}
    return {"operation": "promote_artifact", "status": "promoted", "path": _rel(path)}


def op_build_path_attestation(req: dict) -> dict:
    """5. Build a caller-side path-validation attestation -- delegates entirely to
    paths.build_path_validation_attestation, which genuinely performs the real
    filesystem + symlink-escape check rather than asserting the claim."""
    target_repo_path = _require_str(req, "target_repo_path")
    try:
        attestation = paths.build_path_validation_attestation(target_repo_path, repo_root=REPO_ROOT)
    except paths.PathSafetyError as exc:
        return {"operation": "build_path_attestation", "status": "invalid", "error": exc.message, "code": exc.code}
    return {"operation": "build_path_attestation", "status": "ok", "attestation": attestation}


def op_check_command_identity(req: dict) -> dict:
    """6. Validate that a test-runner result matches its request's identity fields
    -- the same field set and comparison core.py's _run_agent_phase already
    applies before trusting a command_result, re-expressed as a standalone
    check the /work skill can call for its own staged exchanges."""
    request = req.get("request")
    result = req.get("result")
    if not isinstance(request, dict) or not isinstance(result, dict):
        raise LiveCliUsageError("'request' and 'result' must both be JSON objects")
    mismatched = [f for f in _IDENTITY_FIELDS if result.get(f) != request.get(f)]
    if mismatched:
        return {"operation": "check_command_identity", "status": "mismatch", "mismatched_fields": mismatched}
    return {"operation": "check_command_identity", "status": "match"}


def op_retain_rejection(req: dict) -> dict:
    """7. Retain a rejection -- delegates to evidence_io.retain_rejection."""
    run_id = _require_str(req, "run_id")
    rejection = _get_doc(req, "rejection")
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_rejection(run_directory, rejection)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "retain_rejection", "status": "blocked", "error": str(exc)}
    return {"operation": "retain_rejection", "status": "retained", "path": _rel(path)}


def op_retain_policy_event(req: dict) -> dict:
    """8. Retain a policy event -- delegates to evidence_io.retain_policy_event
    (append-only, never collision-guarded, matching multiple accumulating
    events such as repeated mutation sentinels or repeated agent dispatches)."""
    run_id = _require_str(req, "run_id")
    kind = _require_str(req, "kind")
    payload = req.get("payload", {})
    if not isinstance(payload, dict):
        raise LiveCliUsageError("'payload' must be a JSON object")
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    path = evidence_io.retain_policy_event(run_directory, kind, payload)
    return {"operation": "retain_policy_event", "status": "retained", "path": _rel(path)}


def op_write_run_summary(req: dict) -> dict:
    """9. Write a schema-valid run summary -- schema-validates (run-summary has no
    semantic validator in harness.evidence) then delegates to
    evidence_io.write_run_summary. A caller may supply `final_verdict` directly
    inside `summary`, or supply `state` (a harness.orchestrator.state.State
    value name) to have it derived from state.FINAL_VERDICT_BY_STATE -- the
    same table core.py itself uses -- instead of restating the mapping by hand."""
    run_id = _require_str(req, "run_id")
    summary = dict(_get_doc(req, "summary"))

    if "final_verdict" not in summary and "state" in req:
        state_name = _require_str(req, "state")
        try:
            state_value = State(state_name)
        except ValueError as exc:
            raise LiveCliUsageError(f"unrecognized state {state_name!r}") from exc
        if state_value not in FINAL_VERDICT_BY_STATE:
            raise LiveCliUsageError(f"state {state_name!r} has no terminal final_verdict mapping")
        summary["final_verdict"] = FINAL_VERDICT_BY_STATE[state_value]

    schema = _load_schema("run-summary")
    schema_errors = validate_against_schema(summary, schema, artifact="run-summary")
    if schema_errors:
        return {"operation": "write_run_summary", "status": "invalid", "errors": [str(e) for e in schema_errors]}

    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.write_run_summary(run_directory, summary)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "write_run_summary", "status": "blocked", "error": str(exc)}
    return {"operation": "write_run_summary", "status": "written", "path": _rel(path)}


def op_write_checkpoint(req: dict) -> dict:
    """10. Persist runs/<run_id>/checkpoint.json -- delegates entirely to
    harness.orchestrator.checkpoint's record_* builders (never hand-rolled). `kind`
    selects which one: "progress" after a safely completed, non-terminal phase
    (Discovery/Research/Implementation); "completion" after Verification passes;
    "interruption" for a deliberate, evidence-backed pause (Part 5's controlled live
    interruption); "terminal_failure" for any other terminal outcome. `completed_phases`
    and `artifact_refs` (phase-keyed: "discovery"/"research"/"implementation"/
    "verification") are required for every kind except "completion" (which always
    covers all four phases). Refuses (status "blocked", never a Python exception) if the
    resulting document would itself be schema/semantically invalid -- see checkpoint.py's
    own _write()."""
    kind = _require_str(req, "kind")
    if kind not in _CHECKPOINT_KINDS:
        raise LiveCliUsageError(f"'kind' must be one of {sorted(_CHECKPOINT_KINDS)}, got {kind!r}")
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    target_repo_path = _require_str(req, "target_repo_path")
    completed_phases = req.get("completed_phases", [])
    artifact_refs = req.get("artifact_refs", {})
    if not isinstance(completed_phases, list) or not isinstance(artifact_refs, dict):
        raise LiveCliUsageError("'completed_phases' must be a list and 'artifact_refs' must be an object")

    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    updated_at = req.get("updated_at") or _utc_now_iso()

    try:
        if kind == "progress":
            path = checkpoint.record_phase_progress(
                run_directory, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
                updated_at=updated_at, completed_phases=completed_phases, artifact_refs=artifact_refs,
            )
        elif kind == "completion":
            path = checkpoint.record_completion(
                run_directory, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
                updated_at=updated_at, artifact_refs=artifact_refs,
            )
        elif kind == "interruption":
            path = checkpoint.record_interruption(
                run_directory, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
                updated_at=updated_at, completed_phases=completed_phases, artifact_refs=artifact_refs,
                note=req.get("note", ""),
            )
        else:  # "terminal_failure"
            path = checkpoint.record_terminal_failure(
                run_directory, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
                updated_at=updated_at, completed_phases=completed_phases, artifact_refs=artifact_refs,
                reason=_require_str(req, "reason"),
            )
    except checkpoint.CheckpointError as exc:
        return {"operation": "write_checkpoint", "status": "blocked", "error": exc.message, "code": exc.code}
    return {"operation": "write_checkpoint", "status": "written", "path": _rel(path)}


def op_evaluate_resume(req: dict) -> dict:
    """11. Evaluate whether runs/<run_id>/checkpoint.json can be resumed -- delegates
    entirely to checkpoint.evaluate_resume (existence, schema/semantic validity, run_id
    match, terminal-status refusal, real target_repo_path re-check, per-artifact
    existence + full revalidation + identity cross-check, predecessor-order enforcement).
    Retains resume_requested before the check and checkpoint_validated/resume_refused
    after, so the policy-event trail exists regardless of outcome -- the /work skill
    must not additionally retain these itself. On a resumable outcome, returns
    `completed_phases` (reuse these, never redispatch their agent) and `next_phase` (the
    one incomplete phase to restart with a fresh Agent dispatch)."""
    run_id = _require_str(req, "run_id")
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    evidence_io.retain_policy_event(run_directory, "resume_requested", {"run_id": run_id})

    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=run_id, repo_root=REPO_ROOT)

    if not decision.resumable:
        evidence_io.retain_policy_event(
            run_directory, "resume_refused", {"run_id": run_id, "code": decision.code, "reason": decision.reason},
        )
        return {
            "operation": "evaluate_resume", "status": "refused", "code": decision.code, "reason": decision.reason,
            "task_id": decision.task_id, "completed_phases": decision.completed_phases,
        }

    evidence_io.retain_policy_event(
        run_directory, "checkpoint_validated",
        {"run_id": run_id, "completed_phases": decision.completed_phases, "next_phase": decision.next_phase},
    )
    return {
        "operation": "evaluate_resume", "status": "resumable", "task_id": decision.task_id,
        "target_repo_path": decision.target_repo_path, "completed_phases": decision.completed_phases,
        "next_phase": decision.next_phase, "artifact_refs": decision.artifact_refs,
    }


def op_load_memory(req: dict) -> dict:
    """12. The one canonical memory-load operation, used before Discovery -- delegates
    entirely to memory.load_memory (read facts.jsonl + lessons-learned.md, validate
    every entry, derive deterministic keywords from `raw_prompt`, select relevant
    entries by tag/keyword intersection). Read-only except for the memory_loaded policy
    event it retains, naming what was considered (valid_fact_count/valid_lesson_count/
    skipped_count) and what was selected (relevant_fact_ids/relevant_lesson_ids) -- this
    is the evidence trail Part 3 requires distinguishing loaded from used. Never touches
    scope.json or any other canonical artifact; an empty relevant set is a legitimate,
    honestly-reported outcome, not an error (always status "ok")."""
    run_id = _require_str(req, "run_id")
    raw_prompt = _require_str(req, "raw_prompt")
    memory_dir = REPO_ROOT / "memory"

    result = memory.load_memory(memory_dir, raw_prompt=raw_prompt, repo_root=REPO_ROOT)

    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    files_read = [_rel(p) for p in result.files_read]
    evidence_io.retain_policy_event(
        run_directory,
        "memory_loaded",
        {
            "files_read": files_read,
            "keywords": result.keywords,
            "valid_fact_count": result.valid_fact_count,
            "valid_lesson_count": result.valid_lesson_count,
            "skipped_count": len(result.skipped),
            "relevant_fact_ids": [f["id"] for f in result.relevant_facts],
            "relevant_lesson_ids": [lesson["id"] for lesson in result.relevant_lessons],
        },
    )

    return {
        "operation": "load_memory",
        "status": "ok",
        "files_read": files_read,
        "keywords": result.keywords,
        "valid_fact_count": result.valid_fact_count,
        "valid_lesson_count": result.valid_lesson_count,
        "skipped": result.skipped,
        "relevant_facts": result.relevant_facts,
        "relevant_lessons": result.relevant_lessons,
    }


def op_append_memory(req: dict) -> dict:
    """13. Append new, evidence-backed memory -- delegates entirely to
    memory.append_fact / memory.append_lessons (never reimplemented here). `kind`
    selects which: "fact" appends at most one fact from the `fact` field; "lesson"
    appends up to `max_new` (default memory.MAX_LESSONS_PER_RUN = 5) candidates from the
    `candidates` list, in order, skipping the rest with an explicit reason once the cap
    is reached. Every candidate is independently validated (provenance, real evidence,
    no secrets, no duplicates) regardless of what the caller believes about it. Retains
    a memory_fact_append / memory_lessons_append policy event either way, so a rejected
    or duplicate candidate is visible evidence, not a silent no-op."""
    kind = _require_str(req, "kind")
    if kind not in _MEMORY_KINDS:
        raise LiveCliUsageError(f"'kind' must be one of {sorted(_MEMORY_KINDS)}, got {kind!r}")
    run_id = _require_str(req, "run_id")
    memory_dir = REPO_ROOT / "memory"
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)

    if kind == "fact":
        candidate = _get_doc(req, "fact")
        result = memory.append_fact(memory_dir, candidate, repo_root=REPO_ROOT)
        evidence_io.retain_policy_event(
            run_directory,
            "memory_fact_append",
            {"candidate_id": candidate.get("id"), "result": result.status, "errors": result.errors},
        )
        if result.status != "appended":
            return {"operation": "append_memory", "kind": "fact", "status": result.status, "errors": result.errors}
        return {"operation": "append_memory", "kind": "fact", "status": "appended", "fact": result.appended}

    candidates = req.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LiveCliUsageError("'candidates' must be a non-empty list for kind='lesson'")
    max_new = req.get("max_new", memory.MAX_LESSONS_PER_RUN)
    if not isinstance(max_new, int) or isinstance(max_new, bool) or max_new < 0:
        raise LiveCliUsageError("'max_new', if given, must be a non-negative integer")

    result = memory.append_lessons(memory_dir, candidates, repo_root=REPO_ROOT, max_new=max_new)
    evidence_io.retain_policy_event(
        run_directory,
        "memory_lessons_append",
        {"appended_ids": [c["id"] for c in result.appended], "skipped": result.skipped},
    )
    return {
        "operation": "append_memory",
        "kind": "lesson",
        "status": "ok",
        "appended": result.appended,
        "skipped": result.skipped,
    }


def op_record_memory_applied(req: dict) -> dict:
    """14. Retain a memory_applied event -- delegates entirely to
    memory.validate_memory_applied, which refuses (status "blocked") unless: entry_id
    resolves to a currently-valid fact/lesson of the claimed type; entry_id was actually
    selected as relevant by one of *this run's own* retained memory_loaded events (never
    a globally-valid entry this run never loaded and selected); and evidence_path
    resolves to a real, existing, regular file under repo_root whose own content cites
    entry_id by exact id. This operation exists specifically so neither "the entry was
    included in a prompt" nor "the entry exists somewhere in memory" nor "a file exists
    at this path" can ever by itself become a retained memory_applied record -- only a
    caller that can point at this run's own genuine selection AND decision evidence that
    actually names the entry gets one."""
    run_id = _require_str(req, "run_id")
    memory_dir = REPO_ROOT / "memory"
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    payload = {field_name: req.get(field_name) for field_name in _MEMORY_APPLIED_FIELDS}

    errors = memory.validate_memory_applied(
        payload, memory_dir=memory_dir, repo_root=REPO_ROOT, run_directory=run_directory
    )
    evidence_io.ensure_run_dirs(run_directory)

    if errors:
        evidence_io.retain_policy_event(run_directory, "memory_applied_rejected", {**payload, "errors": errors})
        return {"operation": "record_memory_applied", "status": "blocked", "errors": errors}

    evidence_io.retain_policy_event(run_directory, "memory_applied", payload)
    return {"operation": "record_memory_applied", "status": "retained", "event": payload}


def op_summarize_memory(req: dict) -> dict:
    """15. Read-only: derives the run summary's memory_loaded/memory_influenced_run/
    memory_refs_used fields from this run's own retained policy-events.jsonl (delegates
    entirely to memory.summarize_memory_events) -- never from caller assertion. Callers
    invoke this immediately before write_run_summary and merge its three fields into the
    summary document, so "memory influenced the run" can only ever be true because a
    real memory_applied event was actually retained earlier in this same run."""
    run_id = _require_str(req, "run_id")
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    result = memory.summarize_memory_events(run_directory)
    return {"operation": "summarize_memory", "status": "ok", **result}


def op_build_usage_summary(req: dict) -> dict:
    """16. Read-only aggregation + write: builds runs/<run_id>/usage-summary.json from
    every per-agent usage record real SubagentStop hook captures already retained under
    runs/<run_id>/usage/*.json -- delegates entirely to usage.build_run_usage_summary /
    usage.write_run_usage_summary (never reimplemented here). Distinguishes a
    subagent_subtotal from the orchestrator's own (this-milestone-unmeasured) usage and
    from a full_pipeline_total, which is always null this milestone with an explicit
    reason -- see usage.py's own module docstring. Callers (the /work skill, at
    Reporting time) may fold this summary's path into run-summary.json's own optional
    usage_summary_ref field; this operation does not touch run-summary.json itself."""
    run_id = _require_str(req, "run_id")
    path = usage.write_run_usage_summary(run_id, repo_root=REPO_ROOT)
    return {"operation": "build_usage_summary", "status": "written", "path": _rel(path)}


def op_reconcile_quarantined_usage(req: dict) -> dict:
    """17. Called immediately after retain_policy_event(kind: "agent_dispatch") for a
    fresh (non-staged) dispatch -- re-attempts identity matching for a per-agent usage
    record that the real SubagentStop hook necessarily quarantined moments earlier,
    since it fires before this very turn resumes to retain that dispatch evidence (see
    usage.py's own docstring for the confirmed live timing). Delegates entirely to
    usage.reconcile_quarantined_usage -- never re-implemented here. A quarantine record
    that still does not match anything is left in place, unconsumed, and this returns
    its own honest status rather than fabricating a match."""
    agent_id = _require_str(req, "agent_id")
    result = usage.reconcile_quarantined_usage(agent_id, repo_root=REPO_ROOT)
    return {"operation": "reconcile_quarantined_usage", **result}


def op_git_repo_identity(req: dict) -> dict:
    """18. Read-only: repository identity (harness.orchestrator.github) -- real repo
    root, current branch, local HEAD SHA, and configured remote URL, resolved via real
    `git` subprocess calls, never asserted by a caller. Optionally rejects the wrong
    repository (`expected_repo`, an "owner/name" slug) before returning anything else.
    Never writes evidence -- callers needing a retained identity record should follow up
    with retain_commit_evidence/retain_push_attempt/verify_push below, which each
    independently re-derive what they need rather than trusting this call's output."""
    target_repo_path = req.get("target_repo_path", ".")
    remote = req.get("remote", "origin")
    expected_repo = req.get("expected_repo")
    try:
        repo_path = github.resolve_repo_path(target_repo_path, repo_root=REPO_ROOT)
    except paths.PathSafetyError as exc:
        return {"operation": "git_repo_identity", "status": "invalid", "error": exc.message, "code": exc.code}
    try:
        git_root = github.get_repo_root(repo_path)
        branch = github.get_current_branch(repo_path)
        head_sha = github.get_local_head_sha(repo_path)
        remote_url = github.get_remote_url(repo_path, remote)
    except github.GitError as exc:
        return {"operation": "git_repo_identity", "status": exc.code, "error": exc.message}
    if expected_repo is not None and not github.repository_identity_matches(remote_url, expected_repo):
        return {
            "operation": "git_repo_identity", "status": "wrong_repository",
            "error": f"remote {remote!r} ({remote_url}) does not resolve to expected repository {expected_repo!r}",
        }
    return {
        "operation": "git_repo_identity", "status": "ok", "repo_root": git_root, "current_branch": branch,
        "local_head_sha": head_sha, "remote": remote, "remote_url": remote_url,
    }


def op_retain_commit_evidence(req: dict) -> dict:
    """19. Retain evidence that a local commit genuinely exists -- independently
    re-derives the current branch and HEAD SHA via real `git` subprocess calls (never
    trusts a caller-supplied SHA claim), then writes runs/<run_id>/git/commit-evidence.json
    (evidence_io.retain_git_evidence, collision-guarded). If `expected_branch` is given
    and does not match the real current branch, refuses (status "blocked") and writes
    nothing -- proves only what Git itself can currently attest to, never what a
    subagent or the caller believes the state to be."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    target_repo_path = req.get("target_repo_path", ".")
    expected_branch = req.get("expected_branch")
    filename = req.get("filename", "commit-evidence.json")
    try:
        repo_path = github.resolve_repo_path(target_repo_path, repo_root=REPO_ROOT)
    except paths.PathSafetyError as exc:
        return {"operation": "retain_commit_evidence", "status": "invalid", "error": exc.message, "code": exc.code}
    try:
        branch = github.get_current_branch(repo_path)
        commit_sha = github.get_local_head_sha(repo_path)
    except github.GitError as exc:
        return {"operation": "retain_commit_evidence", "status": exc.code, "error": exc.message}
    if expected_branch is not None and branch != expected_branch:
        return {
            "operation": "retain_commit_evidence", "status": "blocked",
            "error": f"current branch {branch!r} does not match expected_branch {expected_branch!r}",
        }
    doc = {
        "task_id": task_id, "run_id": run_id, "target_repo_path": target_repo_path,
        "branch": branch, "commit_sha": commit_sha, "retained_at": _utc_now_iso(),
    }
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_git_evidence(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "retain_commit_evidence", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(run_directory, "git_commit_evidence", doc)
    return {"operation": "retain_commit_evidence", "status": "retained", "path": _rel(path), "commit_sha": commit_sha}


def op_retain_push_attempt(req: dict) -> dict:
    """20. Retain evidence that a push was *attempted* -- this proves nothing about
    whether the push reached the remote; only verify_push (below) can prove that. If
    `expected_sha` is supplied, independently confirms it still equals the real current
    HEAD SHA before retaining anything (refuses, status "blocked", if HEAD moved since
    the caller last checked -- e.g. the commit-evidence step); otherwise the current HEAD
    SHA is used directly. Writes runs/<run_id>/git/push-attempt.json
    (evidence_io.retain_git_evidence, collision-guarded)."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    branch = _require_str(req, "branch")
    target_repo_path = req.get("target_repo_path", ".")
    remote = req.get("remote", "origin")
    filename = req.get("filename", "push-attempt.json")
    try:
        repo_path = github.resolve_repo_path(target_repo_path, repo_root=REPO_ROOT)
        ref = github.derive_expected_ref(branch)
    except (paths.PathSafetyError, github.GitError) as exc:
        return {"operation": "retain_push_attempt", "status": "invalid", "error": exc.message}
    try:
        remote_url = github.get_remote_url(repo_path, remote)
        current_head_sha = github.get_local_head_sha(repo_path)
    except github.GitError as exc:
        return {"operation": "retain_push_attempt", "status": exc.code, "error": exc.message}
    claimed_sha = req.get("expected_sha", current_head_sha)
    if claimed_sha != current_head_sha:
        return {
            "operation": "retain_push_attempt", "status": "blocked",
            "error": f"expected_sha {claimed_sha!r} no longer matches current HEAD {current_head_sha!r}",
        }
    doc = {
        "task_id": task_id, "run_id": run_id, "target_repo_path": target_repo_path, "remote": remote,
        "remote_url": remote_url, "branch": branch, "ref": ref, "expected_local_sha": current_head_sha,
        "attempted_at": _utc_now_iso(),
    }
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_git_evidence(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "retain_push_attempt", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(run_directory, "git_push_attempt", doc)
    return {"operation": "retain_push_attempt", "status": "retained", "path": _rel(path), "expected_local_sha": current_head_sha}


def op_verify_push(req: dict) -> dict:
    """21. The one independent push-verification entry point (ASSIGNMENT.md's cardinal
    rule): runs a real `git ls-remote <remote> refs/heads/<branch>` (always the real
    subprocess -- harness.orchestrator.github's simulated command-result seam is
    deliberately never exposed here, see that module's docstring) and classifies the
    result against `expected_sha` -- one of `verified` / `mismatch` /
    `remote_ref_missing` / `command_failed` / `invalid_output` / `wrong_repository`,
    never a bare boolean. Always retains runs/<run_id>/git/push-verification.json
    (evidence_io.retain_git_evidence, collision-guarded) and a matching
    `push_verification` policy event, regardless of the classification -- a mismatch is
    retained exactly as faithfully as a verified match, never silently dropped."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    branch = _require_str(req, "branch")
    expected_sha = _require_str(req, "expected_sha")
    target_repo_path = req.get("target_repo_path", ".")
    remote = req.get("remote", "origin")
    expected_repo = req.get("expected_repo")
    filename = req.get("filename", "push-verification.json")
    try:
        repo_path = github.resolve_repo_path(target_repo_path, repo_root=REPO_ROOT)
    except paths.PathSafetyError as exc:
        return {"operation": "verify_push", "status": "invalid", "error": exc.message, "code": exc.code}

    result = github.verify_push(
        repo_path=repo_path, remote=remote, branch=branch, expected_sha=expected_sha, expected_repo=expected_repo,
    )
    doc = {"task_id": task_id, "run_id": run_id, "target_repo_path": target_repo_path, "verified_at": _utc_now_iso(),
           **result.as_dict()}
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_git_evidence(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "verify_push", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(
        run_directory, "push_verification",
        {"status": result.status, "remote": result.remote, "ref": result.ref,
         "expected_sha": result.expected_sha, "observed_sha": result.observed_sha},
    )
    return {"operation": "verify_push", "status": result.status, "path": _rel(path), **result.as_dict()}


def op_gh_repo_metadata(req: dict) -> dict:
    """22. Read-only GitHub-side metadata via `gh repo view` (ASSIGNMENT.md Part 9) --
    never a substitute for verify_push's git ls-remote check. Useful only for
    information git itself does not have (e.g. the server-recorded default branch)."""
    target_repo_path = req.get("target_repo_path", ".")
    try:
        repo_path = github.resolve_repo_path(target_repo_path, repo_root=REPO_ROOT)
    except paths.PathSafetyError as exc:
        return {"operation": "gh_repo_metadata", "status": "invalid", "error": exc.message, "code": exc.code}
    result = github.gh_repo_metadata(repo_path=repo_path)
    return {"operation": "gh_repo_metadata", **result}


def op_resolve_jira_issue(req: dict) -> dict:
    """23. The one Jira ticket-resolution entry point (harness.orchestrator.
    jira_connector) -- ticket-mode `/work`'s real connector boundary, always called
    before Discovery for a ticket-mode run. `issue_key` is normalized
    (jira_connector.normalize_issue_key) before resolution but never reformatted or
    guessed beyond that. Credentials are read fresh from the environment on every call
    (jira_connector.JiraCredentials.from_env) -- never cached, never accepted from the
    request -- so a missing JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN classifies honestly
    as `connector_unavailable` rather than raising. Every outcome (`resolved` /
    `not_found` / `unauthorized` / `connector_unavailable` / `identity_mismatch` /
    `invalid_issue_key` / `invalid_response`) is retained unconditionally to
    runs/<run_id>/jira/issue-resolution.json (evidence_io.retain_jira_evidence,
    collision-guarded) and a matching `jira_issue_resolution` policy event -- a failed
    lookup is never treated as an empty issue, and the retained `raw` field never
    contains the Authorization header (jira_connector.build_auth_header's header dict is
    never part of HttpResult/JiraResolution)."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    requested_issue_key_raw = _require_str(req, "issue_key")
    filename = req.get("filename", "issue-resolution.json")
    normalized_key = jira_connector.normalize_issue_key(requested_issue_key_raw)
    credentials = jira_connector.JiraCredentials.from_env()
    result = jira_connector.resolve_issue(normalized_key, credentials)
    doc = {
        "task_id": task_id, "run_id": run_id, "requested_issue_key_raw": requested_issue_key_raw,
        "resolved_at": _utc_now_iso(), **result.as_dict(),
    }
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_jira_evidence(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "resolve_jira_issue", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(
        run_directory, "jira_issue_resolution",
        {"status": result.status, "requested_issue_key": result.requested_issue_key, "reason": result.reason},
    )
    return {"operation": "resolve_jira_issue", "path": _rel(path), **result.as_dict()}


def op_resolve_jira_issue_routed(req: dict) -> dict:
    """27. Resolve a Jira issue through the ASSIGNMENT.md §2.4 dual-route connector
    (harness.orchestrator.connector_router) -- Route A (the MCP route, a real
    JSON-RPC stdio boundary to harness.mcp.jira_server) and Route B (the REST-skill
    fallback, jira_connector.resolve_issue). ``policy`` selects the deterministic
    route order: "rest_first" (default -- REST is the kept production route; MCP is a
    fallback/corroboration) or "mcp_first" (the parity/live-proof order). Both routes
    always run for real -- there is no simulated seam on this boundary. Every outcome
    (resolved_via_rest / resolved_via_mcp / rest_failed_mcp_resolved /
    mcp_failed_rest_resolved / connector_unavailable / not_found / unauthorized /
    identity_mismatch / invalid_issue_key / both_routes_failed / route_disagreement)
    is retained unconditionally to runs/<run_id>/jira/routing/<filename>
    (default route-1.json, evidence_io.retain_connector_routing_evidence,
    collision-guarded) with a matching ``connector_routing`` policy event. A fallback
    never masks an authoritative ``unauthorized`` / ``identity_mismatch``; no secret
    (auth header, raw HTTP body) is ever retained -- the ``raw`` echo is dropped by
    connector_router before this operation sees the result."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    requested_issue_key_raw = _require_str(req, "issue_key")
    policy_name = req.get("policy", connector_router.RoutesPolicy.REST_FIRST.value)
    filename = req.get("filename", "route-1.json")
    if "env" in req or "credentials" in req or "transport" in req:
        raise LiveCliUsageError("credentials/transport are read from the environment, never from the request")
    try:
        policy = connector_router.RoutesPolicy(policy_name)
    except ValueError:
        raise LiveCliUsageError(
            f"'policy' must be one of {[p.value for p in connector_router.RoutesPolicy]}, got {policy_name!r}"
        )

    normalized_key = jira_connector.normalize_issue_key(requested_issue_key_raw)
    outcome = connector_router.resolve_issue_routed(normalized_key, policy=policy)
    doc = {
        "task_id": task_id, "run_id": run_id, "requested_issue_key_raw": requested_issue_key_raw,
        "kept_route": connector_router.KEPT_ROUTE, "resolved_at": _utc_now_iso(), **outcome.as_dict(),
    }
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_connector_routing_evidence(run_directory, "jira", filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "resolve_jira_issue_routed", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(
        run_directory, "connector_routing",
        {"connector": "jira", "operation": "resolve_issue", "policy": outcome.policy,
         "primary_route": outcome.primary_route, "fallback_attempted": outcome.fallback_attempted,
         "final_route": outcome.final_route, "status": outcome.status, "reason": outcome.reason},
    )
    return {"operation": "resolve_jira_issue_routed", "status": outcome.status, "path": _rel(path), **outcome.as_dict()}


def _opt_json_under_run(run_directory: Path, ref: object) -> dict | None:
    """Best-effort load of an optional artifact a run-summary references. Returns None
    (never raises, never fabricates) for a missing/blank ref or an unreadable file --
    a missing optional artifact means an omitted note section, not a broken publish."""
    if not isinstance(ref, str) or not ref:
        return None
    candidate = Path(ref)
    path = candidate if candidate.is_absolute() else REPO_ROOT / candidate
    if not path.is_file():
        return None
    try:
        loaded = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def op_publish_run_summary(req: dict) -> dict:
    """24. Publish this run's concise run summary to the configured Obsidian vault
    (harness.orchestrator.obsidian) -- ASSIGNMENT.md §2.4/§4's "the orchestrator writes
    summaries back to [Obsidian]" / "Obsidian vault receives a run summary." Runs only
    after runs/<run_id>/run-summary.json is final (it is loaded here, never rebuilt).
    The note is rendered strictly from retained evidence (run-summary.json plus the
    scope/verification/usage/Jira/Git evidence it references, best-effort) -- no
    credentials, transcripts, or raw logs. The real filesystem VaultWriter
    (obsidian.DEFAULT_WRITER) is always used -- the simulated seam is never exposed
    here, so a live /work run cannot fabricate a publication. Every outcome
    (`published` / `connector_unavailable` / `invalid_destination` /
    `destination_unavailable` / `collision` / `write_failed`) is retained
    unconditionally to runs/<run_id>/obsidian/summary-publication.json
    (evidence_io.retain_obsidian_evidence, collision-guarded) with a matching
    `obsidian_publication` policy event. Only `published` is an affirmative result --
    an Obsidian delivery failure is never allowed to change the run's own pipeline
    verdict, and is reported as a wholly separate external-delivery claim."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    overwrite = bool(req.get("overwrite", False))
    filename = req.get("filename", "summary-publication.json")
    generated_at = req.get("generated_at") or _utc_now_iso()

    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    run_summary_path = run_directory / "run-summary.json"
    if not run_summary_path.is_file():
        raise LiveCliUsageError(
            f"runs/{run_id}/run-summary.json does not exist -- publish the Obsidian summary only after "
            "the terminal run-summary is finalized"
        )
    run_summary = load_json(run_summary_path)
    if not isinstance(run_summary, dict):
        raise LiveCliUsageError(f"runs/{run_id}/run-summary.json is not a JSON object")

    refs = run_summary.get("artifact_refs") or {}
    scope = _opt_json_under_run(run_directory, refs.get("scope"))
    verification_report = _opt_json_under_run(run_directory, refs.get("verification_report"))
    usage_summary = _opt_json_under_run(run_directory, run_summary.get("usage_summary_ref"))
    jira_resolution = _opt_json_under_run(run_directory, f"runs/{run_id}/jira/issue-resolution.json")
    push_verification = _opt_json_under_run(run_directory, f"runs/{run_id}/git/push-verification.json")

    note_name = obsidian.build_note_name(run_id)
    note_body = obsidian.render_run_summary_note(
        run_id=run_id, task_id=task_id, run_summary=run_summary, generated_at=generated_at,
        scope=scope, verification_report=verification_report, jira_resolution=jira_resolution,
        push_verification=push_verification, usage_summary=usage_summary,
    )

    destination = None
    try:
        destination = obsidian.resolve_destination()
        result = obsidian.publish(
            destination=destination, note_name=note_name, content=note_body, overwrite=overwrite,
        )
    except obsidian.ObsidianError as exc:
        result = obsidian.PublicationResult(
            status=exc.code, note_name=note_name,
            note_relpath=note_name if destination is None else destination.note_relpath(note_name),
            reason=exc.message,
        )

    identity = obsidian.describe_destination(note_name, destination=destination)
    doc = {
        "task_id": task_id, "run_id": run_id, "connector": "filesystem_vault",
        "attempted_at": generated_at, "note_name": note_name,
        "destination": identity.as_dict(), "content_bytes": len(note_body.encode("utf-8")),
        **result.as_dict(),
    }
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_obsidian_evidence(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "publish_run_summary", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(
        run_directory, "obsidian_publication",
        {"status": result.status, "note_name": note_name, "note_relpath": result.note_relpath,
         "reason": result.reason, "content_sha256": result.content_sha256},
    )
    return {"operation": "publish_run_summary", "status": result.status, "path": _rel(path), **result.as_dict()}


_OBSIDIAN_READ_PHASES = {"discovery", "research"}


def op_search_obsidian(req: dict) -> dict:
    """25. Search the configured Obsidian vault for prior design context during
    Discovery or Research (harness.orchestrator.obsidian_reader) -- ASSIGNMENT.md
    §2.4's "the architect reads [the vault]" (design notes, past decisions,
    calibration docs). Runs a deterministic, bounded text search across eligible
    Markdown notes only; `.obsidian/` and every other hidden/system path is skipped,
    the whole vault is never dumped, and at most obsidian_reader.MAX_SEARCH_RESULTS
    notes come back, each with a bounded snippet and a vault-relative path the
    Architect can cite. The real vault reader is always used -- there is no
    simulated seam on this boundary, and no request field can smuggle a vault path:
    OBSIDIAN_VAULT_PATH is read fresh from the environment inside the reader. Every
    outcome (`found` / `no_matches` / `connector_unavailable` /
    `invalid_destination` / `destination_unavailable` / `invalid_query` /
    `search_failed`) is retained unconditionally to
    runs/<run_id>/obsidian/read/<filename> (default `search-1.json`,
    evidence_io.retain_obsidian_read_evidence, collision-guarded) with a matching
    `obsidian_read` policy event. Only `found` is an affirmative retrieval; the
    retrieved text is historical/contextual evidence, never repository truth."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    query = _require_str(req, "query")
    phase = _require_str(req, "phase")
    if phase not in _OBSIDIAN_READ_PHASES:
        raise LiveCliUsageError(f"'phase' must be one of {sorted(_OBSIDIAN_READ_PHASES)}, got {phase!r}")
    if "vault_path" in req or "env" in req or "OBSIDIAN_VAULT_PATH" in req:
        raise LiveCliUsageError("the vault path is read from OBSIDIAN_VAULT_PATH in the environment, never from the request")
    filename = req.get("filename", "search-1.json")
    attempted_at = req.get("attempted_at") or _utc_now_iso()

    result = obsidian_reader.search(query=query)
    doc = {
        "task_id": task_id, "run_id": run_id, "operation": "search_obsidian", "phase": phase,
        "connector": "filesystem_vault", "attempted_at": attempted_at,
        "vault": obsidian_reader.describe_vault(), **result.as_dict(),
    }
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_obsidian_read_evidence(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "search_obsidian", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(
        run_directory, "obsidian_read",
        {"operation": "search_obsidian", "phase": phase, "query": result.query, "status": result.status,
         "returned_count": len(result.matches), "note_paths": [m.note_path for m in result.matches]},
    )
    return {"operation": "search_obsidian", "status": result.status, "path": _rel(path), **result.as_dict()}


def op_read_obsidian_note(req: dict) -> dict:
    """26. Read one vault-relative Markdown note during Discovery or Research
    (harness.orchestrator.obsidian_reader) -- the follow-up to `search_obsidian`
    when a specific prior-decision / calibration note is worth reading in full.
    Path safety is total: an absolute / drive-prefixed / `..`-bearing path, a path
    into a hidden/system directory (`.obsidian/`), a non-`.md` target, or a
    fully-resolved target that escapes the vault is `invalid_note_path`; a
    structurally safe path naming no file is `not_found` (never a silent read of a
    different file). The reader always uses the real vault
    (OBSIDIAN_VAULT_PATH from the environment); no request field can point it
    elsewhere. Retains note identity (path + full-file SHA-256 + byte count) even
    when the returned body is bounded. Every outcome (`read` / `not_found` /
    `connector_unavailable` / `invalid_destination` / `destination_unavailable` /
    `invalid_note_path` / `read_failed`) is retained unconditionally to
    runs/<run_id>/obsidian/read/<filename> (default `read-1.json`,
    collision-guarded) with a matching `obsidian_read` policy event. Only `read` is
    affirmative; the note body is historical/contextual evidence, never repository
    truth."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    note_path = _require_str(req, "note_path")
    phase = _require_str(req, "phase")
    if phase not in _OBSIDIAN_READ_PHASES:
        raise LiveCliUsageError(f"'phase' must be one of {sorted(_OBSIDIAN_READ_PHASES)}, got {phase!r}")
    if "vault_path" in req or "env" in req or "OBSIDIAN_VAULT_PATH" in req:
        raise LiveCliUsageError("the vault path is read from OBSIDIAN_VAULT_PATH in the environment, never from the request")
    filename = req.get("filename", "read-1.json")
    attempted_at = req.get("attempted_at") or _utc_now_iso()

    result = obsidian_reader.read_note(note_path=note_path)
    doc = {
        "task_id": task_id, "run_id": run_id, "operation": "read_obsidian_note", "phase": phase,
        "connector": "filesystem_vault", "attempted_at": attempted_at, "requested_note_path": note_path,
        "vault": obsidian_reader.describe_vault(), **result.as_dict(),
    }
    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    try:
        path = evidence_io.retain_obsidian_read_evidence(run_directory, filename, doc)
    except evidence_io.EvidenceCollisionError as exc:
        return {"operation": "read_obsidian_note", "status": "blocked", "error": str(exc)}
    evidence_io.retain_policy_event(
        run_directory, "obsidian_read",
        {"operation": "read_obsidian_note", "phase": phase, "requested_note_path": note_path,
         "status": result.status, "note_path": result.note_path, "content_sha256": result.content_sha256},
    )
    return {"operation": "read_obsidian_note", "status": result.status, "path": _rel(path), **result.as_dict()}


# ---------------------------------------------------------------------------
# Architect direct-MCP Obsidian evidence retention bridge
# ---------------------------------------------------------------------------
#
# architect.md's "Obsidian docs connector" section lets the Architect call its own
# two mcp__obsidian__* tools directly during Research and echo what it used as a
# validated top-level `obsidian_reads` array in findings.json (harness/schemas/
# findings.schema.json's $defs.obsidianReadEntry). Until this bridge, nothing
# downstream of promote_artifact(phase="research") ever consumed that array -- it
# validated and promoted cleanly but was never retained as run evidence or
# independently checked. This section closes exactly that gap, narrowly: it adds
# no new agent capability and does not loosen the schema.
#
# Trust boundary: obsidian_reads is agent-reported metadata, not automatically
# authoritative connector evidence. A `mcp__obsidian__read_note` entry reporting
# `status: "read"` is independently re-read here via the same obsidian_reader
# boundary the orchestrator-mediated search_obsidian/read_obsidian_note operations
# already use, and its SHA-256 is compared against the Architect's own reported
# content_sha256 -- "verified" only on an exact match, "hash_mismatch" otherwise, so
# a mismatch is retained honestly and is never treated as corroborated evidence. A
# `mcp__obsidian__search_notes` entry is never independently re-run (a vault-wide
# search is not repeated merely to manufacture retention evidence -- least
# privilege); it is retained as "architect_reported" metadata. A negative
# connector outcome the Architect itself observed (connector_unavailable,
# not_found, invalid_note_path, ...) is retained as an attempted-but-non-
# affirmative observation, never upgraded into a manufactured affirmative read.


def _verify_architect_obsidian_read(entry: dict) -> dict:
    """Independently classifies one obsidian_reads entry. Returns
    {"performed": bool, "result": str, "reason": str,
    "independent_content_sha256": str | None}. `result` is one of "verified",
    "hash_mismatch", "verification_unavailable", "not_found", "architect_reported",
    "not_applicable" -- a distinct classification namespace from
    obsidian_reader.READ_STATUSES/SEARCH_STATUSES, never conflated with it."""
    tool = entry.get("tool")
    status = entry.get("status")

    if tool == "mcp__obsidian__search_notes":
        return {
            "performed": False, "result": "architect_reported", "independent_content_sha256": None,
            "reason": "search observations are Architect-reported metadata only; a vault-wide search "
                      "is never automatically repeated for evidence retention (least privilege)",
        }

    if tool != "mcp__obsidian__read_note" or status != "read":
        return {
            "performed": False, "result": "not_applicable", "independent_content_sha256": None,
            "reason": f"Architect-reported status {status!r} for tool {tool!r} is not an affirmative "
                      "read; the connector attempt and its status are retained honestly with no "
                      "independent verification performed",
        }

    note_path = entry.get("note_path") or entry.get("query_or_note_path")
    reported_sha = entry.get("content_sha256")
    independent = obsidian_reader.read_note(note_path=note_path)

    if independent.status == "read":
        if reported_sha and independent.content_sha256 == reported_sha:
            return {
                "performed": True, "result": "verified",
                "independent_content_sha256": independent.content_sha256,
                "reason": "independent orchestrator-mediated read of the same vault-relative note "
                          "produced a matching SHA-256",
            }
        return {
            "performed": True, "result": "hash_mismatch",
            "independent_content_sha256": independent.content_sha256,
            "reason": f"independent SHA-256 {independent.content_sha256!r} does not match "
                      f"Architect-reported content_sha256 {reported_sha!r}",
        }
    if independent.status == "not_found":
        return {
            "performed": True, "result": "not_found", "independent_content_sha256": None,
            "reason": independent.reason,
        }
    # connector_unavailable / destination_unavailable / invalid_destination /
    # invalid_note_path / read_failed -- the vault could not be independently
    # re-consulted right now. Never a hash_mismatch -- there is nothing to compare.
    return {
        "performed": True, "result": "verification_unavailable", "independent_content_sha256": None,
        "reason": f"independent verification unavailable ({independent.status}): {independent.reason}",
    }


def op_retain_architect_obsidian_reads(req: dict) -> dict:
    """27. Consumes a promoted findings.json's optional `obsidian_reads` array (the
    Architect's own direct mcp__obsidian__* tool echo) and retains one normalized,
    independently-classified evidence record per entry under
    runs/<run_id>/obsidian/read/architect-mcp-<n>.json (deterministic 1-based
    index = array position; evidence_io.retain_obsidian_read_evidence,
    collision-guarded) plus a matching `architect_obsidian_read` policy event. No
    note body is ever retained -- obsidian_reads entries never carry one (the
    schema has no such field), and this bridge does not add one.

    Idempotent for checkpoint/resume: an index whose retained file already exists
    on disk is reported `already_retained` and is neither re-verified nor
    rewritten -- a resumed run never re-runs a personal-vault read, and never
    produces a conflicting duplicate, merely because the process restarted.

    Never fails Research: absent/empty `obsidian_reads` is a normal no-op
    (`status: "retained"`, empty `entries`), and every per-entry outcome (an
    affirmative verified read, a hash mismatch, an unavailable verification, a
    not-found note, an architect-reported search, or a negative connector
    attempt) is retained honestly, never raised as an error. run_id/task_id are
    always the orchestrator's own request fields, never read from an entry --
    obsidianReadEntry carries no identity fields for the schema to leak."""
    run_id = _require_str(req, "run_id")
    task_id = _require_str(req, "task_id")
    findings_doc = _get_doc(req, "findings")
    obsidian_reads = findings_doc.get("obsidian_reads") or []

    run_directory = evidence_io.run_dir(REPO_ROOT, run_id)
    evidence_io.ensure_run_dirs(run_directory)

    entries = []
    for index, entry in enumerate(obsidian_reads, start=1):
        filename = f"architect-mcp-{index}.json"
        target_path = run_directory / "obsidian" / "read" / filename
        if target_path.exists():
            entries.append(
                {"index": index, "filename": filename, "status": "already_retained", "path": _rel(target_path)}
            )
            continue

        verification = _verify_architect_obsidian_read(entry)
        doc = {
            "run_id": run_id, "task_id": task_id, "phase": "research",
            "source": "architect_direct_mcp",
            "tool": entry.get("tool"), "query_or_note_path": entry.get("query_or_note_path"),
            "status": entry.get("status"), "note_path": entry.get("note_path"),
            "content_sha256": entry.get("content_sha256"),
            "retained_at": _utc_now_iso(), "verification": verification,
        }
        try:
            path = evidence_io.retain_obsidian_read_evidence(run_directory, filename, doc)
        except evidence_io.EvidenceCollisionError:
            entries.append(
                {"index": index, "filename": filename, "status": "already_retained", "path": _rel(target_path)}
            )
            continue
        evidence_io.retain_policy_event(
            run_directory, "architect_obsidian_read",
            {"run_id": run_id, "task_id": task_id, "phase": "research", "tool": entry.get("tool"),
             "status": entry.get("status"), "verification_result": verification["result"],
             "retained_path": _rel(path)},
        )
        entries.append(
            {"index": index, "filename": filename, "status": "retained", "path": _rel(path),
             "verification_result": verification["result"]}
        )

    return {
        "operation": "retain_architect_obsidian_reads", "status": "retained", "run_id": run_id,
        "task_id": task_id, "count": len(obsidian_reads), "entries": entries,
    }


OPERATIONS = {
    "validate_scope": op_validate_scope,
    "retain_attempt": op_retain_attempt,
    "validate_artifact": op_validate_artifact,
    "promote_artifact": op_promote_artifact,
    "build_path_attestation": op_build_path_attestation,
    "check_command_identity": op_check_command_identity,
    "retain_rejection": op_retain_rejection,
    "retain_policy_event": op_retain_policy_event,
    "write_run_summary": op_write_run_summary,
    "write_checkpoint": op_write_checkpoint,
    "evaluate_resume": op_evaluate_resume,
    "load_memory": op_load_memory,
    "append_memory": op_append_memory,
    "record_memory_applied": op_record_memory_applied,
    "summarize_memory": op_summarize_memory,
    "build_usage_summary": op_build_usage_summary,
    "reconcile_quarantined_usage": op_reconcile_quarantined_usage,
    "git_repo_identity": op_git_repo_identity,
    "retain_commit_evidence": op_retain_commit_evidence,
    "retain_push_attempt": op_retain_push_attempt,
    "verify_push": op_verify_push,
    "gh_repo_metadata": op_gh_repo_metadata,
    "resolve_jira_issue": op_resolve_jira_issue,
    "resolve_jira_issue_routed": op_resolve_jira_issue_routed,
    "publish_run_summary": op_publish_run_summary,
    "search_obsidian": op_search_obsidian,
    "read_obsidian_note": op_read_obsidian_note,
    "retain_architect_obsidian_reads": op_retain_architect_obsidian_reads,
}


# ---------------------------------------------------------------------------
# CLI entry point -- never exits without printing exactly one JSON object.
# ---------------------------------------------------------------------------


def _parse_argv(argv: list[str]) -> str:
    request_file = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--request-file":
            if i + 1 >= len(argv):
                raise LiveCliUsageError("--request-file requires a value")
            if request_file is not None:
                raise LiveCliUsageError("--request-file was supplied more than once")
            request_file = argv[i + 1]
            i += 2
            continue
        raise LiveCliUsageError(f"unrecognized command-line argument: {token!r}")
    if not request_file:
        raise LiveCliUsageError("--request-file is required")
    return request_file


def _load_request(request_file_arg: str) -> dict:
    path = Path(request_file_arg)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveCliUsageError(f"request file does not resolve: {exc}") from exc
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiveCliUsageError(f"request file is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise LiveCliUsageError("request file JSON value is not an object")
    return body


def main(argv: list[str]) -> int:
    operation = None
    try:
        request_file = _parse_argv(argv)
        req = _load_request(request_file)
        operation = req.get("operation")
        handler = OPERATIONS.get(operation)
        if handler is None:
            raise LiveCliUsageError(f"unrecognized operation {operation!r} (expected one of {sorted(OPERATIONS)})")
        response = handler(req)
    except LiveCliUsageError as exc:
        print(json.dumps({"operation": operation, "status": "error", "error": str(exc)}))
        return 2
    except Exception as exc:  # never exit without a parseable JSON result
        print(json.dumps({"operation": operation, "status": "error", "error": f"internal error: {exc}"}))
        return 2

    print(json.dumps(response, indent=2))
    return 0 if response.get("status") in OK_STATUSES else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
