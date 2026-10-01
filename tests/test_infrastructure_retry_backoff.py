"""Quality Engineer infrastructure_flake retry backoff: the bounded backoff policy
(core.plan_infrastructure_retry / apply_infrastructure_retry_backoff), its wiring into
the deterministic core mediation loop, the live_cli bridge /work uses for the same
policy, and the report-vs-executed-retry consistency check.

core._backoff_sleep is always replaced by a recorder -- no test ever really waits.
"""
from __future__ import annotations

import json

import pytest

from harness.orchestrator import core, live_cli
from harness.orchestrator.state import State
from tests.fakes.agent_adapter import ScriptedAgentAdapter, ScriptedDiscoveryAdapter, ScriptedTestRunnerAdapter
from tests.test_orchestrator_core import (
    CREATED_AT,
    DIFF_STATS,
    IMPLEMENTATION_REF,
    RUN_ID,
    SCOPE_REF,
    TARGET_REPO_PATH,
    TASK_ID,
    _command_result,
    _engineer_repair_sequence,
    _engineer_sequence,
    _findings_dict,
    _happy_command_results,
    _make_fixture_repo,
    _policy_events,
    _qe_repair_sequence,
    _qe_sequence,
    _repair_command_results,
    _run,
    _scope_dict,
)

COMMAND = "python -m pytest tests/test_pagination.py"


@pytest.fixture()
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(core, "_backoff_sleep", recorded.append)
    return recorded


def _attempt(attempt_id: str, exit_code: int, retry_number: int = 0) -> dict:
    return {"command": COMMAND, "working_directory": TARGET_REPO_PATH, "exit_code": exit_code, "retry_number": retry_number}


def _retry_request(attempt_id: str, retry_of: str, classification: str = "infrastructure_flake") -> dict:
    return {
        "id": attempt_id, "command": COMMAND, "working_directory": TARGET_REPO_PATH,
        "criteria_ids_targeted": ["AC-1"], "rationale": "named connection reset -- retry",
        "retry_of": {"attempt_id": retry_of, "classification": classification},
    }


# ---------------------------------------------------------------------------
# Policy unit tests
# ---------------------------------------------------------------------------


def test_policy_constants_mean_two_retries_three_total_executions():
    assert core.INFRA_RETRY_BACKOFF_SECONDS == (2.0, 4.0)
    assert core.MAX_INFRA_RETRIES == 2
    assert core.MAX_INFRA_ATTEMPTS == 3
    assert all(b > 0 for b in core.INFRA_RETRY_BACKOFF_SECONDS)
    assert list(core.INFRA_RETRY_BACKOFF_SECONDS) == sorted(core.INFRA_RETRY_BACKOFF_SECONDS)


def test_first_retry_schedules_positive_backoff():
    plan = core.plan_infrastructure_retry(_retry_request("V-2", "V-1"), {"V-1": _attempt("V-1", 1)})
    assert plan["retry_number"] == 1 and plan["attempt_number"] == 2
    assert plan["backoff_seconds"] == 2.0 > 0
    assert plan["retried_attempt_classification"] == "infrastructure_flake"
    assert plan["command_identical"] is True and plan["command"] == COMMAND
    assert plan["retries_remaining"] == 1


def test_second_retry_uses_next_bounded_backoff():
    prior = {"V-1": _attempt("V-1", 1), "V-2": _attempt("V-2", 1, retry_number=1)}
    plan = core.plan_infrastructure_retry(_retry_request("V-3", "V-2"), prior)
    assert plan["retry_number"] == 2 and plan["attempt_number"] == 3
    assert plan["backoff_seconds"] == 4.0
    assert plan["retries_remaining"] == 0


def test_third_retry_refused_at_ceiling():
    prior = {"V-1": _attempt("V-1", 1), "V-2": _attempt("V-2", 1, 1), "V-3": _attempt("V-3", 1, 2)}
    with pytest.raises(core.InfrastructureRetryRefused, match="retry ceiling"):
        core.plan_infrastructure_retry(_retry_request("V-4", "V-3"), prior)


