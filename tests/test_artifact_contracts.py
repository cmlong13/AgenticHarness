"""Contract tests for harness/schemas/*.schema.json + harness/artifacts/examples/*.example.json."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from harness.evidence import (
    ArtifactPair,
    check_schema_document,
    discover_artifact_pairs,
    load_json,
    validate_against_schema,
    validate_checkpoint_semantics,
    validate_findings_semantics,
    validate_implementation_report_semantics,
    validate_verification_report_semantics,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "harness" / "schemas"
EXAMPLE_DIR = REPO_ROOT / "harness" / "artifacts" / "examples"

PAIRS: list[ArtifactPair] = discover_artifact_pairs(SCHEMA_DIR, EXAMPLE_DIR)
assert PAIRS, f"no schema/example files discovered under {SCHEMA_DIR} / {EXAMPLE_DIR}"

COMPLETE_PAIRS = [p for p in PAIRS if p.schema_path and p.example_path]


def _load(pair_name: str) -> dict:
    pair = next(p for p in COMPLETE_PAIRS if p.name == pair_name)
    return load_json(pair.example_path)


# ---------------------------------------------------------------------------
# Discovery / pairing
# ---------------------------------------------------------------------------


def test_every_schema_has_a_matching_example():
    orphans = [p.name for p in PAIRS if p.schema_path and not p.example_path]
    assert not orphans, f"schema(s) with no matching example: {orphans}"


def test_every_example_has_a_matching_schema():
    orphans = [p.name for p in PAIRS if p.example_path and not p.schema_path]
    assert not orphans, f"example(s) with no matching schema: {orphans}"


def test_discover_artifact_pairs_flags_orphans(tmp_path):
    schema_dir = tmp_path / "schemas"
    example_dir = tmp_path / "examples"
    schema_dir.mkdir()
    example_dir.mkdir()
    (schema_dir / "widget.schema.json").write_text("{}", encoding="utf-8")
    (schema_dir / "gadget.schema.json").write_text("{}", encoding="utf-8")
    (example_dir / "gadget.example.json").write_text("{}", encoding="utf-8")
    (example_dir / "gizmo.example.json").write_text("{}", encoding="utf-8")

    pairs = {p.name: p for p in discover_artifact_pairs(schema_dir, example_dir)}

    assert pairs["widget"].schema_path is not None
    assert pairs["widget"].example_path is None
    assert pairs["gadget"].schema_path is not None
    assert pairs["gadget"].example_path is not None
    assert pairs["gizmo"].schema_path is None
    assert pairs["gizmo"].example_path is not None


# ---------------------------------------------------------------------------
# Schema document validity + example schema-conformance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p.name)
def test_schema_document_is_valid(pair: ArtifactPair):
    assert pair.schema_path is not None, f"{pair.name}: no schema file to validate"
    schema = load_json(pair.schema_path)
    errors = check_schema_document(schema, artifact=pair.schema_path.name)
    assert not errors, "\n".join(str(e) for e in errors)


@pytest.mark.parametrize("pair", COMPLETE_PAIRS, ids=lambda p: p.name)
def test_example_conforms_to_schema(pair: ArtifactPair):
    schema = load_json(pair.schema_path)
    instance = load_json(pair.example_path)
    errors = validate_against_schema(instance, schema, artifact=pair.example_path.name)
    assert not errors, "\n".join(str(e) for e in errors)


def test_example_violating_its_schema_is_caught():
    pair = next(p for p in COMPLETE_PAIRS if p.name == "scope")
    schema = load_json(pair.schema_path)
    broken = load_json(pair.example_path)
    del broken["objective"]  # required field

    errors = validate_against_schema(broken, schema, artifact="scope.example.json (mutated)")

    assert errors
    assert any("objective" in e.message for e in errors)


def test_invalid_schema_document_is_caught():
    broken_schema = {"type": "not-a-real-type"}

    errors = check_schema_document(broken_schema, artifact="broken.schema.json")

    assert errors


# ---------------------------------------------------------------------------
# Semantic validation -- findings
# ---------------------------------------------------------------------------


def test_findings_example_has_no_semantic_errors():
    doc = _load("findings")
    errors = validate_findings_semantics(doc, artifact="findings.example.json")
    assert not errors, "\n".join(str(e) for e in errors)


def test_findings_duplicate_id_is_caught():
    doc = copy.deepcopy(_load("findings"))
    doc["findings"].append(copy.deepcopy(doc["findings"][0]))

    errors = validate_findings_semantics(doc, artifact="findings")

    assert any("duplicate finding id" in e.message for e in errors)


def test_findings_unresolved_supporting_id_is_caught():
    doc = copy.deepcopy(_load("findings"))
    inferred = next(f for f in doc["findings"] if f["classification"] == "inferred")
    inferred["supporting_finding_ids"] = ["F-does-not-exist"]

    errors = validate_findings_semantics(doc, artifact="findings")

    assert any("unknown finding id" in e.message for e in errors)


def test_findings_supporting_id_must_be_found_classification():
    doc = copy.deepcopy(_load("findings"))
    inferred = next(f for f in doc["findings"] if f["classification"] == "inferred")
    not_found = next(f for f in doc["findings"] if f["classification"] == "not_found")
    inferred["supporting_finding_ids"] = [not_found["id"]]

    errors = validate_findings_semantics(doc, artifact="findings")

    assert any("must have classification 'found'" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Semantic validation -- implementation report
# ---------------------------------------------------------------------------


def test_implementation_report_example_has_no_semantic_errors():
    impl = _load("implementation-report")
    findings = _load("findings")
    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report.example.json")
    assert not errors, "\n".join(str(e) for e in errors)


def test_implementation_report_duplicate_command_id_is_caught():
    impl = copy.deepcopy(_load("implementation-report"))
    findings = _load("findings")
    impl["commands"].append(copy.deepcopy(impl["commands"][0]))

    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report")

    assert any("duplicate command id" in e.message for e in errors)


def test_implementation_report_findings_ref_must_resolve_to_found():
    impl = copy.deepcopy(_load("implementation-report"))
    findings = _load("findings")
    impl["findings_ref"]["finding_ids"] = ["F-2"]  # classification not_found in the example

    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report")

    assert any("does not resolve to a 'found' finding" in e.message for e in errors)


def test_implementation_report_test_first_evidence_must_resolve_to_commands():
    impl = copy.deepcopy(_load("implementation-report"))
    findings = _load("findings")
    impl["test_first_evidence"]["pre_implementation_command_id"] = "C-missing"

    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report")

    assert any("does not resolve to a command id" in e.message for e in errors)


def test_implementation_report_pre_stage_must_be_pre_implementation():
    impl = copy.deepcopy(_load("implementation-report"))
    findings = _load("findings")
    pre_cmd = next(c for c in impl["commands"] if c["id"] == impl["test_first_evidence"]["pre_implementation_command_id"])
    pre_cmd["stage"] = "other"

    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report")

    assert any("must have stage 'pre_implementation'" in e.message for e in errors)


def test_implementation_report_post_stage_must_be_post_implementation():
    impl = copy.deepcopy(_load("implementation-report"))
    findings = _load("findings")
    post_cmd = next(c for c in impl["commands"] if c["id"] == impl["test_first_evidence"]["post_implementation_command_id"])
    post_cmd["stage"] = "other"

    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report")

    assert any("must have stage 'post_implementation'" in e.message for e in errors)


def test_implementation_report_pre_exit_code_must_be_nonzero():
    impl = copy.deepcopy(_load("implementation-report"))
    findings = _load("findings")
    pre_cmd = next(c for c in impl["commands"] if c["id"] == impl["test_first_evidence"]["pre_implementation_command_id"])
    pre_cmd["exit_code"] = 0

    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report")

    assert any("must have a nonzero exit code" in e.message for e in errors)


def test_implementation_report_post_exit_code_must_be_zero():
    impl = copy.deepcopy(_load("implementation-report"))
    findings = _load("findings")
    post_cmd = next(c for c in impl["commands"] if c["id"] == impl["test_first_evidence"]["post_implementation_command_id"])
    post_cmd["exit_code"] = 1

    errors = validate_implementation_report_semantics(impl, findings, artifact="implementation-report")

    assert any("must have exit code 0" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Schema validation -- implementation report, status-conditional requirements
# ---------------------------------------------------------------------------

# A minimal, honest "blocked" report reflecting zero implementation progress -- e.g. the
# scope was refused, or an Architect citation failed re-verification before any file was
# touched. It must be valid without fabricating changed_files, commands, tests, or
# test_first_evidence.
ZERO_PROGRESS_BLOCKED_REPORT = {
    "schema_version": "1.0",
    "task_id": "T-002",
    "run_id": "run-block-01",
    "created_at": "2026-07-27T00:00:00Z",
    "scope_ref": {"path": "runs/run-block-01/scope.json"},
    "status": "blocked",
    "blocked_reason": "Scope status was 'refused'; no implementation work was performed.",
    "dependency_changes": [],
}


def _implementation_report_schema() -> dict:
    pair = next(p for p in COMPLETE_PAIRS if p.name == "implementation-report")
    return load_json(pair.schema_path)


def test_implementation_report_ready_for_verification_example_is_schema_valid():
    schema = _implementation_report_schema()
    instance = _load("implementation-report")  # example is a ready_for_verification report

    errors = validate_against_schema(instance, schema, artifact="implementation-report.example.json")

    assert not errors, "\n".join(str(e) for e in errors)


def test_implementation_report_zero_progress_blocked_is_schema_valid():
    schema = _implementation_report_schema()

    errors = validate_against_schema(
        ZERO_PROGRESS_BLOCKED_REPORT, schema, artifact="implementation-report (zero-progress blocked)"
    )

    assert not errors, "\n".join(str(e) for e in errors)


def test_implementation_report_blocked_without_blocked_reason_fails():
    schema = _implementation_report_schema()
    doc = copy.deepcopy(ZERO_PROGRESS_BLOCKED_REPORT)
    del doc["blocked_reason"]

    errors = validate_against_schema(doc, schema, artifact="implementation-report (blocked, no reason)")

    assert any("blocked_reason" in e.message for e in errors)


def test_implementation_report_ready_for_verification_without_changed_files_fails():
    schema = _implementation_report_schema()
    doc = copy.deepcopy(_load("implementation-report"))
    del doc["changed_files"]

    errors = validate_against_schema(doc, schema, artifact="implementation-report (missing changed_files)")

    assert any("changed_files" in e.message for e in errors)


def test_implementation_report_ready_for_verification_without_commands_fails():
    schema = _implementation_report_schema()
    doc = copy.deepcopy(_load("implementation-report"))
    del doc["commands"]

    errors = validate_against_schema(doc, schema, artifact="implementation-report (missing commands)")

    assert any("commands" in e.message for e in errors)


def test_implementation_report_ready_for_verification_without_tests_fails():
    schema = _implementation_report_schema()
    doc = copy.deepcopy(_load("implementation-report"))
    del doc["tests"]

    errors = validate_against_schema(doc, schema, artifact="implementation-report (missing tests)")

    assert any("tests" in e.message for e in errors)


def test_implementation_report_ready_for_verification_without_test_first_evidence_fails():
    schema = _implementation_report_schema()
    doc = copy.deepcopy(_load("implementation-report"))
    del doc["test_first_evidence"]

    errors = validate_against_schema(doc, schema, artifact="implementation-report (missing test_first_evidence)")

    assert any("test_first_evidence" in e.message for e in errors)


def test_implementation_report_blocked_does_not_need_fabricated_test_first_evidence():
    # Schema level: the zero-progress blocked report has no test_first_evidence/commands/
    # tests/changed_files keys at all, and is still valid (covered above). Semantic level:
    # the validator must not manufacture "missing" errors for fields that were never
    # required in the first place.
    errors = validate_implementation_report_semantics(
        ZERO_PROGRESS_BLOCKED_REPORT, findings_doc={}, artifact="implementation-report (zero-progress blocked)"
    )

    assert not errors, "\n".join(str(e) for e in errors)


# ---------------------------------------------------------------------------
# Semantic validation -- verification report
# ---------------------------------------------------------------------------


def test_verification_report_example_has_no_semantic_errors():
    verification = _load("verification-report")
    scope = _load("scope")
    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report.example.json")
    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_duplicate_attempt_id_is_caught():
    verification = copy.deepcopy(_load("verification-report"))
    scope = _load("scope")
    verification["attempts"].append(copy.deepcopy(verification["attempts"][0]))

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("duplicate attempt id" in e.message for e in errors)


def test_verification_report_criteria_id_must_resolve_in_scope():
    verification = copy.deepcopy(_load("verification-report"))
    scope = _load("scope")
    verification["acceptance_criteria_results"][0]["criteria_id"] = "AC-missing"

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("does not resolve in the referenced scope artifact" in e.message for e in errors)


def test_verification_report_attempt_refs_must_resolve():
    verification = copy.deepcopy(_load("verification-report"))
    scope = _load("scope")
    verification["acceptance_criteria_results"][0]["attempt_refs"] = ["V-missing"]

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("does not resolve to an existing attempt" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Schema validation -- verification report, status-conditional requirements
# (verification-report.schema.json contract fix: a genuine zero-progress "blocked" report
# must not be forced to fabricate attempts/acceptance_criteria_results it never produced.)
# ---------------------------------------------------------------------------

# An honest "blocked" report reflecting zero real progress -- e.g. the caller's path_validation
# attestation was missing/false, or the implementation report itself never reached
# ready_for_verification, so no test command was ever requested. Must be valid without
# fabricating attempts, acceptance_criteria_results, retry_policy, or routed_back_to_engineer.
VERIFICATION_REPORT_ZERO_PROGRESS_BLOCKED = {
    "schema_version": "1.0",
    "task_id": "T-003",
    "run_id": "run-block-02",
    "created_at": "2026-07-28T00:00:00Z",
    "scope_ref": {"path": "runs/run-block-02/scope.json"},
    "implementation_ref": {"path": "runs/run-block-02/implementation-report.json"},
    "final_verdict": "blocked",
    "blocked_reason": "path_validation attestation was missing; no test command was ever requested.",
}

# A "fail" report backed by real, evidence-shaped logic_bug evidence -- reused across several
# tests below rather than re-declared per test.
VERIFICATION_REPORT_FAIL = {
    "schema_version": "1.0",
    "task_id": "T-004",
    "run_id": "run-fail-01",
    "created_at": "2026-07-28T01:00:00Z",
    "scope_ref": {"path": "runs/run-fail-01/scope.json"},
    "implementation_ref": {"path": "runs/run-fail-01/implementation-report.json"},
    "attempts": [
        {
            "id": "V-1",
            "command": "pytest demo-repo/tests/test_pagination.py",
            "exit_code": 1,
            "classification": "logic_bug",
            "output_ref": "runs/run-fail-01/verify-1.log",
            "retried": False,
        }
    ],
    "retry_policy": {"max_retries": 2},
    "acceptance_criteria_results": [
        {
            "criteria_id": "AC-1",
            "result": "failed",
            "attempt_refs": ["V-1"],
            "evidence_summary": "total_pages() undercounts for non-exact multiples; AssertionError comparing 2 == 3.",
        }
    ],
    "final_verdict": "fail",
    "routed_back_to_engineer": {"routed": True, "reason": "total_pages() has a floor-division logic defect."},
}

# A schema-valid "inconclusive" report -- used only to prove attempts/acceptance_criteria_results
# remain required (unlike "blocked") for this final_verdict too.
VERIFICATION_REPORT_INCONCLUSIVE = {
    "schema_version": "1.0",
    "task_id": "T-005",
    "run_id": "run-inconclusive-01",
    "created_at": "2026-07-28T02:00:00Z",
    "scope_ref": {"path": "runs/run-inconclusive-01/scope.json"},
    "implementation_ref": {"path": "runs/run-inconclusive-01/implementation-report.json"},
    "attempts": [
        {
            "id": "V-1",
            "command": "pytest demo-repo/tests/test_pagination.py",
            "exit_code": 1,
            "classification": "infrastructure_flake",
            "output_ref": "runs/run-inconclusive-01/verify-1.log",
            "retried": False,
        }
    ],
    "retry_policy": {"max_retries": 2},
    "acceptance_criteria_results": [
        {
            "criteria_id": "AC-1",
            "result": "blocked",
            "attempt_refs": ["V-1"],
            "evidence_summary": "Retry budget exhausted on a suspected flake without a clean pass or reproducible failure.",
        }
    ],
    "final_verdict": "inconclusive",
    "routed_back_to_engineer": {"routed": False},
}


def _verification_report_schema() -> dict:
    pair = next(p for p in COMPLETE_PAIRS if p.name == "verification-report")
    return load_json(pair.schema_path)


def _two_criteria_scope() -> dict:
    """The stock scope example, plus a second acceptance criterion (AC-2) -- needed to prove
    coverage checks catch a criterion that a report silently drops."""
    scope = copy.deepcopy(_load("scope"))
    scope["acceptance_criteria"].append(
        {"id": "AC-2", "description": "Second, independent acceptance criterion for coverage tests."}
    )
    return scope


def test_verification_report_pass_example_still_conforms_to_schema():
    # Guards against a regression in the contract fix: the pre-existing "pass" example (now
    # carrying retry_policy/routed_back_to_engineer, newly required for non-"blocked" verdicts)
    # must still validate cleanly.
    schema = _verification_report_schema()
    instance = _load("verification-report")

    errors = validate_against_schema(instance, schema, artifact="verification-report.example.json")

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_zero_progress_blocked_is_schema_valid():
    schema = _verification_report_schema()

    errors = validate_against_schema(
        VERIFICATION_REPORT_ZERO_PROGRESS_BLOCKED, schema, artifact="verification-report (zero-progress blocked)"
    )

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_blocked_without_blocked_reason_fails():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_ZERO_PROGRESS_BLOCKED)
    del doc["blocked_reason"]

    errors = validate_against_schema(doc, schema, artifact="verification-report (blocked, no reason)")

    assert any("blocked_reason" in e.message for e in errors)


def test_verification_report_blocked_does_not_require_attempts():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_ZERO_PROGRESS_BLOCKED)
    assert "attempts" not in doc

    errors = validate_against_schema(doc, schema, artifact="verification-report (blocked, no attempts)")

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_blocked_does_not_require_acceptance_criteria_results():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_ZERO_PROGRESS_BLOCKED)
    assert "acceptance_criteria_results" not in doc

    errors = validate_against_schema(doc, schema, artifact="verification-report (blocked, no criteria results)")

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_pass_without_attempts_fails():
    schema = _verification_report_schema()
    doc = copy.deepcopy(_load("verification-report"))
    del doc["attempts"]

    errors = validate_against_schema(doc, schema, artifact="verification-report (pass, missing attempts)")

    assert any("attempts" in e.message for e in errors)


def test_verification_report_pass_without_acceptance_criteria_results_fails():
    schema = _verification_report_schema()
    doc = copy.deepcopy(_load("verification-report"))
    del doc["acceptance_criteria_results"]

    errors = validate_against_schema(doc, schema, artifact="verification-report (pass, missing criteria results)")

    assert any("acceptance_criteria_results" in e.message for e in errors)


def test_verification_report_fail_without_attempts_fails():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_FAIL)
    del doc["attempts"]

    errors = validate_against_schema(doc, schema, artifact="verification-report (fail, missing attempts)")

    assert any("attempts" in e.message for e in errors)


def test_verification_report_inconclusive_without_real_attempts_fails():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_INCONCLUSIVE)
    del doc["attempts"]

    errors = validate_against_schema(doc, schema, artifact="verification-report (inconclusive, missing attempts)")

    assert any("attempts" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Schema validation -- criterionResult.attempt_refs conditional requirement
# (contract fix: 'passed'/'failed'/'blocked' still require a real attempt_ref; 'not_run' may
# omit the field entirely rather than being forced to carry a fabricated one.)
# ---------------------------------------------------------------------------


def test_verification_report_criterion_result_passed_requires_attempt_refs():
    schema = _verification_report_schema()
    doc = copy.deepcopy(_load("verification-report"))
    del doc["acceptance_criteria_results"][0]["attempt_refs"]

    errors = validate_against_schema(doc, schema, artifact="verification-report (passed, missing attempt_refs)")

    assert any("attempt_refs" in e.message for e in errors)


def test_verification_report_criterion_result_passed_rejects_empty_attempt_refs():
    schema = _verification_report_schema()
    doc = copy.deepcopy(_load("verification-report"))
    doc["acceptance_criteria_results"][0]["attempt_refs"] = []

    errors = validate_against_schema(doc, schema, artifact="verification-report (passed, empty attempt_refs)")

    assert errors


def test_verification_report_criterion_result_failed_requires_attempt_refs():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_FAIL)
    del doc["acceptance_criteria_results"][0]["attempt_refs"]

    errors = validate_against_schema(doc, schema, artifact="verification-report (failed, missing attempt_refs)")

    assert any("attempt_refs" in e.message for e in errors)


def test_verification_report_criterion_result_not_run_may_omit_attempt_refs():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_INCONCLUSIVE)
    doc["acceptance_criteria_results"].append(
        {
            "criteria_id": "AC-2",
            "result": "not_run",
            "evidence_summary": "Verification stopped once the retry budget on AC-1 was exhausted; AC-2 was never attempted.",
        }
    )

    errors = validate_against_schema(doc, schema, artifact="verification-report (not_run, no attempt_refs)")

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_criterion_result_not_run_requires_evidence_summary():
    schema = _verification_report_schema()
    doc = copy.deepcopy(VERIFICATION_REPORT_INCONCLUSIVE)
    doc["acceptance_criteria_results"].append({"criteria_id": "AC-2", "result": "not_run"})

    errors = validate_against_schema(doc, schema, artifact="verification-report (not_run, missing evidence_summary)")

    assert any("evidence_summary" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Semantic validation -- verification report (contract fix additions)
# ---------------------------------------------------------------------------


def test_verification_report_blocked_report_has_no_semantic_errors():
    # The core "no false missing-attempt errors" guarantee: a genuine zero-progress blocked
    # report, run through the semantic validator with no scope doc available, produces nothing.
    errors = validate_verification_report_semantics(
        VERIFICATION_REPORT_ZERO_PROGRESS_BLOCKED, scope_doc={}, artifact="verification-report (zero-progress blocked)"
    )

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_pass_with_failed_criterion_is_rejected():
    verification = copy.deepcopy(_load("verification-report"))
    scope = _load("scope")
    verification["acceptance_criteria_results"][0]["result"] = "failed"

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("final_verdict is 'pass'" in e.message and "not 'passed'" in e.message for e in errors)


def test_verification_report_fail_without_any_failed_criterion_is_rejected():
    verification = copy.deepcopy(VERIFICATION_REPORT_FAIL)
    scope = _load("scope")
    verification["acceptance_criteria_results"][0]["result"] = "passed"

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("final_verdict is 'fail'" in e.message and "no acceptance_criteria_results entry has result 'failed'" in e.message for e in errors)


def test_verification_report_logic_bug_must_not_be_retried():
    verification = copy.deepcopy(VERIFICATION_REPORT_FAIL)
    scope = _load("scope")
    verification["attempts"][0]["retried"] = True

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("logic_bug' must not be marked retried" in e.message for e in errors)


def test_verification_report_routed_back_requires_logic_bug_evidence():
    verification = copy.deepcopy(_load("verification-report"))  # a "pass" report, no logic_bug attempts
    scope = _load("scope")
    verification["routed_back_to_engineer"] = {"routed": True, "reason": "fabricated"}

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("routed_back_to_engineer.routed is true but no attempt has classification 'logic_bug'" in e.message for e in errors)


def test_verification_report_logic_bug_requires_routed_back():
    verification = copy.deepcopy(VERIFICATION_REPORT_FAIL)
    scope = _load("scope")
    verification["routed_back_to_engineer"] = {"routed": False}

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("'logic_bug' is present but routed_back_to_engineer.routed is false" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Semantic validation -- verification report, acceptance-criterion coverage
# (contract fix: acceptance_criteria_results must be a complete, duplicate-free, one-to-one
# mapping onto scope.json's acceptance_criteria whenever final_verdict isn't a genuine
# zero-progress 'blocked'.)
# ---------------------------------------------------------------------------


def test_verification_report_duplicate_criteria_id_is_caught():
    verification = copy.deepcopy(_load("verification-report"))
    scope = _load("scope")
    verification["acceptance_criteria_results"].append(copy.deepcopy(verification["acceptance_criteria_results"][0]))

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("duplicate criteria_id" in e.message for e in errors)


def test_verification_report_pass_omitting_scope_criterion_is_rejected():
    # The report only covers AC-1 (the stock example); the scope now also has AC-2.
    verification = copy.deepcopy(_load("verification-report"))
    scope = _two_criteria_scope()

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("scope criterion 'AC-2' is not represented" in e.message for e in errors)


def test_verification_report_pass_covering_all_scope_criteria_has_no_errors():
    verification = copy.deepcopy(_load("verification-report"))
    scope = _two_criteria_scope()
    verification["attempts"].append(
        {
            "id": "V-2",
            "command": "pytest demo-repo/tests/test_pagination_extra.py",
            "exit_code": 0,
            "classification": "pass",
            "output_ref": "runs/run-20260722-01/verify-2.log",
            "retried": False,
        }
    )
    verification["acceptance_criteria_results"].append(
        {
            "criteria_id": "AC-2",
            "result": "passed",
            "attempt_refs": ["V-2"],
            "evidence_summary": "Second, independent criterion verified via its own targeted test.",
        }
    )

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_fail_omitting_untested_criterion_is_rejected():
    verification = copy.deepcopy(VERIFICATION_REPORT_FAIL)
    scope = _two_criteria_scope()  # AC-2 silently dropped even though verification stopped early

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("scope criterion 'AC-2' is not represented" in e.message for e in errors)


def test_verification_report_fail_with_honest_not_run_criterion_has_no_coverage_errors():
    verification = copy.deepcopy(VERIFICATION_REPORT_FAIL)
    scope = _two_criteria_scope()
    verification["acceptance_criteria_results"].append(
        {
            "criteria_id": "AC-2",
            "result": "not_run",
            "evidence_summary": "Verification stopped once AC-1 failed with a deterministic logic_bug; AC-2 was never attempted.",
        }
    )

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert not errors, "\n".join(str(e) for e in errors)


def test_verification_report_inconclusive_omitting_unresolved_criterion_is_rejected():
    verification = copy.deepcopy(VERIFICATION_REPORT_INCONCLUSIVE)
    scope = _two_criteria_scope()  # AC-2 never appears, not even as 'blocked'/'not_run'

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert any("scope criterion 'AC-2' is not represented" in e.message for e in errors)


def test_verification_report_not_run_criterion_has_no_semantic_errors():
    # Honest "not_run" needs no attempt_refs and no fabricated attempt to point at.
    verification = copy.deepcopy(VERIFICATION_REPORT_INCONCLUSIVE)
    scope = _two_criteria_scope()
    verification["acceptance_criteria_results"].append(
        {
            "criteria_id": "AC-2",
            "result": "not_run",
            "evidence_summary": "Verification stopped after the retry budget on AC-1 was exhausted; AC-2 was never attempted.",
        }
    )

    errors = validate_verification_report_semantics(verification, scope, artifact="verification-report")

    assert not errors, "\n".join(str(e) for e in errors)


# ---------------------------------------------------------------------------
# Semantic validation -- checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_example_has_no_semantic_errors():
    doc = _load("checkpoint")
    errors = validate_checkpoint_semantics(doc, artifact="checkpoint.example.json")
    assert not errors, "\n".join(str(e) for e in errors)


def test_checkpoint_current_phase_must_not_be_in_completed_phases():
    doc = copy.deepcopy(_load("checkpoint"))
    doc["completed_phases"].append(doc["current_phase"])

    errors = validate_checkpoint_semantics(doc, artifact="checkpoint")

    assert any("must not appear in completed_phases" in e.message for e in errors)


def test_checkpoint_artifact_refs_only_for_completed_phases():
    doc = copy.deepcopy(_load("checkpoint"))
    doc["artifact_refs"]["verification"] = "runs/run-20260722-01/verification-report.json"

    errors = validate_checkpoint_semantics(doc, artifact="checkpoint")

    assert any("not in completed_phases" in e.message for e in errors)


def test_checkpoint_complete_status_requires_all_four_phases():
    doc = copy.deepcopy(_load("checkpoint"))
    doc["status"] = "complete"
    doc["current_phase"] = None
    doc["next_phase"] = None
    doc["next_resume_action"] = None
    # completed_phases still only has ["discovery", "research"]

    errors = validate_checkpoint_semantics(doc, artifact="checkpoint")

    assert any("completed_phases is missing" in e.message for e in errors)


def test_checkpoint_complete_status_requires_null_phase_fields():
    doc = copy.deepcopy(_load("checkpoint"))
    doc["status"] = "complete"
    doc["completed_phases"] = ["discovery", "research", "implementation", "verification"]
    doc["current_phase"] = "verification"  # should be null when complete

    errors = validate_checkpoint_semantics(doc, artifact="checkpoint")

    assert any("must be null when status is 'complete'" in e.message for e in errors)
