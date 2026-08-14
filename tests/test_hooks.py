"""Deterministic tests for the .claude/hooks/*.py scripts.

Each hook is loaded as a real module (importlib, since .claude/hooks/ is not a Python
package) and exercised through its actual main() entry point with stdin monkeypatched to
a crafted JSON payload -- this runs the real hook logic, not a reimplementation of it.
REPO_ROOT (and any path computed from it at import time) is monkeypatched to pytest's
tmp_path so no test ever touches the real repository's runs/ or .claude/hooks/logs/.

Live, real-tool-layer blocked-case demonstrations for the original three hooks (a genuine
Bash bypass of run_command.py, a genuine Bash write attempt into demo-repo/, and a genuine
Agent dispatch refused before any subagent was ever created) are retained at
docs/hooks-permission-verification.md -- this file covers exhaustive branch coverage
deterministically, the doc covers "this really works at the tool layer," not just in a
unit test. TestRecordAgentUsage (usage/cost-accounting milestone, 2026-08-14) covers the
fourth hook, record_agent_usage.py, deterministically the same way; its own real,
tool-layer-live demonstration is a controlled single-agent capture, not a crafted payload
-- see the milestone's own retained run evidence.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(f"_hook_{name}", HOOKS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_hook(module, payload: dict, monkeypatch) -> tuple[int, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    exit_code = module.main()
    return exit_code, stderr.getvalue()


# ---------------------------------------------------------------------------
# pre_dispatch_check.py
# ---------------------------------------------------------------------------


@pytest.fixture()
def pre_dispatch(tmp_path, monkeypatch):
    module = _load_hook("pre_dispatch_check")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    return module


def _agent_payload(subagent_type: str, run_id: str | None, task_id: str | None) -> dict:
    prompt_bits = []
    if task_id is not None:
        prompt_bits.append(f'"task_id": "{task_id}"')
    if run_id is not None:
        prompt_bits.append(f'"run_id": "{run_id}"')
    prompt = "{" + ", ".join(prompt_bits) + "}"
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": subagent_type, "prompt": prompt, "description": "x", "run_in_background": False},
    }


class TestPreDispatchCheck:
    def test_non_agent_tool_is_ignored(self, pre_dispatch, monkeypatch) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}
        exit_code, _ = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 0

    def test_unrelated_subagent_type_is_allowed(self, pre_dispatch, monkeypatch) -> None:
        payload = _agent_payload("general-purpose", run_id="run-x", task_id="T-X")
        exit_code, _ = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 0

    def test_missing_run_id_in_prompt_blocks(self, pre_dispatch, monkeypatch) -> None:
        payload = _agent_payload("engineer", run_id=None, task_id="T-X")
        exit_code, stderr = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 2
        assert "does not name a run_id/task_id" in stderr

    def test_engineer_without_findings_blocks(self, pre_dispatch, monkeypatch) -> None:
        payload = _agent_payload("engineer", run_id="run-1", task_id="T-1")
        exit_code, stderr = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 2
        assert "findings.json does not exist" in stderr

    def test_engineer_with_identity_mismatched_findings_blocks(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "findings.json").write_text(json.dumps({"task_id": "T-WRONG", "run_id": "run-1"}), encoding="utf-8")
        payload = _agent_payload("engineer", run_id="run-1", task_id="T-1")
        exit_code, stderr = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 2
        assert "does not match the dispatch prompt" in stderr

    def test_engineer_with_valid_promoted_findings_is_allowed(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "findings.json").write_text(json.dumps({"task_id": "T-1", "run_id": "run-1"}), encoding="utf-8")
        payload = _agent_payload("engineer", run_id="run-1", task_id="T-1")
        exit_code, _ = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 0

    def test_quality_engineer_without_implementation_report_blocks(self, pre_dispatch, monkeypatch) -> None:
        payload = _agent_payload("quality-engineer", run_id="run-1", task_id="T-1")
        exit_code, stderr = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 2
        assert "implementation-report.json does not exist" in stderr

    def test_quality_engineer_with_wrong_status_blocks(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "implementation-report.json").write_text(
            json.dumps({"task_id": "T-1", "run_id": "run-1", "status": "blocked"}), encoding="utf-8"
        )
        payload = _agent_payload("quality-engineer", run_id="run-1", task_id="T-1")
        exit_code, stderr = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 2
        assert "does not authorize this dispatch" in stderr

    def test_quality_engineer_with_ready_status_is_allowed(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "implementation-report.json").write_text(
            json.dumps({"task_id": "T-1", "run_id": "run-1", "status": "ready_for_verification"}), encoding="utf-8"
        )
        payload = _agent_payload("quality-engineer", run_id="run-1", task_id="T-1")
        exit_code, _ = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 0

    def test_quality_engineer_underscore_variant_is_normalized(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        """SKILL.md's own generic prose sometimes writes 'quality_engineer' (underscore) --
        the hook must recognize both spellings, not just the hyphenated real subagent name."""
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "implementation-report.json").write_text(
            json.dumps({"task_id": "T-1", "run_id": "run-1", "status": "ready_for_verification"}), encoding="utf-8"
        )
        payload = _agent_payload("quality_engineer", run_id="run-1", task_id="T-1")
        exit_code, _ = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 0

    def test_architect_without_approved_scope_blocks(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "scope.json").write_text(
            json.dumps({"task_id": "T-1", "run_id": "run-1", "status": "refused"}), encoding="utf-8"
        )
        payload = _agent_payload("architect", run_id="run-1", task_id="T-1")
        exit_code, stderr = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 2
        assert "does not authorize this dispatch" in stderr

    def test_architect_with_approved_scope_is_allowed(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "scope.json").write_text(
            json.dumps({"task_id": "T-1", "run_id": "run-1", "status": "approved"}), encoding="utf-8"
        )
        payload = _agent_payload("architect", run_id="run-1", task_id="T-1")
        exit_code, _ = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 0

    def test_malformed_precondition_json_blocks_rather_than_crashing(self, pre_dispatch, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "findings.json").write_text("{not valid json", encoding="utf-8")
        payload = _agent_payload("engineer", run_id="run-1", task_id="T-1")
        exit_code, stderr = _run_hook(pre_dispatch, payload, monkeypatch)
        assert exit_code == 2
        assert "not valid JSON" in stderr

    def test_malformed_stdin_fails_open(self, pre_dispatch, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("{not valid json"))
        exit_code = pre_dispatch.main()
        assert exit_code == 0


# ---------------------------------------------------------------------------
# skill_enforcement.py
# ---------------------------------------------------------------------------


@pytest.fixture()
def skill_enforcement(tmp_path, monkeypatch):
    module = _load_hook("skill_enforcement")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "LOG_PATH", tmp_path / "skill-enforcement-events.jsonl")
    return module


def _bash_payload(command: str, *, agent_id: str | None = None) -> dict:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
    if agent_id is not None:
        payload["agent_id"] = agent_id
        payload["agent_type"] = "general-purpose"
    return payload


class TestSkillEnforcement:
    def test_non_bash_tool_is_ignored(self, skill_enforcement, monkeypatch) -> None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {}}
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 0

    def test_orchestrator_direct_wrapper_call_is_blocked(self, skill_enforcement, monkeypatch) -> None:
        payload = _bash_payload('python .claude/skills/test-runner/scripts/run_command.py --request-file "x.json"')
        exit_code, stderr = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 2
        assert "outside the real test-runner Skill flow" in stderr

    def test_forked_skill_context_wrapper_call_is_allowed(self, skill_enforcement, monkeypatch) -> None:
        """The real test-runner Skill's own internal Bash call runs inside a forked
        context, which the hook payload marks with agent_id (empirically verified live --
        see docs/hooks-signal-verification.md) -- this must never be blocked."""
        payload = _bash_payload(
            'python ".claude/skills/test-runner/scripts/run_command.py" --request-file "x.json"',
            agent_id="a1234567890",
        )
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 0

    def test_demo_repo_redirect_write_is_blocked(self, skill_enforcement, monkeypatch) -> None:
        payload = _bash_payload('echo "x" > demo-repo/src/loanflow/explanations.py')
        exit_code, stderr = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 2
        assert "orchestrator fallback" in stderr

    def test_demo_repo_sed_write_is_blocked(self, skill_enforcement, monkeypatch) -> None:
        payload = _bash_payload("sed -i 's/x/y/' demo-repo/src/loanflow/explanations.py")
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 2

    def test_demo_repo_python_c_write_is_blocked(self, skill_enforcement, monkeypatch) -> None:
        payload = _bash_payload(
            "python -c \"open('demo-repo/src/loanflow/explanations.py', 'w').write('x')\""
        )
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 2

    def test_demo_repo_read_only_git_status_is_allowed(self, skill_enforcement, monkeypatch) -> None:
        payload = _bash_payload("git status --short -- demo-repo")
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 0

    def test_demo_repo_read_only_git_diff_stat_is_allowed(self, skill_enforcement, monkeypatch) -> None:
        payload = _bash_payload("git diff --stat -- demo-repo")
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 0

    def test_stderr_redirect_is_not_mistaken_for_a_write(self, skill_enforcement, monkeypatch) -> None:
        """`2>&1` is stderr redirection, not a write to a new path -- must never be
        misclassified as a demo-repo write attempt just because '>' appears."""
        payload = _bash_payload("python -m pytest demo-repo/tests -q 2>&1")
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 0

    def test_unrelated_bash_command_is_allowed(self, skill_enforcement, monkeypatch) -> None:
        payload = _bash_payload("python -m pytest tests -q")
        exit_code, _ = _run_hook(skill_enforcement, payload, monkeypatch)
        assert exit_code == 0

    def test_blocked_bypass_is_recorded_to_log(self, skill_enforcement, monkeypatch, tmp_path) -> None:
        payload = _bash_payload('python run_command.py --request-file "x.json"')
        _run_hook(skill_enforcement, payload, monkeypatch)
        log_path = tmp_path / "skill-enforcement-events.jsonl"
        assert log_path.exists()
        event = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert event["kind"] == "skill_mediation_bypass"

    def test_blocked_demo_repo_write_is_recorded_to_log(self, skill_enforcement, monkeypatch, tmp_path) -> None:
        payload = _bash_payload('echo "x" >> demo-repo/notes.txt')
        _run_hook(skill_enforcement, payload, monkeypatch)
        log_path = tmp_path / "skill-enforcement-events.jsonl"
        event = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert event["kind"] == "orchestrator_source_edit_fallback"

    def test_malformed_stdin_fails_open(self, skill_enforcement, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("{not valid json"))
        exit_code = skill_enforcement.main()
        assert exit_code == 0


# ---------------------------------------------------------------------------
# completion_guardrail.py
# ---------------------------------------------------------------------------


@pytest.fixture()
def completion_guardrail(tmp_path, monkeypatch):
    module = _load_hook("completion_guardrail")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "RUNS_DIR", tmp_path / "runs")
    return module


def _stop_payload(*, stop_hook_active: bool = False) -> dict:
    return {"hook_event_name": "Stop", "stop_hook_active": stop_hook_active}


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _mock_git_status(monkeypatch, module, modified_paths: list[str]) -> None:
    class _FakeCompleted:
        def __init__(self, stdout: str):
            self.stdout = stdout

    def fake_run(*args, **kwargs):
        lines = [f" M {p}" for p in modified_paths]
        return _FakeCompleted("\n".join(lines))

    monkeypatch.setattr(module.subprocess, "run", fake_run)


PASS_VERIFICATION = {
    "schema_version": "1.0", "task_id": "T-1", "run_id": "run-1", "created_at": "2026-08-05T00:00:00Z",
    "scope_ref": {"path": "runs/run-1/scope.json"}, "implementation_ref": {"path": "runs/run-1/implementation-report.json"},
    "attempts": [
        {"id": "V-1", "command": "python -m pytest x.py", "exit_code": 0, "classification": "pass",
         "output_ref": "runs/run-1/logs/V-1.log", "retried": False}
    ],
    "retry_policy": {"max_retries": 2},
    "acceptance_criteria_results": [
        {"criteria_id": "AC-1", "result": "passed", "attempt_refs": ["V-1"], "evidence_summary": "x"}
    ],
    "final_verdict": "pass",
    "routed_back_to_engineer": {"routed": False},
}


class TestCompletionGuardrail:
    def test_no_claim_marker_allows_stop(self, completion_guardrail, monkeypatch) -> None:
        exit_code, _ = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 0

    def test_stop_hook_active_always_allows(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        _write(tmp_path / "runs" / "run-1" / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        exit_code, _ = _run_hook(completion_guardrail, _stop_payload(stop_hook_active=True), monkeypatch)
        assert exit_code == 0

    def test_missing_verification_report_blocks(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        _write(tmp_path / "runs" / "run-1" / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "verification-report.json is missing" in stderr

    def test_invalid_json_verification_report_blocks(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        _write(run_dir / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        (run_dir / "verification-report.json").write_text("{not valid json", encoding="utf-8")
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "verification evidence is invalid" in stderr

    def test_deleted_verification_report_after_prior_pass_blocks(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        """Simulates the assignment's canonical demonstration: a run genuinely passed, then
        its verification evidence was deleted -- completion must still be blocked."""
        run_dir = tmp_path / "runs" / "run-1"
        _write(run_dir / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        _write(run_dir / "verification-report.json", PASS_VERIFICATION)
        (run_dir / "verification-report.json").unlink()  # the deletion
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "missing" in stderr.lower()

    def test_non_passing_verdict_blocks(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        _write(run_dir / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        failing = dict(PASS_VERIFICATION, final_verdict="fail")
        _write(run_dir / "verification-report.json", failing)
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "not 'pass'" in stderr

    def test_missing_run_summary_blocks(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        _write(run_dir / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        _write(run_dir / "verification-report.json", PASS_VERIFICATION)
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "run-summary.json is missing" in stderr

    def test_canonical_artifact_identity_mismatch_blocks(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        _write(run_dir / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        _write(run_dir / "verification-report.json", PASS_VERIFICATION)
        _write(run_dir / "scope.json", {"task_id": "T-WRONG", "run_id": "run-1"})
        _write(
            run_dir / "run-summary.json",
            {"artifact_refs": {"scope": "runs/run-1/scope.json", "verification_report": "runs/run-1/verification-report.json"}},
        )
        _mock_git_status(monkeypatch, completion_guardrail, [])
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "does not match this run's claimed" in stderr

    def test_git_reality_conflict_blocks(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        _write(run_dir / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        _write(run_dir / "verification-report.json", PASS_VERIFICATION)
        _write(run_dir / "run-summary.json", {"artifact_refs": {"verification_report": "runs/run-1/verification-report.json"}})
        _write(
            run_dir / "implementation-report.json",
            {"task_id": "T-1", "run_id": "run-1", "changed_files": [{"path": "demo-repo/src/x.py"}]},
        )
        _mock_git_status(monkeypatch, completion_guardrail, [])  # git shows nothing modified
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "not backed by the real working tree" in stderr

    def test_genuinely_passing_run_allows_stop_and_clears_marker(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        run_dir = tmp_path / "runs" / "run-1"
        marker = run_dir / ".completion_claim.json"
        _write(marker, {"task_id": "T-1", "run_id": "run-1"})
        _write(run_dir / "verification-report.json", PASS_VERIFICATION)
        _write(run_dir / "run-summary.json", {"artifact_refs": {"verification_report": "runs/run-1/verification-report.json"}})
        _write(
            run_dir / "implementation-report.json",
            {"task_id": "T-1", "run_id": "run-1", "changed_files": [{"path": "demo-repo/src/x.py"}]},
        )
        _mock_git_status(monkeypatch, completion_guardrail, ["demo-repo/src/x.py"])
        exit_code, _ = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 0
        assert not marker.exists()  # a genuinely passing run does not keep re-triggering this check

    def test_repair_round_canonical_filenames_are_resolved_via_run_summary(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        """A repaired run's final verification report lives at a "*.repair-1.json" path,
        not the plain name -- the guardrail must follow run-summary.json's own
        artifact_refs rather than hardcoding the pre-repair filename."""
        run_dir = tmp_path / "runs" / "run-1"
        _write(run_dir / ".completion_claim.json", {"task_id": "T-1", "run_id": "run-1"})
        _write(run_dir / "verification-report.repair-1.json", PASS_VERIFICATION)
        _write(
            run_dir / "run-summary.json",
            {"artifact_refs": {"verification_report": "runs/run-1/verification-report.repair-1.json"}},
        )
        _mock_git_status(monkeypatch, completion_guardrail, [])
        exit_code, _ = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 0

    def test_malformed_claim_json_is_reported_not_crashed(self, completion_guardrail, monkeypatch, tmp_path) -> None:
        marker = tmp_path / "runs" / "run-1" / ".completion_claim.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{not valid json", encoding="utf-8")
        exit_code, stderr = _run_hook(completion_guardrail, _stop_payload(), monkeypatch)
        assert exit_code == 2
        assert "not valid JSON" in stderr

    def test_malformed_stdin_fails_open(self, completion_guardrail, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("{not valid json"))
        exit_code = completion_guardrail.main()
        assert exit_code == 0


# ---------------------------------------------------------------------------
# record_agent_usage.py
# ---------------------------------------------------------------------------


@pytest.fixture()
def record_agent_usage(tmp_path, monkeypatch):
    module = _load_hook("record_agent_usage")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    return module


def _assistant_usage_line(*, model: str = "claude-sonnet-5", input_tokens: int = 2, output_tokens: int = 10) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
            },
        },
    })


class TestRecordAgentUsage:
    def _setup_dispatched_run(self, tmp_path, run_id: str, agent_id: str) -> Path:
        from harness.orchestrator import evidence_io

        run_directory = evidence_io.run_dir(tmp_path, run_id)
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": "research", "subagent_type": "architect", "agent_id": agent_id, "dispatch_sequence": 1},
        )
        return run_directory

    def _write_transcript(self, tmp_path, name: str) -> Path:
        path = tmp_path / "transcripts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_assistant_usage_line() + "\n", encoding="utf-8")
        return path

    def test_subagent_stop_captures_real_per_agent_record(self, record_agent_usage, monkeypatch, tmp_path) -> None:
        from harness.orchestrator import usage

        (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
        pricing_src = Path(__file__).resolve().parent.parent / "harness" / "model-pricing.json"
        (tmp_path / "harness" / "model-pricing.json").write_text(pricing_src.read_text(encoding="utf-8"), encoding="utf-8")

        self._setup_dispatched_run(tmp_path, "run-1", "agent-1")
        transcript_path = self._write_transcript(tmp_path, "agent-1.jsonl")
        payload = {
            "hook_event_name": "SubagentStop", "agent_id": "agent-1", "agent_type": "architect",
            "agent_transcript_path": str(transcript_path),
        }
        exit_code, _ = _run_hook(record_agent_usage, payload, monkeypatch)
        assert exit_code == 0
        record_path = usage.usage_record_path(tmp_path / "runs" / "run-1", "agent-1")
        assert record_path.is_file()

    def test_post_tool_use_agent_records_corroboration(self, record_agent_usage, monkeypatch, tmp_path) -> None:
        from harness.orchestrator import usage

        payload = {
            "hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": "toolu_01",
            "tool_response": {"agentId": "agent-1", "resolvedModel": "claude-sonnet-5", "usage": {"input_tokens": 2}},
        }
        exit_code, _ = _run_hook(record_agent_usage, payload, monkeypatch)
        assert exit_code == 0
        assert usage.corroboration_record_path(tmp_path, "agent-1").is_file()

    def test_unrelated_event_is_a_noop(self, record_agent_usage, monkeypatch, tmp_path) -> None:
        exit_code, _ = _run_hook(record_agent_usage, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}, monkeypatch)
        assert exit_code == 0
        assert not (tmp_path / "runs").exists()

    def test_post_tool_use_wrong_tool_name_is_a_noop(self, record_agent_usage, monkeypatch, tmp_path) -> None:
        exit_code, _ = _run_hook(
            record_agent_usage, {"hook_event_name": "PostToolUse", "tool_name": "Bash"}, monkeypatch,
        )
        assert exit_code == 0
        assert not (tmp_path / "runs" / "_usage_corroboration").exists()

    def test_malformed_stdin_fails_open(self, record_agent_usage, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("{not valid json"))
        exit_code = record_agent_usage.main()
        assert exit_code == 0

    def test_non_object_stdin_fails_open(self, record_agent_usage, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2, 3]"))
        exit_code = record_agent_usage.main()
        assert exit_code == 0

    def test_internal_error_during_capture_fails_open(self, record_agent_usage, monkeypatch, tmp_path) -> None:
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated internal failure")

        monkeypatch.setattr(record_agent_usage.usage, "capture_subagent_usage", _boom)
        payload = {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_transcript_path": "nope.jsonl"}
        exit_code, _ = _run_hook(record_agent_usage, payload, monkeypatch)
        assert exit_code == 0

    def test_unmatched_agent_is_quarantined_not_silently_dropped(self, record_agent_usage, monkeypatch, tmp_path) -> None:
        from harness.orchestrator import usage

        (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
        pricing_src = Path(__file__).resolve().parent.parent / "harness" / "model-pricing.json"
        (tmp_path / "harness" / "model-pricing.json").write_text(pricing_src.read_text(encoding="utf-8"), encoding="utf-8")

        transcript_path = self._write_transcript(tmp_path, "orphan.jsonl")
        payload = {
            "hook_event_name": "SubagentStop", "agent_id": "never-dispatched-by-any-run",
            "agent_transcript_path": str(transcript_path),
        }
        exit_code, _ = _run_hook(record_agent_usage, payload, monkeypatch)
        assert exit_code == 0
        quarantine_dir = tmp_path / "runs" / usage.UNMATCHED_USAGE_DIRNAME
        assert quarantine_dir.is_dir()
        assert len(list(quarantine_dir.glob("*.json"))) == 1