def test_branching_retries_of_the_same_original_cannot_exceed_ceiling():
    prior = {"V-1": _attempt("V-1", 1), "V-2": _attempt("V-2", 1, 1)}
    with pytest.raises(core.InfrastructureRetryRefused, match="most recent execution"):
        core.plan_infrastructure_retry(_retry_request("V-3", "V-1"), prior)


@pytest.mark.parametrize("classification", ["logic_bug", "environment", "pass", None])
def test_only_infrastructure_flake_may_be_retried(classification):
    with pytest.raises(core.InfrastructureRetryRefused, match="only an infrastructure_flake"):
        core.plan_infrastructure_retry(_retry_request("V-2", "V-1", classification), {"V-1": _attempt("V-1", 1)})


def test_passing_attempt_is_never_retried():
    with pytest.raises(core.InfrastructureRetryRefused, match="passing attempt"):
        core.plan_infrastructure_retry(_retry_request("V-2", "V-1"), {"V-1": _attempt("V-1", 0)})


def test_retry_must_be_identical_command():
    request = dict(_retry_request("V-2", "V-1"), command=COMMAND + " -k other")
    with pytest.raises(core.InfrastructureRetryRefused, match="identical command"):
        core.plan_infrastructure_retry(request, {"V-1": _attempt("V-1", 1)})


def test_undeclared_rerun_of_failed_command_is_refused():
    request = {"id": "V-2", "command": COMMAND, "working_directory": TARGET_REPO_PATH}
    with pytest.raises(core.InfrastructureRetryRefused, match="without declaring retry_of"):
        core.plan_infrastructure_retry(request, {"V-1": _attempt("V-1", 1)})


def test_ordinary_attempt_is_not_a_retry():
    request = {"id": "V-1", "command": COMMAND, "working_directory": TARGET_REPO_PATH}
    assert core.plan_infrastructure_retry(request, {}) is None


def test_apply_waits_before_retaining_event_and_never_waits_on_refusal(tmp_path, sleeps):
    plan = core.apply_infrastructure_retry_backoff(tmp_path, _retry_request("V-2", "V-1"), {"V-1": _attempt("V-1", 1)})
    assert sleeps == [2.0]
    events = [json.loads(l) for l in (tmp_path / "logs" / "policy-events.jsonl").read_text().splitlines()]
    assert events == [{"kind": "infrastructure_retry_backoff", **plan}]

    with pytest.raises(core.InfrastructureRetryRefused):
        core.apply_infrastructure_retry_backoff(
            tmp_path, _retry_request("V-2", "V-1", "logic_bug"), {"V-1": _attempt("V-1", 1)}
        )
    assert sleeps == [2.0]
    last = json.loads((tmp_path / "logs" / "policy-events.jsonl").read_text().splitlines()[-1])
    assert last["kind"] == "infrastructure_retry_refused" and last["command_id"] == "V-2"


def test_backoff_sleep_seam_calls_time_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr(core.time, "sleep", calls.append)
    core._backoff_sleep(2.0)
    assert calls == [2.0]


# ---------------------------------------------------------------------------
# Deterministic core pipeline
# ---------------------------------------------------------------------------


