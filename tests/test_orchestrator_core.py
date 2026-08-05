"""End-to-end tests for harness/orchestrator/core.py using scripted fakes only.

No live Claude Code agent is ever launched here -- every AgentAdapter/TestRunnerAdapter/
DiscoveryAdapter is the deterministic double from tests/fakes/agent_adapter.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness.evidence import load_json, validate_against_schema
from harness.orchestrator import core, evidence_io
from harness.orchestrator.adapters import UnresumableHandleError
from harness.orchestrator.state import State

from tests.fakes.agent_adapter import UNRESUMABLE, ScriptedAgentAdapter, ScriptedDiscoveryAdapter, ScriptedTestRunnerAdapter

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "harness" / "schemas"

TASK_ID = "T-1"
RUN_ID = "run-1"
CREATED_AT = "2026-08-03T00:00:00Z"
TARGET_REPO_PATH = "fixture-repo"
SCOPE_REF = f"runs/{RUN_ID}/scope.json"
FINDINGS_REF = f"runs/{RUN_ID}/findings.json"
IMPLEMENTATION_REF = f"runs/{RUN_ID}/implementation-report.json"


# ---------------------------------------------------------------------------
# Fixture builders -- one small, composable helper per artifact/protocol turn.
# ---------------------------------------------------------------------------


def _make_fixture_repo(root: Path) -> None:
    fixture = root / TARGET_REPO_PATH
    (fixture / "src").mkdir(parents=True)
    (fixture / "tests").mkdir(parents=True)
    (fixture / "src" / "pagination.py").write_text("def paginate():\n    pass\n", encoding="utf-8")
    (fixture / "tests" / "test_pagination.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")


def _scope_dict(*, status="approved", refusal_reason=None, acceptance_criteria=None, in_scope=None) -> dict:
    doc = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "created_at": CREATED_AT,
        "source": {"type": "prompt", "raw_prompt": "Fix off-by-one in pagination helper"},
        "objective": "Fix off-by-one in pagination helper",
        "in_scope": in_scope
        or [f"{TARGET_REPO_PATH}/src/pagination.py", f"{TARGET_REPO_PATH}/tests/test_pagination.py"],
        "out_of_scope": [],
        "constraints": [],
        "acceptance_criteria": acceptance_criteria
        or [{"id": "AC-1", "description": "Existing pagination tests pass; new test covers the off-by-one case"}],
        "task_graph": [
            {"id": "N-1", "description": "Research pagination helper", "phase": "research", "depends_on": []},
            {"id": "N-2", "description": "Fix off-by-one", "phase": "implementation", "depends_on": ["N-1"]},
            {"id": "N-3", "description": "Verify fix", "phase": "verification", "depends_on": ["N-2"]},
        ],
        "status": status,
    }
    if refusal_reason is not None:
        doc["refusal_reason"] = refusal_reason
    return doc


def _findings_dict(*, finding_id="F-1") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF},
        "findings": [
            {
                "id": finding_id,
                "claim": "paginate() has an off-by-one boundary bug",
                "classification": "found",
                "evidence": [
                    {
                        "path": f"{TARGET_REPO_PATH}/src/pagination.py",
                        "start_line": 1,
                        "end_line": 2,
                        "evidence_tier": "executable_code",
                    }
                ],
            }
        ],
    }


def _engineer_changed_files(*, protected_extra: str | None = None) -> list[dict]:
    files = [
        {"path": f"{TARGET_REPO_PATH}/src/pagination.py", "change_type": "modified"},
        {"path": f"{TARGET_REPO_PATH}/tests/test_pagination.py", "change_type": "modified"},
    ]
    if protected_extra:
        files.append({"path": protected_extra, "change_type": "modified"})
    return files


def _engineer_sequence(
    *, finding_id="F-1", reused_command_id_bug=False, protected_extra: str | None = None
) -> list[str]:
    pre = {
        "response_type": "pre_test_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "test_created": {
            "test_file": f"{TARGET_REPO_PATH}/tests/test_pagination.py",
            "test_name": "test_last_page_boundary",
            "status": "added",
        },
        "requested_command": {
            "id": "C-1",
            "command": "python -m pytest tests/test_pagination.py -k boundary",
            "working_directory": TARGET_REPO_PATH,
            "expected_outcome": "fail",
            "expected_failure_reason": "boundary case not yet handled",
        },
        "findings_relied_on": [finding_id],
        "minimal_change_rung_plan": 2,
    }
    post = {
        "response_type": "post_test_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "pre_test_confirmation": {
            "command_id": "C-1",
            "reported_exit_code": 1,
            "failure_reason_confirmed": True,
            "confirmation_rationale": "matches expected boundary failure",
        },
        "changed_files": _engineer_changed_files(protected_extra=protected_extra),
        "minimal_change_rung": 2,
        "minimal_change_rationale": "Reused existing paginate() function and fixed boundary arithmetic.",
        "tests": [
            {"test_file": f"{TARGET_REPO_PATH}/tests/test_pagination.py", "test_name": "test_last_page_boundary", "status": "added"}
        ],
        "dependency_changes": [],
        "requested_command": {
            "id": "C-1" if reused_command_id_bug else "C-2",
            "command": "python -m pytest tests/test_pagination.py",
            "working_directory": TARGET_REPO_PATH,
            "expected_outcome": "pass",
        },
    }
    finalization = {
        "response_type": "finalization_evidence_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "changed_files_known": _engineer_changed_files(protected_extra=protected_extra),
        "note": "Supply real lines_added/lines_removed.",
    }
    changed_files_final = [
        {"path": f"{TARGET_REPO_PATH}/src/pagination.py", "change_type": "modified", "lines_added": 1, "lines_removed": 1},
        {"path": f"{TARGET_REPO_PATH}/tests/test_pagination.py", "change_type": "modified", "lines_added": 8, "lines_removed": 0},
    ]
    if protected_extra:
        changed_files_final.append(
            {"path": protected_extra, "change_type": "modified", "lines_added": 1, "lines_removed": 1}
        )
    final = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF},
        "findings_ref": {"path": FINDINGS_REF, "finding_ids": [finding_id]},
        "minimal_change_rung": 2,
        "minimal_change_rationale": "Reused existing paginate() function and fixed boundary arithmetic.",
        "changed_files": changed_files_final,
        "diff_ref": f"runs/{RUN_ID}/diff.patch",
        "commands": [
            {
                "id": "C-1",
                "stage": "pre_implementation",
                "command": "python -m pytest tests/test_pagination.py -k boundary",
                "exit_code": 1,
                "output_ref": f"runs/{RUN_ID}/logs/C-1.log",
            },
            {
                "id": "C-1" if reused_command_id_bug else "C-2",
                "stage": "post_implementation",
                "command": "python -m pytest tests/test_pagination.py",
                "exit_code": 0,
                "output_ref": f"runs/{RUN_ID}/logs/C-2.log",
            },
        ],
        "test_first_evidence": {
            "pre_implementation_command_id": "C-1",
            "post_implementation_command_id": "C-1" if reused_command_id_bug else "C-2",
        },
        "tests": [
            {"test_file": f"{TARGET_REPO_PATH}/tests/test_pagination.py", "test_name": "test_last_page_boundary", "status": "added"}
        ],
        "dependency_changes": [],
        "status": "ready_for_verification",
    }
    return [json.dumps(pre), json.dumps(post), json.dumps(finalization), json.dumps(final)]


def _qe_sequence(*, verdict="pass", criteria_ids=("AC-1",)) -> list[str]:
    attempt = {
        "response_type": "attempt_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "requested_command": {
            "id": "V-1",
            "command": "python -m pytest tests/test_pagination.py",
            "working_directory": TARGET_REPO_PATH,
            "criteria_ids_targeted": list(criteria_ids),
            "rationale": "run pagination tests to verify acceptance criteria",
        },
    }
    if verdict == "pass":
        exit_code, classification, result = 0, "pass", "passed"
    elif verdict == "fail":
        exit_code, classification, result = 1, "logic_bug", "failed"
    else:  # inconclusive -- an unresolved environment failure, never retried, never "logic_bug"
        exit_code, classification, result = 1, "environment", "blocked"

    acceptance_criteria_results = [
        {
            "criteria_id": cid,
            "result": result,
            "attempt_refs": ["V-1"],
            "evidence_summary": "pagination tests exercised via V-1",
        }
        for cid in criteria_ids
    ]
    final = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF},
        "implementation_ref": {"path": IMPLEMENTATION_REF},
        "attempts": [
            {
                "id": "V-1",
                "command": "python -m pytest tests/test_pagination.py",
                "exit_code": exit_code,
                "classification": classification,
                "output_ref": f"runs/{RUN_ID}/logs/V-1.log",
                "retried": False,
            }
        ],
        "retry_policy": {"max_retries": 2},
        "acceptance_criteria_results": acceptance_criteria_results,
        "final_verdict": verdict,
        "routed_back_to_engineer": (
            {"routed": True, "reason": "AC-1 failed: logic_bug on V-1"} if verdict == "fail" else {"routed": False}
        ),
    }
    return [json.dumps(attempt), json.dumps(final)]


def _command_result(command_id, command, working_directory, exit_code) -> dict:
    return {
        "message_type": "command_result",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "command_id": command_id,
        "command": command,
        "working_directory": working_directory,
        "exit_code": exit_code,
        "output_ref": f"runs/{RUN_ID}/logs/{command_id}.log",
    }


DIFF_STATS = [
    {"path": f"{TARGET_REPO_PATH}/src/pagination.py", "change_type": "modified", "lines_added": 1, "lines_removed": 1},
    {"path": f"{TARGET_REPO_PATH}/tests/test_pagination.py", "change_type": "modified", "lines_added": 8, "lines_removed": 0},
]


def _happy_command_results() -> dict:
    return {
        "C-1": _command_result("C-1", "python -m pytest tests/test_pagination.py -k boundary", TARGET_REPO_PATH, 1),
        "C-2": _command_result("C-2", "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, 0),
        "V-1": _command_result("V-1", "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, 0),
    }


# ---------------------------------------------------------------------------
# Same-run logic-failure route-back fixtures (Part 2)
# ---------------------------------------------------------------------------


def _engineer_repair_sequence(*, finding_id="F-1", pre_exit_code=1, post_command_id="C-4") -> list[str]:
    """A second Engineer cycle, driven entirely by resume() (never start()), fixing the
    logic bug the Quality Engineer reported. Structurally identical in shape to
    _engineer_sequence(), with fresh command ids (C-3/C-4) so retained evidence never
    collides with the original round's C-1/C-2."""
    pre = {
        "response_type": "pre_test_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "test_created": {
            "test_file": f"{TARGET_REPO_PATH}/tests/test_pagination.py",
            "test_name": "test_last_page_boundary_regression",
            "status": "added",
        },
        "requested_command": {
            "id": "C-3",
            "command": "python -m pytest tests/test_pagination.py -k regression",
            "working_directory": TARGET_REPO_PATH,
            "expected_outcome": "fail",
            "expected_failure_reason": "reproduces the logic bug the Quality Engineer reported",
        },
        "findings_relied_on": [finding_id],
        "minimal_change_rung_plan": 2,
    }
    post = {
        "response_type": "post_test_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "pre_test_confirmation": {
            "command_id": "C-3",
            "reported_exit_code": pre_exit_code,
            "failure_reason_confirmed": True,
            "confirmation_rationale": "reproduces the reported logic bug",
        },
        "changed_files": _engineer_changed_files(),
        "minimal_change_rung": 2,
        "minimal_change_rationale": "Corrected the boundary arithmetic the Quality Engineer's failing attempt exposed.",
        "tests": [
            {
                "test_file": f"{TARGET_REPO_PATH}/tests/test_pagination.py",
                "test_name": "test_last_page_boundary_regression",
                "status": "added",
            }
        ],
        "dependency_changes": [],
        "requested_command": {
            "id": post_command_id,
            "command": "python -m pytest tests/test_pagination.py",
            "working_directory": TARGET_REPO_PATH,
            "expected_outcome": "pass",
        },
    }
    finalization = {
        "response_type": "finalization_evidence_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "changed_files_known": _engineer_changed_files(),
        "note": "Supply real lines_added/lines_removed.",
    }
    final = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF},
        "findings_ref": {"path": FINDINGS_REF, "finding_ids": [finding_id]},
        "minimal_change_rung": 2,
        "minimal_change_rationale": "Corrected the boundary arithmetic the Quality Engineer's failing attempt exposed.",
        "changed_files": [
            {"path": f"{TARGET_REPO_PATH}/src/pagination.py", "change_type": "modified", "lines_added": 1, "lines_removed": 1},
            {"path": f"{TARGET_REPO_PATH}/tests/test_pagination.py", "change_type": "modified", "lines_added": 6, "lines_removed": 0},
        ],
        "diff_ref": f"runs/{RUN_ID}/diff.repair-1.patch",
        "commands": [
            {
                "id": "C-3",
                "stage": "pre_implementation",
                "command": "python -m pytest tests/test_pagination.py -k regression",
                "exit_code": pre_exit_code,
                "output_ref": f"runs/{RUN_ID}/logs/C-3.log",
            },
            {
                "id": post_command_id,
                "stage": "post_implementation",
                "command": "python -m pytest tests/test_pagination.py",
                "exit_code": 0,
                "output_ref": f"runs/{RUN_ID}/logs/{post_command_id}.log",
            },
        ],
        "test_first_evidence": {
            "pre_implementation_command_id": "C-3",
            "post_implementation_command_id": post_command_id,
        },
        "tests": [
            {
                "test_file": f"{TARGET_REPO_PATH}/tests/test_pagination.py",
                "test_name": "test_last_page_boundary_regression",
                "status": "added",
            }
        ],
        "dependency_changes": [],
        "status": "ready_for_verification",
    }
    return [json.dumps(pre), json.dumps(post), json.dumps(finalization), json.dumps(final)]


