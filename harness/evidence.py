"""Validation for the harness's artifact contracts.

Covers two layers for the JSON artifacts under harness/artifacts/examples/,
checked against the Draft 2020-12 schemas under harness/schemas/:

1. Schema conformance (via jsonschema).
2. Semantic rules that JSON Schema cannot express (uniqueness within an
   array, cross-references between artifacts, conditional field rules) --
   each schema's own "$comment" documents which rules are deferred here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

SCHEMA_SUFFIX = ".schema.json"
EXAMPLE_SUFFIX = ".example.json"

PHASE_ORDER = ("discovery", "research", "implementation", "verification")


@dataclass(frozen=True)
class ValidationError:
    """A single validation failure, anchored to the artifact and JSON path it came from."""

    artifact: str
    json_path: str
    message: str

    def __str__(self) -> str:
        return f"{self.artifact} [{self.json_path}]: {self.message}"


@dataclass(frozen=True)
class ArtifactPair:
    """A schema/example pair discovered by shared basename (e.g. "scope")."""

    name: str
    schema_path: Path | None
    example_path: Path | None


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def discover_artifact_pairs(schema_dir: str | Path, example_dir: str | Path) -> list[ArtifactPair]:
    """Pair every *.schema.json with its *.example.json by basename.

    A name present on only one side yields an ArtifactPair with the other
    path set to None, so callers can assert every pair is complete.
    """
    schema_dir = Path(schema_dir)
    example_dir = Path(example_dir)
    schemas = {p.name[: -len(SCHEMA_SUFFIX)]: p for p in schema_dir.glob(f"*{SCHEMA_SUFFIX}")}
    examples = {p.name[: -len(EXAMPLE_SUFFIX)]: p for p in example_dir.glob(f"*{EXAMPLE_SUFFIX}")}
    names = sorted(set(schemas) | set(examples))
    return [ArtifactPair(name, schemas.get(name), examples.get(name)) for name in names]


def _json_path(error) -> str:
    parts = ["$"]
    for p in error.absolute_path:
        parts.append(f"[{p!r}]" if isinstance(p, int) else f".{p}")
    return "".join(parts)


def check_schema_document(schema: dict, *, artifact: str = "<schema>") -> list[ValidationError]:
    """Validate that `schema` is itself a well-formed Draft 2020-12 schema."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        path = "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in exc.path)
        return [ValidationError(artifact, path, exc.message)]
    return []


def validate_against_schema(instance: Any, schema: dict, *, artifact: str = "<instance>") -> list[ValidationError]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [ValidationError(artifact, _json_path(e), e.message) for e in errors]


# ---------------------------------------------------------------------------
# Semantic validation -- rules each schema's $comment defers to Python.
# ---------------------------------------------------------------------------


def _duplicate_ids(items: list[dict], id_key: str = "id") -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for item in items:
        item_id = item.get(id_key)
        if item_id is None:
            continue
        if item_id in seen:
            dupes.add(item_id)
        seen.add(item_id)
    return dupes


def validate_findings_semantics(doc: dict, *, artifact: str = "findings") -> list[ValidationError]:
    errors: list[ValidationError] = []
    findings = doc.get("findings", [])

    dupes = _duplicate_ids(findings)
    for idx, finding in enumerate(findings):
        if finding.get("id") in dupes:
            errors.append(
                ValidationError(artifact, f"$.findings[{idx}].id", f"duplicate finding id {finding.get('id')!r}")
            )

    by_id = {f["id"]: f for f in findings if "id" in f}
    for idx, finding in enumerate(findings):
        if finding.get("classification") != "inferred":
            continue
        for ref_idx, supporting_id in enumerate(finding.get("supporting_finding_ids", [])):
            path = f"$.findings[{idx}].supporting_finding_ids[{ref_idx}]"
            target = by_id.get(supporting_id)
            if target is None:
                errors.append(
                    ValidationError(artifact, path, f"supporting_finding_ids references unknown finding id {supporting_id!r}")
                )
            elif target.get("classification") != "found":
                errors.append(
                    ValidationError(
                        artifact,
                        path,
                        f"supporting finding {supporting_id!r} must have classification 'found', "
                        f"got {target.get('classification')!r}",
                    )
                )
    return errors


