"""Real, filesystem-anchored path safety for the orchestrator.

Distinct from harness/evidence.py's schema/semantic validation: this module
answers "is this path safe to touch or reference," never "is this document
shaped correctly." It is deliberately not merged with
.claude/skills/test-runner/scripts/run_command.py's own path-resolution
helpers -- that script anchors itself to a fixed relative depth from being
invoked in place by the Skill mechanism and is designed to run standalone
via subprocess, not to be imported as a library from a different call site.
A small amount of duplication here is a considered exception to reuse, not
an oversight.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

# harness/orchestrator/paths.py -> parents[0]=orchestrator, [1]=harness, [2]=repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

PROTECTED_PATH_PATTERNS = (
    ".claude/**",
    ".git/**",
    "PROJECT_SPEC.md",
    "harness/schemas/**",
    "harness/artifacts/examples/**",
    "harness/evidence.py",
    "runs/**/scope.json",
    "runs/**/findings.json",
    "runs/**/verification-report.json",
)


class PathSafetyError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


def normalize(path_str: str) -> str:
    return path_str.replace("\\", "/")


def _reject_if_absolute_or_traversal(value: str, field_name: str) -> str:
    if not value:
        raise PathSafetyError(f"{field_name} must not be empty", "path_empty")
    normalized = normalize(value)
    if normalized.startswith("/"):
        raise PathSafetyError(f"{field_name} must not be absolute", "path_absolute")
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        raise PathSafetyError(f"{field_name} must not be drive-prefixed", "path_absolute")
    if ".." in normalized.split("/"):
        raise PathSafetyError(f"{field_name} must not contain '..'", "path_traversal")
    return normalized


def is_protected(normalized_relpath: str) -> str | None:
    for pattern in PROTECTED_PATH_PATTERNS:
        if fnmatch.fnmatchcase(normalized_relpath, pattern):
            return pattern
    return None


def _is_contained(candidate: str, root: str) -> bool:
    return candidate == root or candidate.startswith(root + "/")


def validate_target_repo_path(target_repo_path: str, *, repo_root: Path | None = None) -> Path:
    """Real filesystem resolution + containment check, anchored at repo_root
    (defaults to this harness repository's own root). Path.resolve() follows
    symlinks/junctions to their real target, so a symlink-escape attempt is
    caught by the containment check below, not merely a lexical one."""
    root = (repo_root or REPO_ROOT).resolve(strict=True)
    normalized = _reject_if_absolute_or_traversal(target_repo_path, "target_repo_path")
    protected = is_protected(normalized)
    if protected:
        raise PathSafetyError(f"target_repo_path matches protected pattern {protected!r}", "protected_path")
    try:
        resolved = (root / normalized).resolve(strict=True)
    except OSError as exc:
        raise PathSafetyError(f"target_repo_path does not resolve: {exc}", "unresolvable") from exc
    if not (resolved == root or resolved.is_relative_to(root)):
        raise PathSafetyError(
            "target_repo_path resolves outside the configured project root (symlink/junction escape)",
            "path_escape",
        )
    if not resolved.is_dir():
        raise PathSafetyError("target_repo_path does not resolve to a directory", "not_a_directory")
    return resolved


def validate_in_scope_path(entry: str, target_repo_path: str) -> str:
    """Lexical containment + Protected Path check for a scope.json in_scope entry.
    No filesystem access -- the target file may not exist yet (e.g. a new file)."""
    normalized_entry = _reject_if_absolute_or_traversal(entry, "in_scope entry")
    normalized_root = normalize(target_repo_path)
    if not _is_contained(normalized_entry, normalized_root):
        raise PathSafetyError(
            f"{entry!r} is not contained beneath target_repo_path {target_repo_path!r}", "not_contained"
        )
    protected = is_protected(normalized_entry)
    if protected:
        raise PathSafetyError(f"{entry!r} matches protected pattern {protected!r}", "protected_path")
    return normalized_entry


def find_protected_violation(changed_files: list[dict]) -> str | None:
    """Returns the first changed_files[].path that matches a Protected Path pattern, or None.
    Redundant with the Quality Engineer's own Step 5 check by design -- the orchestrator does
    not trust an Engineer's `status: ready_for_verification` claim on faith either."""
    for entry in changed_files:
        path = entry.get("path", "")
        if is_protected(normalize(path)):
            return path
    return None


def build_path_validation_attestation(target_repo_path: str, *, repo_root: Path | None = None) -> dict:
    """Genuinely performs the real-filesystem + symlink-escape check it attests to, rather than
    asserting the claim without doing the work -- this is what makes the orchestrator's
    `path_validation` attestation to Engineer/Quality Engineer true, not just a trusted string."""
    validate_target_repo_path(target_repo_path, repo_root=repo_root)
    return {"validated_by": "controlled_caller", "symlink_escape_checked": True}
