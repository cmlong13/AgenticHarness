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

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import pytest

from harness.orchestrator import core, discovery, evidence_io, github, jira_connector, live_cli, paths


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

    def test_findings_with_valid_obsidian_reads_promotes_and_round_trips(self, repo_root, capsys) -> None:
        """A representative real Architect response that directly used its two
        mcp__obsidian__* tools -- the exact obsidian_reads shape architect.md's "Obsidian
        docs connector" section promises -- must pass through the SAME validate_artifact /
        promote_artifact path an ordinary response does, and survive on disk unchanged."""
        doc = _findings_doc()
        doc["obsidian_reads"] = [
            {
                "tool": "mcp__obsidian__search_notes",
                "query_or_note_path": "pagination design decisions",
                "status": "no_matches",
            },
            {
                "tool": "mcp__obsidian__read_note",
                "query_or_note_path": "design/pagination-decisions.md",
                "status": "read",
                "note_path": "design/pagination-decisions.md",
                "content_sha256": "a" * 64,
            },
        ]

        exit_code, resp = _run_main(
            repo_root, {"operation": "validate_artifact", "phase": "research", "doc": doc}, capsys,
        )
        assert exit_code == 0
        assert resp == {"operation": "validate_artifact", "status": "valid"}

        exit_code, resp = _run_main(
            repo_root, {"operation": "promote_artifact", "run_id": "run-1", "phase": "research", "doc": doc}, capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"
        on_disk = json.loads((repo_root / resp["path"]).read_text(encoding="utf-8"))
        assert on_disk == doc
        assert on_disk["obsidian_reads"] == doc["obsidian_reads"]

    def test_findings_with_unknown_obsidian_tool_is_rejected_at_promotion(self, repo_root, capsys) -> None:
        # A fabricated/unknown MCP tool name must never slip through the real promotion
        # path -- only the two exact read-only Obsidian tools are ever legitimate.
        doc = _findings_doc()
        doc["obsidian_reads"] = [
            {"tool": "mcp__obsidian__write_note", "query_or_note_path": "q", "status": "no_matches"}
        ]

        exit_code, resp = _run_main(
            repo_root, {"operation": "promote_artifact", "run_id": "run-1", "phase": "research", "doc": doc}, capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert not (repo_root / "runs" / "run-1" / "findings.json").exists()

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
# operation 4 (extended): promote_artifact -- same-run logic-repair round promotion
# ---------------------------------------------------------------------------


def _implementation_report_doc(*, blocked_reason="smoke test -- not a real implementation") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": "T-1",
        "run_id": "run-1",
        "created_at": "2026-08-03T12:00:00Z",
        "scope_ref": {"path": "runs/run-1/scope.json"},
        "status": "blocked",
        "blocked_reason": blocked_reason,
        "dependency_changes": [],
    }


def _verification_report_doc(*, blocked_reason="smoke test -- not a real verification") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": "T-1",
        "run_id": "run-1",
        "created_at": "2026-08-03T12:00:00Z",
        "scope_ref": {"path": "runs/run-1/scope.json"},
        "implementation_ref": {"path": "runs/run-1/implementation-report.json"},
        "final_verdict": "blocked",
        "blocked_reason": blocked_reason,
    }


def _seed_scope_and_findings(repo_root) -> None:
    scope_path = repo_root / "runs" / "run-1" / "scope.json"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(json.dumps(_SCOPE_BASE), encoding="utf-8")
    findings_path = repo_root / "runs" / "run-1" / "findings.json"
    findings_path.write_text(json.dumps(_findings_doc()), encoding="utf-8")