def _qe_repair_sequence(*, verdict="pass", command_id="V-2", criteria_ids=("AC-1",)) -> list[str]:
    """A second Quality Engineer turn, driven entirely by resume() (never start()),
    re-verifying the repaired implementation report."""
    attempt = {
        "response_type": "attempt_requested",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "requested_command": {
            "id": command_id,
            "command": "python -m pytest tests/test_pagination.py",
            "working_directory": TARGET_REPO_PATH,
            "criteria_ids_targeted": list(criteria_ids),
            "rationale": "re-verify pagination tests after the Engineer's logic-bug repair",
        },
    }
    if verdict == "pass":
        exit_code, classification, result = 0, "pass", "passed"
    else:  # "fail" -- the repair did not resolve the logic bug
        exit_code, classification, result = 1, "logic_bug", "failed"

    final = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF},
        "implementation_ref": {"path": f"runs/{RUN_ID}/implementation-report.repair-1.json"},
        "attempts": [
            {
                "id": command_id,
                "command": "python -m pytest tests/test_pagination.py",
                "exit_code": exit_code,
                "classification": classification,
                "output_ref": f"runs/{RUN_ID}/logs/{command_id}.log",
                "retried": False,
            }
        ],
        "retry_policy": {"max_retries": 2},
        "acceptance_criteria_results": [
            {
                "criteria_id": cid,
                "result": result,
                "attempt_refs": [command_id],
                "evidence_summary": f"pagination tests re-exercised via {command_id} after repair",
            }
            for cid in criteria_ids
        ],
        "final_verdict": verdict,
        "routed_back_to_engineer": (
            {"routed": True, "reason": f"AC-1 failed again: logic_bug on {command_id}"}
            if verdict == "fail"
            else {"routed": False}
        ),
    }
    return [json.dumps(attempt), json.dumps(final)]