def _qe_retry_sequence(exit_codes: list[int], *, report_overrides: dict | None = None) -> list[str]:
    """One attempt_requested per exit code -- V-1 original, V-2.. declared retries of the
    previous attempt -- then a final report (failed attempts infrastructure_flake)."""
    turns, attempts = [], []
    for n, exit_code in enumerate(exit_codes, start=1):
        requested = {
            "id": f"V-{n}", "command": COMMAND, "working_directory": TARGET_REPO_PATH,
            "criteria_ids_targeted": ["AC-1"], "rationale": "verify pagination",
        }
        if n > 1:
            requested["retry_of"] = {"attempt_id": f"V-{n - 1}", "classification": "infrastructure_flake"}
        turns.append({"response_type": "attempt_requested", "task_id": TASK_ID, "run_id": RUN_ID, "requested_command": requested})
        attempts.append({
            "id": f"V-{n}", "command": COMMAND, "exit_code": exit_code,
            "classification": "pass" if exit_code == 0 else "infrastructure_flake",
            "output_ref": f"runs/{RUN_ID}/logs/V-{n}.log", "retried": n > 1,
        })
    passed = exit_codes[-1] == 0
    for attempt_id, override in (report_overrides or {}).items():
        next(a for a in attempts if a["id"] == attempt_id).update(override)
    final = {
        "schema_version": "1.0", "task_id": TASK_ID, "run_id": RUN_ID, "created_at": CREATED_AT,
        "scope_ref": {"path": SCOPE_REF}, "implementation_ref": {"path": IMPLEMENTATION_REF},
        "attempts": attempts, "retry_policy": {"max_retries": 2},
        "acceptance_criteria_results": [{
            "criteria_id": "AC-1", "result": "passed" if passed else "blocked",
            "attempt_refs": [a["id"] for a in attempts], "evidence_summary": "pagination via V-* attempts",
        }],
        "final_verdict": "pass" if passed else "inconclusive",
        "routed_back_to_engineer": {"routed": False},
    }
    return [json.dumps(t) for t in turns] + [json.dumps(final)]


def _run_retry_pipeline(tmp_path, exit_codes, qe_turns=None):
    _make_fixture_repo(tmp_path)
    agent_adapter = ScriptedAgentAdapter({
        "architect": [json.dumps(_findings_dict())],
        "engineer": _engineer_sequence(),
        "quality_engineer": qe_turns or _qe_retry_sequence(exit_codes),
    })
    results = dict(_happy_command_results())
    for n, exit_code in enumerate(exit_codes, start=1):
        results[f"V-{n}"] = _command_result(f"V-{n}", COMMAND, TARGET_REPO_PATH, exit_code)
    runner = ScriptedTestRunnerAdapter(command_results=results, diff_stats=DIFF_STATS)
    result = _run(tmp_path, ScriptedDiscoveryAdapter(_scope_dict()), agent_adapter, runner)
    v_ids = [r["command_id"] for r in runner.invoked_requests if r["command_id"].startswith("V-")]
    retry_events = [e for e in _policy_events(result) if e["kind"] == "infrastructure_retry_backoff"]
    return result, v_ids, retry_events


def test_pipeline_flake_then_pass_backs_off_once_and_stops(tmp_path, sleeps):
    result, v_ids, events = _run_retry_pipeline(tmp_path, [1, 0])
    assert result.state == State.COMPLETED
    assert v_ids == ["V-1", "V-2"]
    assert sleeps == [2.0]  # the successful retry ends retrying -- no further backoff
    assert [(e["command_id"], e["retry_of_attempt_id"], e["retry_number"], e["backoff_seconds"]) for e in events] == [
        ("V-2", "V-1", 1, 2.0)
    ]
    assert events[0]["retries_remaining"] == 1 and events[0]["command_identical"] is True


def test_pipeline_two_flakes_then_pass_uses_escalating_backoff(tmp_path, sleeps):
    result, v_ids, events = _run_retry_pipeline(tmp_path, [1, 1, 0])
    assert result.state == State.COMPLETED
    assert v_ids == ["V-1", "V-2", "V-3"]
    assert sleeps == [2.0, 4.0]
    assert [(e["attempt_number"], e["retry_number"], e["retries_remaining"]) for e in events] == [(2, 1, 1), (3, 2, 0)]


