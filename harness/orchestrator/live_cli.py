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
from pathlib import Path

from harness.evidence import (
    load_json,
    validate_against_schema,
    validate_findings_semantics,
    validate_implementation_report_semantics,
    validate_verification_report_semantics,
)

from . import discovery, evidence_io, paths
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
# Every other status a handler can return ("invalid", "blocked", "mismatch") is a
# well-formed negative result (exit code 1), not a CLI failure.
OK_STATUSES = {"valid", "match", "ok", "retained", "promoted", "written"}

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