def _repair_command_results(*, pre_exit_code=1, post_command_id="C-4", reverify_command_id="V-2", reverify_exit_code=0) -> dict:
    return {
        "C-3": _command_result(
            "C-3", "python -m pytest tests/test_pagination.py -k regression", TARGET_REPO_PATH, pre_exit_code
        ),
        post_command_id: _command_result(
            post_command_id, "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, 0
        ),
        reverify_command_id: _command_result(
            reverify_command_id, "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, reverify_exit_code
        ),
    }


def _policy_events(result) -> list[dict]:
    log_path = result.run_dir / "logs" / "policy-events.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run(root, discovery_adapter, agent_adapter, test_runner_adapter, **overrides):
    kwargs = dict(
        raw_prompt="Fix off-by-one in pagination helper",
        target_repo_path=TARGET_REPO_PATH,
        task_id=TASK_ID,
        run_id=RUN_ID,
        created_at=CREATED_AT,
        discovery_adapter=discovery_adapter,
        agent_adapter=agent_adapter,
        test_runner_adapter=test_runner_adapter,
        repo_root=root,
    )
    kwargs.update(overrides)
    return core.run(**kwargs)


def _assert_valid_run_summary(result) -> dict:
    summary_path = result.run_dir / "run-summary.json"
    assert summary_path.exists(), "every terminal outcome must produce run-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    schema = load_json(SCHEMA_DIR / "run-summary.schema.json")
    errors = validate_against_schema(summary, schema, artifact="run-summary.json")
    assert not errors, "\n".join(str(e) for e in errors)
    assert summary["task_id"] == TASK_ID
    assert summary["run_id"] == RUN_ID
    return summary


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_reaches_completed(tmp_path):
    _make_fixture_repo(tmp_path)
    before = _hash_tree(tmp_path / TARGET_REPO_PATH)

    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": _qe_sequence(verdict="pass"),
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.COMPLETED
    assert result.final_verdict == "pass"
    for name in (
        "scope.json",
        "findings.json",
        "implementation-report.json",
        "verification-report.json",
        "run-summary.json",
    ):
        assert (result.run_dir / name).exists(), name

    summary = _assert_valid_run_summary(result)
    assert summary["phases_completed"] == ["discovery", "research", "implementation", "verification"]
    assert summary["final_verdict"] == "pass"

    # Discovery must never touch the target repository.
    assert _hash_tree(tmp_path / TARGET_REPO_PATH) == before

    # Continuity: exactly one start() per staged agent, never re-dispatched.
    assert agent_adapter.start_count("architect") == 1
    assert agent_adapter.start_count("engineer") == 1
    assert agent_adapter.start_count("quality_engineer") == 1


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_refused_scope_blocks_research_and_produces_run_summary(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict(status="refused", refusal_reason="ambiguous request"))
    agent_adapter = ScriptedAgentAdapter({"architect": []})
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.DISCOVERY_REFUSED
    assert result.final_verdict == "blocked"
    assert agent_adapter.start_count("architect") == 0
    summary = _assert_valid_run_summary(result)
    assert summary["phases_completed"] == ["discovery"]

    # A refusal is itself a legitimate, schema-valid Discovery outcome -- status: "refused" is a
    # first-class value in scope.schema.json, unlike an invalid draft. It IS promoted to the
    # real canonical scope.json, and artifact_refs.scope must point to exactly that real file,
    # not a placeholder -- this is the opposite case from an invalid draft (see below).
    canonical_scope_path = result.run_dir / "scope.json"
    assert canonical_scope_path.exists()
    assert result.artifact_refs["scope"] == f"runs/{RUN_ID}/scope.json"
    scope_on_disk = _read_json(canonical_scope_path)
    assert scope_on_disk["status"] == "refused"
    assert scope_on_disk["refusal_reason"] == "ambiguous request"

    # The summary must not read as a completed, working implementation.
    assert summary["final_verdict"] != "pass"
    assert "Verification passed" not in summary["objective_summary"]
    assert "discovery_refused" in summary["objective_summary"]