def test_pipeline_never_executes_a_fourth_attempt(tmp_path, sleeps):
    turns = _qe_retry_sequence([1, 1, 1, 0])
    result, v_ids, events = _run_retry_pipeline(tmp_path, [1, 1, 1, 0], qe_turns=turns)
    assert result.state == State.VERIFICATION_BLOCKED
    assert "retry ceiling" in result.reason
    assert v_ids == ["V-1", "V-2", "V-3"]  # V-4 never reached the test runner
    assert sleeps == [2.0, 4.0]  # and was never waited for
    assert len(events) == 2
    refused = [e for e in _policy_events(result) if e["kind"] == "infrastructure_retry_refused"]
    assert [e["command_id"] for e in refused] == ["V-4"]


def test_pipeline_exhausted_flake_ends_inconclusive_without_route_back(tmp_path, sleeps):
    result, v_ids, _ = _run_retry_pipeline(tmp_path, [1, 1, 1])
    assert result.state == State.VERIFICATION_INCONCLUSIVE
    assert v_ids == ["V-1", "V-2", "V-3"]
    assert sleeps == [2.0, 4.0]
    assert not any(e["kind"] == "engineer_route_back" for e in _policy_events(result))


def test_pipeline_logic_bug_routes_back_without_backoff(tmp_path, sleeps):
    """The repair round re-runs V-1's identical command as V-2 -- a fresh round after a
    real fix, never an infrastructure retry, so it gets no backoff and no retry event."""
    _make_fixture_repo(tmp_path)
    agent_adapter = ScriptedAgentAdapter({
        "architect": [json.dumps(_findings_dict())],
        "engineer": _engineer_sequence() + _engineer_repair_sequence(),
        "quality_engineer": _qe_sequence(verdict="fail") + _qe_repair_sequence(verdict="pass"),
    })
    results = dict(_happy_command_results())
    results["V-1"] = _command_result("V-1", COMMAND, TARGET_REPO_PATH, 1)
    results.update(_repair_command_results())
    runner = ScriptedTestRunnerAdapter(command_results=results, diff_stats=DIFF_STATS)
    result = _run(tmp_path, ScriptedDiscoveryAdapter(_scope_dict()), agent_adapter, runner)

    assert result.state == State.COMPLETED
    assert sleeps == []
    kinds = [e["kind"] for e in _policy_events(result)]
    assert "infrastructure_retry_backoff" not in kinds and "infrastructure_retry_refused" not in kinds
    assert "engineer_route_back" in kinds


def test_pipeline_logic_bug_declared_as_retry_is_refused_without_backoff(tmp_path, sleeps):
    turns = _qe_retry_sequence([1, 0])
    retry_turn = json.loads(turns[1])
    retry_turn["requested_command"]["retry_of"]["classification"] = "logic_bug"
    turns[1] = json.dumps(retry_turn)
    result, v_ids, events = _run_retry_pipeline(tmp_path, [1, 0], qe_turns=turns)
    assert result.state == State.VERIFICATION_BLOCKED
    assert v_ids == ["V-1"] and sleeps == [] and events == []


def test_pipeline_rejects_report_contradicting_the_declared_flake(tmp_path, sleeps):
    """The QE obtained a retry by declaring V-1 an infrastructure_flake; its final report
    may not then call V-1 something else."""
    turns = _qe_retry_sequence([1, 0], report_overrides={"V-1": {"classification": "environment"}})
    result, _, _ = _run_retry_pipeline(tmp_path, [1, 0], qe_turns=turns)
    assert result.state == State.VERIFICATION_BLOCKED
    assert "retried as an infrastructure_flake" in result.reason
    assert not (result.run_dir / "verification-report.json").exists()


def test_pipeline_rejects_report_claiming_an_unexecuted_retry(tmp_path, sleeps):
    turns = _qe_retry_sequence([0], report_overrides={"V-1": {"retried": True}})
    result, _, _ = _run_retry_pipeline(tmp_path, [0], qe_turns=turns)
    assert result.state == State.VERIFICATION_BLOCKED
    assert "never executed as an infrastructure retry" in result.reason