def validate_scope_semantics(doc: dict, *, artifact: str = "scope") -> list[ValidationError]:
    errors: list[ValidationError] = []

    acceptance_criteria = doc.get("acceptance_criteria", [])
    ac_dupes = _duplicate_ids(acceptance_criteria)
    for idx, criterion in enumerate(acceptance_criteria):
        if criterion.get("id") in ac_dupes:
            errors.append(
                ValidationError(
                    artifact,
                    f"$.acceptance_criteria[{idx}].id",
                    f"duplicate acceptance criterion id {criterion.get('id')!r}",
                )
            )

    task_graph = doc.get("task_graph", [])
    node_dupes = _duplicate_ids(task_graph)
    for idx, node in enumerate(task_graph):
        if node.get("id") in node_dupes:
            errors.append(
                ValidationError(artifact, f"$.task_graph[{idx}].id", f"duplicate task_graph node id {node.get('id')!r}")
            )

    # depends_on resolution and self-dependency are checked even for a node whose own id is
    # itself a duplicate -- both problems can be real at once and neither excuses the other.
    node_ids = {n["id"] for n in task_graph if "id" in n}
    for idx, node in enumerate(task_graph):
        node_id = node.get("id")
        for dep_idx, dependency_id in enumerate(node.get("depends_on", [])):
            path = f"$.task_graph[{idx}].depends_on[{dep_idx}]"
            if dependency_id == node_id:
                errors.append(ValidationError(artifact, path, f"task_graph node {node_id!r} must not depend on itself"))
            elif dependency_id not in node_ids:
                errors.append(
                    ValidationError(artifact, path, f"depends_on references unknown task_graph node id {dependency_id!r}")
                )

    # The schema's own if/then only requires refusal_reason to be PRESENT when status is
    # 'refused' -- it does not forbid the field when status is 'approved'. A refusal_reason on
    # an approved scope is contradictory on its face, so that direction is enforced here.
    if doc.get("status") == "approved" and "refusal_reason" in doc:
        errors.append(ValidationError(artifact, "$.refusal_reason", "must not be present when status is 'approved'"))

    return errors


