"""Discovery gate: validates a proposed scope document before it is trusted.

Discovery itself -- turning a free-form prompt into scope content -- is
supplied by a DiscoveryAdapter (see adapters.py). This module never invents
or reasons about scope content; it only checks a candidate against
harness/schemas/scope.schema.json, harness/evidence.py's
validate_scope_semantics(), and real path safety. It performs no filesystem
writes of its own outside of what the caller does with its return value, so
it cannot mutate the target repository.
"""
from __future__ import annotations

from pathlib import Path

from harness.evidence import load_json, validate_against_schema, validate_scope_semantics

from . import paths

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "harness" / "schemas" / "scope.schema.json"


def validate_scope_draft(
    draft: dict, *, expected_task_id: str, expected_run_id: str, target_repo_path: str, repo_root: Path
) -> list[str]:
    if not isinstance(draft, dict):
        return ["scope draft is not a JSON object"]

    schema = load_json(SCHEMA_PATH)
    schema_errors = validate_against_schema(draft, schema, artifact="scope")
    if schema_errors:
        # A shape that doesn't even conform to the schema can't be trusted enough to run
        # semantic or path-safety checks against -- report schema errors only.
        return [str(e) for e in schema_errors]

    errors: list[str] = [str(e) for e in validate_scope_semantics(draft)]

    if draft.get("task_id") != expected_task_id or draft.get("run_id") != expected_run_id:
        errors.append("scope draft task_id/run_id does not match the run identity")

    if draft.get("status") == "approved":
        try:
            paths.validate_target_repo_path(target_repo_path, repo_root=repo_root)
        except paths.PathSafetyError as exc:
            errors.append(f"target_repo_path invalid: {exc.message}")
        for entry in draft.get("in_scope", []):
            try:
                paths.validate_in_scope_path(entry, target_repo_path)
            except paths.PathSafetyError as exc:
                errors.append(f"in_scope entry {entry!r} invalid: {exc.message}")

    return errors