# ---------------------------------------------------------------------------
# Live path parity (live_cli -> the same core policy)
# ---------------------------------------------------------------------------


@pytest.fixture()
def live_run(tmp_path, monkeypatch):
    monkeypatch.setattr(live_cli, "REPO_ROOT", tmp_path)
    run_dir = tmp_path / "runs" / RUN_ID
    (run_dir / "requests").mkdir(parents=True)
    (run_dir / "logs").mkdir(parents=True)
    return run_dir


def _retain_executed(run_dir, attempt_id: str, exit_code: int) -> None:
    (run_dir / "requests" / f"{attempt_id}.json").write_text(
        json.dumps({"command_id": attempt_id, "command": COMMAND, "working_directory": TARGET_REPO_PATH}), encoding="utf-8"
    )
    (run_dir / "logs" / f"{attempt_id}.log").write_text(
        f"command: {COMMAND}\nreal_pytest_exit_code: {exit_code}\nreported_exit_code: {exit_code}\n", encoding="utf-8"
    )


def _prepare(requested_command, prior_ids):
    return live_cli.op_prepare_infrastructure_retry(
        {"run_id": RUN_ID, "requested_command": requested_command, "prior_attempt_ids": prior_ids}
    )


def test_live_path_matches_core_policy_through_ceiling(live_run, sleeps):
    _retain_executed(live_run, "V-1", 1)
    first = _prepare(_retry_request("V-2", "V-1"), ["V-1"])
    assert first["status"] == "ok"
    assert first["retry"] == core.plan_infrastructure_retry(_retry_request("V-2", "V-1"), {"V-1": _attempt("V-1", 1)})

    _retain_executed(live_run, "V-2", 1)
    second = _prepare(_retry_request("V-3", "V-2"), ["V-1", "V-2"])
    assert (second["retry"]["retry_number"], second["retry"]["backoff_seconds"]) == (2, 4.0)

    _retain_executed(live_run, "V-3", 1)
    third = _prepare(_retry_request("V-4", "V-3"), ["V-1", "V-2", "V-3"])
    assert third["status"] == "refused" and "retry ceiling" in third["error"]
    assert sleeps == [2.0, 4.0]


def test_live_path_reads_exit_code_from_retained_log_not_caller(live_run, sleeps):
    _retain_executed(live_run, "V-1", 0)
    resp = _prepare(_retry_request("V-2", "V-1"), ["V-1"])
    assert resp["status"] == "refused" and "passing attempt" in resp["error"]
    assert sleeps == []


def test_live_path_ordinary_attempt_and_logic_bug(live_run, sleeps):
    ordinary = {"id": "V-1", "command": COMMAND, "working_directory": TARGET_REPO_PATH}
    assert _prepare(ordinary, []) == {"operation": "prepare_infrastructure_retry", "status": "ok", "retry": None}
    _retain_executed(live_run, "V-1", 1)
    resp = _prepare(_retry_request("V-2", "V-1", "logic_bug"), ["V-1"])
    assert resp["status"] == "refused"
    assert sleeps == []


def test_live_retry_consistency_uses_same_check(live_run, sleeps):
    _retain_executed(live_run, "V-1", 1)
    _prepare(_retry_request("V-2", "V-1"), ["V-1"])
    report = json.loads(_qe_retry_sequence([1, 0])[-1])
    assert live_cli.op_check_retry_consistency({"run_id": RUN_ID, "doc": report})["status"] == "valid"
    report["attempts"][0]["classification"] = "logic_bug"
    resp = live_cli.op_check_retry_consistency({"run_id": RUN_ID, "doc": report})
    assert resp["status"] == "invalid" and "retried as an infrastructure_flake" in resp["errors"][0]


def test_live_operations_registered():
    assert live_cli.OPERATIONS["prepare_infrastructure_retry"] is live_cli.op_prepare_infrastructure_retry
    assert live_cli.OPERATIONS["check_retry_consistency"] is live_cli.op_check_retry_consistency
