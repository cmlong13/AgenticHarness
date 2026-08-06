"""One-pass MVP orchestration engine, plus a bounded same-run logic-failure
route-back loop.

Wires discovery.py, evidence_io.py, paths.py, and harness/evidence.py's
schema/semantic validators together with the AgentAdapter/TestRunnerAdapter/
DiscoveryAdapter interfaces from adapters.py to drive exactly one
straight-through attempt through Discovery -> Research -> Implementation ->
Verification.

A schema-valid "fail" verdict is not automatically terminal: if -- and only
if -- classify_verification_outcome() finds a genuine logic_bug (never an
infrastructure_flake, an environment failure, or malformed/insufficient
evidence), the run resumes the *exact same* Engineer and Quality Engineer
handles from this run's original Implementation/Verification dispatch for
one bounded repair cycle (MAX_LOGIC_REPAIR_ATTEMPTS) before ending the run.
No replacement agent is ever dispatched and presented as a continuation.

The orchestrator never performs the Architect's research, the Engineer's
implementation, or the Quality Engineer's judgment itself -- it dispatches,
validates, mediates staged commands, and records evidence.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from harness.evidence import (
    PHASE_ORDER,
    load_json,
    validate_against_schema,
    validate_findings_semantics,
    validate_implementation_report_semantics,
    validate_verification_report_semantics,
)

from . import checkpoint, discovery, evidence_io, paths
from .adapters import AgentAdapter, DiscoveryAdapter, TestRunnerAdapter
from .state import FINAL_VERDICT_BY_STATE, PHASE_BY_STATE, State

# checkpoint.json's artifact_refs is phase-keyed ("discovery"/"research"/...); a RunResult's
# artifact_refs is artifact-name-keyed ("scope"/"findings"/...), matching run-summary.json.
# The two conventions predate each other's existence and are kept distinct on purpose --
# run-summary.json's keys name the artifact, checkpoint.json's keys name the phase that
# produced it -- so every checkpoint write remaps through this table rather than guessing.
_PHASE_TO_SUMMARY_KEY = {
    "discovery": "scope",
    "research": "findings",
    "implementation": "implementation_report",
    "verification": "verification_report",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _checkpoint_artifact_refs(phases_completed: list[str], artifact_refs: dict) -> dict:
    return {
        phase: artifact_refs[key]
        for phase, key in _PHASE_TO_SUMMARY_KEY.items()
        if phase in phases_completed and key in artifact_refs
    }


def _checkpoint_progress(run_directory: Path, task_id: str, run_id: str, target_repo_path: str, phases_completed: list, artifact_refs: dict) -> None:
    """Called after a phase's canonical artifact is retained, validated, and promoted,
    and the run is continuing -- never called after Verification, whose outcome is
    always terminal and is instead handled by _finalize below."""
    checkpoint.record_phase_progress(
        run_directory,
        task_id=task_id,
        run_id=run_id,
        target_repo_path=target_repo_path,
        updated_at=_now_iso(),
        completed_phases=list(phases_completed),
        artifact_refs=_checkpoint_artifact_refs(phases_completed, artifact_refs),
    )

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "harness" / "schemas"

# response_type envelopes that carry a `requested_command` and are mediated through
# TestRunnerAdapter.invoke() -- pre_test_requested/post_test_requested (Engineer) and
# attempt_requested (Quality Engineer) are all structurally interchangeable for this purpose.
COMMAND_ENVELOPE_TYPES = {"pre_test_requested", "post_test_requested", "attempt_requested"}
FINALIZATION_ENVELOPE_TYPE = "finalization_evidence_requested"

MAX_STAGED_TURNS = 20

# Exactly one same-run logic-repair cycle is permitted for this milestone's
# demonstration, per the assignment's "prefer one logic-repair cycle" guidance. Bumping
# this requires a written contract reason, not a casual increase.
MAX_LOGIC_REPAIR_ATTEMPTS = 1


class OrchestrationError(Exception):
    """Raised only for genuinely unexpected internal failures -- caught by run() and
    reported as State.FAILED, never used for an ordinary agent/validation block."""


@dataclass
class PhaseResult:
    blocked: bool
    doc: dict | None = None
    canonical_path: Path | None = None
    reason: str = ""
    handle: str | None = None


@dataclass
class RunResult:
    task_id: str
    run_id: str
    state: State
    run_dir: Path
    artifact_refs: dict = field(default_factory=dict)
    phases_completed: list = field(default_factory=list)
    final_verdict: str = "blocked"
    reason: str = ""
    run_summary_path: str = ""


def _load_schema(name: str) -> dict:
    return load_json(SCHEMA_DIR / f"{name}.schema.json")


def _rel(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _parse_raw(raw: str) -> tuple[dict | None, str]:
    """Strict JSON parsing only -- no fence-stripping, no prose extraction. Returns
    (parsed_dict_or_None, error_message). Tolerating a fenced/prose-wrapped reply would
    normalize a contract violation every agent's own instructions explicitly forbid."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON output: {exc}"
    if not isinstance(parsed, dict):
        return None, "output is not a JSON object"
    return parsed, ""


def _validate_and_promote(
    msg: dict,
    run_directory: Path,
    phase: str,
    schema_name: str,
    semantic_validate_fn: Callable[[dict], list] | None,
    task_id: str,
    run_id: str,
    *,
    filename: str | None = None,
) -> PhaseResult:
    if msg.get("task_id") != task_id or msg.get("run_id") != run_id:
        return PhaseResult(blocked=True, reason="final artifact task_id/run_id mismatch")

    schema = _load_schema(schema_name)
    schema_errors = validate_against_schema(msg, schema, artifact=schema_name)
    if schema_errors:
        return PhaseResult(
            blocked=True, reason="schema validation failed: " + "; ".join(str(e) for e in schema_errors)
        )

    if semantic_validate_fn is not None:
        semantic_errors = semantic_validate_fn(msg)
        if semantic_errors:
            return PhaseResult(
                blocked=True, reason="semantic validation failed: " + "; ".join(str(e) for e in semantic_errors)
            )

    # `filename` lets a same-run repair round promote a *new* canonical artifact
    # (e.g. "implementation-report.repair-1.json") without colliding with -- or
    # silently overwriting -- the original round's canonical file, which stays
    # retained as pre-repair evidence. Ordinary (non-repair) calls omit it and get the
    # fixed, phase-derived name exactly as before.
    resolved_filename = filename or evidence_io.CANONICAL_FILENAMES[phase]
    try:
        canonical_path = evidence_io.promote_canonical(run_directory, resolved_filename, msg)
    except evidence_io.EvidenceCollisionError as exc:
        return PhaseResult(blocked=True, reason=str(exc))

    return PhaseResult(blocked=False, doc=msg, canonical_path=canonical_path)


