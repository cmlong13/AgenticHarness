"""A Discovery-declared Research skip (ASSIGNMENT.md §2.1: "a typo fix doesn't need a full
research phase -- say so explicitly and skip it").

The skip is declared exactly once, in an approved scope.json's `research_skip_reason`, and
every contract derives from it: scope semantics, the Engineer pre-dispatch hook, the
checkpoint's `skipped_phases`, resume, the implementation report (no findings to cite),
the deterministic core, and the run summary. A phase Discovery did not declare skipped can
never be skipped.
"""
from __future__ import annotations

import json

import pytest

from harness.evidence import (
    validate_checkpoint_semantics,
    validate_implementation_report_semantics,
    validate_scope_semantics,
)
from harness.orchestrator import checkpoint, live_cli
from harness.orchestrator.state import State
from tests.fakes.agent_adapter import ScriptedAgentAdapter, ScriptedDiscoveryAdapter, ScriptedTestRunnerAdapter
from tests.test_hooks import _agent_payload, _load_hook, _run_hook
from tests.test_orchestrator_core import (
    DIFF_STATS,
    RUN_ID,
    TARGET_REPO_PATH,
    TASK_ID,
    _assert_valid_run_summary,
    _engineer_sequence,
    _happy_command_results,
    _make_fixture_repo,
    _policy_events,
    _qe_sequence,
    _read_json,
    _run,
    _scope_dict,
)

SKIP_REASON = "Typo fix in one docstring; no behavior to research."


def _skip_scope() -> dict:
    doc = _scope_dict()
    doc["research_skip_reason"] = SKIP_REASON
    doc["task_graph"] = [
        {"id": "N-2", "description": "Fix typo", "phase": "implementation", "depends_on": []},
        {"id": "N-3", "description": "Verify fix", "phase": "verification", "depends_on": ["N-2"]},
    ]
    return doc


def _skip_engineer_sequence() -> list[str]:
    turns = [json.loads(t) for t in _engineer_sequence()]
    turns[0]["findings_relied_on"] = []
    del turns[-1]["findings_ref"]
    return [json.dumps(t) for t in turns]


# ---------------------------------------------------------------------------
# Scope: the one place a skip is declared
# ---------------------------------------------------------------------------


class TestScopeDeclaration:
    def test_declared_skip_with_no_research_node_is_valid(self) -> None:
        assert validate_scope_semantics(_skip_scope()) == []

    def test_declared_skip_must_omit_research_from_task_graph(self) -> None:
        doc = _scope_dict()
        doc["research_skip_reason"] = SKIP_REASON
        errors = [str(e) for e in validate_scope_semantics(doc)]
        assert any("must not contain a research node" in e for e in errors)

    def test_blank_reason_is_rejected(self) -> None:
        doc = _skip_scope()
        doc["research_skip_reason"] = "   "
        assert any("non-blank reason" in str(e) for e in validate_scope_semantics(doc))

    def test_refused_scope_cannot_skip(self) -> None:
        doc = _skip_scope()
        doc["status"], doc["refusal_reason"] = "refused", "decoration"
        assert any("only an approved scope" in str(e) for e in validate_scope_semantics(doc))

    def test_skipped_phases_derive_only_from_the_declaration(self) -> None:
        assert checkpoint.skipped_phases_for(_skip_scope()) == ["research"]
        assert checkpoint.skipped_phases_for(_scope_dict()) == []
        assert checkpoint.skipped_phases_for(None) == []


# ---------------------------------------------------------------------------
# Implementation report: findings_ref follows the skip exactly
# ---------------------------------------------------------------------------


class TestImplementationReport:
    def _report(self, *, with_findings_ref: bool) -> dict:
        report = json.loads(_engineer_sequence()[-1])
        if not with_findings_ref:
            del report["findings_ref"]
        return report

    def test_skipped_research_report_without_findings_ref_is_valid(self) -> None:
        assert validate_implementation_report_semantics(self._report(with_findings_ref=False), None) == []

    def test_skipped_research_report_must_not_cite_findings(self) -> None:
        errors = validate_implementation_report_semantics(self._report(with_findings_ref=True), None)
        assert any("Research was skipped" in str(e) for e in errors)

    def test_normal_report_still_requires_findings_ref(self) -> None:
        errors = validate_implementation_report_semantics(self._report(with_findings_ref=False), {"findings": []})
        assert any("must cite its findings" in str(e) for e in errors)


