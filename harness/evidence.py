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
    attempts = doc.get("attempts", [])

    dupes = _duplicate_ids(attempts)
    for idx, attempt in enumerate(attempts):
        if attempt.get("id") in dupes:
            errors.append(
                ValidationError(artifact, f"$.attempts[{idx}].id", f"duplicate attempt id {attempt.get('id')!r}")
            )

    attempt_ids = {a["id"] for a in attempts if "id" in a}
    criteria_ids = {c["id"] for c in scope_doc.get("acceptance_criteria", []) if "id" in c}

    for idx, result in enumerate(doc.get("acceptance_criteria_results", [])):
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
    return errors