def _build_canonical_request(
    task_id: str, run_id: str, requested_command: dict, target_repo_path: str, purpose: str
) -> dict:
    return {
        "task_id": task_id,
        "run_id": run_id,
        "command_id": requested_command["id"],
        "command": requested_command["command"],
        "working_directory": requested_command["working_directory"],
        "target_repo_path": target_repo_path,
        "purpose": purpose,
    }


def _command_result_message(task_id: str, run_id: str, result: dict) -> dict:
    message = {
        "message_type": "command_result",
        "task_id": task_id,
        "run_id": run_id,
        "command_id": result["command_id"],
        "command": result["command"],
        "working_directory": result["working_directory"],
        "exit_code": result["exit_code"],
    }
    if "output_ref" in result:
        message["output_ref"] = result["output_ref"]
    else:
        message["output_summary"] = result.get("output_summary")
    return message


def _drive_staged_protocol(
    *,
    agent_adapter: AgentAdapter,
    test_runner_adapter: TestRunnerAdapter,
    handle: str,
    raw: str,
    run_directory: Path,
    phase: str,
    schema_name: str,
    semantic_validate_fn: Callable[[dict], list] | None,
    task_id: str,
    run_id: str,
    target_repo_path: str | None,
    attempt_phase_label: str | None = None,
    canonical_filename: str | None = None,
) -> PhaseResult:
    """Drives an already-started or already-resumed agent handle through the rest of its
    protocol: a single-shot agent (Architect) returns a final artifact immediately (no
    response_type key); a staged agent (Engineer, Quality Engineer) exchanges
    `response_type` envelopes -- command-request envelopes mediated via
    TestRunnerAdapter.invoke(), finalization_evidence_requested via
    TestRunnerAdapter.diff_stats() -- until it too emits a final artifact. `handle` is
    reused for every resume() call; a resume() failure blocks the phase immediately
    rather than calling start() again.

    `attempt_phase_label` (defaults to `phase`) and `canonical_filename` (defaults to the
    phase's fixed name) let a same-run logic-repair round retain its raw turns and promote
    its final artifact under distinct, collision-free names -- e.g.
    "implementation-repair-1" / "implementation-report.repair-1.json" -- without touching
    the original round's retained evidence. `phase` itself is always the real schema
    phase ("implementation"/"verification"), since that is what selects the schema and
    semantic validator; only the *filenames* differ for a repair round."""
    attempt_phase_label = attempt_phase_label or phase

    for offset in range(MAX_STAGED_TURNS):
        attempt_n = offset + 1
        try:
            evidence_io.retain_raw_attempt(run_directory, attempt_phase_label, attempt_n, raw)
        except evidence_io.EvidenceCollisionError as exc:
            return PhaseResult(blocked=True, reason=str(exc), handle=handle)

        msg, parse_error = _parse_raw(raw)
        if msg is None:
            return PhaseResult(blocked=True, reason=parse_error, handle=handle)

        response_type = msg.get("response_type")
        if response_type is None:
            # Final artifact -- the only message in the whole workflow that is not a
            # protocol envelope, per every agent's own contract.
            result = _validate_and_promote(
                msg, run_directory, phase, schema_name, semantic_validate_fn, task_id, run_id,
                filename=canonical_filename,
            )
            result.handle = handle
            return result

        if msg.get("task_id") != task_id or msg.get("run_id") != run_id:
            return PhaseResult(blocked=True, reason="protocol envelope task_id/run_id mismatch", handle=handle)

        if response_type == FINALIZATION_ENVELOPE_TYPE:
            changed_files_known = msg.get("changed_files_known", [])
            try:
                stats = test_runner_adapter.diff_stats(changed_files_known)
            except Exception as exc:
                return PhaseResult(blocked=True, reason=f"unable to compute finalization evidence: {exc}", handle=handle)
            message = {
                "message_type": "finalization_evidence",
                "task_id": task_id,
                "run_id": run_id,
                "changed_files": stats,
            }
            try:
                raw = agent_adapter.resume(handle, message)
            except Exception as exc:
                return PhaseResult(blocked=True, reason=f"lost continuity resuming agent: {exc}", handle=handle)
            continue

        if response_type not in COMMAND_ENVELOPE_TYPES:
            return PhaseResult(
                blocked=True, reason=f"unrecognized protocol envelope response_type {response_type!r}", handle=handle
            )

        requested_command = msg.get("requested_command")
        if not requested_command:
            return PhaseResult(blocked=True, reason=f"{response_type} envelope missing requested_command", handle=handle)

        request = _build_canonical_request(
            task_id, run_id, requested_command, target_repo_path or "", purpose=f"{phase} {response_type}"
        )
        try:
            evidence_io.retain_request(run_directory, request)
        except evidence_io.EvidenceCollisionError as exc:
            return PhaseResult(blocked=True, reason=str(exc), handle=handle)

        try:
            result = test_runner_adapter.invoke(request)
        except Exception as exc:
            return PhaseResult(blocked=True, reason=f"test-runner invocation failed: {exc}", handle=handle)

        if result.get("message_type") == "command_rejected":
            evidence_io.retain_rejection(run_directory, result)
            return PhaseResult(blocked=True, reason=f"command rejected: {result.get('rejection_reason')}", handle=handle)

        for check_field in ("task_id", "run_id", "command_id", "command", "working_directory"):
            if result.get(check_field) != request.get(check_field):
                return PhaseResult(
                    blocked=True,
                    reason=f"command_result field {check_field!r} does not match the request that was sent",
                    handle=handle,
                )

        if result.get("exit_code") == 125:
            evidence_io.retain_policy_event(run_directory, "mutation_detected", result)

        message = _command_result_message(task_id, run_id, result)
        try:
            raw = agent_adapter.resume(handle, message)
        except Exception as exc:
            return PhaseResult(blocked=True, reason=f"lost continuity resuming agent: {exc}", handle=handle)

    return PhaseResult(blocked=True, reason="staged protocol exceeded the maximum number of turns", handle=handle)


