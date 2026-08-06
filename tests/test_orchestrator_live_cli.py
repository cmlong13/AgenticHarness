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
