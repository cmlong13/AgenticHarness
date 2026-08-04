"""Static structural checks on `.claude/skills/work/SKILL.md`, the `/work` entry point.

Frontmatter is parsed with the same standard-library-only flat "key: value" parser
already used by tests/test_skill_definitions.py and tests/test_agent_definitions.py --
no PyYAML dependency in this repo.

These tests check for the *presence of required concepts*, not exact paragraph wording
-- SKILL.md's prose is free to be rephrased/reworded without breaking these tests, as
long as the underlying requirement (e.g. "exactly one Architect correction attempt") is
still actually expressed somewhere in the document.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "work" / "SKILL.md"


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


class TestWorkSkillExistsAndFrontmatter:
    def setup_method(self) -> None:
        assert SKILL_PATH.exists(), ".claude/skills/work/SKILL.md must exist"
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())
        self.fields = parse_frontmatter(SKILL_PATH)

    def test_skill_path_exists(self) -> None:
        assert SKILL_PATH.is_file()

    def test_frontmatter_has_closing_delimiter_and_parses(self) -> None:
        # parse_frontmatter itself asserts the '---'...'---' block exists; a nonempty
        # fields dict proves at least one key: value pair was actually parsed from it.
        assert self.fields

    def test_correct_skill_name(self) -> None:
        assert self.fields.get("name") == "work"

    def test_disable_model_invocation_true(self) -> None:
        assert self.fields.get("disable-model-invocation") == "true"

    def test_user_invocable_true(self) -> None:
        assert self.fields.get("user-invocable") == "true"

    def test_no_context_fork(self) -> None:
        # A forked context would prevent this skill from acting as the main-session
        # orchestrator (subagents can't spawn nested subagents) -- must be entirely
        # absent from frontmatter, not merely set to something other than "fork".
        assert "context" not in self.fields

    def test_arguments_placeholder_used(self) -> None:
        assert "$ARGUMENTS" in self.text


class TestWorkSkillBehavior:
    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_free_form_mode_supported(self) -> None:
        assert "free-form" in self.flat_text.lower()

    def test_dry_run_mode_supported(self) -> None:
        assert "--dry-run" in self.text
        assert "dry_run = true" in self.flat_text or "dry_run" in self.flat_text

    def test_jira_shaped_input_explicitly_unsupported(self) -> None:
        assert "Jira" in self.text
        assert "not implemented in this milestone" in self.flat_text
        # A recognizable ticket-id shaped pattern check must be documented, not just
        # a vague mention of Jira.
        assert re.search(r"\[A-Z\]\[A-Z0-9\]\+-\\d\+|PROJ-123", self.text)

    def test_no_automatic_commit_or_push(self) -> None:
        assert "Never commit or push" in self.flat_text

    def test_main_session_owns_discovery(self) -> None:
        assert "main session owns Discovery" in self.flat_text or (
            "main session" in self.flat_text and "Discovery" in self.flat_text and "no subagent" in self.flat_text.lower()
        )

    def test_raw_attempts_retained_before_validation(self) -> None:
        assert "retain_attempt" in self.text
        assert "before" in self.flat_text
        assert "never validate first and retain only on success" in self.flat_text

    def test_missing_canonical_evidence_blocks_success(self) -> None:
        assert "Missing evidence blocks success" in self.flat_text

    def test_same_agent_continuation_mandatory_for_engineer_and_qe(self) -> None:
        assert "same Engineer and Quality Engineer identities must be resumed" in self.flat_text

    def test_resumed_agent_completion_notifications_must_be_awaited(self) -> None:
        assert "wait for that same agent" in self.flat_text.lower() or "wait for the completion notification" in self.flat_text.lower()
        assert "do not act on a partial or absent result" in self.flat_text.lower()


class TestArchitectTransportRepair:
    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_transport_repair_section_present(self) -> None:
        assert "Architect transport repair" in self.text

    def test_malformed_output_retained_verbatim_not_locally_fixed(self) -> None:
        assert "Never locally strip Markdown fences" in self.flat_text or "never fence-strip it" in self.flat_text.lower()

    def test_transport_failure_policy_event_recorded(self) -> None:
        assert "transport_parse_failure" in self.text

    def test_correction_targets_same_agent_id_via_send_message(self) -> None:
        assert "same Architect agent id" in self.flat_text
        assert "SendMessage" in self.text
        assert "never a new `Agent` call" in self.flat_text or "never a new Agent call" in self.flat_text

    def test_correction_message_terms_documented(self) -> None:
        for phrase in [
            "do not redo research",
            "do not change findings",
            "return the same artifact as raw JSON",
            "no Markdown fence",
            "no leading or trailing prose",
        ]:
            assert phrase in self.flat_text, f"expected correction-message term {phrase!r} in SKILL.md"

    def test_wait_for_same_agent_completion_before_proceeding(self) -> None:
        assert "Wait for the completion notification from that same agent id" in self.flat_text

    def test_corrected_response_retained_as_next_research_attempt(self) -> None:
        assert 'attempt_n: 2' in self.flat_text

    def test_corrected_response_validated_normally(self) -> None:
        assert "This is normal validation -- no relaxed rules for a corrected" in self.flat_text

    def test_exactly_one_correction_attempt_permitted(self) -> None:
        assert "one and only" in self.flat_text.lower() or "at most one correction attempt" in self.flat_text.lower()
        assert "do **not** send a second correction request" in self.text or "do not send a second correction request" in self.flat_text.lower()

    def test_still_malformed_or_invalid_after_correction_blocks_research(self) -> None:
        assert "research_blocked" in self.text
        assert "transport_repair_exhausted" in self.text

    def test_replacement_architect_fallback_forbidden(self) -> None:
        assert "Never spawn a replacement Architect" in self.flat_text
        assert "fabricates continuity" in self.flat_text.lower() or "fabricate continuity" in self.flat_text.lower()


class TestOrchestratorNeverRewritesRequestedCommands:
    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_commands_submitted_byte_for_byte(self) -> None:
        assert "byte-for-byte" in self.flat_text

    def test_rewriting_explicitly_forbidden(self) -> None:
        assert "never widened, corrected, retried with rewritten syntax, or otherwise" in self.flat_text.lower()

    def test_command_rejected_halts_rather_than_gets_patched_around(self) -> None:
        assert "command_rejected` halts the staged protocol" in self.text or "halts the staged protocol" in self.flat_text


class TestEngineerTransportRepair:
    """Formalizes the implementation-phase transport-repair behavior exercised live in
    run-20260804-riskband-001 (an Engineer envelope wrapped in a Markdown fence), matching
    the Architect transport-repair policy but scoped to the Engineer's multi-turn staged
    protocol."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_transport_repair_section_present(self) -> None:
        assert "Engineer transport repair" in self.text

    def test_malformed_output_retained_verbatim_not_locally_fixed(self) -> None:
        assert "Never locally strip Markdown fences" in self.flat_text

    def test_transport_failure_policy_event_recorded_for_implementation_phase(self) -> None:
        assert '`kind: "transport_parse_failure"`, `phase: "implementation"`' in self.text or (
            "transport_parse_failure" in self.text and "phase: \"implementation\"" in self.text
        )

    def test_correction_targets_same_engineer_agent_id_via_send_message(self) -> None:
        assert "same Engineer agent id" in self.flat_text
        assert "SendMessage" in self.text
        assert "never a new `Agent` call" in self.flat_text or "never a new Agent call" in self.flat_text

    def test_correction_message_terms_documented(self) -> None:
        for phrase in [
            "do not make any further `Edit`/`Write` call",
            "do not change `changed_files`, `tests`, `minimal_change_rung`",
            "return the exact same envelope content as raw JSON",
            "no Markdown fence",
            "no leading or trailing prose",
        ]:
            assert phrase in self.text, f"expected correction-message term {phrase!r} in SKILL.md"

    def test_no_additional_code_edits_permitted_during_correction(self) -> None:
        assert "never asks the Engineer to redo work" in self.flat_text
        assert "any further `Edit`/`Write` call" in self.text

    def test_wait_for_same_agent_completion_before_proceeding(self) -> None:
        assert "Wait for the completion notification from that same agent id" in self.flat_text

    def test_exactly_one_correction_attempt_permitted_per_phase_not_per_turn(self) -> None:
        assert "exactly one transport-only correction attempt is permitted per implementation phase" in self.flat_text.lower()
        assert "not one per turn" in self.flat_text
        assert "do **not** send a second correction request" in self.text

    def test_still_malformed_or_invalid_after_correction_blocks_implementation(self) -> None:
        assert '"implementation_blocked"' in self.text
        assert "transport_repair_exhausted" in self.text

    def test_replacement_engineer_fallback_forbidden(self) -> None:
        assert "Never spawn a replacement Engineer" in self.flat_text
        assert "fabricates continuity" in self.flat_text.lower()

    def test_content_failures_excluded_and_routed_to_rejected_reply_mechanism(self) -> None:
        assert "content failure" in self.flat_text.lower()
        assert "rejected_reply" in self.text
        assert "not eligible for this repair" in self.flat_text.lower()