def _run_agent_phase(
    *,
    agent_adapter: AgentAdapter,
    test_runner_adapter: TestRunnerAdapter,
    agent_type: str,
    payload: dict,
    run_directory: Path,
    phase: str,
    schema_name: str,
    semantic_validate_fn: Callable[[dict], list] | None,
    task_id: str,
    run_id: str,
    target_repo_path: str | None,
) -> PhaseResult:
    """Dispatches a fresh agent instance (agent_adapter.start()) and drives it through its
    complete protocol via _drive_staged_protocol. Every subsequent resume() for this phase
    -- including any later same-run repair round -- must reuse the handle this call
    returns (PhaseResult.handle); never call start() again for the same phase."""
    try:
        handle, raw = agent_adapter.start(agent_type, payload)
    except Exception as exc:
        return PhaseResult(blocked=True, reason=f"agent invocation failed: {exc}")

    return _drive_staged_protocol(
        agent_adapter=agent_adapter,
        test_runner_adapter=test_runner_adapter,
        handle=handle,
        raw=raw,
        run_directory=run_directory,
        phase=phase,
        schema_name=schema_name,
        semantic_validate_fn=semantic_validate_fn,
        task_id=task_id,
        run_id=run_id,
        target_repo_path=target_repo_path,
    )


def classify_verification_outcome(verify_doc: dict | None) -> str:
    """Classify an already schema-and-semantically-validated verification-report for
    same-run route-back eligibility. Returns exactly one of:

    - "logic_bug" -- the only classification ever eligible to route back to the
      Engineer. Requires final_verdict == "fail" AND routed_back_to_engineer.routed is
      True AND at least one attempts[] entry is classified "logic_bug" -- all three,
      not verdict alone, since routed_back_to_engineer.routed and the presence of a
      logic_bug attempt are independently checked here even though
      validate_verification_report_semantics already guarantees they agree; this is
      the route-back gate re-deriving its own answer rather than trusting a single field.
    - "infrastructure_flake" -- final_verdict == "inconclusive" backed by an unresolved
      infrastructure_flake attempt. quality-engineer.md's own bounded retry (max 2,
      evidence-supported) already resolves or exhausts this *within* the Quality
      Engineer's own turn, before any verdict reaches this function -- never routed back.
    - "environment" -- final_verdict == "inconclusive" backed by an environment attempt.
      Never routed back and never retried -- retrying cannot fix a broken environment.
    - "insufficient_evidence" -- verify_doc is None/empty (the Verification phase never
      produced a validly promoted final artifact at all -- a transport failure, a
      schema/semantic validation failure, a broken continuity, or a command_rejected
      escalation), or a "fail"/"inconclusive" verdict whose supporting fields do not
      actually justify a definitive classification. Never treated as a logic bug and
      never routed back -- there is no real verdict to act on.

    Never called for a "pass" or self-reported "blocked" verdict -- callers only need
    this function to decide whether a "fail"/"inconclusive" verdict is a genuine logic
    bug or something route-back must never touch."""
    if not verify_doc:
        return "insufficient_evidence"

    verdict = verify_doc.get("final_verdict")
    attempts = verify_doc.get("attempts") or []
    routed = (verify_doc.get("routed_back_to_engineer") or {}).get("routed")

    if verdict == "fail":
        has_logic_bug = any(a.get("classification") == "logic_bug" for a in attempts)
        if routed is True and has_logic_bug:
            return "logic_bug"
        return "insufficient_evidence"

    if verdict == "inconclusive":
        if any(a.get("classification") == "environment" for a in attempts):
            return "environment"
        if any(a.get("classification") == "infrastructure_flake" for a in attempts):
            return "infrastructure_flake"
        return "insufficient_evidence"

    return "insufficient_evidence"


def _route_back_message(*, task_id: str, run_id: str, repair_attempt: int, verification_ref: str, verify_doc: dict) -> dict:
    """The real Quality Engineer failure evidence handed to the resumed Engineer --
    never summarized, softened, or partially redacted (per work/SKILL.md's route-back
    protocol)."""
    failing_attempts = [a for a in verify_doc.get("attempts", []) if a.get("classification") == "logic_bug"]
    failed_criteria = [
        r["criteria_id"] for r in verify_doc.get("acceptance_criteria_results", []) if r.get("result") == "failed"
    ]
    return {
        "message_type": "verification_failed",
        "task_id": task_id,
        "run_id": run_id,
        "repair_attempt": repair_attempt,
        "verification_ref": {"path": verification_ref},
        "failing_attempts": failing_attempts,
        "acceptance_criteria_failed": failed_criteria,
    }


def _reverify_message(*, task_id: str, run_id: str, repair_attempt: int, implementation_ref: str) -> dict:
    return {
        "message_type": "implementation_updated",
        "task_id": task_id,
        "run_id": run_id,
        "repair_attempt": repair_attempt,
        "implementation_ref": {"path": implementation_ref},
    }