# ---------------------------------------------------------------------------
# Checkpoint semantics
# ---------------------------------------------------------------------------


def _checkpoint(**overrides) -> dict:
    doc = {
        "completed_phases": ["discovery"],
        "skipped_phases": ["research"],
        "current_phase": "implementation",
        "next_phase": "verification",
        "artifact_refs": {"discovery": f"runs/{RUN_ID}/scope.json"},
        "status": "in_progress",
    }
    doc.update(overrides)
    return doc


class TestCheckpointSemantics:
    def test_declared_skip_satisfies_phase_order(self) -> None:
        assert validate_checkpoint_semantics(_checkpoint()) == []

    def test_undeclared_gap_is_still_rejected(self) -> None:
        doc = _checkpoint(completed_phases=["discovery", "implementation"], current_phase="verification", next_phase=None)
        doc.pop("skipped_phases")
        doc["artifact_refs"]["implementation"] = "x"
        assert any("no undeclared phase skipped" in str(e) for e in validate_checkpoint_semantics(doc))

    def test_skipped_phase_cannot_also_be_completed_or_current(self) -> None:
        assert validate_checkpoint_semantics(_checkpoint(current_phase="research"))
        doc = _checkpoint(completed_phases=["discovery", "research"])
        doc["artifact_refs"]["research"] = "x"
        assert validate_checkpoint_semantics(doc)

    def test_complete_with_declared_skip_needs_only_three_phases(self) -> None:
        refs = {"discovery": "a", "implementation": "b", "verification": "c"}
        complete = _checkpoint(
            completed_phases=["discovery", "implementation", "verification"], current_phase=None,
            next_phase=None, next_resume_action=None, artifact_refs=refs, status="complete",
        )
        assert validate_checkpoint_semantics(complete) == []
        complete.pop("skipped_phases")
        assert any("missing" in str(e) for e in validate_checkpoint_semantics(complete))


# ---------------------------------------------------------------------------
# Deterministic core: run, then resume
# ---------------------------------------------------------------------------


def _skip_run(tmp_path):
    _make_fixture_repo(tmp_path)
    agent_adapter = ScriptedAgentAdapter(
        {"architect": [], "engineer": _skip_engineer_sequence(), "quality_engineer": _qe_sequence(verdict="pass")}
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)
    result = _run(tmp_path, ScriptedDiscoveryAdapter(_skip_scope()), agent_adapter, test_runner_adapter)
    return result, agent_adapter


def test_core_run_skips_research_explicitly_and_completes(tmp_path):
    result, agent_adapter = _skip_run(tmp_path)

    assert result.state == State.COMPLETED and result.final_verdict == "pass"
    assert agent_adapter.start_count("architect") == 0
    assert not (result.run_dir / "findings.json").exists()
    engineer_payload = next(p for op, at, _h, p in agent_adapter.calls if op == "start" and at == "engineer")
    assert "findings_ref" not in engineer_payload

    summary = _assert_valid_run_summary(result)
    assert summary["phases_completed"] == ["discovery", "implementation", "verification"]
    assert summary["phases_skipped"] == ["research"]
    assert "findings" not in summary["artifact_refs"]

    final_checkpoint = _read_json(result.run_dir / "checkpoint.json")
    assert final_checkpoint["status"] == "complete"
    assert final_checkpoint["skipped_phases"] == ["research"]

    skipped = [e for e in _policy_events(result) if e["kind"] == "phase_skipped"]
    assert skipped and skipped[0]["phase"] == "research" and skipped[0]["reason"] == SKIP_REASON