def validate_implementation_report_semantics(
    doc: dict, findings_doc: dict, *, artifact: str = "implementation-report"
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    commands = doc.get("commands", [])

    dupes = _duplicate_ids(commands)
    for idx, command in enumerate(commands):
        if command.get("id") in dupes:
            errors.append(
                ValidationError(artifact, f"$.commands[{idx}].id", f"duplicate command id {command.get('id')!r}")
            )

    by_id = {c["id"]: c for c in commands if "id" in c}

    # findings_ref is optional on a "blocked" report (e.g. a zero-progress block reported
    # before any findings were consulted). Only cross-check it when it's actually present --
    # its own field-level structure (path, finding_ids) is still enforced by the schema
    # whenever the key exists, regardless of status.
    if "findings_ref" in doc:
        found_ids = {f["id"] for f in findings_doc.get("findings", []) if f.get("classification") == "found"}
        findings_ref = doc.get("findings_ref", {})
        for idx, finding_id in enumerate(findings_ref.get("finding_ids", [])):
            if finding_id not in found_ids:
                errors.append(
                    ValidationError(
                        artifact,
                        f"$.findings_ref.finding_ids[{idx}]",
                        f"finding id {finding_id!r} does not resolve to a 'found' finding in the referenced findings artifact",
                    )
                )

    # test_first_evidence is likewise optional on a "blocked" report -- only resolve it against
    # commands[] when the report actually includes it.
    if "test_first_evidence" in doc:
        test_first_evidence = doc.get("test_first_evidence", {})
        checks = [
            ("pre_implementation_command_id", "pre_implementation"),
            ("post_implementation_command_id", "post_implementation"),
        ]
        for field, expected_stage in checks:
            command_id = test_first_evidence.get(field)
            path = f"$.test_first_evidence.{field}"
            command = by_id.get(command_id)
            if command is None:
                errors.append(
                    ValidationError(artifact, path, f"does not resolve to a command id in commands[] ({command_id!r})")
                )
                continue
            if command.get("stage") != expected_stage:
                errors.append(
                    ValidationError(
                        artifact,
                        path,
                        f"referenced command {command_id!r} must have stage {expected_stage!r}, got {command.get('stage')!r}",
                    )
                )
            exit_code = command.get("exit_code")
            if expected_stage == "pre_implementation" and exit_code == 0:
                errors.append(
                    ValidationError(
                        artifact, path, f"pre-implementation command {command_id!r} must have a nonzero exit code, got 0"
                    )
                )
            if expected_stage == "post_implementation" and exit_code != 0:
                errors.append(
                    ValidationError(
                        artifact,
                        path,
                        f"post-implementation command {command_id!r} must have exit code 0, got {exit_code!r}",
                    )
                )
    return errors


def validate_verification_report_semantics(
    doc: dict, scope_doc: dict, *, artifact: str = "verification-report"
) -> list[ValidationError]:
    errors: list[ValidationError] = []

    # attempts and acceptance_criteria_results are both optional on a "blocked" report with no
    # real progress (per the schema's status-conditional "required" fields) -- defaulting to []
    # here means every check below naturally no-ops for a genuine zero-progress block instead of
    # manufacturing "missing" errors for fields that were never required in the first place.
    attempts = doc.get("attempts", [])
    criterion_results = doc.get("acceptance_criteria_results", [])
    final_verdict = doc.get("final_verdict")

    dupes = _duplicate_ids(attempts)
    for idx, attempt in enumerate(attempts):
        if attempt.get("id") in dupes:
            errors.append(
                ValidationError(artifact, f"$.attempts[{idx}].id", f"duplicate attempt id {attempt.get('id')!r}")
            )

    attempt_ids = {a["id"] for a in attempts if "id" in a}
    criteria_ids = {c["id"] for c in scope_doc.get("acceptance_criteria", []) if "id" in c}

    # A criterion covered twice is exactly as dishonest as one silently dropped -- both hide the
    # true one-to-one mapping to scope.json's acceptance_criteria.
    criterion_dupes = _duplicate_ids(criterion_results, id_key="criteria_id")
    for idx, result in enumerate(criterion_results):
        if result.get("criteria_id") in criterion_dupes:
            errors.append(
                ValidationError(
                    artifact,
                    f"$.acceptance_criteria_results[{idx}].criteria_id",
                    f"duplicate criteria_id {result.get('criteria_id')!r} in acceptance_criteria_results",
                )
            )

    # A non-"blocked" verdict (schema-required to carry acceptance_criteria_results at all) must
    # still represent every scope criterion exactly once -- a criterion the verification never
    # reached must appear honestly as 'blocked'/'not_run', not be silently omitted. Only
    # meaningful when criterion_results is actually present, so a genuine zero-progress
    # "blocked" report (which omits the field entirely) is naturally exempt.
    if final_verdict in ("pass", "fail", "inconclusive") and criterion_results:
        covered_ids = {r.get("criteria_id") for r in criterion_results}
        for missing_id in sorted(criteria_ids - covered_ids):
            errors.append(
                ValidationError(
                    artifact,
                    "$.acceptance_criteria_results",
                    f"final_verdict {final_verdict!r} but scope criterion {missing_id!r} is not represented in acceptance_criteria_results",
                )
            )

    for idx, result in enumerate(criterion_results):
        criteria_id = result.get("criteria_id")
        if criteria_id not in criteria_ids:
            errors.append(
                ValidationError(
                    artifact,
                    f"$.acceptance_criteria_results[{idx}].criteria_id",
                    f"criteria id {criteria_id!r} does not resolve in the referenced scope artifact",
                )
            )
        for ref_idx, attempt_ref in enumerate(result.get("attempt_refs", [])):
            if attempt_ref not in attempt_ids:
                errors.append(
                    ValidationError(
                        artifact,
                        f"$.acceptance_criteria_results[{idx}].attempt_refs[{ref_idx}]",
                        f"attempt_refs entry {attempt_ref!r} does not resolve to an existing attempt",
                    )
                )

    # final_verdict "pass" requires every included criterion result to genuinely be "passed" --
    # only meaningful when criterion_results is actually present (never on a zero-progress block).
    if final_verdict == "pass":
        for idx, result in enumerate(criterion_results):
            if result.get("result") != "passed":
                errors.append(
                    ValidationError(
                        artifact,
                        f"$.acceptance_criteria_results[{idx}].result",
                        f"final_verdict is 'pass' but this criterion result is {result.get('result')!r}, not 'passed'",
                    )
                )

    # final_verdict "fail" requires at least one criterion result to genuinely be "failed" -- a
    # "fail" verdict with no failed criterion anywhere is not evidence-backed.
    if final_verdict == "fail" and criterion_results:
        if not any(r.get("result") == "failed" for r in criterion_results):
            errors.append(
                ValidationError(
                    artifact,
                    "$.final_verdict",
                    "final_verdict is 'fail' but no acceptance_criteria_results entry has result 'failed'",
                )
            )

    # A logic_bug is deterministic by definition -- retrying it is never appropriate, so an
    # attempt classified logic_bug must never itself be the product of a retry.
    for idx, attempt in enumerate(attempts):
        if attempt.get("classification") == "logic_bug" and attempt.get("retried") is True:
            errors.append(
                ValidationError(
                    artifact,
                    f"$.attempts[{idx}].retried",
                    "an attempt classified 'logic_bug' must not be marked retried -- logic bugs are never retried",
                )
            )

    # routed_back_to_engineer must be evidence-backed in both directions: claiming a route-back
    # with no logic_bug attempt is fabricated, and finding a logic_bug without routing back
    # contradicts the requirement to hand implementation defects back to the Engineer rather than
    # fix them directly. Only checked when the field is actually present (optional on "blocked").
    routed_info = doc.get("routed_back_to_engineer")
    if routed_info is not None:
        has_logic_bug = any(a.get("classification") == "logic_bug" for a in attempts)
        routed = routed_info.get("routed")
        if routed is True and not has_logic_bug:
            errors.append(
                ValidationError(
                    artifact,
                    "$.routed_back_to_engineer.routed",
                    "routed_back_to_engineer.routed is true but no attempt has classification 'logic_bug' to justify it",
                )
            )
        if routed is False and has_logic_bug:
            errors.append(
                ValidationError(
                    artifact,
                    "$.routed_back_to_engineer.routed",
                    "an attempt classified 'logic_bug' is present but routed_back_to_engineer.routed is false",
                )
            )

    return errors


def validate_checkpoint_semantics(doc: dict, *, artifact: str = "checkpoint") -> list[ValidationError]:
    errors: list[ValidationError] = []
    completed_phases = doc.get("completed_phases", [])
    current_phase = doc.get("current_phase")

    if current_phase is not None and current_phase in completed_phases:
        errors.append(
            ValidationError(
                artifact, "$.current_phase", f"current_phase {current_phase!r} must not appear in completed_phases"
            )
        )

    artifact_refs = doc.get("artifact_refs", {})
    for phase in artifact_refs:
        if phase not in completed_phases:
            errors.append(
                ValidationError(
                    artifact,
                    f"$.artifact_refs.{phase}",
                    f"artifact_refs has an entry for phase {phase!r}, which is not in completed_phases",
                )
            )

    # The reverse of the check above -- every phase claimed complete must have a
    # corresponding artifact_refs entry. Without this, a checkpoint could claim
    # completed_phases: ["discovery", "research"] with an empty artifact_refs and pass
    # validation: a false completed-phase claim with nothing backing it.
    for phase in completed_phases:
        if phase not in artifact_refs:
            errors.append(
                ValidationError(
                    artifact,
                    "$.completed_phases",
                    f"phase {phase!r} is marked completed but artifact_refs has no entry for it",
                )
            )

    # completed_phases must be a contiguous prefix of the fixed phase order -- no phase
    # skipped, no phase out of order. This is what makes "Research requires valid
    # scope.json," "Implementation requires valid scope.json and findings.json," and
    # "Verification requires scope, findings, and implementation report" true by
    # construction rather than by convention: a phase can only be complete if every
    # phase before it in PHASE_ORDER is complete too.
    completed_set = set(completed_phases)
    prefix_len = 0
    for phase in PHASE_ORDER:
        if phase not in completed_set:
            break
        prefix_len += 1
    if len(completed_set) != prefix_len:
        errors.append(
            ValidationError(
                artifact,
                "$.completed_phases",
                f"completed_phases must be a contiguous prefix of {list(PHASE_ORDER)} with no phase skipped "
                f"or out of order, got {completed_phases!r}",
            )
        )

    if doc.get("status") == "complete":
        missing = [p for p in PHASE_ORDER if p not in completed_phases]
        if missing:
            errors.append(
                ValidationError(
                    artifact,
                    "$.completed_phases",
                    f"status is 'complete' but completed_phases is missing: {missing}",
                )
            )
        for field in ("current_phase", "next_phase", "next_resume_action"):
            if doc.get(field) is not None:
                errors.append(
                    ValidationError(artifact, f"$.{field}", f"must be null when status is 'complete', got {doc.get(field)!r}")
                )

    # No rule here checks next_phase against current_phase -- deliberately, not an
    # omission. See checkpoint.schema.json's own $comment for the full reasoning: every
    # harness/orchestrator/checkpoint.py record_* builder derives next_phase as
    # current_phase's immediate successor by construction (given the contiguous-prefix
    # rule above already holds), and checkpoint.evaluate_resume() never reads the
    # persisted current_phase/next_phase fields back as authoritative -- it re-derives
    # the phase to restart directly from the revalidated completed_phases. A stale or
    # hand-edited next_phase can therefore mislead a human reading checkpoint.json, but
    # cannot mislead resume() itself, so it is not part of this safety-critical list.
    return errors