def test_schema_invalid_scope_blocks_research(tmp_path):
    _make_fixture_repo(tmp_path)
    broken = _scope_dict()
    del broken["objective"]
    discovery_adapter = ScriptedDiscoveryAdapter(broken)
    agent_adapter = ScriptedAgentAdapter({"architect": []})
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.DISCOVERY_INVALID
    assert agent_adapter.start_count("architect") == 0

    # No valid scope draft was ever produced, so no canonical scope.json can honestly exist.
    # artifact_refs.scope must reference retained candidate/error evidence instead -- a real,
    # existing file, but never the canonical scope.json path, and never described as accepted.
    assert not (result.run_dir / "scope.json").exists()
    scope_ref = result.artifact_refs["scope"]
    assert scope_ref != f"runs/{RUN_ID}/scope.json"
    assert "attempts/" in scope_ref
    referenced_path = tmp_path / scope_ref
    assert referenced_path.exists()
    assert "no scope draft was ever produced or promoted" in referenced_path.read_text(encoding="utf-8")

    summary = _assert_valid_run_summary(result)
    assert summary["final_verdict"] != "pass"
    assert "discovery_invalid" in summary["objective_summary"]


def test_semantically_invalid_scope_blocks_research(tmp_path):
    _make_fixture_repo(tmp_path)
    broken = _scope_dict()
    broken["task_graph"][1]["depends_on"] = ["N-99"]
    discovery_adapter = ScriptedDiscoveryAdapter(broken)
    agent_adapter = ScriptedAgentAdapter({"architect": []})
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.DISCOVERY_INVALID
    assert agent_adapter.start_count("architect") == 0

    assert not (result.run_dir / "scope.json").exists()
    scope_ref = result.artifact_refs["scope"]
    assert scope_ref != f"runs/{RUN_ID}/scope.json"
    assert "attempts/" in scope_ref
    referenced_path = tmp_path / scope_ref
    assert referenced_path.exists()
    assert "no scope draft was ever produced or promoted" in referenced_path.read_text(encoding="utf-8")

    summary = _assert_valid_run_summary(result)
    assert summary["final_verdict"] != "pass"
    assert "discovery_invalid" in summary["objective_summary"]


def test_protected_in_scope_path_is_rejected_at_core_level(tmp_path):
    # in_scope entries are target-repo-root-relative, like target_repo_path itself, so a path
    # can be both genuinely contained under target_repo_path AND match a Protected Path pattern
    # -- constructed here via the "runs/**/scope.json" pattern against target_repo_path="runs".
    (tmp_path / "runs").mkdir()
    broken = _scope_dict(in_scope=["runs/some-run/scope.json"])
    broken["task_id"] = TASK_ID
    discovery_adapter = ScriptedDiscoveryAdapter(broken)
    agent_adapter = ScriptedAgentAdapter({"architect": []})
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter, target_repo_path="runs")

    assert result.state == State.DISCOVERY_INVALID
    assert "protected pattern" in result.reason
    assert agent_adapter.start_count("architect") == 0


# ---------------------------------------------------------------------------
# Internal orchestration failure
# ---------------------------------------------------------------------------


def test_internal_failure_after_run_identity_exists_produces_failed_summary(tmp_path, monkeypatch):
    """Simulates a genuinely unexpected internal defect (not an agent/validation failure) in an
    evidence operation, occurring after task_id/run_id and the run directory already exist. This
    must land in State.FAILED via run()'s top-level safety net, never propagate as an unhandled
    exception, and never be silently swallowed into a false success."""
    _make_fixture_repo(tmp_path)

    real_promote_canonical = evidence_io.promote_canonical

    def _boom(run_directory, filename, doc):
        if filename == "scope.json":
            raise RuntimeError("simulated internal defect in evidence persistence")
        return real_promote_canonical(run_directory, filename, doc)

    monkeypatch.setattr(evidence_io, "promote_canonical", _boom)

    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter({"architect": []})
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.FAILED
    assert result.final_verdict == "blocked"
    assert "internal orchestration error" in result.reason
    assert "simulated internal defect" in result.reason

    # No later phase was ever reached.
    assert agent_adapter.start_count("architect") == 0

    # The raw Discovery candidate (retained before the crash) and the fallback error-evidence
    # placeholder (retained by _finalize's safety net) must both survive as real evidence.
    assert (result.run_dir / "attempts" / "discovery-1.raw.txt").exists()
    assert (result.run_dir / "attempts" / "discovery-error-1.raw.txt").exists()
    assert not (result.run_dir / "scope.json").exists()

    summary = _assert_valid_run_summary(result)
    assert summary["final_verdict"] != "pass"
    assert summary["final_verdict"] == "blocked"
    assert "internal orchestration error" in summary["objective_summary"]
    assert summary["phases_completed"] == []


