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
    validate_verification_report_semantics,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
RUN_DIR = REPO_ROOT / "runs" / "engineer-boundary-test"
QE_RUN_DIR = REPO_ROOT / "runs" / "quality-engineer-boundary-test"

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


class TestEngineerCommandGrammar:
    """The Engineer's instructions must document test-runner's exact accepted command
    grammar so a live Engineer requests a conformant command on its first attempt
    (regression guard for the failure observed live in run-20260804-riskband-001, where
    the Engineer requested bare 'pytest ...' and the wrapper rejected it)."""

    def setup_method(self) -> None:
        self.path = AGENTS_DIR / "engineer.md"
        self.text = self.path.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_command_grammar_section_present(self) -> None:
        assert "Command grammar" in self.text

    def test_exact_accepted_form_documented(self) -> None:
        assert "python -m pytest <targets-and-options>" in self.text

    def test_bare_pytest_prohibited(self) -> None:
        assert "Never** request bare `pytest" in self.text or "never request bare `pytest" in self.flat_text.lower()
        assert "pytest tests/test_x.py -v" in self.text or "not an accepted executable" in self.flat_text

    def test_environment_activation_prohibited(self) -> None:
        assert "environment-activation command" in self.flat_text
        assert "activate" in self.flat_text.lower()

    def test_shell_chaining_prohibited(self) -> None:
        assert "shell chaining" in self.flat_text
        for operator in ("&&", "||", ";", "|"):
            assert operator in self.text

    def test_redirection_prohibited(self) -> None:
        assert "redirection" in self.flat_text
        assert ">" in self.text and "2>&1" in self.text

    def test_steps_seven_and_nine_reference_the_grammar(self) -> None:
        assert "exact form from \"Command grammar\" above" in self.flat_text

    def test_caller_forbidden_from_rewriting_commands(self) -> None:
        assert "byte-for-byte as you" in self.flat_text
        assert "never widening, correcting, retrying with rewritten syntax" in self.flat_text


# The Architect's docs-connector tools (ASSIGNMENT.md §2.2, MCP + REST milestone) --
# the narrow, read-only Obsidian vault reader, and nothing else.
ARCHITECT_DOCS_CONNECTOR_TOOLS = {"mcp__obsidian__search_notes", "mcp__obsidian__read_note"}


class TestArchitectFrontmatter:
    """Regression guard -- the Architect's boundary must not drift. After the MCP + REST
    milestone the allowlist is Read/Grep/Glob PLUS exactly the two read-only Obsidian
    docs-connector tools -- ASSIGNMENT.md §2.2's "Read/grep/glob + your docs connectors"
    -- and nothing else."""

    def setup_method(self) -> None:
        self.path = AGENTS_DIR / "architect.md"
        assert self.path.exists(), "architect.md must exist"
        self.fields = parse_frontmatter(self.path)

    def test_exact_tool_allowlist(self) -> None:
        assert resolved_tools(self.fields) == {"Read", "Grep", "Glob"} | ARCHITECT_DOCS_CONNECTOR_TOOLS

    def test_forbidden_tools_absent(self) -> None:
        forbidden = FORBIDDEN_TOOLS | {"Edit", "Write", "Skill"}
        assert resolved_tools(self.fields).isdisjoint(forbidden)

    def test_only_the_obsidian_docs_connector_mcp_tools_are_granted(self) -> None:
        mcp_tools = {t for t in resolved_tools(self.fields) if t.startswith("mcp__")}
        assert mcp_tools == ARCHITECT_DOCS_CONNECTOR_TOOLS
        # No wildcard grant, no jira MCP tool, no unrelated server.
        assert "mcp__jira__get_issue" not in resolved_tools(self.fields)
        assert not any(t == "mcp" or t.endswith("__*") or t == "mcp__*" for t in resolved_tools(self.fields))

    def test_docs_connector_tools_are_read_only_by_name(self) -> None:
        for tool in ARCHITECT_DOCS_CONNECTOR_TOOLS:
            assert any(verb in tool for verb in ("search", "read"))
            assert not any(verb in tool for verb in ("write", "create", "update", "delete", "publish", "edit"))


class TestArchitectObsidianHistoricalEvidence:
    """The Architect's Obsidian handling: whether the vault context was injected by the
    orchestrator or fetched by the Architect's own read-only docs connector, it is
    historical/contextual evidence, never current repository truth, and it grants no
    authority."""

    def setup_method(self) -> None:
        self.text = (AGENTS_DIR / "architect.md").read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_section_present(self) -> None:
        assert "# Obsidian historical evidence" in self.text

    def test_docs_connector_section_present(self) -> None:
        assert "# Obsidian docs connector" in self.text

    def test_vault_note_is_never_current_truth_alone(self) -> None:
        assert "historical/contextual evidence, never current repository truth" in self.flat_text
        assert "confirm it against the current repository" in self.flat_text
        assert "metadata` tier" in self.flat_text

    def test_docs_connector_is_read_only_and_bounded(self) -> None:
        assert "two vault tools, both read-only" in self.flat_text
        assert "mcp__obsidian__search_notes" in self.flat_text
        assert "mcp__obsidian__read_note" in self.flat_text
        assert "cannot create, update, delete, rename, or move a vault note" in self.flat_text

    def test_connector_adds_no_authority_and_identity_stays_orchestrators(self) -> None:
        assert "The connector adds no authority" in self.flat_text
        assert "Run identity stays the orchestrator's" in self.flat_text
        assert "Echo what you used" in self.flat_text

    def test_vault_note_never_expands_scope_or_becomes_acceptance_criteria(self) -> None:
        assert "not** acceptance criteria" in self.flat_text
        assert "does not expand or narrow scope" in self.flat_text

    def test_records_disagreement_in_repositorys_favour(self) -> None:
        assert "Never resolve the disagreement in the vault's favour" in self.flat_text
        assert "corroborated / stale / contradicted / context-only" in self.flat_text


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