def _handle_verification_failure(
    *,
    verify_doc: dict,
    verify_canonical_path: Path,
    engineer_handle: str | None,
    qe_handle: str | None,
    repair_round: int,
    agent_adapter: AgentAdapter,
    test_runner_adapter: TestRunnerAdapter,
    run_directory: Path,
    root: Path,
    task_id: str,
    run_id: str,
    target_repo_path: str,
    findings_doc: dict,
    scope_doc: dict,
    artifact_refs: dict,
    finalize: Callable[..., "RunResult"],
) -> "RunResult":
    """Classifies a "fail" verification verdict and either ends the run honestly or drives
    exactly one bounded same-run repair cycle: resume the same Engineer with the real QE
    failure evidence -> staged Implementation protocol -> resume the same Quality Engineer
    with the updated implementation report -> staged Verification protocol -> recurse on
    the new verdict. Recursion depth is bounded by MAX_LOGIC_REPAIR_ATTEMPTS, checked
    before any further routing is attempted."""
    verification_ref = _rel(verify_canonical_path, root)
    classification = classify_verification_outcome(verify_doc)
    evidence_io.retain_policy_event(
        run_directory,
        "verification_failure_classified",
        {"repair_attempt": repair_round + 1, "classification": classification, "verification_ref": verification_ref},
    )

    if classification != "logic_bug":
        return finalize(
            State.VERIFICATION_FAILED,
            reason=(
                f"verification failed ({classification}); not eligible for route-back -- "
                + (verify_doc.get("blocked_reason") or "see verification-report attempts for detail")
            ),
        )

    evidence_io.retain_policy_event(
        run_directory, "logic_failure_detected",
        {"repair_attempt": repair_round + 1, "verification_ref": verification_ref},
    )

    if repair_round >= MAX_LOGIC_REPAIR_ATTEMPTS:
        evidence_io.retain_policy_event(
            run_directory, "route_back_exhaustion",
            {"repair_attempt": repair_round, "max_logic_repair_attempts": MAX_LOGIC_REPAIR_ATTEMPTS},
        )
        return finalize(
            State.VERIFICATION_FAILED,
            reason=f"logic_bug persisted after the permitted repair attempt(s) ({MAX_LOGIC_REPAIR_ATTEMPTS})",
        )

    if not engineer_handle or not qe_handle:
        # Defensive only -- both handles always exist by the time a "fail" verdict is
        # reachable (Implementation and Verification both already dispatched successfully).
        return finalize(State.VERIFICATION_FAILED, reason="internal error: missing agent handle for route-back")

    repair_attempt = repair_round + 1

    # ---- Resume the SAME Engineer with the real QE failure evidence ----
    evidence_io.retain_policy_event(
        run_directory, "engineer_route_back",
        {"repair_attempt": repair_attempt, "engineer_handle": engineer_handle, "verification_ref": verification_ref},
    )
    route_back_msg = _route_back_message(
        task_id=task_id, run_id=run_id, repair_attempt=repair_attempt,
        verification_ref=verification_ref, verify_doc=verify_doc,
    )
    try:
        raw = agent_adapter.resume(engineer_handle, route_back_msg)
    except Exception as exc:
        evidence_io.retain_policy_event(
            run_directory, "continuity_broken",
            {"phase": "implementation", "repair_attempt": repair_attempt, "detail": str(exc)},
        )
        return finalize(
            State.IMPLEMENTATION_BLOCKED,
            reason=f"lost continuity resuming Engineer for repair attempt {repair_attempt}: {exc}",
        )

    repair_impl_result = _drive_staged_protocol(
        agent_adapter=agent_adapter, test_runner_adapter=test_runner_adapter, handle=engineer_handle, raw=raw,
        run_directory=run_directory, phase="implementation",
        attempt_phase_label=f"implementation-repair-{repair_attempt}",
        canonical_filename=f"implementation-report.repair-{repair_attempt}.json",
        schema_name="implementation-report",
        semantic_validate_fn=lambda doc: validate_implementation_report_semantics(doc, findings_doc),
        task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
    )
    if repair_impl_result.blocked:
        return finalize(
            State.IMPLEMENTATION_BLOCKED,
            reason=f"repair attempt {repair_attempt} implementation phase blocked: {repair_impl_result.reason}",
        )

    repair_impl_doc = repair_impl_result.doc
    if repair_impl_doc.get("status") == "blocked":
        return finalize(
            State.IMPLEMENTATION_BLOCKED,
            reason=repair_impl_doc.get("blocked_reason", "repair implementation reported blocked"),
        )

    violation = paths.find_protected_violation(repair_impl_doc.get("changed_files", []))
    if violation:
        return finalize(
            State.IMPLEMENTATION_BLOCKED, reason=f"repair changed_files includes protected path {violation!r}"
        )

    artifact_refs["implementation_report"] = _rel(repair_impl_result.canonical_path, root)

    # ---- Resume the SAME Quality Engineer with the updated implementation report ----
    evidence_io.retain_policy_event(
        run_directory, "re_verification",
        {
            "repair_attempt": repair_attempt,
            "qe_handle": qe_handle,
            "implementation_ref": artifact_refs["implementation_report"],
        },
    )
    reverify_msg = _reverify_message(
        task_id=task_id, run_id=run_id, repair_attempt=repair_attempt,
        implementation_ref=artifact_refs["implementation_report"],
    )
    try:
        raw = agent_adapter.resume(qe_handle, reverify_msg)
    except Exception as exc:
        evidence_io.retain_policy_event(
            run_directory, "continuity_broken",
            {"phase": "verification", "repair_attempt": repair_attempt, "detail": str(exc)},
        )
        return finalize(
            State.VERIFICATION_BLOCKED,
            reason=f"lost continuity resuming Quality Engineer for repair attempt {repair_attempt}: {exc}",
        )

    repair_verify_result = _drive_staged_protocol(
        agent_adapter=agent_adapter, test_runner_adapter=test_runner_adapter, handle=qe_handle, raw=raw,
        run_directory=run_directory, phase="verification",
        attempt_phase_label=f"verification-repair-{repair_attempt}",
        canonical_filename=f"verification-report.repair-{repair_attempt}.json",
        schema_name="verification-report",
        semantic_validate_fn=lambda doc: validate_verification_report_semantics(doc, scope_doc),
        task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
    )
    if repair_verify_result.blocked:
        return finalize(
            State.VERIFICATION_BLOCKED,
            reason=f"repair attempt {repair_attempt} verification phase blocked: {repair_verify_result.reason}",
        )

    artifact_refs["verification_report"] = _rel(repair_verify_result.canonical_path, root)
    repair_verify_doc = repair_verify_result.doc
    repair_verdict = repair_verify_doc.get("final_verdict")

    if repair_verdict == "pass":
        return finalize(
            State.COMPLETED,
            reason=f"all acceptance criteria passed after {repair_attempt} logic-bug repair cycle(s)",
        )
    if repair_verdict == "fail":
        return _handle_verification_failure(
            verify_doc=repair_verify_doc, verify_canonical_path=repair_verify_result.canonical_path,
            engineer_handle=engineer_handle, qe_handle=qe_handle, repair_round=repair_attempt,
            agent_adapter=agent_adapter, test_runner_adapter=test_runner_adapter, run_directory=run_directory,
            root=root, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
            findings_doc=findings_doc, scope_doc=scope_doc, artifact_refs=artifact_refs, finalize=finalize,
        )
    if repair_verdict == "inconclusive":
        return finalize(
            State.VERIFICATION_INCONCLUSIVE,
            reason=repair_verify_doc.get("blocked_reason", "verification inconclusive after repair"),
        )
    return finalize(
        State.VERIFICATION_BLOCKED, reason=repair_verify_doc.get("blocked_reason", "verification blocked after repair")
    )