class TestPromoteArtifactRepairRound:
    """Covers op_promote_artifact's `repair_attempt` extension -- the production fix for
    the gap discovered live in run-20260917-logicrepair-002: the live bridge had no way
    to promote a same-run logic-repair round's implementation/verification artifact to
    its own distinctly-named path, unlike core.py's already-tested
    _handle_verification_failure (tests/test_orchestrator_core.py). See PROJECT_SPEC.md
    for the full account of the gap and the fix."""

    # 1. ordinary canonical promotions remain unchanged (omitting repair_attempt) -------

    def test_ordinary_implementation_promotion_is_unaffected(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": _implementation_report_doc(),
                "context_refs": {"findings": "runs/run-1/findings.json"},
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"
        assert resp["path"] == "runs/run-1/implementation-report.json"

    def test_ordinary_verification_promotion_is_unaffected(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "verification",
                "doc": _verification_report_doc(),
                "context_refs": {"scope": "runs/run-1/scope.json"},
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"
        assert resp["path"] == "runs/run-1/verification-report.json"

    # 2/3. repair attempt 1 promotes the intended distinct repair artifact -------------

    def test_implementation_repair_1_promotion_succeeds(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": _implementation_report_doc(blocked_reason="repair round"),
                "context_refs": {"findings": "runs/run-1/findings.json"},
                "repair_attempt": 1,
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"
        assert resp["path"] == "runs/run-1/implementation-report.repair-1.json"

    def test_verification_repair_1_promotion_succeeds(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "verification",
                "doc": _verification_report_doc(blocked_reason="repair round"),
                "context_refs": {"scope": "runs/run-1/scope.json"},
                "repair_attempt": 1,
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"
        assert resp["path"] == "runs/run-1/verification-report.repair-1.json"

    # 4. the original canonical artifact is never touched by a repair promotion --------

    def test_original_canonical_artifact_untouched_by_repair_promotion(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        original = _implementation_report_doc(blocked_reason="original, pre-repair")
        first_exit, _ = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": original, "context_refs": {"findings": "runs/run-1/findings.json"},
            },
            capsys,
        )
        assert first_exit == 0

        repaired = _implementation_report_doc(blocked_reason="repaired")
        second_exit, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": repaired, "context_refs": {"findings": "runs/run-1/findings.json"},
                "repair_attempt": 1,
            },
            capsys,
        )
        assert second_exit == 0

        original_on_disk = json.loads(
            (repo_root / "runs" / "run-1" / "implementation-report.json").read_text(encoding="utf-8")
        )
        assert original_on_disk == original
        repair_on_disk = json.loads(
            (repo_root / "runs" / "run-1" / "implementation-report.repair-1.json").read_text(encoding="utf-8")
        )
        assert repair_on_disk == repaired

    # 5. duplicate promotion to the same repair-round artifact is collision-rejected ---

    def test_duplicate_repair_promotion_is_collision_rejected(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        request = {
            "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
            "doc": _implementation_report_doc(), "context_refs": {"findings": "runs/run-1/findings.json"},
            "repair_attempt": 1,
        }
        first_exit, first = _run_main(repo_root, request, capsys)
        assert first_exit == 0
        assert first["status"] == "promoted"

        second_exit, second = _run_main(repo_root, request, capsys)
        assert second_exit == 1
        assert second["status"] == "blocked"
        on_disk = json.loads(
            (repo_root / "runs" / "run-1" / "implementation-report.repair-1.json").read_text(encoding="utf-8")
        )
        assert on_disk == _implementation_report_doc()

    # 6. invalid repair_attempt values are rejected as a caller usage error ------------

    @pytest.mark.parametrize("bad_value", [0, -1, "1", 1.5, True])
    def test_invalid_repair_attempt_values_are_rejected(self, repo_root, capsys, bad_value) -> None:
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": _implementation_report_doc(), "context_refs": {"findings": "runs/run-1/findings.json"},
                "repair_attempt": bad_value,
            },
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"
        assert list((repo_root / "runs" / "run-1").glob("implementation-report*")) == []

    def test_repair_attempt_above_max_logic_repair_attempts_is_rejected(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        too_high = core.MAX_LOGIC_REPAIR_ATTEMPTS + 1
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": _implementation_report_doc(), "context_refs": {"findings": "runs/run-1/findings.json"},
                "repair_attempt": too_high,
            },
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"
        assert not (repo_root / "runs" / "run-1" / f"implementation-report.repair-{too_high}.json").exists()

    # 7. no arbitrary filename/path can be injected through repair_attempt ------------

    def test_repair_attempt_cannot_carry_a_path_fragment(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": _implementation_report_doc(), "context_refs": {"findings": "runs/run-1/findings.json"},
                "repair_attempt": "../../etc/passwd",
            },
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"
        assert list((repo_root / "runs" / "run-1").glob("implementation-report*")) == []
        assert not (repo_root.parent / "etc" / "passwd").exists()

    def test_promote_artifact_ignores_any_caller_supplied_filename_field(self, repo_root, capsys) -> None:
        """op_promote_artifact's request contract has no filename/path field at all --
        even a caller that tries to smuggle one through has it silently ignored, since
        the write path is always derived from `phase` (+ `repair_attempt`), never from
        request content."""
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": _implementation_report_doc(), "context_refs": {"findings": "runs/run-1/findings.json"},
                "filename": "../../outside.json",
                "path": "../../outside.json",
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["path"] == "runs/run-1/implementation-report.json"
        assert not (repo_root.parent / "outside.json").exists()

    # 8. repair_attempt is rejected for phases with no repair-round artifact -----------

    def test_repair_attempt_rejected_for_discovery(self, repo_root, capsys) -> None:
        (repo_root / "demo-repo").mkdir()
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "discovery",
                "expected_task_id": "T-1", "expected_run_id": "run-1", "target_repo_path": "demo-repo",
                "doc": _SCOPE_BASE, "repair_attempt": 1,
            },
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"
        assert not (repo_root / "runs" / "run-1" / "scope.json").exists()

    def test_repair_attempt_rejected_for_research(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "research",
                "doc": _findings_doc(), "repair_attempt": 1,
            },
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"
        assert not (repo_root / "runs" / "run-1" / "findings.json").exists()

    # 10. the repair-attempt bound is core.MAX_LOGIC_REPAIR_ATTEMPTS, not a second limit

    def test_repair_bound_matches_core_constant(self, repo_root, capsys) -> None:
        assert core.MAX_LOGIC_REPAIR_ATTEMPTS == 1
        _seed_scope_and_findings(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "implementation",
                "doc": _implementation_report_doc(), "context_refs": {"findings": "runs/run-1/findings.json"},
                "repair_attempt": core.MAX_LOGIC_REPAIR_ATTEMPTS,
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["path"] == "runs/run-1/implementation-report.repair-1.json"

    def test_live_cli_reuses_core_constant_directly(self) -> None:
        """Proves live_cli.py's bound is literally core.MAX_LOGIC_REPAIR_ATTEMPTS, not an
        independently-maintained copy of the number -- bumping the real policy constant
        changes what this operation accepts too, with no second place to update."""
        assert live_cli.core.MAX_LOGIC_REPAIR_ATTEMPTS is core.MAX_LOGIC_REPAIR_ATTEMPTS

    # 12. an ordinary (non-repair) request is entirely unaffected by this extension ----

    def test_omitting_repair_attempt_is_identical_to_prior_behavior(self, repo_root, capsys) -> None:
        _seed_scope_and_findings(repo_root)
        doc = _verification_report_doc()
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "promote_artifact", "run_id": "run-1", "phase": "verification",
                "doc": doc, "context_refs": {"scope": "runs/run-1/scope.json"},
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "promoted"
        assert resp["path"] == "runs/run-1/verification-report.json"
        assert not (repo_root / "runs" / "run-1" / "verification-report.repair-1.json").exists()


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


# ---------------------------------------------------------------------------
# operations 18-22: git_repo_identity / retain_commit_evidence /
# retain_push_attempt / verify_push / gh_repo_metadata
# ---------------------------------------------------------------------------


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture()
def git_repo_root(repo_root):
    """Turns the sandboxed `repo_root` fixture (already monkeypatched onto
    live_cli.REPO_ROOT) into a real, disposable Git repository -- one commit on branch
    "main", a fake "origin" remote URL. target_repo_path="." then resolves to this same
    directory, exactly as it resolves to the real AgenticHarness repo root in
    production."""
    _git("init", "--initial-branch=main", cwd=repo_root)
    _git("config", "user.email", "test@example.invalid", cwd=repo_root)
    _git("config", "user.name", "Test", cwd=repo_root)
    (repo_root / "file.txt").write_text("hello\n", encoding="utf-8")
    _git("add", "file.txt", cwd=repo_root)
    _git("commit", "-m", "initial commit", cwd=repo_root)
    _git("remote", "add", "origin", "https://github.com/example-owner/example-repo.git", cwd=repo_root)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo_root, head_sha


class TestGitRepoIdentity:
    def test_real_repo_identity_reported(self, git_repo_root, capsys) -> None:
        repo_root, head_sha = git_repo_root
        exit_code, resp = _run_main(repo_root, {"operation": "git_repo_identity", "target_repo_path": "."}, capsys)
        assert exit_code == 0
        assert resp["status"] == "ok"
        assert resp["current_branch"] == "main"
        assert resp["local_head_sha"] == head_sha
        assert resp["remote_url"] == "https://github.com/example-owner/example-repo.git"

    def test_non_git_directory_is_command_failed(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(repo_root, {"operation": "git_repo_identity", "target_repo_path": "."}, capsys)
        assert exit_code == 1
        assert resp["status"] == "command_failed"

    def test_wrong_expected_repo_rejected(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "git_repo_identity", "target_repo_path": ".", "expected_repo": "someone-else/other-repo"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "wrong_repository"

    def test_protected_path_rejected(self, repo_root, capsys) -> None:
        (repo_root / ".claude").mkdir()
        (repo_root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        exit_code, resp = _run_main(
            repo_root, {"operation": "git_repo_identity", "target_repo_path": ".claude/settings.json"}, capsys
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert resp["code"] == "protected_path"


class TestRetainCommitEvidence:
    def test_commit_evidence_retained_with_real_head_sha(self, git_repo_root, capsys) -> None:
        repo_root, head_sha = git_repo_root
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "retain_commit_evidence", "run_id": "run-1", "task_id": "T-1", "target_repo_path": "."},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "retained"
        assert resp["commit_sha"] == head_sha
        doc = json.loads((repo_root / "runs" / "run-1" / "git" / "commit-evidence.json").read_text(encoding="utf-8"))
        assert doc["commit_sha"] == head_sha
        assert doc["branch"] == "main"

    def test_expected_branch_mismatch_is_blocked_and_writes_nothing(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "retain_commit_evidence", "run_id": "run-1", "task_id": "T-1",
                "target_repo_path": ".", "expected_branch": "some-other-branch",
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert not (repo_root / "runs" / "run-1" / "git" / "commit-evidence.json").exists()

    def test_collision_is_blocked(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        body = {"operation": "retain_commit_evidence", "run_id": "run-1", "task_id": "T-1", "target_repo_path": "."}
        _run_main(repo_root, body, capsys)
        exit_code, resp = _run_main(repo_root, body, capsys)
        assert exit_code == 1
        assert resp["status"] == "blocked"


class TestRetainPushAttempt:
    def test_push_attempt_retained(self, git_repo_root, capsys) -> None:
        repo_root, head_sha = git_repo_root
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "retain_push_attempt", "run_id": "run-1", "task_id": "T-1",
                "target_repo_path": ".", "branch": "main",
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "retained"
        assert resp["expected_local_sha"] == head_sha
        doc = json.loads((repo_root / "runs" / "run-1" / "git" / "push-attempt.json").read_text(encoding="utf-8"))
        assert doc["ref"] == "refs/heads/main"
        assert doc["remote_url"] == "https://github.com/example-owner/example-repo.git"

    def test_stale_expected_sha_is_blocked(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "retain_push_attempt", "run_id": "run-1", "task_id": "T-1", "target_repo_path": ".",
                "branch": "main", "expected_sha": "a" * 40,
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert not (repo_root / "runs" / "run-1" / "git" / "push-attempt.json").exists()


class TestVerifyPush:
    def test_mismatch_is_retained_honestly_never_reported_as_verified(self, git_repo_root, monkeypatch, capsys) -> None:
        """The live_cli-level false-push proof: the real repo's HEAD SHA is used as
        expected_sha, but the real subprocess `git ls-remote` call is monkeypatched at
        github.DEFAULT_RUNNER to return a different, fabricated SHA -- proving
        verify_push (as exercised through the exact CLI boundary /work itself calls)
        refuses to report this as a verified push."""
        repo_root, head_sha = git_repo_root
        fabricated_remote_sha = "f" * 40

        def fake_runner(argv, cwd):
            if argv[:2] == ["git", "remote"]:
                return github.CommandResult(
                    exit_code=0, stdout="https://github.com/example-owner/example-repo.git\n", stderr=""
                )
            if argv[:2] == ["git", "ls-remote"]:
                return github.CommandResult(
                    exit_code=0, stdout=f"{fabricated_remote_sha}\trefs/heads/main\n", stderr=""
                )
            raise AssertionError(f"unexpected git invocation in test: {argv}")

        monkeypatch.setattr(github, "DEFAULT_RUNNER", fake_runner)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "verify_push", "run_id": "run-1", "task_id": "T-1", "target_repo_path": ".",
                "branch": "main", "expected_sha": head_sha,
            },
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "mismatch"
        assert resp["observed_sha"] == fabricated_remote_sha
        assert resp["expected_sha"] == head_sha
        doc = json.loads((repo_root / "runs" / "run-1" / "git" / "push-verification.json").read_text(encoding="utf-8"))
        assert doc["status"] == "mismatch"

    def test_verified_when_remote_sha_matches(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, head_sha = git_repo_root

        def fake_runner(argv, cwd):
            if argv[:2] == ["git", "remote"]:
                return github.CommandResult(
                    exit_code=0, stdout="https://github.com/example-owner/example-repo.git\n", stderr=""
                )
            if argv[:2] == ["git", "ls-remote"]:
                return github.CommandResult(exit_code=0, stdout=f"{head_sha}\trefs/heads/main\n", stderr="")
            raise AssertionError(f"unexpected git invocation in test: {argv}")

        monkeypatch.setattr(github, "DEFAULT_RUNNER", fake_runner)
        exit_code, resp = _run_main(
            repo_root,
            {
                "operation": "verify_push", "run_id": "run-1", "task_id": "T-1", "target_repo_path": ".",
                "branch": "main", "expected_sha": head_sha,
            },
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "verified"

    def test_verify_push_never_exposes_a_simulation_parameter(self) -> None:
        # There is no request field that lets a caller inject a fake ls-remote result
        # through the live CLI boundary -- op_verify_push always calls github.verify_push
        # without a runner=, so DEFAULT_RUNNER (the real subprocess) is always used
        # unless a test monkeypatches it directly, as the two tests above do.
        sig = inspect.signature(live_cli.op_verify_push)
        assert "runner" not in sig.parameters
        assert "simulated" not in " ".join(sig.parameters).lower()


class TestGhRepoMetadata:
    def test_delegates_to_github_module(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            github, "gh_repo_metadata",
            lambda **kwargs: {"status": "ok", "metadata": {"nameWithOwner": "example-owner/example-repo"}, "raw": {}},
        )
        exit_code, resp = _run_main(repo_root, {"operation": "gh_repo_metadata", "target_repo_path": "."}, capsys)
        assert exit_code == 0
        assert resp["metadata"]["nameWithOwner"] == "example-owner/example-repo"


# ---------------------------------------------------------------------------
# github/ skill pack (ASSIGNMENT.md §2.3): read-file, search-code,
# commit-history, pr-review (read-only), pr-create (the one state-changing op)
# ---------------------------------------------------------------------------


def _no_subprocess(argv, cwd):
    raise AssertionError(f"no command should have run: {argv}")


class TestReadFile:
    def test_reads_real_file_at_branch(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        exit_code, resp = _run_main(
            repo_root, {"operation": "read_file", "target_repo_path": ".", "ref": "main", "file_path": "file.txt"}, capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "found"
        assert resp["content"] == "hello\n"

    def test_missing_file_is_not_found(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        exit_code, resp = _run_main(
            repo_root, {"operation": "read_file", "target_repo_path": ".", "ref": "main", "file_path": "nope.txt"}, capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "not_found"

    def test_repo_slug_routes_to_a_get_only_github_contents_call(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, _ = git_repo_root
        calls: list[list] = []

        def fake_runner(argv, cwd):
            calls.append(argv)
            return github.CommandResult(exit_code=0, stdout=json.dumps({
                "type": "file", "encoding": "base64", "size": 6, "sha": "b" * 40, "content": "aGVsbG8K",
            }), stderr="")

        monkeypatch.setattr(github, "DEFAULT_RUNNER", fake_runner)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "read_file", "ref": "main", "file_path": "a.py", "repo_slug": "owner/name"},
            capsys,
        )
        assert exit_code == 0
        assert resp["content"] == "hello\n"
        assert calls == [["gh", "api", "--method", "GET", "repos/owner/name/contents/a.py?ref=main"]]

    def test_flag_shaped_ref_rejected_without_running_anything(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, _ = git_repo_root
        monkeypatch.setattr(github, "DEFAULT_RUNNER", _no_subprocess)
        exit_code, resp = _run_main(
            repo_root, {"operation": "read_file", "ref": "--output=x", "file_path": "file.txt"}, capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid_ref"

    def test_protected_path_rejected(self, repo_root, capsys) -> None:
        (repo_root / ".claude").mkdir()
        (repo_root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "read_file", "target_repo_path": ".claude/settings.json", "ref": "main", "file_path": "x"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid"
        assert resp["code"] == "protected_path"

    def test_missing_required_field_is_a_usage_error(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        exit_code, resp = _run_main(repo_root, {"operation": "read_file", "target_repo_path": ".", "ref": "main"}, capsys)
        assert exit_code == 2
        assert resp["status"] == "error"


class TestSearchCode:
    def test_delegates_to_github_module(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, _ = git_repo_root
        seen: dict = {}

        def fake_search(repo_path, query, **kwargs):
            seen.update(kwargs)
            return {"status": "found", "query": query, "results": [{"path": "a.py"}]}

        monkeypatch.setattr(github, "search_code", fake_search)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "search_code", "query": "def foo", "repo_slugs": ["owner/name"], "limit": 5},
            capsys,
        )
        assert exit_code == 0
        assert resp["results"] == [{"path": "a.py"}]
        assert seen == {"repo_slugs": ["owner/name"], "limit": 5}

    @pytest.mark.parametrize("extra", [{}, {"repo_slugs": "not-a-list"}, {"repo_slugs": [1]}])
    def test_missing_or_malformed_repo_slugs_is_a_usage_error(self, git_repo_root, monkeypatch, capsys, extra) -> None:
        repo_root, _ = git_repo_root
        monkeypatch.setattr(github, "DEFAULT_RUNNER", _no_subprocess)
        exit_code, resp = _run_main(repo_root, {"operation": "search_code", "query": "x", **extra}, capsys)
        assert exit_code == 2
        assert resp["status"] == "error"

    def test_unscoped_empty_list_refused_without_running_gh(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, _ = git_repo_root
        monkeypatch.setattr(github, "DEFAULT_RUNNER", _no_subprocess)
        exit_code, resp = _run_main(repo_root, {"operation": "search_code", "query": "x", "repo_slugs": []}, capsys)
        assert exit_code == 1
        assert resp["status"] == "invalid_query"


class TestCommitHistory:
    def test_real_repo_history(self, git_repo_root, capsys) -> None:
        repo_root, head_sha = git_repo_root
        exit_code, resp = _run_main(repo_root, {"operation": "commit_history", "target_repo_path": "."}, capsys)
        assert exit_code == 0
        assert resp["status"] == "ok"
        assert resp["commits"][0]["sha"] == head_sha

    def test_ref_and_path_are_passed_through(self, git_repo_root, capsys) -> None:
        repo_root, head_sha = git_repo_root
        exit_code, resp = _run_main(
            repo_root, {"operation": "commit_history", "ref": head_sha, "path": "file.txt", "max_count": 1}, capsys,
        )
        assert exit_code == 0
        assert [c["sha"] for c in resp["commits"]] == [head_sha]
        assert resp["truncated"] is True

    def test_invalid_max_count_is_a_negative_result_not_a_crash(self, git_repo_root, capsys) -> None:
        repo_root, _ = git_repo_root
        exit_code, resp = _run_main(
            repo_root, {"operation": "commit_history", "target_repo_path": ".", "max_count": 0}, capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid_input"


class TestPrReview:
    def test_delegates_to_github_module(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, _ = git_repo_root
        monkeypatch.setattr(
            github, "pr_review",
            lambda repo_path, pr_ref, **kwargs: {"status": "found", "pr_ref": pr_ref, "pull_request": {"number": 3}},
        )
        exit_code, resp = _run_main(repo_root, {"operation": "pr_review", "target_repo_path": ".", "pr_ref": "3"}, capsys)
        assert exit_code == 0
        assert resp["pull_request"]["number"] == 3

    def test_not_found_is_a_negative_result(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, _ = git_repo_root
        monkeypatch.setattr(
            github, "pr_review", lambda repo_path, pr_ref, **kwargs: {"status": "not_found", "pr_ref": pr_ref},
        )
        exit_code, resp = _run_main(repo_root, {"operation": "pr_review", "target_repo_path": ".", "pr_ref": "3"}, capsys)
        assert exit_code == 1
        assert resp["status"] == "not_found"

    def test_flag_shaped_pr_ref_rejected_without_running_gh(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, _ = git_repo_root
        monkeypatch.setattr(github, "DEFAULT_RUNNER", _no_subprocess)
        exit_code, resp = _run_main(repo_root, {"operation": "pr_review", "pr_ref": "--web"}, capsys)
        assert exit_code == 1
        assert resp["status"] == "invalid_input"


class _ScriptedRemote:
    """`git rev-parse` runs for real against the disposable repo; the remote URL,
    per-ref `git ls-remote`, `gh pr create` and `gh pr view` are fixtures. Anything
    else (a commit, a push) fails the test."""

    def __init__(self, real_runner, remote_refs: dict, *, create=None, view=None):
        self.real_runner = real_runner
        self.remote_refs = remote_refs
        self.create = create
        self.view = view
        self.calls: list[list] = []

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        if argv[:2] == ["git", "rev-parse"]:
            return self.real_runner(argv, cwd)
        if argv[:3] == ["git", "remote", "get-url"]:
            return github.CommandResult(exit_code=0, stdout="https://github.com/example-owner/example-repo.git\n", stderr="")
        if argv[:2] == ["git", "ls-remote"]:
            sha = self.remote_refs.get(argv[3])
            return github.CommandResult(exit_code=0, stdout=f"{sha}\t{argv[3]}\n" if sha else "", stderr="")
        if argv[:3] == ["gh", "pr", "create"] and self.create is not None:
            return self.create
        if argv[:3] == ["gh", "pr", "view"] and self.view is not None:
            return self.view
        raise AssertionError(f"unexpected invocation in test: {argv}")

    def ran_gh(self) -> bool:
        return any(argv[0] == "gh" for argv in self.calls)


_PR_CREATE_REQUEST = {
    "operation": "pr_create", "run_id": "run-1", "task_id": "T-1", "target_repo_path": ".",
    "base": "release", "head": "main", "title": "Add x", "body": "body",
    "expected_repo": "example-owner/example-repo",
    "authorized": True, "authorization_source": "user instruction: open a PR for main into release",
}


class TestPrCreate:
    def _install(self, monkeypatch, head_sha, **kwargs) -> _ScriptedRemote:
        remote_refs = kwargs.pop("remote_refs", {"refs/heads/main": head_sha, "refs/heads/release": "e" * 40})
        fake = _ScriptedRemote(github.DEFAULT_RUNNER, remote_refs, **kwargs)
        monkeypatch.setattr(github, "DEFAULT_RUNNER", fake)
        return fake

    @staticmethod
    def _policy_events(repo_root) -> list:
        log = repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    @pytest.mark.parametrize("auth", [{"authorized": None}, {"authorized": "true"}, {"authorization_source": ""}])
    def test_unauthorized_refusal_runs_nothing_but_is_retained(self, git_repo_root, monkeypatch, capsys, auth) -> None:
        repo_root, head_sha = git_repo_root
        fake = self._install(monkeypatch, head_sha, create=github.CommandResult(exit_code=0, stdout="", stderr=""))
        request = {k: v for k, v in {**_PR_CREATE_REQUEST, **auth}.items() if v is not None}
        exit_code, resp = _run_main(repo_root, request, capsys)
        assert exit_code == 1
        assert resp["status"] == "not_authorized"
        assert fake.calls == []
        doc = json.loads((repo_root / "runs" / "run-1" / "git" / "pr-create.json").read_text(encoding="utf-8"))
        assert doc["status"] == "not_authorized"
        assert self._policy_events(repo_root)[-1]["status"] == "not_authorized"

    def test_push_not_verified_retains_evidence_but_never_calls_gh_pr_create(
        self, git_repo_root, monkeypatch, capsys,
    ) -> None:
        repo_root, _ = git_repo_root
        fake = self._install(monkeypatch, None, remote_refs={"refs/heads/main": "f" * 40, "refs/heads/release": "e" * 40})
        exit_code, resp = _run_main(repo_root, _PR_CREATE_REQUEST, capsys)
        assert exit_code == 1
        assert resp["status"] == "push_not_verified"
        assert not fake.ran_gh()
        doc = json.loads((repo_root / "runs" / "run-1" / "git" / "pr-create.json").read_text(encoding="utf-8"))
        assert doc["status"] == "push_not_verified"
        assert doc["verification"]["status"] == "mismatch"

    def test_created_retains_evidence_with_pr_url(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, head_sha = git_repo_root
        self._install(
            monkeypatch, head_sha,
            create=github.CommandResult(exit_code=0, stdout="https://github.com/example-owner/example-repo/pull/9\n", stderr=""),
            view=github.CommandResult(exit_code=0, stdout=json.dumps({
                "number": 9, "url": "https://github.com/example-owner/example-repo/pull/9", "state": "OPEN",
                "baseRefName": "release", "headRefName": "main", "headRefOid": head_sha,
            }), stderr=""),
        )
        exit_code, resp = _run_main(repo_root, _PR_CREATE_REQUEST, capsys)
        assert exit_code == 0
        assert resp["status"] == "created"
        assert resp["number"] == 9
        doc = json.loads((repo_root / "runs" / "run-1" / "git" / "pr-create.json").read_text(encoding="utf-8"))
        assert doc["status"] == "created"
        assert doc["url"] == "https://github.com/example-owner/example-repo/pull/9"
        assert doc["head_sha"] == head_sha
        assert doc["authorization_source"] == _PR_CREATE_REQUEST["authorization_source"]
        event = self._policy_events(repo_root)[-1]
        assert event["kind"] == "pr_create_attempt" and event["status"] == "created"

    def test_created_unverified_is_a_failure_exit(self, git_repo_root, monkeypatch, capsys) -> None:
        repo_root, head_sha = git_repo_root
        self._install(
            monkeypatch, head_sha,
            create=github.CommandResult(exit_code=0, stdout="https://github.com/example-owner/example-repo/pull/9\n", stderr=""),
            view=github.CommandResult(exit_code=1, stdout="", stderr="no pull requests found"),
        )
        exit_code, resp = _run_main(repo_root, _PR_CREATE_REQUEST, capsys)
        assert exit_code == 1
        assert resp["status"] == "created_unverified"

    def test_existing_evidence_blocks_before_anything_runs(self, git_repo_root, monkeypatch, capsys) -> None:
        """The collision check happens first: a PR must never be opened whose evidence
        record then cannot be retained."""
        repo_root, head_sha = git_repo_root
        evidence = repo_root / "runs" / "run-1" / "git" / "pr-create.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text('{"status": "earlier"}', encoding="utf-8")
        fake = self._install(monkeypatch, head_sha)
        exit_code, resp = _run_main(repo_root, _PR_CREATE_REQUEST, capsys)
        assert exit_code == 1
        assert resp["status"] == "blocked"
        assert fake.calls == []
        assert json.loads(evidence.read_text(encoding="utf-8")) == {"status": "earlier"}

    @pytest.mark.parametrize("filename", ["../escape.json", "sub/x.json", "x.txt", ""])
    def test_unsafe_filename_is_a_usage_error(self, git_repo_root, monkeypatch, capsys, filename) -> None:
        repo_root, head_sha = git_repo_root
        fake = self._install(monkeypatch, head_sha)
        exit_code, resp = _run_main(repo_root, {**_PR_CREATE_REQUEST, "filename": filename}, capsys)
        assert exit_code == 2
        assert resp["status"] == "error"
        assert fake.calls == []

    def test_pr_create_never_exposes_a_simulation_parameter(self) -> None:
        sig = inspect.signature(live_cli.op_pr_create)
        assert "runner" not in sig.parameters
        assert "simulated" not in " ".join(sig.parameters).lower()


class TestResolveJiraIssue:
    """op_resolve_jira_issue -- the live-CLI boundary /work's ticket-mode resolution
    calls. Every scenario here goes through jira_connector.DEFAULT_TRANSPORT
    (monkeypatched to a fake, network-free callable at the exact seam the module's own
    docstring names) or through real env-var absence -- never a real Jira instance."""

    def _issue_body(self, key: str = "PROJ-123") -> str:
        return json.dumps(
            {
                "key": key,
                "fields": {
                    "summary": "Fix the flaky login test",
                    "description": {"type": "doc", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Details here."}]}
                    ]},
                    "issuetype": {"name": "Bug"},
                    "status": {"name": "To Do"},
                    "project": {"key": "PROJ"},
                },
            }
        )

    def test_missing_credentials_classified_connector_unavailable(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.delenv("JIRA_BASE_URL", raising=False)
        monkeypatch.delenv("JIRA_EMAIL", raising=False)
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
        exit_code, resp = _run_main(
            repo_root, {"operation": "resolve_jira_issue", "run_id": "run-1", "task_id": "T-1", "issue_key": "PROJ-123"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "connector_unavailable"
        doc = json.loads((repo_root / "runs" / "run-1" / "jira" / "issue-resolution.json").read_text(encoding="utf-8"))
        assert doc["status"] == "connector_unavailable"
        # The reason names the missing env vars by name; no actual secret value can leak
        # here because none was ever set -- the real no-leakage guarantee is proven by
        # test_resolved_issue_retained_and_reported below (Authorization header absent).

    def test_invalid_issue_key_shape_classified_without_network_call(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@example.invalid")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")

        def fail_if_called(method, url, headers):
            raise AssertionError("resolve_issue must not attempt a network call for an invalid key shape")

        monkeypatch.setattr(jira_connector, "DEFAULT_TRANSPORT", fail_if_called)
        exit_code, resp = _run_main(
            repo_root, {"operation": "resolve_jira_issue", "run_id": "run-1", "task_id": "T-1", "issue_key": "not-a-key"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid_issue_key"

    def test_resolved_issue_retained_and_reported(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@example.invalid")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        body = self._issue_body()
        monkeypatch.setattr(
            jira_connector, "DEFAULT_TRANSPORT",
            lambda method, url, headers: jira_connector.HttpResult(status_code=200, body=body),
        )
        exit_code, resp = _run_main(
            repo_root, {"operation": "resolve_jira_issue", "run_id": "run-1", "task_id": "T-1", "issue_key": "proj-123"},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "resolved"
        assert resp["issue"]["issue_key"] == "PROJ-123"
        doc = json.loads((repo_root / "runs" / "run-1" / "jira" / "issue-resolution.json").read_text(encoding="utf-8"))
        assert doc["status"] == "resolved"
        assert doc["issue"]["issue_key"] == "PROJ-123"
        assert doc["requested_issue_key_raw"] == "proj-123"  # normalization never overwrites the raw caller input
        assert "tok" not in json.dumps(doc)  # the real API token is never retained in evidence
        assert "Authorization" not in json.dumps(doc)

    def test_identity_mismatch_never_reported_as_resolved(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@example.invalid")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        body = self._issue_body(key="PROJ-999")  # a real Jira response for a different issue than requested
        monkeypatch.setattr(
            jira_connector, "DEFAULT_TRANSPORT",
            lambda method, url, headers: jira_connector.HttpResult(status_code=200, body=body),
        )
        exit_code, resp = _run_main(
            repo_root, {"operation": "resolve_jira_issue", "run_id": "run-1", "task_id": "T-1", "issue_key": "PROJ-123"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "identity_mismatch"
        assert resp["issue"] is None

    def test_not_found_retained_honestly_not_as_empty_issue(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@example.invalid")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        monkeypatch.setattr(
            jira_connector, "DEFAULT_TRANSPORT",
            lambda method, url, headers: jira_connector.HttpResult(status_code=404, body="{}"),
        )
        exit_code, resp = _run_main(
            repo_root, {"operation": "resolve_jira_issue", "run_id": "run-1", "task_id": "T-1", "issue_key": "PROJ-404"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "not_found"
        assert resp["issue"] is None

    def test_resolve_jira_issue_never_exposes_a_credentials_field(self) -> None:
        # There is no request field that lets a caller pass credentials through the live
        # CLI boundary -- op_resolve_jira_issue always reads them fresh from the real
        # environment via jira_connector.JiraCredentials.from_env().
        sig = inspect.signature(live_cli.op_resolve_jira_issue)
        params = list(sig.parameters)
        assert params == ["req"]


class TestResolveJiraIssueRouted:
    """op_resolve_jira_issue_routed -- the ASSIGNMENT.md §2.4 dual-route Jira boundary.
    With no JIRA_* credentials set (the environment's real state), both the REST and MCP
    transports run for real and independently report connector_unavailable."""

    def _no_creds(self, monkeypatch):
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            monkeypatch.delenv(k, raising=False)

    def test_rest_first_connector_unavailable_corroborated_by_mcp(self, repo_root, monkeypatch, capsys) -> None:
        self._no_creds(monkeypatch)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "resolve_jira_issue_routed", "run_id": "run-1", "task_id": "T-1", "issue_key": "PROJ-1"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "connector_unavailable"
        assert resp["fallback_attempted"] is True
        assert resp["primary_route"] == "rest" and resp["fallback_route"] == "mcp"
        assert resp["primary_result"]["outcome"] == "connector_unavailable"
        assert resp["fallback_result"]["outcome"] == "connector_unavailable"

    def test_mcp_first_policy_runs_mcp_route_first(self, repo_root, monkeypatch, capsys) -> None:
        self._no_creds(monkeypatch)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "resolve_jira_issue_routed", "run_id": "run-2", "task_id": "T-1",
             "issue_key": "PROJ-1", "policy": "mcp_first"},
            capsys,
        )
        assert resp["policy"] == "mcp_first" and resp["primary_route"] == "mcp"
        assert resp["status"] == "connector_unavailable"

    def test_routing_evidence_and_policy_event_retained(self, repo_root, monkeypatch, capsys) -> None:
        self._no_creds(monkeypatch)
        _run_main(
            repo_root,
            {"operation": "resolve_jira_issue_routed", "run_id": "run-3", "task_id": "T-1", "issue_key": "PROJ-1"},
            capsys,
        )
        routing = json.loads(
            (repo_root / "runs" / "run-3" / "jira" / "routing" / "route-1.json").read_text(encoding="utf-8")
        )
        assert routing["status"] == "connector_unavailable"
        assert routing["kept_route"] == "rest"
        # No secret / raw HTTP echo anywhere in the retained routing record.
        blob = json.dumps(routing)
        assert "Authorization" not in blob and "Basic " not in blob and '"raw"' not in blob
        events = (repo_root / "runs" / "run-3" / "logs" / "policy-events.jsonl").read_text(encoding="utf-8")
        assert '"kind": "connector_routing"' in events

    def test_invalid_policy_is_a_usage_error(self, repo_root, monkeypatch, capsys) -> None:
        self._no_creds(monkeypatch)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "resolve_jira_issue_routed", "run_id": "run-4", "task_id": "T-1",
             "issue_key": "PROJ-1", "policy": "whatever"},
            capsys,
        )
        assert exit_code == 2
        assert "policy" in resp["error"]

    def test_credentials_and_transport_request_fields_are_rejected(self, repo_root, monkeypatch, capsys) -> None:
        self._no_creds(monkeypatch)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "resolve_jira_issue_routed", "run_id": "run-5", "task_id": "T-1",
             "issue_key": "PROJ-1", "env": {"JIRA_API_TOKEN": "x"}},
            capsys,
        )
        assert exit_code == 2

    def test_affirmative_routed_statuses_are_ok_statuses(self) -> None:
        for status in ("resolved_via_rest", "resolved_via_mcp", "rest_failed_mcp_resolved", "mcp_failed_rest_resolved"):
            assert status in live_cli.OK_STATUSES
        for status in ("connector_unavailable", "both_routes_failed", "not_found", "unauthorized", "identity_mismatch"):
            assert status not in live_cli.OK_STATUSES

    def test_operation_never_exposes_a_credentials_parameter(self) -> None:
        assert list(inspect.signature(live_cli.op_resolve_jira_issue_routed).parameters) == ["req"]


class TestPublishRunSummary:
    """op_publish_run_summary -- the live-CLI boundary /work's Obsidian publication
    calls. Every scenario points OBSIDIAN_VAULT_PATH at a pytest tmp dir or unsets it;
    no personal vault is ever touched. The production filesystem VaultWriter is always
    used -- there is no request field that swaps it for a simulated one."""

    def _seed_run_summary(self, repo_root, run_id="run-1", **over) -> None:
        rundir = repo_root / "runs" / run_id
        rundir.mkdir(parents=True, exist_ok=True)
        doc = {
            "schema_version": "1.0", "task_id": "T-1", "run_id": run_id,
            "created_at": "2026-09-08T00:00:00Z", "objective_summary": "do the thing",
            "artifact_refs": {"scope": f"runs/{run_id}/scope.json"},
            "final_verdict": "pass", "phases_completed": ["discovery", "research", "implementation", "verification"],
        }
        doc.update(over)
        (rundir / "run-summary.json").write_text(json.dumps(doc), encoding="utf-8")
        (rundir / "scope.json").write_text(json.dumps({"source": {"type": "prompt"}}), encoding="utf-8")

    def test_missing_run_summary_is_a_usage_error(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "publish_run_summary", "run_id": "run-1", "task_id": "T-1",
             "generated_at": "2026-09-08T00:00:00Z"},
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"
        assert "run-summary.json" in resp["error"]

    def test_connector_unavailable_retained_honestly(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        self._seed_run_summary(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "publish_run_summary", "run_id": "run-1", "task_id": "T-1",
             "generated_at": "2026-09-08T00:00:00Z"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "connector_unavailable"
        doc = json.loads((repo_root / "runs" / "run-1" / "obsidian" / "summary-publication.json").read_text())
        assert doc["status"] == "connector_unavailable"
        assert doc["destination"]["configured"] is False
        assert doc["connector"] == "filesystem_vault"
        events = (repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl").read_text()
        assert '"obsidian_publication"' in events

    def test_published_into_configured_temp_vault(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.delenv("OBSIDIAN_SUMMARY_DIR", raising=False)
        self._seed_run_summary(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "publish_run_summary", "run_id": "run-1", "task_id": "T-1",
             "generated_at": "2026-09-08T00:00:00Z"},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "published"
        note = vault / "Harness Run Summaries" / "run-summary-run-1.md"
        assert note.is_file()
        assert "run_id: run-1" in note.read_text(encoding="utf-8")
        import hashlib
        assert resp["content_sha256"] == hashlib.sha256(note.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        doc = json.loads((repo_root / "runs" / "run-1" / "obsidian" / "summary-publication.json").read_text())
        assert doc["status"] == "published"
        assert doc["destination"]["vault_path"] == str(vault.resolve())
        assert doc["absolute_path"] == str(note)

    def test_collision_without_overwrite_then_overwrite(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        self._seed_run_summary(repo_root)
        first = _run_main(
            repo_root,
            {"operation": "publish_run_summary", "run_id": "run-1", "task_id": "T-1",
             "generated_at": "2026-09-08T00:00:00Z"},
            capsys,
        )
        assert first[1]["status"] == "published"
        second = _run_main(
            repo_root,
            {"operation": "publish_run_summary", "run_id": "run-1", "task_id": "T-1",
             "generated_at": "2026-09-08T00:00:00Z", "filename": "summary-publication.retry-1.json"},
            capsys,
        )
        assert second[0] == 1
        assert second[1]["status"] == "collision"
        third = _run_main(
            repo_root,
            {"operation": "publish_run_summary", "run_id": "run-1", "task_id": "T-1",
             "generated_at": "2026-09-08T00:00:00Z", "overwrite": True,
             "filename": "summary-publication.retry-2.json"},
            capsys,
        )
        assert third[1]["status"] == "published"

    def test_invalid_destination_when_vault_path_relative(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "some/relative/dir")
        self._seed_run_summary(repo_root)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "publish_run_summary", "run_id": "run-1", "task_id": "T-1",
             "generated_at": "2026-09-08T00:00:00Z"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid_destination"
        doc = json.loads((repo_root / "runs" / "run-1" / "obsidian" / "summary-publication.json").read_text())
        assert doc["destination"]["raw_env_value"] == "some/relative/dir"

    def test_publish_run_summary_never_exposes_a_writer_parameter(self) -> None:
        sig = inspect.signature(live_cli.op_publish_run_summary)
        assert list(sig.parameters) == ["req"]
        # op always calls obsidian.publish() without writer=, so DEFAULT_WRITER (the real
        # filesystem write) is always used unless a test monkeypatches it directly.
        src = inspect.getsource(live_cli.op_publish_run_summary)
        assert "writer=" not in src


class TestSearchObsidian:
    """op_search_obsidian -- the live-CLI boundary /work's Discovery/Research Obsidian
    consultation calls. Every scenario points OBSIDIAN_VAULT_PATH at a pytest tmp dir or
    unsets it; no personal vault is ever touched, and there is no request field that can
    point the reader elsewhere."""

    def _vault(self, tmp_path):
        v = tmp_path / "vault"
        (v / "Design").mkdir(parents=True)
        (v / ".obsidian").mkdir()
        (v / "Design" / "Risk band calibration.md").write_text(
            "# Risk band calibration\nPast decision: thresholds 620/680/740.\n", encoding="utf-8"
        )
        (v / ".obsidian" / "workspace.json").write_text('{"x": "risk band calibration secret"}', encoding="utf-8")
        return v

    def test_found_retained_and_affirmative_exit(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "search_obsidian", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "query": "risk band calibration"},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "found"
        assert any(m["note_path"] == "Design/Risk band calibration.md" for m in resp["matches"])
        doc = json.loads((repo_root / "runs" / "run-1" / "obsidian" / "read" / "search-1.json").read_text())
        assert doc["operation"] == "search_obsidian"
        assert doc["phase"] == "research"
        assert doc["query"] == "risk band calibration"
        assert doc["connector"] == "filesystem_vault"
        events = (repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl").read_text()
        assert '"obsidian_read"' in events

    def test_no_matches_is_negative_exit_but_retained(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "search_obsidian", "run_id": "run-1", "task_id": "T-1",
             "phase": "discovery", "query": "quantumfluxcapacitor"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "no_matches"
        assert (repo_root / "runs" / "run-1" / "obsidian" / "read" / "search-1.json").is_file()

    def test_connector_unavailable_retained_honestly(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "search_obsidian", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "query": "risk band"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "connector_unavailable"
        doc = json.loads((repo_root / "runs" / "run-1" / "obsidian" / "read" / "search-1.json").read_text())
        assert doc["vault"]["configured"] is False

    def test_bad_phase_is_usage_error(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "search_obsidian", "run_id": "run-1", "task_id": "T-1",
             "phase": "implementation", "query": "x"},
            capsys,
        )
        assert exit_code == 2
        assert resp["status"] == "error"

    def test_request_cannot_smuggle_a_vault_path(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        for field in ("vault_path", "env", "OBSIDIAN_VAULT_PATH"):
            exit_code, resp = _run_main(
                repo_root,
                {"operation": "search_obsidian", "run_id": "run-1", "task_id": "T-1",
                 "phase": "research", "query": "risk band", field: "/etc"},
                capsys,
            )
            assert exit_code == 2
            assert "environment" in resp["error"]

    def test_never_exposes_a_reader_parameter(self) -> None:
        sig = inspect.signature(live_cli.op_search_obsidian)
        assert list(sig.parameters) == ["req"]
        src = inspect.getsource(live_cli.op_search_obsidian)
        assert "reader=" not in src and "env=" not in src

    def test_collision_guard_on_repeat(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        body = {"operation": "search_obsidian", "run_id": "run-1", "task_id": "T-1",
                "phase": "research", "query": "risk band"}
        assert _run_main(repo_root, body, capsys)[1]["status"] == "found"
        exit_code, resp = _run_main(repo_root, body, capsys)
        assert exit_code == 1
        assert resp["status"] == "blocked"
        # a distinct filename is accepted
        body2 = dict(body, filename="search-2.json")
        assert _run_main(repo_root, body2, capsys)[1]["status"] == "found"


class TestReadObsidianNote:
    def _vault(self, tmp_path):
        v = tmp_path / "vault"
        (v / "Design").mkdir(parents=True)
        (v / ".obsidian").mkdir()
        (v / "Design" / "ADR-001.md").write_text("# ADR-001\nAppend risk band after DTI.\n", encoding="utf-8")
        (v / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
        return v

    def test_read_retained_and_affirmative(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "read_obsidian_note", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "note_path": "Design/ADR-001.md"},
            capsys,
        )
        assert exit_code == 0
        assert resp["status"] == "read"
        assert "Append risk band after DTI" in resp["content"]
        assert resp["content_sha256"]
        doc = json.loads((repo_root / "runs" / "run-1" / "obsidian" / "read" / "read-1.json").read_text())
        assert doc["requested_note_path"] == "Design/ADR-001.md"
        assert doc["phase"] == "research"

    def test_traversal_is_invalid_note_path_negative_exit(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "read_obsidian_note", "run_id": "run-1", "task_id": "T-1",
             "phase": "discovery", "note_path": "../../../etc/passwd"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid_note_path"
        assert (repo_root / "runs" / "run-1" / "obsidian" / "read" / "read-1.json").is_file()

    def test_dot_obsidian_is_rejected(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "read_obsidian_note", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "note_path": ".obsidian/app.json"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "invalid_note_path"

    def test_missing_note_is_not_found(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "read_obsidian_note", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "note_path": "Design/nope.md"},
            capsys,
        )
        assert exit_code == 1
        assert resp["status"] == "not_found"

    def test_read_evidence_is_separate_from_publication_evidence(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        _run_main(
            repo_root,
            {"operation": "read_obsidian_note", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "note_path": "Design/ADR-001.md"},
            capsys,
        )
        read_dir = repo_root / "runs" / "run-1" / "obsidian" / "read"
        assert (read_dir / "read-1.json").is_file()
        assert not (repo_root / "runs" / "run-1" / "obsidian" / "summary-publication.json").exists()

    def test_request_cannot_smuggle_a_vault_path(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(self._vault(tmp_path)))
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "read_obsidian_note", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "note_path": "Design/ADR-001.md", "vault_path": "/etc"},
            capsys,
        )
        assert exit_code == 2
        assert "environment" in resp["error"]


class TestRetainArchitectObsidianReads:
    """op_retain_architect_obsidian_reads -- the production bridge that consumes a
    promoted findings.json's optional `obsidian_reads` array (the Architect's own
    direct mcp__obsidian__* tool echo) and retains normalized, independently-
    classified evidence under runs/<run_id>/obsidian/read/architect-mcp-<n>.json."""

    def _vault(self, tmp_path):
        v = tmp_path / "vault"
        (v / "Harness Run Summaries").mkdir(parents=True)
        (v / "Harness Run Summaries" / "note.md").write_text("# Note\nSome content.\n", encoding="utf-8")
        return v

    def _req(self, *, findings_extra_reads=None, **overrides) -> dict:
        doc = _findings_doc()
        if findings_extra_reads is not None:
            doc["obsidian_reads"] = findings_extra_reads
        req = {"operation": "retain_architect_obsidian_reads", "run_id": "run-1", "task_id": "T-1", "findings": doc}
        req.update(overrides)
        return req

    # 1. No obsidian_reads -> existing Research behavior unchanged (a normal no-op).
    def test_no_obsidian_reads_is_a_no_op(self, repo_root, capsys) -> None:
        exit_code, resp = _run_main(repo_root, self._req(), capsys)
        assert exit_code == 0
        assert resp == {
            "operation": "retain_architect_obsidian_reads", "status": "retained",
            "run_id": "run-1", "task_id": "T-1", "count": 0, "entries": [],
        }
        assert not (repo_root / "runs" / "run-1" / "obsidian" / "read").exists()

    # 2. One negative direct MCP observation retained.
    def test_negative_observation_retained_without_manufactured_read(self, repo_root, capsys) -> None:
        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/gone.md",
             "status": "connector_unavailable"}
        ]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        assert resp["count"] == 1
        assert resp["entries"][0]["status"] == "retained"
        assert resp["entries"][0]["verification_result"] == "not_applicable"
        doc = json.loads(
            (repo_root / "runs" / "run-1" / "obsidian" / "read" / "architect-mcp-1.json").read_text()
        )
        assert doc["status"] == "connector_unavailable"
        assert doc["source"] == "architect_direct_mcp"
        assert doc["phase"] == "research"
        assert doc["verification"]["performed"] is False
        assert doc["verification"]["result"] == "not_applicable"

    # 3. Multiple observations retain deterministic ordering.
    def test_multiple_observations_retain_deterministic_ordering(self, repo_root, capsys) -> None:
        reads = [
            {"tool": "mcp__obsidian__search_notes", "query_or_note_path": "alpha", "status": "no_matches"},
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "b.md", "status": "not_found"},
            {"tool": "mcp__obsidian__search_notes", "query_or_note_path": "gamma", "status": "invalid_query"},
        ]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        assert [e["filename"] for e in resp["entries"]] == [
            "architect-mcp-1.json", "architect-mcp-2.json", "architect-mcp-3.json",
        ]
        read_dir = repo_root / "runs" / "run-1" / "obsidian" / "read"
        for n, expected_query in enumerate(["alpha", "b.md", "gamma"], start=1):
            doc = json.loads((read_dir / f"architect-mcp-{n}.json").read_text())
            assert doc["query_or_note_path"] == expected_query

    # 4. Affirmative direct read + matching independent SHA -> verified.
    def test_affirmative_read_with_matching_sha_is_verified(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        actual_sha = hashlib.sha256((vault / "Harness Run Summaries" / "note.md").read_bytes()).hexdigest()
        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/note.md",
             "status": "read", "note_path": "Harness Run Summaries/note.md", "content_sha256": actual_sha}
        ]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        assert resp["entries"][0]["verification_result"] == "verified"
        doc = json.loads(
            (repo_root / "runs" / "run-1" / "obsidian" / "read" / "architect-mcp-1.json").read_text()
        )
        assert doc["verification"]["result"] == "verified"
        assert doc["verification"]["independent_content_sha256"] == actual_sha

    # 5. Affirmative read + mismatching SHA -> hash mismatch, never treated as valid evidence.
    def test_affirmative_read_with_mismatching_sha_is_hash_mismatch(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/note.md",
             "status": "read", "note_path": "Harness Run Summaries/note.md", "content_sha256": "f" * 64}
        ]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        assert resp["entries"][0]["verification_result"] == "hash_mismatch"
        doc = json.loads(
            (repo_root / "runs" / "run-1" / "obsidian" / "read" / "architect-mcp-1.json").read_text()
        )
        assert doc["verification"]["result"] == "hash_mismatch"
        assert doc["verification"]["independent_content_sha256"] != "f" * 64

    # 6. Independent verification unavailable -> honest verification_unavailable, not silently valid.
    def test_verification_unavailable_when_connector_unconfigured(self, repo_root, monkeypatch, capsys) -> None:
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/note.md",
             "status": "read", "note_path": "Harness Run Summaries/note.md", "content_sha256": "a" * 64}
        ]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        assert resp["entries"][0]["verification_result"] == "verification_unavailable"

    # 7. No raw note body retained from agent output.
    def test_no_note_body_ever_retained(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        actual_sha = hashlib.sha256((vault / "Harness Run Summaries" / "note.md").read_bytes()).hexdigest()
        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/note.md",
             "status": "read", "note_path": "Harness Run Summaries/note.md", "content_sha256": actual_sha}
        ]
        _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        raw = (repo_root / "runs" / "run-1" / "obsidian" / "read" / "architect-mcp-1.json").read_text()
        assert "Some content" not in raw
        doc = json.loads(raw)
        assert "content" not in doc

    # 8. A search observation is never automatically re-searched (least privilege).
    def test_search_observation_is_not_automatically_researched(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

        def _forbidden_search(**kwargs):
            raise AssertionError("search_notes must never be independently re-run for retention")

        from harness.orchestrator import obsidian_reader
        monkeypatch.setattr(obsidian_reader, "search", _forbidden_search)

        reads = [{"tool": "mcp__obsidian__search_notes", "query_or_note_path": "risk band calibration", "status": "found"}]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        assert resp["entries"][0]["verification_result"] == "architect_reported"
        doc = json.loads(
            (repo_root / "runs" / "run-1" / "obsidian" / "read" / "architect-mcp-1.json").read_text()
        )
        assert doc["verification"]["performed"] is False
        assert doc["verification"]["result"] == "architect_reported"

    # 9. Collision protection: a pre-existing retained record is never overwritten or re-verified.
    def test_pre_existing_retained_record_is_not_overwritten(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        read_dir = repo_root / "runs" / "run-1" / "obsidian" / "read"
        read_dir.mkdir(parents=True)
        sentinel = {"sentinel": "already retained by an earlier attempt"}
        (read_dir / "architect-mcp-1.json").write_text(json.dumps(sentinel), encoding="utf-8")

        def _forbidden_read(**kwargs):
            raise AssertionError("an already-retained index must never be independently re-read")

        from harness.orchestrator import obsidian_reader
        monkeypatch.setattr(obsidian_reader, "read_note", _forbidden_read)

        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/note.md",
             "status": "read", "note_path": "Harness Run Summaries/note.md", "content_sha256": "a" * 64}
        ]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        assert resp["entries"][0]["status"] == "already_retained"
        assert json.loads((read_dir / "architect-mcp-1.json").read_text()) == sentinel

    # 10. Policy event emitted.
    def test_policy_event_emitted_without_note_content(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        actual_sha = hashlib.sha256((vault / "Harness Run Summaries" / "note.md").read_bytes()).hexdigest()
        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/note.md",
             "status": "read", "note_path": "Harness Run Summaries/note.md", "content_sha256": actual_sha}
        ]
        _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        events_path = repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        matching = [e for e in events if e["kind"] == "architect_obsidian_read"]
        assert len(matching) == 1
        assert matching[0]["verification_result"] == "verified"
        assert "content" not in json.dumps(matching[0])
        assert "Some content" not in json.dumps(matching[0])

    # 11. run/task identity comes from the orchestrator's own request fields, never an entry.
    def test_identity_comes_from_request_not_entry(self, repo_root, capsys) -> None:
        reads = [{"tool": "mcp__obsidian__search_notes", "query_or_note_path": "q", "status": "no_matches"}]
        exit_code, resp = _run_main(
            repo_root, self._req(findings_extra_reads=reads, run_id="run-1", task_id="T-1"), capsys
        )
        assert exit_code == 0
        doc = json.loads(
            (repo_root / "runs" / "run-1" / "obsidian" / "read" / "architect-mcp-1.json").read_text()
        )
        assert doc["run_id"] == "run-1"
        assert doc["task_id"] == "T-1"

    # 12. Checkpoint/resume does not duplicate already-retained observations.
    def test_second_call_after_resume_does_not_duplicate(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        actual_sha = hashlib.sha256((vault / "Harness Run Summaries" / "note.md").read_bytes()).hexdigest()
        reads = [
            {"tool": "mcp__obsidian__read_note", "query_or_note_path": "Harness Run Summaries/note.md",
             "status": "read", "note_path": "Harness Run Summaries/note.md", "content_sha256": actual_sha},
            {"tool": "mcp__obsidian__search_notes", "query_or_note_path": "q", "status": "no_matches"},
        ]
        req = self._req(findings_extra_reads=reads)
        exit_code, resp1 = _run_main(repo_root, req, capsys)
        assert exit_code == 0
        assert [e["status"] for e in resp1["entries"]] == ["retained", "retained"]

        # Simulate a fresh process resuming the same run with the same promoted findings.
        exit_code, resp2 = _run_main(repo_root, req, capsys)
        assert exit_code == 0
        assert [e["status"] for e in resp2["entries"]] == ["already_retained", "already_retained"]

        events_path = repo_root / "runs" / "run-1" / "logs" / "policy-events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        matching = [e for e in events if e["kind"] == "architect_obsidian_read"]
        assert len(matching) == 2  # not 4 -- the resumed call added no new events

    # 13. A malformed/unknown obsidian_reads tool remains schema-rejected at promotion,
    # unchanged by this bridge (this operation is only ever reached with already-promoted,
    # already-schema-valid findings -- see TestValidateAndPromoteFindings for the rejection
    # itself). Confirmed here only as a cross-check that this operation's own request
    # parsing never re-derives or loosens that rule.
    def test_findings_ref_resolution_matches_get_doc_convention(self, repo_root, capsys) -> None:
        findings_path = repo_root / "runs" / "run-1" / "findings.json"
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        doc = _findings_doc()
        findings_path.write_text(json.dumps(doc), encoding="utf-8")
        exit_code, resp = _run_main(
            repo_root,
            {"operation": "retain_architect_obsidian_reads", "run_id": "run-1", "task_id": "T-1",
             "findings_ref": "runs/run-1/findings.json"},
            capsys,
        )
        assert exit_code == 0
        assert resp["count"] == 0

    # 14. Existing orchestrator-mediated Obsidian reads (search_obsidian/read_obsidian_note)
    # remain unaffected by this bridge -- distinct filenames, distinct directory, no shared
    # collision-guard interaction.
    def test_does_not_collide_with_orchestrator_mediated_read_evidence(self, repo_root, monkeypatch, capsys, tmp_path) -> None:
        vault = self._vault(tmp_path)
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
        _run_main(
            repo_root,
            {"operation": "read_obsidian_note", "run_id": "run-1", "task_id": "T-1",
             "phase": "research", "note_path": "Harness Run Summaries/note.md"},
            capsys,
        )
        reads = [{"tool": "mcp__obsidian__search_notes", "query_or_note_path": "q", "status": "no_matches"}]
        exit_code, resp = _run_main(repo_root, self._req(findings_extra_reads=reads), capsys)
        assert exit_code == 0
        read_dir = repo_root / "runs" / "run-1" / "obsidian" / "read"
        assert (read_dir / "read-1.json").is_file()
        assert (read_dir / "architect-mcp-1.json").is_file()