# ---------------------------------------------------------------------------
# Research (Architect)
# ---------------------------------------------------------------------------


def test_blocked_architect_output_stops_research_cleanly(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter({"architect": ["I cannot comply with this request."]})
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.RESEARCH_BLOCKED
    raw_path = result.run_dir / "attempts" / "research-1.raw.txt"
    assert raw_path.read_text(encoding="utf-8") == "I cannot comply with this request."
    assert not (result.run_dir / "findings.json").exists()
    _assert_valid_run_summary(result)


def test_invalid_findings_block_implementation(tmp_path):
    _make_fixture_repo(tmp_path)
    broken_findings = _findings_dict()
    broken_findings["findings"].append(dict(broken_findings["findings"][0]))  # duplicate id F-1
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {"architect": [json.dumps(broken_findings)], "engineer": []}
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.RESEARCH_BLOCKED
    assert not (result.run_dir / "findings.json").exists()
    assert agent_adapter.start_count("engineer") == 0
    _assert_valid_run_summary(result)


# ---------------------------------------------------------------------------
# Implementation (Engineer)
# ---------------------------------------------------------------------------


def test_blocked_engineer_output_before_any_test_request_stops_cleanly(tmp_path):
    _make_fixture_repo(tmp_path)
    blocked_report = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF},
        "dependency_changes": [],
        "status": "blocked",
        "blocked_reason": "path_validation attestation missing",
    }
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": [json.dumps(blocked_report)],
            "quality_engineer": [],
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert test_runner_adapter.invoked_requests == []
    assert agent_adapter.start_count("quality_engineer") == 0
    assert (result.run_dir / "implementation-report.json").exists()  # a valid `blocked` report IS retained
    _assert_valid_run_summary(result)


def test_invalid_implementation_report_blocks_verification(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            # findings_ref references a finding id that was never classified 'found'.
            "engineer": _engineer_sequence(finding_id="F-DOES-NOT-EXIST"),
            "quality_engineer": [],
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert not (result.run_dir / "implementation-report.json").exists()
    assert agent_adapter.start_count("quality_engineer") == 0
    _assert_valid_run_summary(result)


def test_protected_changed_files_block_before_verification(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(protected_extra="harness/evidence.py"),
            "quality_engineer": [],
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert "protected path" in result.reason
    assert (result.run_dir / "implementation-report.json").exists()  # the report itself was valid, just refused
    assert agent_adapter.start_count("quality_engineer") == 0
    _assert_valid_run_summary(result)


def test_evidence_collision_from_reused_command_id_is_rejected(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(reused_command_id_bug=True),
        }
    )
    command_results = {
        "C-1": _command_result("C-1", "python -m pytest tests/test_pagination.py -k boundary", TARGET_REPO_PATH, 1),
    }
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert "refusing to overwrite" in result.reason
    _assert_valid_run_summary(result)


def test_command_rejected_blocks_implementation(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {"architect": [json.dumps(_findings_dict())], "engineer": _engineer_sequence()}
    )
    rejection = {
        "message_type": "command_rejected",
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "command_id": "C-1",
        "command": "python -m pytest tests/test_pagination.py -k boundary",
        "working_directory": TARGET_REPO_PATH,
        "rejection_reason": "grammar did not match the accepted form",
        "rejection_category": "grammar_no_match",
    }
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={"C-1": rejection})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert "command rejected" in result.reason
    assert (result.run_dir / "logs" / "C-1.rejected.json").exists()
    _assert_valid_run_summary(result)


def test_command_result_identity_mismatch_blocks(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {"architect": [json.dumps(_findings_dict())], "engineer": _engineer_sequence()}
    )
    mismatched = _command_result("C-1", "a completely different command", TARGET_REPO_PATH, 1)
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={"C-1": mismatched})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert "does not match the request" in result.reason
    _assert_valid_run_summary(result)


def test_exit_code_124_is_forwarded_unchanged(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    blocked_after_timeout = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF}, "dependency_changes": [], "status": "blocked",
        "blocked_reason": "pre-implementation command timed out",
    }
    engineer_turns = [_engineer_sequence()[0], json.dumps(blocked_after_timeout)]
    agent_adapter = ScriptedAgentAdapter(
        {"architect": [json.dumps(_findings_dict())], "engineer": engineer_turns}
    )
    timeout_result = _command_result("C-1", "python -m pytest tests/test_pagination.py -k boundary", TARGET_REPO_PATH, 124)
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results={"C-1": timeout_result})

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    resume_calls = [c for c in agent_adapter.calls if c[0] == "resume" and c[1] == "engineer"]
    assert len(resume_calls) == 1
    forwarded_message = resume_calls[0][3]
    assert forwarded_message["exit_code"] == 124
    _assert_valid_run_summary(result)


