"""Deterministic, low-level tests for harness/orchestrator/checkpoint.py.

These exercise checkpoint.py directly -- no AgentAdapter, no core.py pipeline -- so each
precondition evaluate_resume() enforces (and each record_* builder's own write-time
validation) can be proven in isolation. Integration-level proof that core.py's run()/
resume() actually call these at the right times lives in tests/test_orchestrator_core.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.evidence import load_json, validate_against_schema
from harness.orchestrator import checkpoint, evidence_io

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "harness" / "schemas"
CHECKPOINT_SCHEMA = load_json(SCHEMA_DIR / "checkpoint.schema.json")

TASK_ID = "T-CKPT"
RUN_ID = "run-ckpt-1"
TARGET_REPO_PATH = "fixture-repo"
UPDATED_AT = "2026-08-06T00:00:00Z"


def _make_fixture_repo(root: Path) -> None:
    (root / TARGET_REPO_PATH).mkdir(parents=True)
    (root / TARGET_REPO_PATH / "placeholder.txt").write_text("x\n", encoding="utf-8")


def _run_dir(root: Path) -> Path:
    d = evidence_io.run_dir(root, RUN_ID)
    evidence_io.ensure_run_dirs(d)
    return d


def _assert_schema_valid(doc: dict) -> None:
    errors = validate_against_schema(doc, CHECKPOINT_SCHEMA, artifact="checkpoint")
    assert not errors, "\n".join(str(e) for e in errors)


def _scope_doc(*, task_id=TASK_ID, run_id=RUN_ID) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "created_at": UPDATED_AT,
        "source": {"type": "prompt", "raw_prompt": "test"},
        "objective": "test objective",
        "in_scope": [f"{TARGET_REPO_PATH}/placeholder.txt"],
        "out_of_scope": [],
        "constraints": [],
        "acceptance_criteria": [{"id": "AC-1", "description": "criterion"}],
        "task_graph": [{"id": "N-1", "description": "node", "phase": "research", "depends_on": []}],
        "status": "approved",
    }


def _findings_doc(*, task_id=TASK_ID, run_id=RUN_ID, scope_ref="") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "created_at": UPDATED_AT,
        "scope_ref": {"path": scope_ref},
        "findings": [
            {
                "id": "F-1",
                "claim": "placeholder.txt exists",
                "classification": "found",
                "evidence": [
                    {
                        "path": f"{TARGET_REPO_PATH}/placeholder.txt",
                        "start_line": 1,
                        "end_line": 1,
                        "evidence_tier": "executable_code",
                    }
                ],
            }
        ],
    }


def _promote_scope(run_directory: Path, root: Path, *, task_id=TASK_ID, run_id=RUN_ID) -> str:
    path = evidence_io.promote_canonical(run_directory, "scope.json", _scope_doc(task_id=task_id, run_id=run_id))
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


# ---------------------------------------------------------------------------
# record_phase_progress / record_completion / record_interruption / record_terminal_failure
# ---------------------------------------------------------------------------


def test_record_phase_progress_after_discovery(tmp_path):
    run_directory = _run_dir(tmp_path)
    path = checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": "runs/x/scope.json"},
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    _assert_schema_valid(doc)
    assert doc["completed_phases"] == ["discovery"]
    assert doc["current_phase"] == "research"
    assert doc["next_phase"] == "implementation"
    assert doc["status"] == "in_progress"
    assert doc["target_repo_path"] == TARGET_REPO_PATH


def test_record_phase_progress_after_research(tmp_path):
    run_directory = _run_dir(tmp_path)
    path = checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery", "research"],
        artifact_refs={"discovery": "runs/x/scope.json", "research": "runs/x/findings.json"},
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    _assert_schema_valid(doc)
    assert doc["current_phase"] == "implementation"
    assert doc["next_phase"] == "verification"


def test_record_phase_progress_after_implementation(tmp_path):
    run_directory = _run_dir(tmp_path)
    path = checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery", "research", "implementation"],
        artifact_refs={
            "discovery": "runs/x/scope.json", "research": "runs/x/findings.json",
            "implementation": "runs/x/implementation-report.json",
        },
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    _assert_schema_valid(doc)
    assert doc["current_phase"] == "verification"
    assert doc["next_phase"] is None


def test_record_completion_after_verification(tmp_path):
    run_directory = _run_dir(tmp_path)
    path = checkpoint.record_completion(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT,
        artifact_refs={
            "discovery": "runs/x/scope.json", "research": "runs/x/findings.json",
            "implementation": "runs/x/implementation-report.json", "verification": "runs/x/verification-report.json",
        },
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    _assert_schema_valid(doc)
    assert doc["status"] == "complete"
    assert doc["current_phase"] is None
    assert doc["next_phase"] is None
    assert doc["next_resume_action"] is None
    assert set(doc["completed_phases"]) == {"discovery", "research", "implementation", "verification"}


def test_record_interruption_records_blocker_and_status(tmp_path):
    run_directory = _run_dir(tmp_path)
    path = checkpoint.record_interruption(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery", "research"],
        artifact_refs={"discovery": "runs/x/scope.json", "research": "runs/x/findings.json"},
        note="deliberate interruption for milestone demonstration",
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    _assert_schema_valid(doc)
    assert doc["status"] == "interrupted"
    assert doc["current_phase"] == "implementation"
    assert doc["blockers"] == [{"description": "deliberate interruption for milestone demonstration", "phase": "implementation"}]


def test_record_terminal_failure_records_status_and_reason(tmp_path):
    run_directory = _run_dir(tmp_path)
    path = checkpoint.record_terminal_failure(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"],
        artifact_refs={"discovery": "runs/x/scope.json"}, reason="research_blocked: architect failed",
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    _assert_schema_valid(doc)
    assert doc["status"] == "failed"
    assert doc["current_phase"] == "research"
    assert doc["blockers"][0]["description"] == "research_blocked: architect failed"
    assert "not resumable" in doc["next_resume_action"]


# ---------------------------------------------------------------------------
# Atomic replacement
# ---------------------------------------------------------------------------


def test_checkpoint_write_is_atomic_replacement_no_leftover_temp_files(tmp_path):
    run_directory = _run_dir(tmp_path)
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": "runs/x/scope.json"},
    )
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery", "research"],
        artifact_refs={"discovery": "runs/x/scope.json", "research": "runs/x/findings.json"},
    )
    doc = json.loads(checkpoint.checkpoint_path(run_directory).read_text(encoding="utf-8"))
    assert doc["completed_phases"] == ["discovery", "research"], "second write must overwrite the first"

    leftover_tmp_files = list(run_directory.glob(".checkpoint.json.*.tmp"))
    assert leftover_tmp_files == [], f"atomic_write must never leave a temp file behind: {leftover_tmp_files}"

    # Exactly one checkpoint.json file exists at the canonical path -- no
    # checkpoint.json.1, checkpoint.json.bak, or similar accumulation across writes.
    assert list(run_directory.glob("checkpoint.json*")) == [checkpoint.checkpoint_path(run_directory)]


# ---------------------------------------------------------------------------
# Refusal to write a malformed/inconsistent checkpoint (write-side)
# ---------------------------------------------------------------------------


def test_write_refuses_schema_invalid_document(tmp_path):
    run_directory = _run_dir(tmp_path)
    bad_doc = {"schema_version": "1.0", "task_id": TASK_ID}  # missing every other required field
    with pytest.raises(checkpoint.CheckpointError) as exc_info:
        checkpoint._write(run_directory, bad_doc)
    assert exc_info.value.code == "invalid_checkpoint"
    assert not checkpoint.checkpoint_path(run_directory).exists()


def test_write_refuses_semantically_inconsistent_document(tmp_path):
    run_directory = _run_dir(tmp_path)
    # current_phase appears in completed_phases -- schema-shaped but semantically false.
    bad_doc = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "target_repo_path": TARGET_REPO_PATH,
        "updated_at": UPDATED_AT, "completed_phases": ["discovery", "research"], "current_phase": "research",
        "next_phase": "implementation", "artifact_refs": {"discovery": "x", "research": "y"}, "blockers": [],
        "next_resume_action": "dispatch implementation", "status": "in_progress",
    }
    with pytest.raises(checkpoint.CheckpointError) as exc_info:
        checkpoint._write(run_directory, bad_doc)
    assert exc_info.value.code == "invalid_checkpoint"
    assert not checkpoint.checkpoint_path(run_directory).exists()


def test_record_phase_progress_refuses_when_all_phases_already_completed(tmp_path):
    run_directory = _run_dir(tmp_path)
    with pytest.raises(checkpoint.CheckpointError):
        checkpoint.record_phase_progress(
            run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
            updated_at=UPDATED_AT,
            completed_phases=["discovery", "research", "implementation", "verification"],
            artifact_refs={},
        )


# ---------------------------------------------------------------------------
# evaluate_resume -- every Part 3 precondition
# ---------------------------------------------------------------------------


def test_evaluate_resume_refuses_missing_checkpoint(tmp_path):
    run_directory = _run_dir(tmp_path)
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "no_checkpoint"


def test_evaluate_resume_refuses_malformed_json(tmp_path):
    run_directory = _run_dir(tmp_path)
    checkpoint.checkpoint_path(run_directory).write_text("{not valid json", encoding="utf-8")
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "malformed_checkpoint"


def test_evaluate_resume_refuses_schema_invalid_checkpoint(tmp_path):
    run_directory = _run_dir(tmp_path)
    checkpoint.checkpoint_path(run_directory).write_text(json.dumps({"task_id": TASK_ID}), encoding="utf-8")
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "invalid_checkpoint"


def test_evaluate_resume_refuses_run_id_mismatch(tmp_path):
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    scope_ref = _promote_scope(run_directory, tmp_path)
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id="run-ckpt-DIFFERENT", target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": scope_ref},
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "run_id_mismatch"


def test_evaluate_resume_refuses_terminal_complete(tmp_path):
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    scope_ref = _promote_scope(run_directory, tmp_path)
    checkpoint.record_completion(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT,
        artifact_refs={
            "discovery": scope_ref, "research": f"runs/{RUN_ID}/findings.json",
            "implementation": f"runs/{RUN_ID}/implementation-report.json",
            "verification": f"runs/{RUN_ID}/verification-report.json",
        },
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "terminal_complete"


def test_evaluate_resume_refuses_terminal_failed(tmp_path):
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    scope_ref = _promote_scope(run_directory, tmp_path)
    checkpoint.record_terminal_failure(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": scope_ref},
        reason="research_blocked",
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "terminal_failed"


def test_evaluate_resume_refuses_when_run_summary_already_exists(tmp_path):
    """Independent of checkpoint.json's own status: a run-summary.json means the run
    already concluded, so resume is refused even if checkpoint.json were somehow left
    saying "in_progress" (e.g. a same-run repair-loop edge case core.py's _finalize
    deliberately does not force into checkpoint.json's linear phase model)."""
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    scope_ref = _promote_scope(run_directory, tmp_path)
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": scope_ref},
    )
    evidence_io.write_run_summary(
        run_directory,
        {
            "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": UPDATED_AT,
            "objective_summary": "test", "artifact_refs": {"scope": scope_ref}, "final_verdict": "fail",
            "phases_completed": ["discovery"],
        },
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "run_already_terminal"


def test_evaluate_resume_refuses_target_repo_path_no_longer_valid(tmp_path):
    run_directory = _run_dir(tmp_path)
    scope_ref = _promote_scope(run_directory, tmp_path)
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path="this-directory-does-not-exist",
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": scope_ref},
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "target_repo_path_invalid"


def test_evaluate_resume_refuses_missing_artifact(tmp_path):
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"],
        artifact_refs={"discovery": f"runs/{RUN_ID}/scope.json"},  # never actually promoted
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "missing_artifact"


def test_evaluate_resume_refuses_invalid_artifact(tmp_path):
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    bad_scope_path = run_directory / "scope.json"
    bad_scope_path.write_text(json.dumps({"task_id": TASK_ID, "run_id": RUN_ID}), encoding="utf-8")
    scope_ref = f"runs/{RUN_ID}/scope.json"
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": scope_ref},
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "invalid_artifact"


def test_evaluate_resume_refuses_task_id_mismatch_between_checkpoint_and_artifact(tmp_path):
    """A checkpoint claiming a completed phase whose own artifact was written under a
    different task_id is exactly the false completed-phase claim Part 3 step 8 guards
    against: the artifact on disk does not actually belong to this checkpoint's run."""
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    # scope.json genuinely belongs to a different task_id than the checkpoint claims.
    mismatched_path = evidence_io.promote_canonical(
        run_directory, "scope.json", _scope_doc(task_id="T-OTHER", run_id=RUN_ID)
    )
    scope_ref = str(mismatched_path.resolve().relative_to(tmp_path.resolve())).replace("\\", "/")
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": scope_ref},
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "artifact_identity_mismatch"


def test_evaluate_resume_refuses_false_completed_phase_claim(tmp_path):
    """completed_phases claims research is done but artifact_refs has no research entry
    -- caught by validate_checkpoint_semantics before any per-artifact check runs."""
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    scope_ref = _promote_scope(run_directory, tmp_path)
    doc = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "target_repo_path": TARGET_REPO_PATH,
        "updated_at": UPDATED_AT, "completed_phases": ["discovery", "research"], "current_phase": "implementation",
        "next_phase": "verification", "artifact_refs": {"discovery": scope_ref}, "blockers": [],
        "next_resume_action": "dispatch the implementation phase", "status": "in_progress",
    }
    checkpoint.checkpoint_path(run_directory).write_text(json.dumps(doc), encoding="utf-8")
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is False
    assert decision.code == "invalid_checkpoint"
    assert "research" in decision.reason


def test_evaluate_resume_returns_resumable_for_a_genuinely_valid_checkpoint(tmp_path):
    _make_fixture_repo(tmp_path)
    run_directory = _run_dir(tmp_path)
    scope_ref = _promote_scope(run_directory, tmp_path)
    checkpoint.record_phase_progress(
        run_directory, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at=UPDATED_AT, completed_phases=["discovery"], artifact_refs={"discovery": scope_ref},
    )
    decision = checkpoint.evaluate_resume(run_directory, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable is True
    assert decision.code == "ok"
    assert decision.completed_phases == ["discovery"]
    assert decision.next_phase == "research"
    assert decision.task_id == TASK_ID
    assert decision.target_repo_path == TARGET_REPO_PATH
    assert decision.docs["discovery"]["task_id"] == TASK_ID