class TestTestRunnerMediationAndRestrictionLifecycle:
    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_test_runner_skill_mediation_required_not_direct_wrapper(self) -> None:
        assert "real `test-runner` Skill" in self.text
        assert "run_command.py" in self.text
        assert "never a direct `Bash` call" in self.flat_text.lower() or "never fall back to invoking" in self.flat_text.lower()

    def test_restriction_lifecycle_section_present(self) -> None:
        assert "Test-runner Skill restriction lifecycle" in self.text

    def test_restriction_scoped_to_current_user_turn(self) -> None:
        assert "current user turn" in self.flat_text.lower()

    def test_restriction_is_expected_behavior_not_a_defect(self) -> None:
        assert "expected turn-lifecycle behavior" in self.flat_text
        assert "not an unexplained defect" in self.flat_text

    def test_post_test_runner_evidence_ops_must_use_bash_bridge(self) -> None:
        assert "must not depend on `Write` or `Edit`" in self.flat_text
        assert "harness/orchestrator/live_cli.py" in self.text
        assert "Bash" in self.text

    def test_bash_forbidden_from_editing_demo_repo_source(self) -> None:
        assert "must **never** be used to edit application source" in self.text or "never be used to edit application source" in self.flat_text.lower()

    def test_only_engineer_may_modify_demo_repo(self) -> None:
        assert "only the **Engineer** subagent" in self.text or "only the Engineer subagent" in self.flat_text

    def test_test_execution_still_mediated_through_skill_after_restriction(self) -> None:
        # Guards against "restrictions are active, so fall back to the wrapper directly"
        # reasoning -- the Skill tool must remain the only path even under restriction.
        assert "Test execution must still always go through the real" in self.flat_text


class TestTddSourceChangeInspectionAndCompletionEvidence:
    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_tdd_orchestration_check_documented(self) -> None:
        assert "Mandatory TDD orchestration check" in self.flat_text

    def test_source_change_before_failing_test_blocks_phase(self) -> None:
        assert "No production source file changed yet" in self.flat_text
        assert "Engineer skipped TDD" in self.flat_text

    def test_canonical_evidence_and_independent_checks_required_for_completion(self) -> None:
        assert "Completion claims from any subagent are independently checked" in self.flat_text
        assert "Missing evidence blocks success" in self.flat_text
        assert "not evidence" in self.flat_text.lower()
