"""Tests for harness/orchestrator/discovery.py -- the Discovery validation gate.

Discovery content itself always comes from a scripted fake DiscoveryAdapter; these tests only
exercise the deterministic gate that decides whether a proposed scope draft may be promoted.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from harness.orchestrator import discovery

REPO_ROOT = Path(__file__).resolve().parent.parent


def _valid_draft(*, task_id="T-1", run_id="run-1", target_repo_path="fixture-repo") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": run_id,
        "created_at": "2026-08-03T00:00:00Z",
        "source": {"type": "prompt", "raw_prompt": "Fix the thing"},
        "objective": "Fix the thing",
        "in_scope": [f"{target_repo_path}/src/thing.py"],
        "out_of_scope": [],
        "constraints": [],
        "acceptance_criteria": [{"id": "AC-1", "description": "thing is fixed"}],
        "task_graph": [
            {"id": "N-1", "description": "research", "phase": "research", "depends_on": []},
            {"id": "N-2", "description": "implement", "phase": "implementation", "depends_on": ["N-1"]},
            {"id": "N-3", "description": "verify", "phase": "verification", "depends_on": ["N-2"]},
        ],
        "status": "approved",
    }


def _make_fixture_repo(tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture-repo"
    (fixture / "src").mkdir(parents=True)
    (fixture / "src" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    return fixture


def test_valid_approved_draft_passes(tmp_path):
    _make_fixture_repo(tmp_path)
    draft = _valid_draft()

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    assert errors == []


def test_schema_invalid_draft_is_rejected(tmp_path):
    _make_fixture_repo(tmp_path)
    draft = _valid_draft()
    del draft["objective"]  # required by scope.schema.json

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    assert errors
    assert any("objective" in e for e in errors)


def test_semantically_invalid_draft_is_rejected(tmp_path):
    _make_fixture_repo(tmp_path)
    draft = _valid_draft()
    draft["task_graph"][1]["depends_on"] = ["N-99"]  # unresolved reference

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    assert errors
    assert any("unknown task_graph node id" in e for e in errors)


def test_task_id_mismatch_is_rejected(tmp_path):
    _make_fixture_repo(tmp_path)
    draft = _valid_draft(task_id="T-WRONG")

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    assert any("does not match the run identity" in e for e in errors)


def test_protected_in_scope_path_is_rejected(tmp_path):
    # in_scope entries are target-repo-root-relative paths (like target_repo_path itself), so a
    # path can be both genuinely contained under target_repo_path AND match a Protected Path
    # pattern -- this constructs that overlap using the "runs/**/scope.json" pattern.
    (tmp_path / "runs").mkdir(parents=True)
    draft = _valid_draft(target_repo_path="runs")
    draft["in_scope"] = ["runs/some-run/scope.json"]

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="runs", repo_root=tmp_path,
    )

    assert any("protected pattern" in e for e in errors)


def test_in_scope_path_outside_target_repo_is_rejected(tmp_path):
    _make_fixture_repo(tmp_path)
    draft = _valid_draft()
    draft["in_scope"].append("some-other-repo/file.py")

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    assert any("not contained beneath target_repo_path" in e for e in errors)


def test_missing_target_repo_directory_is_rejected(tmp_path):
    # fixture-repo deliberately never created
    draft = _valid_draft()

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    assert any("target_repo_path invalid" in e for e in errors)


def test_refused_draft_skips_path_checks_and_is_otherwise_valid(tmp_path):
    # No fixture-repo directory created -- a refused draft must not be rejected merely because
    # target_repo_path doesn't resolve; refusal is a valid, path-safety-independent outcome.
    draft = _valid_draft()
    draft["status"] = "refused"
    draft["refusal_reason"] = "ambiguous request, no identifiable target file"

    errors = discovery.validate_scope_draft(
        draft, expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    assert errors == []


def _hash_tree(root: Path) -> dict[str, str]:
    digests = {}
    for path in root.rglob("*"):
        if path.is_file():
            digests[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def test_discovery_does_not_mutate_the_target_repository(tmp_path):
    fixture = _make_fixture_repo(tmp_path)
    before = _hash_tree(fixture)
    draft = _valid_draft()

    # Exercise the full Discovery gate exactly as core.py would, including a raw-candidate
    # retention step, to prove nothing under target_repo_path is ever touched.
    (tmp_path / "runs" / "run-1" / "attempts").mkdir(parents=True)
    (tmp_path / "runs" / "run-1" / "attempts" / "discovery-1.raw.txt").write_text(
        json.dumps(draft), encoding="utf-8"
    )
    errors = discovery.validate_scope_draft(
        copy.deepcopy(draft), expected_task_id="T-1", expected_run_id="run-1",
        target_repo_path="fixture-repo", repo_root=tmp_path,
    )

    after = _hash_tree(fixture)
    assert errors == []
    assert before == after
