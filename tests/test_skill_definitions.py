"""Static structural checks on the .claude/skills/*/SKILL.md skill definitions.

Frontmatter is parsed with the standard library only, mirroring the flat "key: value"
parser already used by tests/test_agent_definitions.py for the subagent .md files.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

PROTECTED_PATH_ENTRIES = [
    ".claude/**",
    ".git/**",
    "PROJECT_SPEC.md",
    "harness/schemas/**",
    "harness/artifacts/examples/**",
    "harness/evidence.py",
    "runs/**/scope.json",
    "runs/**/findings.json",
    "runs/**/verification-report.json",
]


def parse_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", f"{path} must start with a '---' frontmatter delimiter"
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    assert end is not None, f"{path} frontmatter has no closing '---' delimiter"

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if line.startswith((" ", "\t")):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


class TestCodeCraftsmanshipSkill:
    def setup_method(self) -> None:
        self.path = SKILLS_DIR / "code-craftsmanship" / "SKILL.md"
        assert self.path.exists(), "code-craftsmanship/SKILL.md must exist"
        self.text = self.path.read_text(encoding="utf-8")
        # Whitespace-normalized so a phrase check doesn't break just because the
        # source markdown happens to wrap a line in the middle of the phrase.
        self.flat_text = " ".join(self.text.split())
        self.fields = parse_frontmatter(self.path)

    def test_name(self) -> None:
        assert self.fields.get("name") == "code-craftsmanship"

    def test_no_tools_frontmatter_field(self) -> None:
        # A skill has no independent tool allowlist -- it must not declare "tools:".
        assert "tools" not in self.fields

    def test_no_allowed_or_disallowed_tools_field(self) -> None:
        # Unlike test-runner, this skill has no bundled script to gate -- it needs
        # neither allowed-tools nor disallowed-tools.
        assert "allowed-tools" not in self.fields
        assert "disallowed-tools" not in self.fields

    def test_checklist_items_present(self) -> None:
        for phrase in [
            "read existing code/tests",
            "Behavior preservation",
            "Smallest rung",
            "Extend, don't replace",
            "speculative abstraction",
            "Do any public interfaces change unless scope explicitly requires it?",
            "Safety preserved",
            "narrowest",
            "drive-by cleanup",
            "Justification",
            "in_scope",
            "Protected Paths",
        ]:
            assert phrase in self.flat_text, f"expected checklist phrase {phrase!r} in SKILL.md"

    def test_exact_protected_path_list_present(self) -> None:
        for entry in PROTECTED_PATH_ENTRIES:
            assert entry in self.text, f"Protected Path entry {entry!r} missing from SKILL.md"
        assert "target_repo_path" in self.text

    def test_boundaries_present(self) -> None:
        for phrase in [
            "must not",
            "Perform Discovery",
            "claim the task is done",
            "Expand the task",
            "Engineer's final",
        ]:
            assert phrase in self.flat_text, f"expected boundary phrase {phrase!r} in SKILL.md"


class TestTestRunnerSkill:
    def setup_method(self) -> None:
        self.path = SKILLS_DIR / "test-runner" / "SKILL.md"
        assert self.path.exists(), "test-runner/SKILL.md must exist"
        self.text = self.path.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())
        self.fields = parse_frontmatter(self.path)

    def test_name(self) -> None:
        assert self.fields.get("name") == "test-runner"

    def test_allowed_tools_pre_approves_only_the_wrapper(self) -> None:
        allowed = self.fields.get("allowed-tools", "")
        assert "run_command.py" in allowed
        assert "CLAUDE_SKILL_DIR" in allowed

    def test_disallowed_tools_removes_write_and_edit_but_keeps_bash(self) -> None:
        disallowed = {t.strip() for t in self.fields.get("disallowed-tools", "").split(",")}
        for tool in ("Write", "Edit", "NotebookEdit", "PowerShell", "Agent", "Skill", "WebFetch", "WebSearch"):
            assert tool in disallowed, f"{tool} should be in disallowed-tools"
        assert "Bash" not in disallowed

    def test_runs_in_a_forked_context(self) -> None:
        # Isolates this skill's disallowed-tools restriction to its own subagent
        # context instead of leaking into the invoking turn -- see
        # runs/run-20260804-riskband-002/ for the live evidence that motivated this.
        assert self.fields.get("context") == "fork"

    def test_forked_invocation_is_synchronous(self) -> None:
        # background: false (Claude Code v2.1.218+) makes the fork wait for its result
        # in the invoking turn instead of running detached -- the orchestrator still
        # needs the real command_result before it can resume the staged protocol.
        assert self.fields.get("background") == "false"

    def test_fork_frontmatter_documented_in_body(self) -> None:
        assert "context: fork" in self.text
        assert "background: false" in self.text
        assert "v2.1.218" in self.text

    def test_allowed_and_disallowed_tools_still_declared_alongside_fork(self) -> None:
        # The fork repairs *where* disallowed-tools applies, not whether it's declared
        # at all -- the strict wrapper-only allowlist and the Write/Edit/... denylist
        # must both still be present on the forked skill.
        assert self.fields.get("allowed-tools")
        assert self.fields.get("disallowed-tools")

    def test_does_not_prevent_bypass_disclaimer_present(self) -> None:
        assert "does not sandbox the caller" in self.flat_text

    def test_request_file_must_preexist_instruction_present(self) -> None:
        assert "this skill cannot create or modify its own request file" in self.flat_text

    def test_sentinel_exit_codes_documented(self) -> None:
        for code in ("124", "125", "126"):
            assert code in self.text

    def test_mutation_wording_does_not_claim_git_tracking(self) -> None:
        assert "not Git tracked-file status" in self.flat_text or "Git is never consulted" in self.flat_text

    def test_run_command_script_exists(self) -> None:
        assert (SKILLS_DIR / "test-runner" / "scripts" / "run_command.py").exists()


class TestGithubSkill:
    def setup_method(self) -> None:
        self.path = SKILLS_DIR / "github" / "SKILL.md"
        assert self.path.exists(), "github/SKILL.md must exist"
        self.text = self.path.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())
        self.fields = parse_frontmatter(self.path)

    def test_name(self) -> None:
        assert self.fields.get("name") == "github"

    def test_no_tools_frontmatter_field(self) -> None:
        # Like code-craftsmanship, this skill has no independent tool allowlist of its
        # own -- it is a procedure the orchestrator's own already-granted tools follow,
        # not a forked, tool-restricted script wrapper like test-runner.
        assert "tools" not in self.fields
        assert "allowed-tools" not in self.fields
        assert "disallowed-tools" not in self.fields
        assert "context" not in self.fields

    def test_cardinal_rule_present(self) -> None:
        for phrase in [
            "A local commit is not a verified push",
            "not independently sufficient",
            "is not evidence",
            "Remote verification is mandatory",
        ]:
            assert phrase in self.flat_text, f"expected cardinal-rule phrase {phrase!r} in SKILL.md"

    def test_required_coverage_areas_present(self) -> None:
        for heading in [
            "# 1. Repository identity",
            "# 2. Working-tree inspection",
            "# 3. Branch inspection",
            "# 4. Diff review",
            "# 5. Staging",
            "# 6. Commit creation",
            "# 7. Push",
            "# 8. Independent remote verification",
            "# 9. GitHub metadata via",
            "# 10. Failure and mismatch reporting",
        ]:
            assert heading in self.text, f"expected section {heading!r} in SKILL.md"

    def test_all_six_classifications_documented(self) -> None:
        for status in ("verified", "mismatch", "remote_ref_missing", "command_failed", "invalid_output", "wrong_repository"):
            assert status in self.text

    def test_gh_never_substitutes_for_ls_remote(self) -> None:
        assert "never a substitute for `git ls-remote`" in self.flat_text

    def test_safe_operations_present(self) -> None:
        for phrase in ["Never change Git config globally", "Never force-push", "rewrite history", "move `origin/"]:
            assert phrase in self.flat_text

    def test_boundaries_present(self) -> None:
        for phrase in ["must not", "Decide whether a commit or push is authorized", "Fabricate", "Duplicate the test-runner"]:
            assert phrase in self.flat_text

    def test_live_cli_operations_named(self) -> None:
        for op in ("git_repo_identity", "retain_commit_evidence", "retain_push_attempt", "verify_push", "gh_repo_metadata"):
            assert op in self.text


class TestJiraSkill:
    def setup_method(self) -> None:
        self.path = SKILLS_DIR / "jira" / "SKILL.md"
        assert self.path.exists(), "jira/SKILL.md must exist"
        self.text = self.path.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())
        self.fields = parse_frontmatter(self.path)

    def test_name(self) -> None:
        assert self.fields.get("name") == "jira"

    def test_no_tools_frontmatter_field(self) -> None:
        # Like github/code-craftsmanship, this skill has no independent tool allowlist
        # of its own -- it is a procedure the orchestrator's own already-granted tools
        # follow, not a forked, tool-restricted script wrapper like test-runner.
        assert "tools" not in self.fields
        assert "allowed-tools" not in self.fields
        assert "disallowed-tools" not in self.fields
        assert "context" not in self.fields

    def test_cardinal_rule_present(self) -> None:
        for phrase in [
            "A ticket ID is not a resolved ticket",
            "Never guess, invent, or auto-complete",
            "never silently accepted",
            "never treated as an empty or placeholder issue",
        ]:
            assert phrase in self.flat_text, f"expected cardinal-rule phrase {phrase!r} in SKILL.md"

    def test_required_coverage_areas_present(self) -> None:
        for heading in [
            "# 1. Issue-key shape validation",
            "# 2. Connector availability",
            "# 3. Real issue resolution",
            "# 4. Requested-key vs returned-key identity validation",
            "# 5. Acceptance-criteria extraction",
            "# 6. Retained evidence",
            "# 7. Failure classification",
            "# 8. What this skill does not do",
        ]:
            assert heading in self.text, f"expected section {heading!r} in SKILL.md"

    def test_all_seven_classifications_documented(self) -> None:
        for status in (
            "resolved", "not_found", "unauthorized", "connector_unavailable",
            "identity_mismatch", "invalid_issue_key", "invalid_response",
        ):
            assert status in self.text

    def test_no_speculative_write_support_documented(self) -> None:
        for phrase in [
            "No create-ticket, edit-ticket, status-transition, or comment-posting",
            "Never write, transition, comment on, or otherwise mutate a real Jira issue",
        ]:
            assert phrase in self.flat_text

    def test_credential_handling_documented(self) -> None:
        for phrase in ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "never retained in any evidence file"]:
            assert phrase in self.flat_text

    def test_boundaries_present(self) -> None:
        for phrase in ["must not", "Fabricate, soften, or infer a resolution result", "Duplicate the test-runner"]:
            assert phrase in self.flat_text

    def test_live_cli_operation_named(self) -> None:
        assert "resolve_jira_issue" in self.text


class TestObsidianSkill:
    def setup_method(self) -> None:
        self.path = SKILLS_DIR / "obsidian" / "SKILL.md"
        assert self.path.exists(), "obsidian/SKILL.md must exist"
        self.text = self.path.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())
        self.fields = parse_frontmatter(self.path)

    def test_name(self) -> None:
        assert self.fields.get("name") == "obsidian"

    def test_no_tools_frontmatter_field(self) -> None:
        # Like github/jira/code-craftsmanship: a procedure the orchestrator's own
        # already-granted tools follow, not a forked tool-restricted wrapper.
        assert "tools" not in self.fields
        assert "allowed-tools" not in self.fields
        assert "disallowed-tools" not in self.fields
        assert "context" not in self.fields

    def test_cardinal_rule_present(self) -> None:
        for phrase in [
            "A generated Markdown summary is not proof that the note was published to Obsidian",
            "not publication",
            "separate external-delivery claim",
            "never let an Obsidian delivery failure rewrite a valid pipeline/test result",
        ]:
            assert phrase in self.flat_text, f"expected cardinal-rule phrase {phrase!r} in SKILL.md"

    def test_required_coverage_areas_present(self) -> None:
        for heading in [
            "# 1. Destination resolution",
            "# 2. Path safety",
            "# 3. Note naming",
            "# 4. Summary structure",
            "# 5. Collision / update policy",
            "# 6. Connector boundary",
            "# 7. Retained evidence",
            "# 8. Failure classification",
            "# 9. Terminal-`/work` placement",
            "# 10. Relationship to the memory loop",
        ]:
            assert heading in self.text, f"expected section {heading!r} in SKILL.md"

    def test_all_six_classifications_documented(self) -> None:
        for status in (
            "published", "connector_unavailable", "invalid_destination",
            "destination_unavailable", "collision", "write_failed",
        ):
            assert status in self.text

    def test_only_published_is_affirmative(self) -> None:
        assert "Only `published` may ever be treated as publication success" in self.text

    def test_destination_from_environment_not_hardcoded(self) -> None:
        assert "OBSIDIAN_VAULT_PATH" in self.text
        assert "OBSIDIAN_SUMMARY_DIR" in self.text
        assert "read fresh from the real environment on every call" in self.flat_text
        # no user-specific absolute path baked into the skill
        assert "Obsidian Vault" not in self.text
        assert r"C:\Users" not in self.text

    def test_no_credentials_language(self) -> None:
        assert "No credential or secret is ever involved or retained" in self.flat_text

    def test_evidence_path_named(self) -> None:
        assert "runs/<run_id>/obsidian/summary-publication.json" in self.text

    def test_memory_loop_kept_separate(self) -> None:
        for phrase in [
            "different requirements",
            "Existing memory behavior is unchanged",
            "Neither is derived from the other",
        ]:
            assert phrase in self.flat_text

    def test_mcp_vs_rest_left_as_separate_milestone(self) -> None:
        assert "not** the MCP-vs-REST dual implementation" in self.flat_text
        assert "that requirement remains its own future milestone" in self.flat_text

    def test_live_cli_operation_named(self) -> None:
        assert "publish_run_summary" in self.text

    def test_boundaries_present(self) -> None:
        for phrase in [
            "Boundaries -- this skill must not",
            "Decide the run's pipeline verdict",
            "Fabricate, soften, or infer a publication result",
        ]:
            assert phrase in self.flat_text
