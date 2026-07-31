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
