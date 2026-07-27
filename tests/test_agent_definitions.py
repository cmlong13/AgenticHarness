"""Static and evidence-based checks on the .claude/agents/*.md subagent definitions.

Frontmatter is parsed with the standard library only (no YAML dependency) -- the
frontmatter used by these agent files is a flat "key: value" block delimited by
"---" lines, which a simple line-oriented parser handles without ambiguity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.evidence import (
    load_json,
    validate_against_schema,
    validate_implementation_report_semantics,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
RUN_DIR = REPO_ROOT / "runs" / "engineer-boundary-test"

FORBIDDEN_TOOLS = {"Bash", "PowerShell", "Agent", "NotebookEdit", "WebFetch", "WebSearch"}


def parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse a "---"-delimited "key: value" frontmatter block, stdlib only.

    Returns a dict of the top-level scalar fields (name, tools, model). The
    multi-line "description" field is intentionally not reconstructed here --
    nothing in this test suite needs its exact wrapped text.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", f"{path} must start with a '---' frontmatter delimiter"

    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    assert end is not None, f"{path} frontmatter has no closing '---' delimiter"

    fields: dict[str, str] = {}
    current_key: str | None = None
    for line in lines[1:end]:
        if line.startswith((" ", "\t")):
            # Continuation of a wrapped multi-line value (e.g. description) --
            # not parsed into `fields`, just skipped, since no field consumed
            # by this test suite wraps across lines.
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current_key = key.strip()
            fields[current_key] = value.strip()
    return fields


def resolved_tools(fields: dict[str, str]) -> set[str]:
    raw = fields.get("tools", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


class TestEngineerFrontmatter:
    def setup_method(self) -> None:
        self.path = AGENTS_DIR / "engineer.md"
        assert self.path.exists(), "engineer.md must exist"
        self.fields = parse_frontmatter(self.path)

    def test_name_is_engineer(self) -> None:
        assert self.fields.get("name") == "engineer"

    def test_model_is_inherit(self) -> None:
        assert self.fields.get("model") == "inherit"

    def test_exact_tool_allowlist(self) -> None:
        assert resolved_tools(self.fields) == {"Read", "Grep", "Glob", "Edit", "Write"}

    def test_forbidden_tools_absent(self) -> None:
        assert resolved_tools(self.fields).isdisjoint(FORBIDDEN_TOOLS)


class TestArchitectFrontmatter:
    """Regression guard -- the Architect's boundary must not drift either."""

    def setup_method(self) -> None:
        self.path = AGENTS_DIR / "architect.md"
        assert self.path.exists(), "architect.md must exist"
        self.fields = parse_frontmatter(self.path)

    def test_exact_tool_allowlist(self) -> None:
        assert resolved_tools(self.fields) == {"Read", "Grep", "Glob"}

    def test_forbidden_tools_absent(self) -> None:
        forbidden = FORBIDDEN_TOOLS | {"Edit", "Write"}
        assert resolved_tools(self.fields).isdisjoint(forbidden)


class TestSavedBoundaryTestReports:
    """The reports the Engineer actually produced during its live boundary
    verification (runs/engineer-boundary-test/) must validate for real."""

    def _validate(self, filename: str, findings_doc: dict) -> tuple[list, list]:
        schema = load_json(REPO_ROOT / "harness" / "schemas" / "implementation-report.schema.json")
        doc = load_json(RUN_DIR / filename)
        schema_errors = validate_against_schema(doc, schema, artifact=filename)
        semantic_errors = validate_implementation_report_semantics(doc, findings_doc, artifact=filename)
        return schema_errors, semantic_errors

    def test_ready_for_verification_report_is_valid(self) -> None:
        findings_doc = load_json(RUN_DIR / "fixture-repo" / "findings.json")
        schema_errors, semantic_errors = self._validate("implementation-report.ready.json", findings_doc)
        assert schema_errors == []
        assert semantic_errors == []

    @pytest.mark.parametrize(
        "filename",
        [
            "implementation-report.blocked-missing-attestation.json",
            "implementation-report.blocked-false-attestation.json",
            "implementation-report.blocked-out-of-scope.json",
            "implementation-report.blocked-protected-path.json",
        ],
    )
    def test_blocked_report_is_valid(self, filename: str) -> None:
        schema_errors, semantic_errors = self._validate(filename, findings_doc={})
        assert schema_errors == []
        assert semantic_errors == []

    @pytest.mark.parametrize(
        "filename",
        [
            "implementation-report.blocked-missing-attestation.json",
            "implementation-report.blocked-false-attestation.json",
            "implementation-report.blocked-out-of-scope.json",
            "implementation-report.blocked-protected-path.json",
        ],
    )
    def test_blocked_report_has_no_implementation_only_fields(self, filename: str) -> None:
        doc = load_json(RUN_DIR / filename)
        assert doc["status"] == "blocked"
        for field in (
            "findings_ref",
            "minimal_change_rung",
            "minimal_change_rationale",
            "changed_files",
            "commands",
            "test_first_evidence",
            "tests",
        ):
            assert field not in doc, f"{filename} should not carry implementation-only field {field!r}"

    def test_ready_report_has_no_protocol_envelope_keys(self) -> None:
        doc = load_json(RUN_DIR / "implementation-report.ready.json")
        for field in ("response_type", "message_type", "changed_files_draft", "caller_action_required", "partial_evidence"):
            assert field not in doc, f"final report leaked protocol-envelope field {field!r}"
