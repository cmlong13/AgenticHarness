"""One-pass MVP orchestration engine.

Wires discovery.py, evidence_io.py, paths.py, and harness/evidence.py's
schema/semantic validators together with the AgentAdapter/TestRunnerAdapter/
DiscoveryAdapter interfaces from adapters.py to drive exactly one
straight-through attempt through Discovery -> Research -> Implementation ->
Verification. No automatic route-back to Engineer on a verification
failure; a schema-valid "fail" verdict is terminal for this slice.

The orchestrator never performs the Architect's research, the Engineer's
implementation, or the Quality Engineer's judgment itself -- it dispatches,
validates, mediates staged commands, and records evidence.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from harness.evidence import (
    load_json,
    validate_against_schema,
    validate_findings_semantics,
    validate_implementation_report_semantics,
    validate_verification_report_semantics,
)

from . import discovery, evidence_io, paths
from .adapters import AgentAdapter, DiscoveryAdapter, TestRunnerAdapter
from .state import FINAL_VERDICT_BY_STATE, PHASE_BY_STATE, State

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "harness" / "schemas"

# response_type envelopes that carry a `requested_command` and are mediated through
# TestRunnerAdapter.invoke() -- pre_test_requested/post_test_requested (Engineer) and
# attempt_requested (Quality Engineer) are all structurally interchangeable for this purpose.
COMMAND_ENVELOPE_TYPES = {"pre_test_requested", "post_test_requested", "attempt_requested"}
FINALIZATION_ENVELOPE_TYPE = "finalization_evidence_requested"

MAX_STAGED_TURNS = 20


class OrchestrationError(Exception):
    """Raised only for genuinely unexpected internal failures -- caught by run() and
    reported as State.FAILED, never used for an ordinary agent/validation block."""


@dataclass
class PhaseResult:
    blocked: bool
    doc: dict | None = None
    canonical_path: Path | None = None
    reason: str = ""


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

    filename = evidence_io.CANONICAL_FILENAMES[phase]
    try:
        canonical_path = evidence_io.promote_canonical(run_directory, filename, msg)
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
    """Drives one agent through its complete protocol: a single-shot agent (Architect) returns
    a final artifact immediately (no response_type key); a staged agent (Engineer, Quality
    Engineer) exchanges `response_type` envelopes -- command-request envelopes mediated via
    TestRunnerAdapter.invoke(), finalization_evidence_requested via TestRunnerAdapter.diff_stats()
    -- until it too emits a final artifact. The SAME handle from start() is used for every
    resume() call in this phase; a resume() failure blocks the phase immediately rather than
    calling start() again."""
    try:
        handle, raw = agent_adapter.start(agent_type, payload)
    except Exception as exc:
        return PhaseResult(blocked=True, reason=f"agent invocation failed: {exc}")

    for attempt_n in range(1, MAX_STAGED_TURNS + 1):
        try:
            evidence_io.retain_raw_attempt(run_directory, phase, attempt_n, raw)
        except evidence_io.EvidenceCollisionError as exc:
            return PhaseResult(blocked=True, reason=str(exc))

        msg, parse_error = _parse_raw(raw)
        if msg is None:
            return PhaseResult(blocked=True, reason=parse_error)

        response_type = msg.get("response_type")
        if response_type is None:
            # Final artifact -- the only message in the whole workflow that is not a
            # protocol envelope, per every agent's own contract.
            return _validate_and_promote(msg, run_directory, phase, schema_name, semantic_validate_fn, task_id, run_id)

        if msg.get("task_id") != task_id or msg.get("run_id") != run_id:
            return PhaseResult(blocked=True, reason="protocol envelope task_id/run_id mismatch")

        if response_type == FINALIZATION_ENVELOPE_TYPE:
            changed_files_known = msg.get("changed_files_known", [])
            try:
                stats = test_runner_adapter.diff_stats(changed_files_known)
            except Exception as exc:
                return PhaseResult(blocked=True, reason=f"unable to compute finalization evidence: {exc}")
            message = {
                "message_type": "finalization_evidence",
                "task_id": task_id,
                "run_id": run_id,
                "changed_files": stats,
            }
            try:
                raw = agent_adapter.resume(handle, message)
            except Exception as exc:
                return PhaseResult(blocked=True, reason=f"lost continuity resuming agent: {exc}")
            continue

        if response_type not in COMMAND_ENVELOPE_TYPES:
            return PhaseResult(blocked=True, reason=f"unrecognized protocol envelope response_type {response_type!r}")

        requested_command = msg.get("requested_command")
        if not requested_command:
            return PhaseResult(blocked=True, reason=f"{response_type} envelope missing requested_command")

        request = _build_canonical_request(
            task_id, run_id, requested_command, target_repo_path or "", purpose=f"{phase} {response_type}"
        )
        try:
            evidence_io.retain_request(run_directory, request)
        except evidence_io.EvidenceCollisionError as exc:
            return PhaseResult(blocked=True, reason=str(exc))

        try:
            result = test_runner_adapter.invoke(request)
        except Exception as exc:
            return PhaseResult(blocked=True, reason=f"test-runner invocation failed: {exc}")

        if result.get("message_type") == "command_rejected":
            evidence_io.retain_rejection(run_directory, result)
            return PhaseResult(blocked=True, reason=f"command rejected: {result.get('rejection_reason')}")

        for check_field in ("task_id", "run_id", "command_id", "command", "working_directory"):
            if result.get(check_field) != request.get(check_field):
                return PhaseResult(
                    blocked=True,
                    reason=f"command_result field {check_field!r} does not match the request that was sent",
                )

        if result.get("exit_code") == 125:
            evidence_io.retain_policy_event(run_directory, "mutation_detected", result)

        message = _command_result_message(task_id, run_id, result)
        try:
            raw = agent_adapter.resume(handle, message)
        except Exception as exc:
            return PhaseResult(blocked=True, reason=f"lost continuity resuming agent: {exc}")

    return PhaseResult(blocked=True, reason="staged protocol exceeded the maximum number of turns")


def _build_objective_summary(state: State, phase: str | None, reason: str) -> str:
    if state == State.COMPLETED:
        return "Verification passed; every acceptance criterion in scope.json is satisfied."
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

    canonical_path = evidence_io.write_run_summary(run_directory, summary)
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
            reason=reason, duration_seconds=time.monotonic() - start_time,
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

    # ---- Research (Architect) ----
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
        return finalize(State.RESEARCH_BLOCKED, reason=research_result.reason)
    artifact_refs["findings"] = _rel(research_result.canonical_path, root)
    findings_doc = research_result.doc
    phases_completed.append("research")

    # ---- Implementation (Engineer) ----
    try:
        path_validation = paths.build_path_validation_attestation(target_repo_path, repo_root=root)
    except paths.PathSafetyError as exc:
        return finalize(State.IMPLEMENTATION_BLOCKED, reason=f"target_repo_path failed path safety re-check: {exc}")

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
        return finalize(State.IMPLEMENTATION_BLOCKED, reason=impl_result.reason)
    artifact_refs["implementation_report"] = _rel(impl_result.canonical_path, root)
    impl_doc = impl_result.doc

    if impl_doc.get("status") == "blocked":
        return finalize(
            State.IMPLEMENTATION_BLOCKED, reason=impl_doc.get("blocked_reason", "implementation reported blocked")
        )

    violation = paths.find_protected_violation(impl_doc.get("changed_files", []))
    if violation:
        return finalize(
            State.IMPLEMENTATION_BLOCKED, reason=f"changed_files includes protected path {violation!r}"
        )

    phases_completed.append("implementation")

    # ---- Verification (Quality Engineer) ----
    qe_payload = {
        "task_id": task_id, "run_id": run_id, "created_at": created_at,
        "target_repo_path": target_repo_path, "path_validation": path_validation,
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
        return finalize(State.VERIFICATION_FAILED, reason=verify_doc.get("blocked_reason", "verification failed"))
    if verdict == "inconclusive":
        phases_completed.append("verification")
        return finalize(
            State.VERIFICATION_INCONCLUSIVE, reason=verify_doc.get("blocked_reason", "verification inconclusive")
        )
    return finalize(State.VERIFICATION_BLOCKED, reason=verify_doc.get("blocked_reason", "verification blocked"))


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
        )