def test_resume_never_dispatches_a_declared_skipped_phase(tmp_path):
    result, _ = _skip_run(tmp_path)
    run_dir = result.run_dir
    # Rewind to "Discovery done, Research skipped, Implementation interrupted".
    (run_dir / "run-summary.json").unlink()
    checkpoint.record_phase_progress(
        run_dir, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at="2026-10-01T00:00:00Z", completed_phases=["discovery"],
        artifact_refs={"discovery": f"runs/{RUN_ID}/scope.json"}, skipped_phases=["research"],
    )

    decision = checkpoint.evaluate_resume(run_dir, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert decision.resumable, decision.reason
    assert decision.skipped_phases == ["research"]
    assert decision.next_phase == "implementation"


def test_resume_refuses_a_checkpoint_skip_the_scope_never_declared(tmp_path):
    result, _ = _skip_run(tmp_path)
    run_dir = result.run_dir
    (run_dir / "run-summary.json").unlink()
    scope = _read_json(run_dir / "scope.json")
    scope.pop("research_skip_reason")
    scope["task_graph"].insert(0, {"id": "N-1", "description": "Research", "phase": "research", "depends_on": []})
    (run_dir / "scope.json").write_text(json.dumps(scope), encoding="utf-8")
    checkpoint.record_phase_progress(
        run_dir, task_id=TASK_ID, run_id=RUN_ID, target_repo_path=TARGET_REPO_PATH,
        updated_at="2026-10-01T00:00:00Z", completed_phases=["discovery"],
        artifact_refs={"discovery": f"runs/{RUN_ID}/scope.json"}, skipped_phases=["research"],
    )

    decision = checkpoint.evaluate_resume(run_dir, requested_run_id=RUN_ID, repo_root=tmp_path)
    assert not decision.resumable
    assert "does not match the scope's declared skips" in decision.reason


# ---------------------------------------------------------------------------
# Live bridge: implementation-report validation context
# ---------------------------------------------------------------------------


class TestLiveValidationContext:
    def _write_scope(self, tmp_path, monkeypatch, scope: dict) -> str:
        monkeypatch.setattr(live_cli, "REPO_ROOT", tmp_path)
        path = tmp_path / "runs" / RUN_ID / "scope.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(scope), encoding="utf-8")
        return f"runs/{RUN_ID}/scope.json"

    def test_report_without_findings_validates_against_declared_skip(self, tmp_path, monkeypatch) -> None:
        scope_ref = self._write_scope(tmp_path, monkeypatch, _skip_scope())
        report = json.loads(_skip_engineer_sequence()[-1])
        assert live_cli._validate_phase_doc("implementation", "implementation-report", report, {"scope": scope_ref}) == []

    def test_omitting_findings_without_declared_skip_is_refused(self, tmp_path, monkeypatch) -> None:
        scope_ref = self._write_scope(tmp_path, monkeypatch, _scope_dict())
        report = json.loads(_skip_engineer_sequence()[-1])
        with pytest.raises(live_cli.LiveCliUsageError):
            live_cli._validate_phase_doc("implementation", "implementation-report", report, {"scope": scope_ref})


# ---------------------------------------------------------------------------
# Pre-dispatch hook
# ---------------------------------------------------------------------------


@pytest.fixture()
def pre_dispatch(tmp_path, monkeypatch):
    module = _load_hook("pre_dispatch_check")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    return module


def _promote_scope(tmp_path, scope: dict) -> None:
    path = tmp_path / "runs" / RUN_ID / "scope.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scope), encoding="utf-8")


class TestPreDispatchHook:
    def test_engineer_allowed_without_findings_when_skip_declared(self, pre_dispatch, tmp_path, monkeypatch) -> None:
        _promote_scope(tmp_path, _skip_scope())
        exit_code, _ = _run_hook(pre_dispatch, _agent_payload("engineer", RUN_ID, TASK_ID), monkeypatch)
        assert exit_code == 0

    def test_engineer_still_blocked_without_findings_when_no_skip(self, pre_dispatch, tmp_path, monkeypatch) -> None:
        _promote_scope(tmp_path, _scope_dict())
        exit_code, stderr = _run_hook(pre_dispatch, _agent_payload("engineer", RUN_ID, TASK_ID), monkeypatch)
        assert exit_code == 2 and "findings.json does not exist" in stderr

    def test_engineer_blocked_when_skip_scope_belongs_to_another_run(self, pre_dispatch, tmp_path, monkeypatch) -> None:
        scope = _skip_scope()
        scope["task_id"] = "T-OTHER"
        _promote_scope(tmp_path, scope)
        exit_code, _ = _run_hook(pre_dispatch, _agent_payload("engineer", RUN_ID, TASK_ID), monkeypatch)
        assert exit_code == 2

    def test_architect_refused_when_research_declared_skipped(self, pre_dispatch, tmp_path, monkeypatch) -> None:
        _promote_scope(tmp_path, _skip_scope())
        exit_code, stderr = _run_hook(pre_dispatch, _agent_payload("architect", RUN_ID, TASK_ID), monkeypatch)
        assert exit_code == 2 and "declared Research skipped" in stderr