def _build_objective_summary(state: State, phase: str | None, reason: str) -> str:
    if state == State.COMPLETED:
        base = "Verification passed; every acceptance criterion in scope.json is satisfied."
        if reason and "repair" in reason:
            return f"{base} {reason}."
        return base
    if state == State.VERIFICATION_FAILED:
        return f"Verification ran to a definitive result and failed: {reason}"
    if state == State.VERIFICATION_INCONCLUSIVE:
        return f"Verification could not reach a pass or fail verdict: {reason}"
    if phase:
        return f"Run stopped in the {phase} phase ({state.value}): {reason}"
    return f"Run stopped ({state.value}): {reason}"


def _finalize(
    run_directory: Path,
    root: Path,
    state: State,
    task_id: str,
    run_id: str,
    created_at: str,
    artifact_refs: dict,
    phases_completed: list,
    *,
    reason: str,
    duration_seconds: float,
    target_repo_path: str,
    phases_reused: list | None = None,
    phases_restarted: list | None = None,
) -> RunResult:
    artifact_refs = dict(artifact_refs)
    if "scope" not in artifact_refs:
        # Every terminal outcome must still produce a schema-valid run-summary.json, and
        # artifact_refs.scope is unconditionally required by run-summary.schema.json even when
        # Discovery never produced a valid scope document at all (see final report: this is a
        # real, minor representational gap, worked around honestly rather than fabricated).
        placeholder = evidence_io.retain_raw_attempt(
            run_directory,
            "discovery-error",
            1,
            f"no scope draft was ever produced or promoted for this run.\nreason: {reason}",
        )
        artifact_refs["scope"] = _rel(placeholder, root)

    final_verdict = FINAL_VERDICT_BY_STATE[state]
    phase = PHASE_BY_STATE.get(state)
    completed = state == State.COMPLETED

    summary = {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "created_at": created_at,
        "objective_summary": _build_objective_summary(state, phase, reason),
        "artifact_refs": artifact_refs,
        "final_verdict": final_verdict,
        "phases_completed": phases_completed,
        "remaining_risks": [] if completed else [reason] if reason else [],
        "follow_up_actions": (
            []
            if completed
            else [f"Review retained evidence under {_rel(run_directory, root)}/ for the candidate that blocked this run."]
        ),
        "duration_seconds": duration_seconds,
    }
    if phases_reused:
        summary["phases_reused"] = list(phases_reused)
    if phases_restarted:
        summary["phases_restarted"] = list(phases_restarted)

    canonical_path = evidence_io.write_run_summary(run_directory, summary)

    # Every terminal outcome updates checkpoint.json to reflect reality -- "complete" on
    # a genuine pass, "failed" for anything else -- but only if a checkpoint already
    # exists for this run (i.e. Discovery got far enough to be checkpointed at all; a
    # Discovery-refused/invalid run never had forward progress worth checkpointing).
    if state == State.COMPLETED:
        checkpoint.record_completion(
            run_directory, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
            updated_at=_now_iso(), artifact_refs=_checkpoint_artifact_refs(phases_completed, artifact_refs),
        )
    elif checkpoint.checkpoint_path(run_directory).exists():
        # phases_completed (run-summary's sense) can include the very phase whose
        # non-passing outcome is why the run is terminal -- e.g. Verification produced a
        # perfectly valid verification-report.json with final_verdict "fail"/
        # "inconclusive", so the *phase itself* ran to completion even though the *run*
        # did not succeed. Checkpoint's completed_phases means something stricter --
        # "safe to reuse without redoing" -- so the phase PHASE_BY_STATE associates with
        # this terminal state is excluded here, keeping current_phase (that same phase)
        # correctly absent from completed_phases per checkpoint's own invariant.
        terminal_phase = PHASE_BY_STATE.get(state)
        checkpoint_completed_phases = [p for p in phases_completed if p != terminal_phase]
        try:
            checkpoint.record_terminal_failure(
                run_directory, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
                updated_at=_now_iso(), completed_phases=checkpoint_completed_phases,
                artifact_refs=_checkpoint_artifact_refs(checkpoint_completed_phases, artifact_refs),
                reason=reason or state.value,
            )
        except checkpoint.CheckpointError as exc:
            # Only reachable from a state raised deep inside the same-run logic-repair
            # loop (_handle_verification_failure), which revisits Implementation/
            # Verification a second time within one process and one run -- something
            # checkpoint.json's simple, strictly-linear phase model was never designed
            # to represent, and never needs to: the repair loop is same-run/same-process
            # by construction (ASSIGNMENT.md, work/SKILL.md) and is never itself resumed
            # across a process restart. Leaving the last valid checkpoint in place
            # (rather than crashing the run over an unrepresentable edge case) is safe
            # because evaluate_resume also refuses any run_id whose run-summary.json
            # already exists, independent of what checkpoint.json says.
            evidence_io.retain_policy_event(
                run_directory, "checkpoint_update_skipped",
                {"reason": exc.message, "code": exc.code, "state": state.value},
            )

    return RunResult(
        task_id=task_id,
        run_id=run_id,
        state=state,
        run_dir=run_directory,
        artifact_refs=artifact_refs,
        phases_completed=phases_completed,
        final_verdict=final_verdict,
        reason=reason,
        run_summary_path=_rel(canonical_path, root),
    )