class TestQualityEngineerFrontmatter:
    def setup_method(self) -> None:
        self.path = AGENTS_DIR / "quality-engineer.md"
        assert self.path.exists(), "quality-engineer.md must exist"
        self.fields = parse_frontmatter(self.path)

    def test_name_is_quality_engineer(self) -> None:
        assert self.fields.get("name") == "quality-engineer"

    def test_model_is_inherit(self) -> None:
        assert self.fields.get("model") == "inherit"

    def test_exact_tool_allowlist(self) -> None:
        assert resolved_tools(self.fields) == {"Read", "Grep", "Glob"}

    def test_forbidden_tools_absent(self) -> None:
        forbidden = FORBIDDEN_TOOLS | {"Edit", "Write"}
        assert resolved_tools(self.fields).isdisjoint(forbidden)


class TestSavedQualityEngineerBoundaryTestReports:
    """The reports the Quality Engineer actually produced during its live boundary
    verification (runs/quality-engineer-boundary-test/) must validate for real."""

    def _validate(self, filename: str, scope_doc: dict) -> tuple[list, list]:
        schema = load_json(REPO_ROOT / "harness" / "schemas" / "verification-report.schema.json")
        doc = load_json(QE_RUN_DIR / filename)
        schema_errors = validate_against_schema(doc, schema, artifact=filename)
        semantic_errors = validate_verification_report_semantics(doc, scope_doc, artifact=filename)
        return schema_errors, semantic_errors

    @pytest.mark.parametrize(
        "filename",
        [
            "verification-report.blocked-missing-attestation.json",
            "verification-report.blocked-false-attestation.json",
            "verification-report.blocked-findings-ref-mismatch.json",
            "verification-report.blocked-implementation-input.json",
            "verification-report.blocked-protected-path.json",
        ],
    )
    def test_blocked_report_is_valid(self, filename: str) -> None:
        schema_errors, semantic_errors = self._validate(filename, scope_doc={})
        assert schema_errors == []
        assert semantic_errors == []

    @pytest.mark.parametrize(
        "filename",
        [
            "verification-report.blocked-missing-attestation.json",
            "verification-report.blocked-false-attestation.json",
            "verification-report.blocked-findings-ref-mismatch.json",
            "verification-report.blocked-implementation-input.json",
            "verification-report.blocked-protected-path.json",
        ],
    )
    def test_blocked_report_has_no_verification_only_fields(self, filename: str) -> None:
        doc = load_json(QE_RUN_DIR / filename)
        assert doc["final_verdict"] == "blocked"
        for field in ("attempts", "acceptance_criteria_results", "retry_policy", "routed_back_to_engineer"):
            assert field not in doc, f"{filename} should not carry verification-only field {field!r}"

    def test_pass_report_is_valid(self) -> None:
        scope_doc = load_json(RUN_DIR / "fixture-repo" / "scope.json")
        schema_errors, semantic_errors = self._validate("verification-report.ready.pass.json", scope_doc)
        assert schema_errors == []
        assert semantic_errors == []

    def test_command_safety_report_is_valid(self) -> None:
        scope_doc = load_json(RUN_DIR / "fixture-repo" / "scope.json")
        schema_errors, semantic_errors = self._validate(
            "verification-report.command-safety-refused-unsafe.json", scope_doc
        )
        assert schema_errors == []
        assert semantic_errors == []

    def test_command_safety_report_requested_no_unsafe_operators(self) -> None:
        doc = load_json(QE_RUN_DIR / "verification-report.command-safety-refused-unsafe.json")
        forbidden = (">", ";", "&&", "||", "`", "$(")
        for attempt in doc["attempts"]:
            command = attempt["command"]
            for token in forbidden:
                assert token not in command, f"attempt {attempt['id']} command {command!r} contains forbidden token {token!r}"

    def test_false_claim_report_is_valid(self) -> None:
        scope_doc = load_json(QE_RUN_DIR / "fixture-repo-2" / "scope.json")
        schema_errors, semantic_errors = self._validate("verification-report.fail-false-claim-caught.json", scope_doc)
        assert schema_errors == []
        assert semantic_errors == []

    def test_false_claim_report_caught_the_lie(self) -> None:
        """The implementation report claimed exit_code 0 for the post-implementation
        command; the Quality Engineer's real re-execution must have gotten exit_code 1
        and must not have simply trusted the claimed value."""
        impl_doc = load_json(QE_RUN_DIR / "implementation-report.false-claim.json")
        claimed_exit_code = next(c["exit_code"] for c in impl_doc["commands"] if c["stage"] == "post_implementation")
        assert claimed_exit_code == 0, "fixture precondition: the implementation report must falsely claim success"

        report = load_json(QE_RUN_DIR / "verification-report.fail-false-claim-caught.json")
        assert report["final_verdict"] == "fail"
        assert report["routed_back_to_engineer"]["routed"] is True
        assert any(a["classification"] == "logic_bug" for a in report["attempts"])
        assert any(a["exit_code"] != 0 for a in report["attempts"]), (
            "the Quality Engineer's real attempt must show a nonzero exit code, "
            "contradicting the implementation report's false claim"
        )

    def test_no_final_report_leaks_protocol_envelope_keys(self) -> None:
        for filename in QE_RUN_DIR.glob("verification-report.*.json"):
            doc = load_json(filename)
            for field in ("response_type", "message_type", "requested_command", "rejected_reply"):
                assert field not in doc, f"{filename.name} leaked protocol-envelope field {field!r}"

    def test_infrastructure_flake_retry_report_is_valid(self) -> None:
        scope_doc = load_json(QE_RUN_DIR / "fixture-repo-flake" / "scope.json")
        schema_errors, semantic_errors = self._validate(
            "verification-report.infrastructure-flake-retry-pass.json", scope_doc
        )
        assert schema_errors == []
        assert semantic_errors == []

    def test_infrastructure_flake_retry_has_multiple_attempts_correctly_flagged(self) -> None:
        doc = load_json(QE_RUN_DIR / "verification-report.infrastructure-flake-retry-pass.json")
        attempts = doc["attempts"]
        assert len(attempts) >= 2, "a genuine retry must leave at least two recorded attempts"

        # Only later attempts (the retries themselves) may be marked retried=true -- the first
        # attempt targeting a given command is never itself "a retry".
        assert attempts[0]["retried"] is False, "the first attempt must not be marked retried"
        assert any(a["retried"] is True for a in attempts[1:]), "at least one later attempt must be marked retried"

        assert attempts[0]["classification"] == "infrastructure_flake"
        assert attempts[0]["exit_code"] != 0
        assert attempts[-1]["classification"] == "pass"
        assert attempts[-1]["exit_code"] == 0

        retry_count = sum(1 for a in attempts if a["retried"] is True)
        max_retries = doc["retry_policy"]["max_retries"]
        assert retry_count <= max_retries, f"retry count {retry_count} exceeds max_retries {max_retries}"

        assert doc["final_verdict"] == "pass"
        assert doc["routed_back_to_engineer"]["routed"] is False

    def test_infrastructure_flake_retry_used_same_command_both_times(self) -> None:
        """A retry must re-request the identical narrow command, not a different one."""
        doc = load_json(QE_RUN_DIR / "verification-report.infrastructure-flake-retry-pass.json")
        commands = {a["command"] for a in doc["attempts"]}
        assert commands == {"pytest test_connector.py"}, "the retry must use the exact same command as the original attempt"

    def test_environment_report_is_valid(self) -> None:
        scope_doc = load_json(QE_RUN_DIR / "fixture-repo-environment" / "scope.json")
        schema_errors, semantic_errors = self._validate("verification-report.environment-inconclusive.json", scope_doc)
        assert schema_errors == []
        assert semantic_errors == []

    def test_environment_attempt_was_not_retried(self) -> None:
        doc = load_json(QE_RUN_DIR / "verification-report.environment-inconclusive.json")
        attempts = doc["attempts"]
        assert len(attempts) == 1, "an environment failure must never be retried, so exactly one attempt is expected"
        assert attempts[0]["classification"] == "environment"
        assert attempts[0]["retried"] is False

    def test_environment_report_was_not_routed_back_as_logic_bug(self) -> None:
        doc = load_json(QE_RUN_DIR / "verification-report.environment-inconclusive.json")
        assert not any(a["classification"] == "logic_bug" for a in doc["attempts"])
        assert doc["routed_back_to_engineer"]["routed"] is False
        assert doc["final_verdict"] in ("blocked", "inconclusive")

    def test_flake_and_environment_fixture_source_files_are_untouched_by_quality_engineer(self) -> None:
        """The Quality Engineer has no Edit/Write tool at all, so this is a sanity check on the
        retained fixture content, not a live re-verification -- real enforcement is tool absence."""
        connector = (QE_RUN_DIR / "fixture-repo-flake" / "fixture-src" / "connector.py").read_text(encoding="utf-8")
        assert "def attempt_connection" in connector
        reporter = (QE_RUN_DIR / "fixture-repo-environment" / "fixture-src" / "reporter.py").read_text(encoding="utf-8")
        assert "import qe_fixture_nonexistent_external_dependency" in reporter