def test_exit_code_125_is_forwarded_unchanged_and_retained_as_policy_event(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    blocked_after_mutation = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF}, "dependency_changes": [], "status": "blocked",
        "blocked_reason": "post-implementation command reported a persistent mutation",
    }
    engineer_turns = _engineer_sequence()[:2] + [json.dumps(blocked_after_mutation)]
    agent_adapter = ScriptedAgentAdapter(
        {"architect": [json.dumps(_findings_dict())], "engineer": engineer_turns}
    )
    mutated_result = _command_result("C-2", "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, 125)
    command_results = {
        "C-1": _command_result("C-1", "python -m pytest tests/test_pagination.py -k boundary", TARGET_REPO_PATH, 1),
        "C-2": mutated_result,
    }
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    resume_calls = [c for c in agent_adapter.calls if c[0] == "resume" and c[1] == "engineer"]
    forwarded_exit_code = resume_calls[-1][3]["exit_code"]
    assert forwarded_exit_code == 125
    policy_log = (result.run_dir / "logs" / "policy-events.jsonl").read_text(encoding="utf-8")
    assert "mutation_detected" in policy_log
    assert "C-2" in policy_log
    _assert_valid_run_summary(result)


def test_missing_handle_blocks_rather_than_restarting(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    engineer_turns = [_engineer_sequence()[0], UNRESUMABLE]
    agent_adapter = ScriptedAgentAdapter(
        {"architect": [json.dumps(_findings_dict())], "engineer": engineer_turns}
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(
        command_results={"C-1": _command_result("C-1", "python -m pytest tests/test_pagination.py -k boundary", TARGET_REPO_PATH, 1)}
    )

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert "lost continuity" in result.reason
    assert agent_adapter.start_count("engineer") == 1  # never restarted with a fresh instance
    _assert_valid_run_summary(result)


def test_engineer_uses_the_same_handle_across_every_staged_exchange(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": _qe_sequence(verdict="pass"),
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.COMPLETED
    handles = agent_adapter.handles_used("engineer")
    assert len(handles) == 4  # 1 start + 3 resume turns (pre, post, finalization)
    assert len(set(handles)) == 1, f"engineer used more than one handle: {handles}"


def test_quality_engineer_uses_the_same_handle_across_every_staged_exchange(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": _qe_sequence(verdict="pass"),
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.COMPLETED
    handles = agent_adapter.handles_used("quality_engineer")
    assert len(handles) == 2  # 1 start + 1 resume turn
    assert len(set(handles)) == 1, f"quality_engineer used more than one handle: {handles}"


# ---------------------------------------------------------------------------
# Verification (Quality Engineer)
# ---------------------------------------------------------------------------


def test_blocked_quality_engineer_output_before_an_attempt_stops_cleanly(tmp_path):
    _make_fixture_repo(tmp_path)
    blocked_report = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF}, "implementation_ref": {"path": IMPLEMENTATION_REF},
        "final_verdict": "blocked", "blocked_reason": "findings_ref mismatch",
    }
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": [json.dumps(blocked_report)],
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_BLOCKED
    assert not any(r["command_id"].startswith("V-") for r in test_runner_adapter.invoked_requests)
    _assert_valid_run_summary(result)


def test_verification_pass_permits_completion(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": _qe_sequence(verdict="pass"),
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.COMPLETED
    assert result.final_verdict == "pass"


def test_verification_fail_is_terminal_and_does_not_permit_completion(tmp_path):
    """A genuine logic_bug 'fail' verdict now routes back for exactly one bounded repair
    cycle (Part 2) instead of being immediately terminal -- see the
    "Same-run logic-failure route-back" tests below for the repair cycle itself. Without a
    scripted repair-cycle reply, the resumed Engineer's queue is exhausted, which the
    orchestrator must treat as a broken continuity block, never as license to fabricate a
    result or silently terminate as a plain "fail" -- confirming route-back really is
    attempted rather than silently skipped."""
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": _qe_sequence(verdict="fail"),
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert result.final_verdict != "pass"
    assert "repair attempt 1" in result.reason.lower()
    events = _policy_events(result)
    assert any(e["kind"] == "logic_failure_detected" for e in events)
    assert any(e["kind"] == "engineer_route_back" for e in events)
    assert any(e["kind"] == "continuity_broken" for e in events)
    # Never a replacement Engineer -- exactly one start(), the route-back only ever resumes it.
    assert agent_adapter.start_count("engineer") == 1
    _assert_valid_run_summary(result)


# ---------------------------------------------------------------------------
# Same-run logic-failure route-back (Part 2)
# ---------------------------------------------------------------------------


def test_classify_verification_outcome_distinguishes_all_four_categories():
    pass_doc = json.loads(_qe_sequence(verdict="pass")[1])
    fail_doc = json.loads(_qe_sequence(verdict="fail")[1])
    inconclusive_environment_doc = json.loads(_qe_sequence(verdict="inconclusive")[1])
    inconclusive_flake_doc = dict(inconclusive_environment_doc)
    inconclusive_flake_doc["attempts"] = [dict(inconclusive_environment_doc["attempts"][0], classification="infrastructure_flake")]

    assert core.classify_verification_outcome(fail_doc) == "logic_bug"
    assert core.classify_verification_outcome(inconclusive_environment_doc) == "environment"
    assert core.classify_verification_outcome(inconclusive_flake_doc) == "infrastructure_flake"
    assert core.classify_verification_outcome(None) == "insufficient_evidence"
    assert core.classify_verification_outcome({}) == "insufficient_evidence"
    # Never called for "pass" in production code, but must not misclassify it as a logic bug.
    assert core.classify_verification_outcome(pass_doc) == "insufficient_evidence"


def test_logic_bug_routes_back_to_the_same_engineer_and_repairs_successfully(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence() + _engineer_repair_sequence(),
            "quality_engineer": _qe_sequence(verdict="fail") + _qe_repair_sequence(verdict="pass"),
        }
    )
    command_results = dict(_happy_command_results())
    command_results.update(_repair_command_results())
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.COMPLETED
    assert result.final_verdict == "pass"

    # Never a replacement agent -- exactly one start() per role, the repair cycle only resumes.
    assert agent_adapter.start_count("engineer") == 1
    assert agent_adapter.start_count("quality_engineer") == 1
    engineer_handles = set(agent_adapter.handles_used("engineer"))
    qe_handles = set(agent_adapter.handles_used("quality_engineer"))
    assert len(engineer_handles) == 1
    assert len(qe_handles) == 1

    # Test-runner mediation remains mandatory through the repair cycle too.
    invoked_ids = {r["command_id"] for r in test_runner_adapter.invoked_requests}
    assert {"C-1", "C-2", "V-1", "C-3", "C-4", "V-2"} <= invoked_ids

    # The repair round's canonical artifacts are retained separately from the originals --
    # neither original is silently overwritten -- and the run summary cites the final ones.
    assert (result.run_dir / "implementation-report.json").exists()
    assert (result.run_dir / "verification-report.json").exists()
    assert (result.run_dir / "implementation-report.repair-1.json").exists()
    assert (result.run_dir / "verification-report.repair-1.json").exists()
    assert result.artifact_refs["implementation_report"].endswith("implementation-report.repair-1.json")
    assert result.artifact_refs["verification_report"].endswith("verification-report.repair-1.json")

    # Policy events for every required repair-cycle milestone.
    events = _policy_events(result)
    kinds = [e["kind"] for e in events]
    assert "logic_failure_detected" in kinds
    assert "engineer_route_back" in kinds
    assert "re_verification" in kinds
    route_back_event = next(e for e in events if e["kind"] == "engineer_route_back")
    assert route_back_event["repair_attempt"] == 1
    assert "route_back_exhaustion" not in kinds  # never exhausted on a successful first repair
    assert "continuity_broken" not in kinds

    # The final summary must accurately record that a repair cycle occurred, not read as an
    # unqualified first-pass success.
    summary = _assert_valid_run_summary(result)
    assert summary["final_verdict"] == "pass"
    assert "repair" in summary["objective_summary"].lower()
    assert summary["artifact_refs"]["implementation_report"].endswith("implementation-report.repair-1.json")
    assert summary["artifact_refs"]["verification_report"].endswith("verification-report.repair-1.json")


def test_logic_bug_repair_count_is_bounded_and_exhaustion_ends_honestly(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence() + _engineer_repair_sequence(),
            # The repair does not resolve the logic bug -- the Quality Engineer fails it again.
            "quality_engineer": _qe_sequence(verdict="fail") + _qe_repair_sequence(verdict="fail"),
        }
    )
    command_results = dict(_happy_command_results())
    command_results.update(_repair_command_results(reverify_exit_code=1))
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_FAILED
    assert result.final_verdict == "fail"
    assert result.state != State.COMPLETED

    # Exactly one repair attempt was made -- the budget (MAX_LOGIC_REPAIR_ATTEMPTS == 1) is
    # never exceeded, and no second repair cycle (a third Engineer/QE resume round) occurs.
    assert core.MAX_LOGIC_REPAIR_ATTEMPTS == 1
    events = _policy_events(result)
    kinds = [e["kind"] for e in events]
    assert kinds.count("engineer_route_back") == 1
    assert "route_back_exhaustion" in kinds
    assert agent_adapter.start_count("engineer") == 1
    assert agent_adapter.start_count("quality_engineer") == 1

    # Both rounds' verification reports survive as honest evidence.
    assert (result.run_dir / "verification-report.json").exists()
    assert (result.run_dir / "verification-report.repair-1.json").exists()

    summary = _assert_valid_run_summary(result)
    assert summary["final_verdict"] == "fail"
    assert "repair" in summary["objective_summary"].lower()


def test_infrastructure_flake_does_not_route_back_to_engineer(tmp_path):
    _make_fixture_repo(tmp_path)
    flake_report = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF}, "implementation_ref": {"path": IMPLEMENTATION_REF},
        "attempts": [
            {
                "id": "V-1", "command": "python -m pytest tests/test_pagination.py", "exit_code": 1,
                "classification": "infrastructure_flake", "output_ref": f"runs/{RUN_ID}/logs/V-1.log", "retried": False,
            }
        ],
        "retry_policy": {"max_retries": 2},
        "acceptance_criteria_results": [
            {"criteria_id": "AC-1", "result": "blocked", "attempt_refs": ["V-1"], "evidence_summary": "unresolved flake"}
        ],
        "final_verdict": "inconclusive",
        "routed_back_to_engineer": {"routed": False},
    }
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": [_qe_sequence()[0], json.dumps(flake_report)],
        }
    )
    command_results = dict(_happy_command_results())
    command_results["V-1"] = _command_result("V-1", "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, 1)
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_INCONCLUSIVE
    assert result.final_verdict == "inconclusive"
    assert agent_adapter.start_count("engineer") == 1
    engineer_resumes = [c for c in agent_adapter.calls if c[0] == "resume" and c[1] == "engineer"]
    assert len(engineer_resumes) == 3  # only the original pre/post/finalization staged turns
    events = _policy_events(result)
    assert not any(e["kind"] == "engineer_route_back" for e in events)
    assert not any(e["kind"] == "logic_failure_detected" for e in events)


def test_environment_failure_does_not_route_back_to_engineer(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": _qe_sequence(verdict="inconclusive"),
        }
    )
    command_results = dict(_happy_command_results())
    command_results["V-1"] = _command_result("V-1", "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, 1)
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_INCONCLUSIVE
    assert result.final_verdict == "inconclusive"
    assert agent_adapter.start_count("engineer") == 1
    engineer_resumes = [c for c in agent_adapter.calls if c[0] == "resume" and c[1] == "engineer"]
    assert len(engineer_resumes) == 3
    events = _policy_events(result)
    assert not any(e["kind"] == "engineer_route_back" for e in events)
    assert not any(e["kind"] == "logic_failure_detected" for e in events)


def test_missing_or_invalid_verification_evidence_blocks_routing_rather_than_repairing(tmp_path):
    """A verification phase that never produces a validly promoted final artifact at all
    (here: a schema/semantically-valid self-reported 'blocked' report) is 'malformed or
    insufficient evidence', per classify_verification_outcome -- never treated as a logic
    bug, never routed back."""
    _make_fixture_repo(tmp_path)
    blocked_report = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF}, "implementation_ref": {"path": IMPLEMENTATION_REF},
        "final_verdict": "blocked", "blocked_reason": "command safety denylist violation requested by caller",
    }
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": [_qe_sequence()[0], json.dumps(blocked_report)],
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_BLOCKED
    assert agent_adapter.start_count("engineer") == 1
    engineer_resumes = [c for c in agent_adapter.calls if c[0] == "resume" and c[1] == "engineer"]
    assert len(engineer_resumes) == 3  # never resumed for a route-back that never happened
    events = _policy_events(result)
    assert not any(e["kind"] == "engineer_route_back" for e in events)