def _do_research(
    *, root: Path, run_directory: Path, agent_adapter: AgentAdapter, test_runner_adapter: TestRunnerAdapter,
    task_id: str, run_id: str, created_at: str, artifact_refs: dict, finalize: Callable[..., RunResult],
) -> tuple[dict | None, RunResult | None]:
    """Dispatches a fresh Architect. Returns (findings_doc, None) on success or
    (None, blocked_RunResult) otherwise. Shared by _run_phases (a fresh run) and
    resume() (an incomplete Research phase restarting in a new process) -- the only
    difference between those two callers is what happened *before* this call, never
    what happens inside it."""
    architect_payload = {
        "task_id": task_id, "run_id": run_id, "created_at": created_at,
        "scope_ref": {"path": artifact_refs["scope"]},
    }
    research_result = _run_agent_phase(
        agent_adapter=agent_adapter, test_runner_adapter=test_runner_adapter, agent_type="architect",
        payload=architect_payload, run_directory=run_directory, phase="research", schema_name="findings",
        semantic_validate_fn=validate_findings_semantics, task_id=task_id, run_id=run_id,
        target_repo_path=None,
    )
    if research_result.blocked:
        return None, finalize(State.RESEARCH_BLOCKED, reason=research_result.reason)
    artifact_refs["findings"] = _rel(research_result.canonical_path, root)
    return research_result.doc, None


def _do_implementation(
    *, root: Path, run_directory: Path, agent_adapter: AgentAdapter, test_runner_adapter: TestRunnerAdapter,
    task_id: str, run_id: str, created_at: str, target_repo_path: str, artifact_refs: dict, findings_doc: dict,
    finalize: Callable[..., RunResult],
) -> tuple[dict | None, str | None, RunResult | None]:
    """Dispatches a fresh Engineer. Returns (impl_doc, engineer_handle, None) on success
    or (None, None, blocked_RunResult) otherwise. See _do_research's docstring for why
    this is shared between a fresh run and a resumed one."""
    try:
        path_validation = paths.build_path_validation_attestation(target_repo_path, repo_root=root)
    except paths.PathSafetyError as exc:
        return None, None, finalize(
            State.IMPLEMENTATION_BLOCKED, reason=f"target_repo_path failed path safety re-check: {exc}"
        )

    found_finding_ids = [f["id"] for f in findings_doc.get("findings", []) if f.get("classification") == "found"]
    engineer_payload = {
        "task_id": task_id, "run_id": run_id, "created_at": created_at,
        "target_repo_path": target_repo_path, "path_validation": path_validation,
        "scope_ref": {"path": artifact_refs["scope"]},
        "findings_ref": {"path": artifact_refs["findings"], "finding_ids": found_finding_ids},
    }
    impl_result = _run_agent_phase(
        agent_adapter=agent_adapter, test_runner_adapter=test_runner_adapter, agent_type="engineer",
        payload=engineer_payload, run_directory=run_directory, phase="implementation",
        schema_name="implementation-report",
        semantic_validate_fn=lambda doc: validate_implementation_report_semantics(doc, findings_doc),
        task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
    )
    if impl_result.blocked:
        return None, None, finalize(State.IMPLEMENTATION_BLOCKED, reason=impl_result.reason)
    artifact_refs["implementation_report"] = _rel(impl_result.canonical_path, root)
    impl_doc = impl_result.doc

    if impl_doc.get("status") == "blocked":
        return None, None, finalize(
            State.IMPLEMENTATION_BLOCKED, reason=impl_doc.get("blocked_reason", "implementation reported blocked")
        )

    violation = paths.find_protected_violation(impl_doc.get("changed_files", []))
    if violation:
        return None, None, finalize(
            State.IMPLEMENTATION_BLOCKED, reason=f"changed_files includes protected path {violation!r}"
        )

    return impl_doc, impl_result.handle, None


def _do_verification(
    *, root: Path, run_directory: Path, agent_adapter: AgentAdapter, test_runner_adapter: TestRunnerAdapter,
    task_id: str, run_id: str, created_at: str, target_repo_path: str, artifact_refs: dict, findings_doc: dict,
    scope_doc: dict, engineer_handle: str | None, finalize: Callable[..., RunResult], phases_completed: list,
) -> RunResult:
    """Dispatches a fresh Quality Engineer and always returns a terminal RunResult --
    Verification's outcome (pass/fail-with-possible-repair/inconclusive/blocked) is
    never "still in progress" once this call returns. See _do_research's docstring for
    why this is shared between a fresh run and a resumed one."""
    qe_payload = {
        "task_id": task_id, "run_id": run_id, "created_at": created_at,
        "target_repo_path": target_repo_path,
        "path_validation": paths.build_path_validation_attestation(target_repo_path, repo_root=root),
        "scope_ref": {"path": artifact_refs["scope"]},
        "findings_ref": {"path": artifact_refs["findings"]},
        "implementation_ref": {"path": artifact_refs["implementation_report"]},
    }
    verify_result = _run_agent_phase(
        agent_adapter=agent_adapter, test_runner_adapter=test_runner_adapter, agent_type="quality_engineer",
        payload=qe_payload, run_directory=run_directory, phase="verification",
        schema_name="verification-report",
        semantic_validate_fn=lambda doc: validate_verification_report_semantics(doc, scope_doc),
        task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
    )
    if verify_result.blocked:
        return finalize(State.VERIFICATION_BLOCKED, reason=verify_result.reason)
    artifact_refs["verification_report"] = _rel(verify_result.canonical_path, root)
    verify_doc = verify_result.doc
    verdict = verify_doc.get("final_verdict")

    if verdict == "pass":
        phases_completed.append("verification")
        return finalize(State.COMPLETED, reason="all acceptance criteria passed")
    if verdict == "fail":
        phases_completed.append("verification")
        return _handle_verification_failure(
            verify_doc=verify_doc, verify_canonical_path=verify_result.canonical_path,
            engineer_handle=engineer_handle, qe_handle=verify_result.handle, repair_round=0,
            agent_adapter=agent_adapter, test_runner_adapter=test_runner_adapter, run_directory=run_directory,
            root=root, task_id=task_id, run_id=run_id, target_repo_path=target_repo_path,
            findings_doc=findings_doc, scope_doc=scope_doc, artifact_refs=artifact_refs, finalize=finalize,
        )
    if verdict == "inconclusive":
        phases_completed.append("verification")
        return finalize(
            State.VERIFICATION_INCONCLUSIVE, reason=verify_doc.get("blocked_reason", "verification inconclusive")
        )
    return finalize(State.VERIFICATION_BLOCKED, reason=verify_doc.get("blocked_reason", "verification blocked"))


