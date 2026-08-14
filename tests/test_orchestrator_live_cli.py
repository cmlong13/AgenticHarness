"""Tests for harness/orchestrator/live_cli.py -- the thin deterministic live bridge.

These tests exercise live_cli's own thin layer: request parsing, dispatch, exit-code
convention, and delegation to the already-tested lower-level modules (discovery.py,
evidence_io.py, paths.py, harness/evidence.py). They deliberately do not re-derive
every schema/semantic/path rule those modules already cover in their own test files
(test_orchestrator_discovery.py, test_orchestrator_evidence_io.py,
test_artifact_contracts.py) -- only enough of a real call to prove live_cli calls
through correctly and reports the real result honestly, plus a couple of explicit
monkeypatch-based delegation proofs for the two most complex underlying checks
(scope semantics, path safety).

All file writes happen under a monkeypatched REPO_ROOT pointed at pytest's tmp_path,
so no test ever touches the real repository's runs/ directory. harness/schemas/ is
deliberately left unpatched (SCHEMA_DIR is computed once at import time from the real
REPO_ROOT) -- every test validates against the real, checked-in schemas.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.orchestrator import discovery, evidence_io, live_cli, paths


@pytest.fixture()
def repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(live_cli, "REPO_ROOT", tmp_path)
    return tmp_path


def _write(path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _run_main(repo_root, request_body: dict, capsys) -> tuple[int, dict]:
    request_path = repo_root / "scratch-request.json"
    request_path.write_text(json.dumps(request_body), encoding="utf-8")
    exit_code = live_cli.main(["--request-file", str(request_path)])
    captured = capsys.readouterr()
    # json.loads is whitespace-insensitive, so this handles both the single-line error
    # shape and the indent=2 pretty-printed success shape identically.
    response = json.loads(captured.out)
    return exit_code, response


_SCOPE_BASE = {
    "schema_version": "1.0",
    "task_id": "T-1",
    "run_id": "run-1",
    "created_at": "2026-08-03T12:00:00Z",
    "source": {"type": "prompt", "raw_prompt": "test"},
    "objective": "test objective",
    "in_scope": ["demo-repo/src/x.py"],
    "out_of_scope": [],
    "constraints": [],
    "acceptance_criteria": [{"id": "AC-1", "description": "criterion"}],
    "task_graph": [{"id": "N-1", "description": "node", "phase": "research", "depends_on": []}],
    "status": "approved",
}


def _findings_doc(*, extra_findings=None) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": "T-1",
        "run_id": "run-1",
        "created_at": "2026-08-03T12:00:00Z",
        "scope_ref": {"path": "runs/run-1/scope.json"},
        "findings": [
            {
                "id": "F-1",
                "claim": "x() returns y",
                "classification": "found",
                "evidence": [
                    {"path": "demo-repo/src/x.py", "start_line": 1, "end_line": 2, "evidence_tier": "executable_code"}
                ],
            },
            *(extra_findings or []),
        ],
    }


# ---------------------------------------------------------------------------
# operation 1: validate_scope
# ---------------------------------------------------------------------------


class TestValidateScope:
    def test_valid_candidate_is_valid(self, repo_root, capsys) -> None:
        (repo_root / "demo-repo").mkdir()
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "validate_scope",
                "expected_task_id": "T-1",
                "expected_run_id": "run-1",
                "target_repo_path": "demo-repo",
                "candidate": _SCOPE_BASE,
            },
            capsys,
        )
        assert exit_code == 0
        assert resp == {"operation": "validate_scope", "status": "valid"}

    def test_invalid_candidate_is_rejected(self, repo_root, capsys) -> None:
        (repo_root / "demo-repo").mkdir()
        bad = dict(_SCOPE_BASE, in_scope=["../outside.py"])
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "validate_scope",
                "expected_task_id": "T-1",
                "expected_run_id": "run-1",
                "target_repo_path": "demo-repo",
                "candidate": bad,
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert resp["errors"]

    def test_delegates_to_discovery_validate_scope_draft_not_reimplemented(
        self, repo_root, monkeypatch, capsys
    ) -> None:
        calls = []

        def fake_validate(draft, **kwargs):
            calls.append((draft, kwargs))
            return ["a sentinel error only the real discovery module would never produce"]

        monkeypatch.setattr(discovery, "validate_scope_draft", fake_validate)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "validate_scope",
                "expected_task_id": "T-1",
                "expected_run_id": "run-1",
                "target_repo_path": "demo-repo",
                "candidate": _SCOPE_BASE,
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["errors"] == ["a sentinel error only the real discovery module would never produce"]
        assert len(calls) == 1
        draft, kwargs = calls[0]
        assert draft == _SCOPE_BASE
        assert kwargs["expected_task_id"] == "T-1"
        assert kwargs["expected_run_id"] == "run-1"
        assert kwargs["target_repo_path"] == "demo-repo"


# ---------------------------------------------------------------------------
# operation 2: retain_attempt
# ---------------------------------------------------------------------------


class TestRetainAttempt:
    def test_raw_evidence_exists_before_any_validation(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "retain_attempt",
                "run_id": "run-1",
                "phase": "research",
                "attempt_n": 1,
                "raw_text": "not even valid json -- this must still be retained verbatim",
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "retained"
        raw_path = repo_root / resp["path"]
        assert raw_path.read_text(encoding="utf-8") == "not even valid json -- this must still be retained verbatim"

    def test_collision_is_blocked_and_original_evidence_survives(self, repo_root, capsys) -> None:
        _run_main(
            repo_root,
            {"operation": "retain_attempt", "run_id": "run-1", "phase": "research", "attempt_n": 1, "raw_text": "first"},
            capsys,
        )
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "retain_attempt", "run_id": "run-1", "phase": "research", "attempt_n": 1, "raw_text": "second"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert (repo_root / "runs" / "run-1" / "attempts" / "research-1.raw.txt").read_text(
            encoding="utf-8"
        ) == "first"


# ---------------------------------------------------------------------------
# operations 3 + 4: validate_artifact / promote_artifact
# ---------------------------------------------------------------------------


class TestValidateAndPromoteFindings:
    def test_valid_findings_can_be_promoted(self, repo_root, capsys) -> None:
        doc = _findings_doc()
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "validate_artifact", "phase": "research", "doc": doc},
            capsys,
        )
        assert exit_code == 0
        assert resp == {"operation": "validate_artifact", "status": "valid"}

        exit_code, resp = _run_main(
            repo_root,
            {"operation": "promote_artifact", "run_id": "run-1", "phase": "research", "doc": doc},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"
        on_disk = json.loads((repo_root / resp["path"]).read_text(encoding="utf-8"))
        assert on_disk == doc

    def test_malformed_findings_is_retained_but_not_promoted(self, repo_root, capsys) -> None:
        malformed_raw = '{"findings": [}'  # invalid JSON on purpose -- retained as raw text only

        exit_code, resp = _run_main(
            repo_root,
            {"operation": "retain_attempt", "run_id": "run-1", "phase": "research", "attempt_n": 1, "raw_text": malformed_raw},
            capsys,
        )
        assert exit_code == 0
        raw_path = repo_root / resp["path"]
        assert raw_path.exists()

        # Schema-invalid once parsed as an object (missing every required field).
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "promote_artifact", "run_id": "run-1", "phase": "research", "doc": {}},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert not (repo_root / "runs" / "run-1" / "findings.json").exists()
        # The raw candidate survives regardless of the promotion outcome.
        assert raw_path.exists()

    def test_semantic_validation_failure_blocks_promotion(self, repo_root, capsys) -> None:
        # Schema-valid, but an 'inferred' finding cites a 'not_found' finding as support --
        # forbidden by validate_findings_semantics, never checked by JSON Schema itself.
        doc = _findings_doc(
            extra_findings=[
                {
                    "id": "F-2",
                    "claim": "nothing found here",
                    "classification": "not_found",
                    "search_attempts": [{"method": "grep", "query": "q", "scope": "s", "result": "no matches"}],
                },
                {
                    "id": "F-3",
                    "claim": "probably true",
                    "classification": "inferred",
                    "supporting_finding_ids": ["F-2"],
                    "reasoning": "inferring from a not_found finding, which is forbidden",
                },
            ]
        )
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "promote_artifact", "run_id": "run-1", "phase": "research", "doc": doc},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert any("not_found" in e or "'found'" in e for e in resp["errors"])
        assert not (repo_root / "runs" / "run-1" / "findings.json").exists()

    def test_canonical_collision_remains_blocked(self, repo_root, capsys) -> None:
        doc = _findings_doc()
        first_exit, first = _run_main(
            repo_root, {"operation": "promote_artifact", "run_id": "run-1", "phase": "research", "doc": doc}, capsys
        )
        assert first_exit == 0
        assert first["status"] == "promoted"

        second_exit, second = _run_main(
            repo_root, {"operation": "promote_artifact", "run_id": "run-1", "phase": "research", "doc": doc}, capsys
        )
        assert second_exit == 1
        assert second["status"] == "blocked"
        # The original canonical artifact is never silently overwritten.
        on_disk = json.loads((repo_root / "runs" / "run-1" / "findings.json").read_text(encoding="utf-8"))
        assert on_disk == doc

    def test_context_ref_resolution_for_verification_phase(self, repo_root, capsys) -> None:
        """Proves the generic phase-dispatch path also works for a phase whose semantic
        validator needs external context (scope_doc), not just findings (which needs none)."""
        scope_path = repo_root / "runs" / "run-1" / "scope.json"
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        scope_path.write_text(json.dumps(_SCOPE_BASE), encoding="utf-8")

        verification_doc = {
            "schema_version": "1.0",
            "task_id": "T-1",
            "run_id": "run-1",
            "created_at": "2026-08-03T12:00:00Z",
            "scope_ref": {"path": "runs/run-1/scope.json"},
            "implementation_ref": {"path": "runs/run-1/implementation-report.json"},
            "final_verdict": "blocked",
            "blocked_reason": "smoke test -- not a real verification",
        }
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact",
                "run_id": "run-1",
                "phase": "verification",
                "doc": verification_doc,
                "context_refs": {"scope": "runs/run-1/scope.json"},
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"


# ---------------------------------------------------------------------------
# operation 5: build_path_attestation
# ---------------------------------------------------------------------------


class TestBuildPathAttestation:
    def test_valid_target_repo_path_delegates_to_paths_module(self, repo_root, monkeypatch, capsys) -> None:
        sentinel = {"validated_by": "controlled_caller", "symlink_escape_checked": True, "marker": "from-paths-py"}
        captured_args = {}

        def fake_build(target_repo_path, *, repo_root=None):
            captured_args["target_repo_path"] = target_repo_path
            captured_args["repo_root"] = repo_root
            return sentinel

        monkeypatch.setattr(paths, "build_path_validation_attestation", fake_build)
        exit_code, resp = _run_main(
            repo_root, {"operation": "build_path_attestation", "target_repo_path": "demo-repo"}, capsys
        )
        assert exit_code == 0
        assert resp["attestation"] == sentinel
        assert captured_args["target_repo_path"] == "demo-repo"

    def test_invalid_target_repo_path_reports_the_real_path_safety_error(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root, {"operation": "build_path_attestation", "target_repo_path": "../escape"}, capsys
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert resp["code"] == "path_traversal"


# ---------------------------------------------------------------------------
# operation 6: check_command_identity
# ---------------------------------------------------------------------------


class TestCheckCommandIdentity:
    _REQUEST = {
        "task_id": "T-1", "run_id": "run-1", "command_id": "C-1",
        "command": "python -m pytest tests/unit/test_x.py -q", "working_directory": "demo-repo",
    }

    def test_matching_identity_is_reported_as_match(self, repo_root, capsys) -> None:
        result = dict(self._REQUEST, exit_code=0, output_ref="runs/run-1/logs/C-1.log")
        exit_code, resp = _run_main(
            repo_root, {"operation": "check_command_identity", "request": self._REQUEST, "result": result}, capsys
        )
        assert exit_code == 0
        assert resp == {"operation": "check_command_identity", "status": "match"}

    def test_command_result_identity_mismatch_is_rejected(self, repo_root, capsys) -> None:
        result = dict(self._REQUEST, command_id="C-2", exit_code=0, output_ref="runs/run-1/logs/C-1.log")
        exit_code, resp = _run_main(
            repo_root, {"operation": "check_command_identity", "request": self._REQUEST, "result": result}, capsys
        )
        assert exit_code == 1
        assert resp["status"] == "mismatch"
        assert resp["mismatched_fields"] == ["command_id"]


# ---------------------------------------------------------------------------
# operation 7: retain_rejection
# ---------------------------------------------------------------------------


class TestRetainRejection:
    def test_rejection_is_retained(self, repo_root, capsys) -> None:
        rejection = {"message_type": "command_rejected", "command_id": "C-1", "rejection_reason": "grammar_no_match"}
        exit_code, resp = _run_main(
            repo_root, {"operation": "retain_rejection", "run_id": "run-1", "rejection": rejection}, capsys
        )
        assert exit_code == 0
        on_disk = json.loads((repo_root / resp["path"]).read_text(encoding="utf-8"))
        assert on_disk == rejection


# ---------------------------------------------------------------------------
# operation 8: retain_policy_event
# ---------------------------------------------------------------------------


class TestRetainPolicyEvent:
    def test_mutation_sentinel_125_can_be_retained_as_a_policy_event(self, repo_root, capsys) -> None:
        payload = {"command_id": "C-1", "exit_code": 125, "command": "python -m pytest x.py"}
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "retain_policy_event", "run_id": "run-1", "kind": "mutation_detected", "payload": payload},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "retained"
        line = (repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl").read_text(encoding="utf-8").strip()
        event = json.loads(line)
        assert event["kind"] == "mutation_detected"
        assert event["exit_code"] == 125


# ---------------------------------------------------------------------------
# operation 9: write_run_summary
# ---------------------------------------------------------------------------


class TestWriteRunSummary:
    _VALID_SUMMARY = {
        "schema_version": "1.0",
        "task_id": "T-1",
        "run_id": "run-1",
        "created_at": "2026-08-03T12:00:00Z",
        "objective_summary": "smoke test summary",
        "artifact_refs": {"scope": "runs/run-1/scope.json"},
        "final_verdict": "blocked",
        "phases_completed": ["discovery"],
    }

    def test_valid_run_summary_is_written(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root, {"operation": "write_run_summary", "run_id": "run-1", "summary": self._VALID_SUMMARY}, capsys
        )
        assert exit_code == 0
        assert resp["status"] == "written"
        on_disk = json.loads((repo_root / resp["path"]).read_text(encoding="utf-8"))
        assert on_disk == self._VALID_SUMMARY

    def test_invalid_run_summary_is_rejected(self, repo_root, capsys) -> None:
        bad = dict(self._VALID_SUMMARY)
        del bad["objective_summary"]
        exit_code, resp = _run_main(
            repo_root, {"operation": "write_run_summary", "run_id": "run-1", "summary": bad}, capsys
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert resp["errors"]
        assert not (repo_root / "runs" / "run-1" / "run-summary.json").exists()

    def test_final_verdict_can_be_derived_from_state_instead_of_restated(self, repo_root, capsys) -> None:
        summary_without_verdict = dict(self._VALID_SUMMARY)
        del summary_without_verdict["final_verdict"]
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "write_run_summary",
                "run_id": "run-1",
                "summary": summary_without_verdict,
                "state": "research_blocked",
            },
            capsys,
        )
        assert exit_code == 0
        on_disk = json.loads((repo_root / resp["path"]).read_text(encoding="utf-8"))
        # Must match harness.orchestrator.state.FINAL_VERDICT_BY_STATE[State.RESEARCH_BLOCKED]
        # exactly -- proving live_cli reused that table instead of restating "blocked" itself.
        assert on_disk["final_verdict"] == "blocked"


# ---------------------------------------------------------------------------
# CLI-level error handling (never a bare exception, always JSON + nonzero exit)
# ---------------------------------------------------------------------------


class TestCliErrorHandling:
    def test_malformed_request_json_produces_machine_readable_error(self, repo_root, capsys) -> None:
        request_path = repo_root / "bad-request.json"
        request_path.write_text("{not valid json", encoding="utf-8")
        exit_code = live_cli.main(["--request-file", str(request_path)])
        captured = capsys.readouterr()
        assert exit_code == 2
        resp = json.loads(captured.out.strip())
        assert resp["status"] == "error"
        assert "error" in resp

    def test_unrecognized_operation_produces_machine_readable_error(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(repo_root, {"operation": "not_a_real_operation"}, capsys)
        assert exit_code == 2
        assert resp["status"] == "error"

    def test_missing_request_file_flag_still_prints_json(self, repo_root, capsys) -> None:
        exit_code = live_cli.main([])
        captured = capsys.readouterr()
        assert exit_code == 2
        resp = json.loads(captured.out.strip())
        assert resp["status"] == "error"

    def test_nonexistent_request_file_produces_machine_readable_error(self, repo_root, capsys) -> None:
        exit_code = live_cli.main(["--request-file", str(repo_root / "does-not-exist.json")])
        captured = capsys.readouterr()
        assert exit_code == 2
        resp = json.loads(captured.out.strip())
        assert resp["status"] == "error"

    def test_nonzero_exit_code_on_deterministic_negative_result(self, repo_root, capsys) -> None:
        (repo_root / "demo-repo").mkdir()
        bad = dict(_SCOPE_BASE, in_scope=["../outside.py"])
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "validate_scope",
                "expected_task_id": "T-1",
                "expected_run_id": "run-1",
                "target_repo_path": "demo-repo",
                "candidate": bad,
            },
            capsys,
        )
        assert exit_code != 0
        assert resp["status"] == "invalid"


# ---------------------------------------------------------------------------
# operations 10 + 11: write_checkpoint / evaluate_resume
# ---------------------------------------------------------------------------


class TestWriteCheckpoint:
    def test_progress_checkpoint_is_written(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "write_checkpoint", "kind": "progress", "run_id": "run-1", "task_id": "T-1",
                "target_repo_path": "demo-repo", "completed_phases": ["discovery"],
                "artifact_refs": {"discovery": "runs/run-1/scope.json"},
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "written"
        doc = json.loads((repo_root / resp["path"]).read_text(encoding="utf-8"))
        assert doc["status"] == "in_progress"
        assert doc["current_phase"] == "research"
        assert doc["target_repo_path"] == "demo-repo"

    def test_unrecognized_kind_is_a_usage_error(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "write_checkpoint", "kind": "not-a-real-kind", "run_id": "run-1", "task_id": "T-1",
                "target_repo_path": "demo-repo",
            },
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"

    def test_invalid_resulting_document_is_blocked_not_written(self, repo_root, capsys) -> None:
        # completed_phases claims "research" complete with no artifact_refs entry for it
        # -- a false completed-phase claim; the write must be refused, not written.
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "write_checkpoint", "kind": "progress", "run_id": "run-1", "task_id": "T-1",
                "target_repo_path": "demo-repo", "completed_phases": ["discovery", "research"],
                "artifact_refs": {"discovery": "runs/run-1/scope.json"},
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert not (repo_root / "runs" / "run-1" / "checkpoint.json").exists()

    def test_completion_checkpoint_covers_all_four_phases(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "write_checkpoint", "kind": "completion", "run_id": "run-1", "task_id": "T-1",
                "target_repo_path": "demo-repo",
                "artifact_refs": {
                    "discovery": "runs/run-1/scope.json", "research": "runs/run-1/findings.json",
                    "implementation": "runs/run-1/implementation-report.json",
                    "verification": "runs/run-1/verification-report.json",
                },
            },
            capsys,
        )
        assert exit_code == 0
        doc = json.loads((repo_root / resp["path"]).read_text(encoding="utf-8"))
        assert doc["status"] == "complete"
        assert doc["current_phase"] is None


class TestEvaluateResume:
    def test_missing_checkpoint_is_refused(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(repo_root, {"operation": "evaluate_resume", "run_id": "run-1"}, capsys)
        assert exit_code == 1
        assert resp["status"] == "refused"
        assert resp["code"] == "no_checkpoint"

        events_path = repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl"
        kinds = [json.loads(line)["kind"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        assert kinds == ["resume_requested", "resume_refused"]

    def test_valid_checkpoint_is_resumable(self, repo_root, capsys) -> None:
        (repo_root / "demo-repo").mkdir()
        _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "discovery",
                "expected_task_id": "T-1", "expected_run_id": "run-1", "target_repo_path": "demo-repo",
                "doc": _SCOPE_BASE,
            },
            capsys,
        )
        _run_main(
            repo_root,
            {
                "operation": "write_checkpoint", "kind": "progress", "run_id": "run-1", "task_id": "T-1",
                "target_repo_path": "demo-repo", "completed_phases": ["discovery"],
                "artifact_refs": {"discovery": "runs/run-1/scope.json"},
            },
            capsys,
        )

        exit_code, resp = _run_main(repo_root, {"operation": "evaluate_resume", "run_id": "run-1"}, capsys)
        assert exit_code == 0
        assert resp["status"] == "resumable"
        assert resp["task_id"] == "T-1"
        assert resp["target_repo_path"] == "demo-repo"
        assert resp["completed_phases"] == ["discovery"]
        assert resp["next_phase"] == "research"

        events_path = repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl"
        kinds = [json.loads(line)["kind"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        assert kinds == ["checkpoint_written", "resume_requested", "checkpoint_validated"]


# ---------------------------------------------------------------------------
# operations 12-15: load_memory / append_memory / record_memory_applied / summarize_memory
# ---------------------------------------------------------------------------


def _memory_fact(**overrides) -> dict:
    base = {
        "id": "FACT-0001",
        "content": "The test-runner Skill must run forked (context: fork) or disallowed-tools leak into a resumed Engineer.",
        "source_run_id": "run-source-1",
        "evidence_ref": "runs/run-source-1/logs/policy-events.jsonl",
        "recorded_at": "2026-08-06",
        "status": "confirmed",
        "tags": ["test-runner", "fork"],
    }
    base.update(overrides)
    return base


def _memory_lesson(**overrides) -> dict:
    base = {
        "id": "L-0001",
        "text": "Strict JSON transport failures must be classified and repaired before semantic validation, within a bounded correction budget.",
        "source_run_id": "run-source-1",
        "evidence_ref": "runs/run-source-1/logs/policy-events.jsonl",
        "date": "2026-08-06",
        "tags": ["transport", "validation"],
    }
    base.update(overrides)
    return base


def _seed_source_evidence(repo_root) -> None:
    evidence_dir = repo_root / "runs" / "run-source-1" / "logs"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "policy-events.jsonl").write_text('{"kind": "transport_parse_failure"}\n', encoding="utf-8")


class TestLoadMemory:
    def test_empty_memory_returns_honest_empty_result_and_retains_memory_loaded(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root, {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "fix a typo"}, capsys
        )
        assert exit_code == 0
        assert resp["status"] == "ok"
        assert resp["valid_fact_count"] == 0
        assert resp["relevant_facts"] == []
        assert resp["relevant_lessons"] == []

        events_path = repo_root / "runs" / "run-b" / "logs" / "policy-events.jsonl"
        kinds = [json.loads(line)["kind"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        assert kinds == ["memory_loaded"]

    def test_relevant_entries_are_selected_by_deterministic_keyword_match(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(
            repo_root,
            {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()},
            capsys,
        )
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "the test-runner fork isolation is broken again"},
            capsys,
        )
        assert exit_code == 0
        assert [f["id"] for f in resp["relevant_facts"]] == ["FACT-0001"]

    def test_load_memory_can_run_before_any_scope_artifact_exists(self, repo_root, capsys) -> None:
        """Demonstrates memory loading has no dependency on Discovery having run yet --
        it can be, and per work/SKILL.md must be, called before Discovery."""
        exit_code, resp = _run_main(
            repo_root, {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "anything at all"}, capsys
        )
        assert exit_code == 0
        assert not (repo_root / "runs" / "run-b" / "scope.json").exists()


class TestAppendMemory:
    def test_append_fact_success(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "appended"
        assert (repo_root / "memory" / "facts.jsonl").exists()

    def test_append_fact_duplicate_is_a_well_formed_negative_result(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(repo_root, {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()}, capsys)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact(id="FACT-9999")},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "duplicate"

    def test_append_lesson_candidates_capped_at_five(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        candidates = [
            _memory_lesson(id=f"L-{n:04d}", text=f"Lesson {n} about harness orchestration validation ordering.")
            for n in range(7)
        ]
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "append_memory", "kind": "lesson", "run_id": "run-a", "candidates": candidates},
            capsys,
        )
        assert exit_code == 0
        assert len(resp["appended"]) == 5
        assert len(resp["skipped"]) == 2

        events_path = repo_root / "runs" / "run-a" / "logs" / "policy-events.jsonl"
        kinds = [json.loads(line)["kind"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        assert "memory_lessons_append" in kinds


class TestRecordMemoryApplied:
    def test_valid_applied_claim_is_retained(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(repo_root, {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()}, capsys)
        # Audit 1: entry_id must have been selected relevant by THIS run's own load_memory call.
        _run_main(
            repo_root, {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "the test-runner fork isolation is broken"}, capsys
        )

        run_b_scope = repo_root / "runs" / "run-b" / "scope.json"
        run_b_scope.parent.mkdir(parents=True, exist_ok=True)
        run_b_scope.write_text('{"constraints": ["forked test-runner required, per FACT-0001"]}', encoding="utf-8")

        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "record_memory_applied", "run_id": "run-b",
                "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
                "current_run_id": "run-b", "phase": "discovery",
                "decision": "Scope constraints require the forked test-runner Skill, per FACT-0001.",
                "evidence_path": "runs/run-b/scope.json",
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "retained"

    def test_missing_decision_evidence_is_blocked_not_fabricated(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(repo_root, {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()}, capsys)

        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "record_memory_applied", "run_id": "run-b",
                "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
                "current_run_id": "run-b", "phase": "discovery", "decision": "x",
                "evidence_path": "runs/run-b/does-not-exist.json",
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"

        events_path = repo_root / "runs" / "run-b" / "logs" / "policy-events.jsonl"
        kinds = [json.loads(line)["kind"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        assert kinds == ["memory_applied_rejected"]  # never "memory_applied"


class TestSummarizeMemory:
    def test_loaded_only_does_not_report_influenced(self, repo_root, capsys) -> None:
        _run_main(repo_root, {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "anything"}, capsys)
        exit_code, resp = _run_main(repo_root, {"operation": "summarize_memory", "run_id": "run-b"}, capsys)
        assert exit_code == 0
        assert resp["memory_loaded"] is True
        assert resp["memory_influenced_run"] is False
        assert resp["memory_refs_used"] == []

    def test_loaded_and_applied_reports_influenced_with_refs(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(repo_root, {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()}, capsys)
        _run_main(repo_root, {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "test-runner fork"}, capsys)

        scope_path = repo_root / "runs" / "run-b" / "scope.json"
        scope_path.write_text('{"constraints": ["applies FACT-0001"]}', encoding="utf-8")
        _run_main(
            repo_root,
            {
                "operation": "record_memory_applied", "run_id": "run-b",
                "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
                "current_run_id": "run-b", "phase": "discovery", "decision": "used the fact",
                "evidence_path": "runs/run-b/scope.json",
            },
            capsys,
        )

        exit_code, resp = _run_main(repo_root, {"operation": "summarize_memory", "run_id": "run-b"}, capsys)
        assert exit_code == 0
        assert resp["memory_loaded"] is True
        assert resp["memory_influenced_run"] is True
        assert resp["memory_refs_used"] == ["FACT-0001"]


class TestRecordMemoryAppliedRunScopedAndEvidenceCites:
    """Integration-level proof (through the real live_cli dispatch, not memory.py
    directly) that op_record_memory_applied enforces both Audit 1 (run-scoped
    selection) and Audit 2 (evidence must cite the entry)."""

    def test_globally_valid_but_not_selected_entry_is_blocked(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(repo_root, {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()}, capsys)
        # load_memory with a prompt that shares no vocabulary with the fact's tags/content.
        _run_main(repo_root, {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "unrelated loan underwriting policy text"}, capsys)

        scope_path = repo_root / "runs" / "run-b" / "scope.json"
        scope_path.write_text('{"constraints": ["cites FACT-0001"]}', encoding="utf-8")
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "record_memory_applied", "run_id": "run-b",
                "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
                "current_run_id": "run-b", "phase": "discovery", "decision": "x",
                "evidence_path": "runs/run-b/scope.json",
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert any("was not selected as relevant" in e for e in resp["errors"])

    def test_no_load_memory_call_at_all_blocks_application(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(repo_root, {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()}, capsys)
        # run-b never calls load_memory at all.
        scope_path = repo_root / "runs" / "run-b" / "scope.json"
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        scope_path.write_text('{"constraints": ["cites FACT-0001"]}', encoding="utf-8")
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "record_memory_applied", "run_id": "run-b",
                "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
                "current_run_id": "run-b", "phase": "discovery", "decision": "x",
                "evidence_path": "runs/run-b/scope.json",
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert any("no memory_loaded event found" in e for e in resp["errors"])

    def test_evidence_file_that_does_not_cite_the_id_is_blocked(self, repo_root, capsys) -> None:
        _seed_source_evidence(repo_root)
        _run_main(repo_root, {"operation": "append_memory", "kind": "fact", "run_id": "run-a", "fact": _memory_fact()}, capsys)
        _run_main(repo_root, {"operation": "load_memory", "run_id": "run-b", "raw_prompt": "test-runner fork"}, capsys)

        scope_path = repo_root / "runs" / "run-b" / "scope.json"
        scope_path.write_text('{"objective": "totally unrelated content"}', encoding="utf-8")
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "record_memory_applied", "run_id": "run-b",
                "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
                "current_run_id": "run-b", "phase": "discovery", "decision": "x",
                "evidence_path": "runs/run-b/scope.json",
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert any("does not contain entry_id" in e for e in resp["errors"])


# ---------------------------------------------------------------------------
# build_usage_summary (usage/cost-accounting milestone, 2026-08-14)
# ---------------------------------------------------------------------------


class TestBuildUsageSummary:
    def test_aggregates_a_real_captured_agent_record(self, repo_root, capsys) -> None:
        from harness.orchestrator import usage

        run_directory = repo_root / "runs" / "run-1"
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": "research", "subagent_type": "architect", "agent_id": "agent-1", "dispatch_sequence": 1},
        )
        (repo_root / "harness").mkdir(parents=True, exist_ok=True)
        real_pricing = Path(__file__).resolve().parent.parent / "harness" / "model-pricing.json"
        (repo_root / "harness" / "model-pricing.json").write_text(real_pricing.read_text(encoding="utf-8"), encoding="utf-8")

        transcript_path = repo_root / "t.jsonl"
        transcript_path.write_text(
            json.dumps({
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-5",
                    "usage": {
                        "input_tokens": 2, "output_tokens": 10, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
                    },
                },
            }) + "\n",
            encoding="utf-8",
        )
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "agent-1", "agent_transcript_path": str(transcript_path)},
            repo_root=repo_root,
        )

        exit_code, resp = _run_main(repo_root, {"operation": "build_usage_summary", "run_id": "run-1"}, capsys)
        assert exit_code == 0
        assert resp["status"] == "written"
        summary_path = repo_root / "runs" / "run-1" / "usage-summary.json"
        assert summary_path.is_file()
        doc = json.loads(summary_path.read_text(encoding="utf-8"))
        assert doc["subagent_subtotal"]["agent_count"] == 1
        assert doc["coverage_status"] == "partial"
        assert doc["full_pipeline_total"] is None

    def test_empty_run_still_writes_a_zeroed_summary(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(repo_root, {"operation": "build_usage_summary", "run_id": "run-empty"}, capsys)
        assert exit_code == 0
        doc = json.loads((repo_root / "runs" / "run-empty" / "usage-summary.json").read_text(encoding="utf-8"))
        assert doc["subagent_subtotal"]["agent_count"] == 0

    def test_missing_run_id_is_a_usage_error(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(repo_root, {"operation": "build_usage_summary"}, capsys)
        assert exit_code == 2
        assert resp["status"] == "error"


# ---------------------------------------------------------------------------
# run-summary integration: usage_summary_ref is optional and schema-valid
# ---------------------------------------------------------------------------


class TestUsageSummaryRefOnRunSummary:
    def test_run_summary_accepts_optional_usage_summary_ref(self, repo_root, capsys) -> None:
        summary = {
            "schema_version": "1.0", "task_id": "T-1", "run_id": "run-1",
            "created_at": "2026-08-14T00:00:00Z", "objective_summary": "x",
            "artifact_refs": {"scope": "runs/run-1/scope.json"},
            "final_verdict": "pass", "phases_completed": ["discovery"],
            "usage_summary_ref": "runs/run-1/usage-summary.json",
        }
        exit_code, resp = _run_main(
            repo_root, {"operation": "write_run_summary", "run_id": "run-1", "summary": summary}, capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "written"
        doc = json.loads((repo_root / "runs" / "run-1" / "run-summary.json").read_text(encoding="utf-8"))
        assert doc["usage_summary_ref"] == "runs/run-1/usage-summary.json"

    def test_run_summary_without_usage_summary_ref_still_valid(self, repo_root, capsys) -> None:
        summary = {
            "schema_version": "1.0", "task_id": "T-1", "run_id": "run-1",
            "created_at": "2026-08-14T00:00:00Z", "objective_summary": "x",
            "artifact_refs": {"scope": "runs/run-1/scope.json"},
            "final_verdict": "pass", "phases_completed": ["discovery"],
        }
        exit_code, resp = _run_main(
            repo_root, {"operation": "write_run_summary", "run_id": "run-1", "summary": summary}, capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "written"


class TestReconcileQuarantinedUsage:
    def test_reconciles_a_real_quarantined_record_via_live_cli(self, repo_root, capsys) -> None:
        from harness.orchestrator import usage

        (repo_root / "harness").mkdir(parents=True, exist_ok=True)
        real_pricing = Path(__file__).resolve().parent.parent / "harness" / "model-pricing.json"
        (repo_root / "harness" / "model-pricing.json").write_text(real_pricing.read_text(encoding="utf-8"), encoding="utf-8")

        transcript_path = repo_root / "t.jsonl"
        transcript_path.write_text(
            json.dumps({
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-5",
                    "usage": {
                        "input_tokens": 2, "output_tokens": 10, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
                    },
                },
            }) + "\n",
            encoding="utf-8",
        )
        # Simulates the real observed ordering: SubagentStop-driven capture happens
        # before the orchestrator retains the agent_dispatch event.
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "agent-1", "agent_transcript_path": str(transcript_path)},
            repo_root=repo_root,
        )
        assert (repo_root / "runs" / usage.UNMATCHED_USAGE_DIRNAME).is_dir()

        run_directory = repo_root / "runs" / "run-1"
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": "research", "subagent_type": "architect", "agent_id": "agent-1", "dispatch_sequence": 1},
        )

        exit_code, resp = _run_main(repo_root, {"operation": "reconcile_quarantined_usage", "agent_id": "agent-1"}, capsys)
        assert exit_code == 0
        assert resp["status"] == "captured"
        assert resp["run_id"] == "run-1"
        assert usage.usage_record_path(run_directory, "agent-1").is_file()

    def test_missing_agent_id_is_a_usage_error(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(repo_root, {"operation": "reconcile_quarantined_usage"}, capsys)
        assert exit_code == 2

    def test_no_quarantine_record_reports_honestly(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root, {"operation": "reconcile_quarantined_usage", "agent_id": "never-seen"}, capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "no_quarantine_record"
