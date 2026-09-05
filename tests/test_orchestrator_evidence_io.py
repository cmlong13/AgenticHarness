"""Tests for harness/orchestrator/evidence_io.py -- run-directory evidence persistence."""
from __future__ import annotations

import json

import pytest

from harness.orchestrator import evidence_io


def test_ensure_run_dirs_creates_expected_layout(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)

    assert run_directory.is_dir()
    assert (run_directory / "attempts").is_dir()
    assert (run_directory / "requests").is_dir()
    assert (run_directory / "logs").is_dir()


def test_retain_raw_attempt_writes_exact_text(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)

    path = evidence_io.retain_raw_attempt(run_directory, "research", 1, "not json at all")

    assert path.read_text(encoding="utf-8") == "not json at all"
    assert path.name == "research-1.raw.txt"


def test_retain_raw_attempt_collision_is_rejected(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)
    evidence_io.retain_raw_attempt(run_directory, "research", 1, "first")

    with pytest.raises(evidence_io.EvidenceCollisionError):
        evidence_io.retain_raw_attempt(run_directory, "research", 1, "second")

    # The original evidence must survive the rejected overwrite attempt unchanged.
    assert (run_directory / "attempts" / "research-1.raw.txt").read_text(encoding="utf-8") == "first"


def test_retain_request_collision_is_rejected(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)
    request = {"command_id": "C-1", "task_id": "T-1", "run_id": "run-1"}
    evidence_io.retain_request(run_directory, request)

    with pytest.raises(evidence_io.EvidenceCollisionError):
        evidence_io.retain_request(run_directory, request)


def test_promote_canonical_collision_is_rejected(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)
    evidence_io.promote_canonical(run_directory, "scope.json", {"a": 1})

    with pytest.raises(evidence_io.EvidenceCollisionError):
        evidence_io.promote_canonical(run_directory, "scope.json", {"a": 2})

    # The first, valid canonical artifact must never be silently overwritten by a second attempt.
    on_disk = json.loads((run_directory / "scope.json").read_text(encoding="utf-8"))
    assert on_disk == {"a": 1}


def test_retain_git_evidence_writes_under_git_subdirectory(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)

    path = evidence_io.retain_git_evidence(run_directory, "push-attempt.json", {"remote": "origin"})

    assert path == run_directory / "git" / "push-attempt.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"remote": "origin"}


def test_retain_git_evidence_creates_git_subdirectory_on_demand(tmp_path):
    # No ensure_run_dirs call at all -- retain_git_evidence must create its own
    # parent directory, exactly as promote_canonical/retain_raw_attempt already do
    # via atomic_write's own mkdir(parents=True, exist_ok=True).
    run_directory = evidence_io.run_dir(tmp_path, "run-1")

    path = evidence_io.retain_git_evidence(run_directory, "commit-evidence.json", {"commit_sha": "a" * 40})

    assert path.is_file()


def test_retain_git_evidence_collision_is_rejected(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)
    evidence_io.retain_git_evidence(run_directory, "push-verification.json", {"status": "verified"})

    with pytest.raises(evidence_io.EvidenceCollisionError):
        evidence_io.retain_git_evidence(run_directory, "push-verification.json", {"status": "mismatch"})

    # The original evidence must survive the rejected overwrite attempt unchanged.
    doc = json.loads((run_directory / "git" / "push-verification.json").read_text(encoding="utf-8"))
    assert doc == {"status": "verified"}


def test_retain_jira_evidence_writes_under_jira_subdirectory(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)

    path = evidence_io.retain_jira_evidence(run_directory, "issue-resolution.json", {"status": "resolved"})

    assert path == run_directory / "jira" / "issue-resolution.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "resolved"}


def test_retain_jira_evidence_creates_jira_subdirectory_on_demand(tmp_path):
    # No ensure_run_dirs call at all -- retain_jira_evidence must create its own
    # parent directory, exactly as retain_git_evidence already does.
    run_directory = evidence_io.run_dir(tmp_path, "run-1")

    path = evidence_io.retain_jira_evidence(run_directory, "issue-resolution.json", {"status": "not_found"})

    assert path.is_file()


def test_retain_jira_evidence_collision_is_rejected(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)
    evidence_io.retain_jira_evidence(run_directory, "issue-resolution.json", {"status": "resolved"})

    with pytest.raises(evidence_io.EvidenceCollisionError):
        evidence_io.retain_jira_evidence(run_directory, "issue-resolution.json", {"status": "not_found"})

    # The original evidence must survive the rejected overwrite attempt unchanged.
    doc = json.loads((run_directory / "jira" / "issue-resolution.json").read_text(encoding="utf-8"))
    assert doc == {"status": "resolved"}


def test_promote_canonical_writes_valid_json(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)
    doc = {"schema_version": "1.0", "nested": {"x": [1, 2, 3]}}

    path = evidence_io.promote_canonical(run_directory, "findings.json", doc)

    assert json.loads(path.read_text(encoding="utf-8")) == doc


def test_write_run_summary_uses_canonical_filename(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)

    path = evidence_io.write_run_summary(run_directory, {"final_verdict": "pass"})

    assert path == run_directory / "run-summary.json"
    assert path.exists()


def test_retain_rejection_writes_evidence_log(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)
    rejection = {"message_type": "command_rejected", "command_id": "C-9", "rejection_reason": "grammar_no_match"}

    path = evidence_io.retain_rejection(run_directory, rejection)

    assert json.loads(path.read_text(encoding="utf-8")) == rejection


def test_retain_policy_event_appends_multiple_lines(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)

    evidence_io.retain_policy_event(run_directory, "mutation_detected", {"command_id": "C-1"})
    evidence_io.retain_policy_event(run_directory, "mutation_detected", {"command_id": "C-2"})

    lines = (run_directory / "logs" / "policy-events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["command_id"] == "C-1"
    assert json.loads(lines[1])["command_id"] == "C-2"


def test_no_tmp_file_left_behind_after_a_successful_write(tmp_path):
    run_directory = evidence_io.run_dir(tmp_path, "run-1")
    evidence_io.ensure_run_dirs(run_directory)

    evidence_io.promote_canonical(run_directory, "scope.json", {"a": 1})

    leftovers = [p.name for p in run_directory.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