def _run_phases(
    root: Path,
    run_directory: Path,
    raw_prompt: str,
    target_repo_path: str,
    task_id: str,
    run_id: str,
    created_at: str,
    discovery_adapter: DiscoveryAdapter,
    agent_adapter: AgentAdapter,
    test_runner_adapter: TestRunnerAdapter,
    artifact_refs: dict,
    phases_completed: list,
    start_time: float,
) -> RunResult:
    def finalize(state: State, *, reason: str) -> RunResult:
        return _finalize(
            run_directory, root, state, task_id, run_id, created_at, artifact_refs, phases_completed,
            reason=reason, duration_seconds=time.monotonic() - start_time, target_repo_path=target_repo_path,
        )

    # ---- Discovery ----
    proposed = discovery_adapter.propose_scope(
        raw_prompt=raw_prompt, task_id=task_id, run_id=run_id, created_at=created_at,
        target_repo_path=target_repo_path,
    )
    evidence_io.retain_raw_attempt(run_directory, "discovery", 1, json.dumps(proposed, indent=2))

    errors = discovery.validate_scope_draft(
        proposed, expected_task_id=task_id, expected_run_id=run_id,
        target_repo_path=target_repo_path, repo_root=root,
    )
    if errors:
        return finalize(State.DISCOVERY_INVALID, reason="; ".join(errors))

    canonical_scope_path = evidence_io.promote_canonical(run_directory, "scope.json", proposed)
    artifact_refs["scope"] = _rel(canonical_scope_path, root)
    scope_doc = proposed

    if scope_doc.get("status") == "refused":
        phases_completed.append("discovery")
        return finalize(State.DISCOVERY_REFUSED, reason=scope_doc.get("refusal_reason", "scope refused"))

    phases_completed.append("discovery")
    _checkpoint_progress(run_directory, task_id, run_id, target_repo_path, phases_completed, artifact_refs)

    # ---- Research (Architect) ----
    findings_doc, blocked = _do_research(
        root=root, run_directory=run_directory, agent_adapter=agent_adapter,
        test_runner_adapter=test_runner_adapter, task_id=task_id, run_id=run_id, created_at=created_at,
        artifact_refs=artifact_refs, finalize=finalize,
    )
    if blocked:
        return blocked
    phases_completed.append("research")
    _checkpoint_progress(run_directory, task_id, run_id, target_repo_path, phases_completed, artifact_refs)

    # ---- Implementation (Engineer) ----
    impl_doc, engineer_handle, blocked = _do_implementation(
        root=root, run_directory=run_directory, agent_adapter=agent_adapter,
        test_runner_adapter=test_runner_adapter, task_id=task_id, run_id=run_id, created_at=created_at,
        target_repo_path=target_repo_path, artifact_refs=artifact_refs, findings_doc=findings_doc,
        finalize=finalize,
    )
    if blocked:
        return blocked
    phases_completed.append("implementation")
    _checkpoint_progress(run_directory, task_id, run_id, target_repo_path, phases_completed, artifact_refs)

    # ---- Verification (Quality Engineer) ----
    return _do_verification(
        root=root, run_directory=run_directory, agent_adapter=agent_adapter,
        test_runner_adapter=test_runner_adapter, task_id=task_id, run_id=run_id, created_at=created_at,
        target_repo_path=target_repo_path, artifact_refs=artifact_refs, findings_doc=findings_doc,
        scope_doc=scope_doc, engineer_handle=engineer_handle, finalize=finalize, phases_completed=phases_completed,
    )


def run(
    *,
    raw_prompt: str,
    target_repo_path: str,
    task_id: str,
    run_id: str,
    created_at: str,
    discovery_adapter: DiscoveryAdapter,
    agent_adapter: AgentAdapter,
    test_runner_adapter: TestRunnerAdapter,
    repo_root: Path | None = None,
) -> RunResult:
    root = (repo_root or paths.REPO_ROOT).resolve()
    run_directory = evidence_io.run_dir(root, run_id)
    evidence_io.ensure_run_dirs(run_directory)

    artifact_refs: dict = {}
    phases_completed: list = []
    start_time = time.monotonic()

    try:
        return _run_phases(
            root, run_directory, raw_prompt, target_repo_path, task_id, run_id, created_at,
            discovery_adapter, agent_adapter, test_runner_adapter, artifact_refs, phases_completed, start_time,
        )
    except Exception as exc:
        return _finalize(
            run_directory, root, State.FAILED, task_id, run_id, created_at, artifact_refs, phases_completed,
            reason=f"internal orchestration error: {exc}", duration_seconds=time.monotonic() - start_time,
            target_repo_path=target_repo_path,
        )