def test_repair_implementation_report_must_preserve_test_first_ordering(tmp_path):
    """The repair round is validated by the exact same schema/semantic rules as the
    original round -- a repair report whose pre-implementation command did not actually
    fail (test-first ordering violated) is rejected exactly like it would be the first
    time, never waved through because it's "just a repair"."""
    _make_fixture_repo(tmp_path)
    engineer_repair_turns = _engineer_repair_sequence(pre_exit_code=0)  # pre-test "failure" that didn't fail
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence() + engineer_repair_turns,
            "quality_engineer": _qe_sequence(verdict="fail"),
        }
    )
    command_results = dict(_happy_command_results())
    command_results.update(_repair_command_results(pre_exit_code=0))
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert "semantic validation failed" in result.reason.lower() or "nonzero" in result.reason.lower()
    assert not (result.run_dir / "implementation-report.repair-1.json").exists()


def test_replacement_engineer_during_repair_is_never_dispatched_as_continuation(tmp_path):
    """If the Engineer handle cannot be resumed for the route-back, the run blocks --
    it must never fall back to a fresh Agent dispatch and present it as a continuation."""
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence() + [UNRESUMABLE],
            "quality_engineer": _qe_sequence(verdict="fail"),
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.IMPLEMENTATION_BLOCKED
    assert "lost continuity" in result.reason.lower()
    assert agent_adapter.start_count("engineer") == 1  # never restarted with a fresh instance
    events = _policy_events(result)
    assert any(e["kind"] == "continuity_broken" and e.get("phase") == "implementation" for e in events)


def test_replacement_quality_engineer_during_reverification_is_never_dispatched_as_continuation(tmp_path):
    """Same guarantee for the re-verification leg: if the Quality Engineer handle cannot
    be resumed after a successful repair, the run blocks rather than dispatching a fresh
    Quality Engineer and calling it a continuation."""
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence() + _engineer_repair_sequence(),
            "quality_engineer": _qe_sequence(verdict="fail") + [UNRESUMABLE],
        }
    )
    command_results = dict(_happy_command_results())
    command_results.update(_repair_command_results())
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_BLOCKED
    assert "lost continuity" in result.reason.lower()
    assert agent_adapter.start_count("quality_engineer") == 1
    events = _policy_events(result)
    assert any(e["kind"] == "continuity_broken" and e.get("phase") == "verification" for e in events)


def test_verification_blocked_is_terminal(tmp_path):
    _make_fixture_repo(tmp_path)
    blocked_report = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF}, "implementation_ref": {"path": IMPLEMENTATION_REF},
        "final_verdict": "blocked", "blocked_reason": "command safety denylist violation requested by caller",
    }
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": [_qe_sequence()[0], json.dumps(blocked_report)],
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_BLOCKED
    assert result.final_verdict == "blocked"
    _assert_valid_run_summary(result)


def test_verification_inconclusive_is_terminal_and_does_not_permit_completion(tmp_path):
    _make_fixture_repo(tmp_path)
    discovery_adapter = ScriptedDiscoveryAdapter(_scope_dict())
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            "quality_engineer": _qe_sequence(verdict="inconclusive"),
        }
    )
    command_results = dict(_happy_command_results())
    command_results["V-1"] = _command_result("V-1", "python -m pytest tests/test_pagination.py", TARGET_REPO_PATH, 1)
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=command_results, diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_INCONCLUSIVE
    assert result.state != State.COMPLETED
    assert result.final_verdict == "inconclusive"
    assert (result.run_dir / "verification-report.json").exists()
    verification_on_disk = _read_json(result.run_dir / "verification-report.json")
    assert verification_on_disk["final_verdict"] == "inconclusive"

    # One-pass MVP: an inconclusive verdict never triggers a second Engineer dispatch either.
    assert agent_adapter.start_count("engineer") == 1
    assert agent_adapter.start_count("quality_engineer") == 1

    summary = _assert_valid_run_summary(result)
    assert summary["final_verdict"] == "inconclusive"
    assert summary["final_verdict"] != "pass"
    assert "inconclusive" in summary["objective_summary"].lower()
    assert summary["phases_completed"] == ["discovery", "research", "implementation", "verification"]


def test_incomplete_acceptance_criterion_coverage_prevents_completion(tmp_path):
    _make_fixture_repo(tmp_path)
    two_criteria_scope = _scope_dict(
        acceptance_criteria=[
            {"id": "AC-1", "description": "Existing pagination tests pass"},
            {"id": "AC-2", "description": "New boundary test covers the off-by-one case"},
        ]
    )
    discovery_adapter = ScriptedDiscoveryAdapter(two_criteria_scope)
    agent_adapter = ScriptedAgentAdapter(
        {
            "architect": [json.dumps(_findings_dict())],
            "engineer": _engineer_sequence(),
            # Reports "pass" but only ever covers AC-1 -- AC-2 is silently missing, which
            # validate_verification_report_semantics must catch as a coverage violation.
            "quality_engineer": _qe_sequence(verdict="pass", criteria_ids=("AC-1",)),
        }
    )
    test_runner_adapter = ScriptedTestRunnerAdapter(command_results=_happy_command_results(), diff_stats=DIFF_STATS)

    result = _run(tmp_path, discovery_adapter, agent_adapter, test_runner_adapter)

    assert result.state == State.VERIFICATION_BLOCKED
    assert result.state != State.COMPLETED
    assert not (result.run_dir / "verification-report.json").exists()
    _assert_valid_run_summary(result)