def resume(
    *,
    run_id: str,
    agent_adapter: AgentAdapter,
    test_runner_adapter: TestRunnerAdapter,
    repo_root: Path | None = None,
) -> RunResult:
    """Resumes an interrupted run from its checkpoint. Every phase already in
    completed_phases is reused (its canonical artifact is loaded and revalidated, never
    re-dispatched); the one incomplete phase and everything after it is run fresh,
    dispatching brand-new agent instances -- this process holds no live handle from
    whatever process wrote the checkpoint, and none is ever claimed to survive. Refuses
    outright (State.FAILED, no run-summary.json written or overwritten) if
    checkpoint.evaluate_resume finds the checkpoint missing, malformed, mismatched, or
    terminal -- see checkpoint.py's own docstring for the full precondition list."""
    root = (repo_root or paths.REPO_ROOT).resolve()
    run_directory = evidence_io.run_dir(root, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    start_time = time.monotonic()

    evidence_io.retain_policy_event(run_directory, "resume_requested", {"run_id": run_id})
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=run_id, repo_root=root)

    if not decision.resumable:
        evidence_io.retain_policy_event(
            run_directory, "resume_refused", {"run_id": run_id, "code": decision.code, "reason": decision.reason},
        )
        return RunResult(
            task_id=decision.task_id or "", run_id=run_id, state=State.FAILED, run_dir=run_directory,
            artifact_refs=dict(decision.artifact_refs), phases_completed=list(decision.completed_phases),
            final_verdict="blocked", reason=f"resume refused ({decision.code}): {decision.reason}",
            run_summary_path="",
        )

    evidence_io.retain_policy_event(
        run_directory, "checkpoint_validated",
        {"run_id": run_id, "completed_phases": decision.completed_phases, "next_phase": decision.next_phase},
    )
    for phase in decision.completed_phases:
        evidence_io.retain_policy_event(
            run_directory, "phase_reused", {"phase": phase, "artifact_ref": decision.artifact_refs.get(phase)},
        )
    restarted_phase = decision.next_phase
    evidence_io.retain_policy_event(run_directory, "phase_restarted", {"phase": restarted_phase})

    task_id = decision.task_id
    target_repo_path = decision.target_repo_path
    scope_doc = decision.docs["discovery"]
    created_at = scope_doc.get("created_at")
    findings_doc = decision.docs.get("research")
    impl_doc = decision.docs.get("implementation")
    engineer_handle = None  # a fresh process; no live handle from before ever existed here.

    phases_reused = list(decision.completed_phases)
    phases_completed = list(decision.completed_phases)
    # Only decision.next_phase was genuinely interrupted mid-flight and is being
    # restarted -- any phase after it in this same call was never attempted by any
    # process before now, so it is ordinary forward progress (like a fresh run()), not
    # a restart, even though it is also dispatched fresh in this loop.
    phases_restarted: list = [restarted_phase]
    artifact_refs = {_PHASE_TO_SUMMARY_KEY[phase]: rel for phase, rel in decision.artifact_refs.items()}

    def finalize(state: State, *, reason: str) -> RunResult:
        # Every terminal exit from this resume() call -- whether the restarted phase
        # itself blocks (research/implementation) or the run proceeds all the way
        # through verification (pass, fail, inconclusive, or a same-run repair cycle) --
        # routes through this one closure, since _do_research/_do_implementation/
        # _do_verification/_handle_verification_failure all call `finalize` for every
        # return path. Retaining resume_completed here, exactly once per resume() call,
        # is what makes that guarantee hold regardless of which phase the run stops in --
        # matching work/SKILL.md's "retain resume_completed immediately before reporting
        # the run's outcome" for every resumed run, not only ones that reach Verification.
        result = _finalize(
            run_directory, root, state, task_id, run_id, created_at, artifact_refs, phases_completed,
            reason=reason, duration_seconds=time.monotonic() - start_time, target_repo_path=target_repo_path,
            phases_reused=phases_reused, phases_restarted=phases_restarted,
        )
        evidence_io.retain_policy_event(
            run_directory, "resume_completed",
            {"run_id": run_id, "final_verdict": result.final_verdict, "phases_completed": result.phases_completed},
        )
        return result

    for phase in PHASE_ORDER:
        if phase in phases_completed:
            continue

        if phase == "research":
            findings_doc, blocked = _do_research(
                root=root, run_directory=run_directory, agent_adapter=agent_adapter,
                test_runner_adapter=test_runner_adapter, task_id=task_id, run_id=run_id, created_at=created_at,
                artifact_refs=artifact_refs, finalize=finalize,
            )
            if blocked:
                return blocked
            phases_completed.append("research")
            _checkpoint_progress(run_directory, task_id, run_id, target_repo_path, phases_completed, artifact_refs)
        elif phase == "implementation":
            impl_doc, engineer_handle, blocked = _do_implementation(
                root=root, run_directory=run_directory, agent_adapter=agent_adapter,
                test_runner_adapter=test_runner_adapter, task_id=task_id, run_id=run_id, created_at=created_at,
                target_repo_path=target_repo_path, artifact_refs=artifact_refs, findings_doc=findings_doc,
                finalize=finalize,
            )
            if blocked:
                return blocked
            phases_completed.append("implementation")
            _checkpoint_progress(run_directory, task_id, run_id, target_repo_path, phases_completed, artifact_refs)
        elif phase == "verification":
            # resume_completed is retained inside `finalize` above, not here -- every
            # terminal path _do_verification can take (including a same-run repair
            # cycle) already calls `finalize` internally.
            return _do_verification(
                root=root, run_directory=run_directory, agent_adapter=agent_adapter,
                test_runner_adapter=test_runner_adapter, task_id=task_id, run_id=run_id, created_at=created_at,
                target_repo_path=target_repo_path, artifact_refs=artifact_refs, findings_doc=findings_doc,
                scope_doc=scope_doc, engineer_handle=engineer_handle, finalize=finalize,
                phases_completed=phases_completed,
            )
        else:
            raise OrchestrationError(f"resume() cannot restart unrecognized phase {phase!r}")

    # Unreachable: evaluate_resume only returns resumable=True when next_phase is one of
    # research/implementation/verification (status "complete" -- next_phase None -- is
    # already refused above), so the loop above always returns from inside the
    # "verification" branch before falling through here.
    raise OrchestrationError("resume() reached no remaining phases -- internal inconsistency")
