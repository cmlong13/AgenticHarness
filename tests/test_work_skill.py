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


class TestQualityEngineerTransportRepair:
    """Formalizes the verification-phase transport-repair behavior exercised live in
    run-20260804-riskband-003 (a Quality Engineer envelope wrapped in a Markdown fence),
    matching the Architect/Engineer transport-repair policies but scoped to the Quality
    Engineer's staged protocol, as its own explicit protocol rather than an informal
    "same shape as Phase 3" analogy."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_transport_repair_section_present(self) -> None:
        assert "Quality Engineer transport repair" in self.text

    def test_phase4_references_dedicated_protocol_not_just_analogy(self) -> None:
        phase4_idx = self.text.index("# Phase 4")
        repair_idx = self.text.index("## Quality Engineer transport repair")
        assert phase4_idx < repair_idx
        phase4_flat = " ".join(self.text[phase4_idx:repair_idx].split())
        assert "Quality Engineer transport repair" in phase4_flat
        assert "own explicit, authoritative protocol" in phase4_flat.lower()

    def test_malformed_output_retained_verbatim_not_locally_fixed(self) -> None:
        # The Quality Engineer transport-repair section reuses the same "never locally
        # strip fences" language already asserted for Architect/Engineer -- this test
        # only pins that the *section itself* still carries the instruction nearby.
        repair_idx = self.text.index("## Quality Engineer transport repair")
        section = self.text[repair_idx:]
        assert "Never locally strip Markdown fences" in section

    def test_transport_failure_policy_event_recorded_for_verification_phase(self) -> None:
        assert '`kind: "transport_parse_failure"`, `phase: "verification"`' in self.text or (
            "transport_parse_failure" in self.text and 'phase: "verification"' in self.text
        )

    def test_correction_targets_same_qe_agent_id_via_send_message(self) -> None:
        assert "same Quality Engineer agent id" in self.flat_text
        assert "SendMessage" in self.text
        assert "never a new `Agent` call" in self.flat_text or "never a new Agent call" in self.flat_text

    def test_correction_message_terms_documented(self) -> None:
        for phrase in [
            "do not request a different command, criterion, or classification",
            "do not change `final_verdict`, `acceptance_criteria_results`, `attempts`",
            "return the exact same envelope content as raw JSON",
            "no Markdown fence",
            "no leading or trailing prose",
        ]:
            assert phrase in self.text, f"expected correction-message term {phrase!r} in SKILL.md"

    def test_no_redo_and_no_further_tool_call_permitted_during_correction(self) -> None:
        repair_idx = self.text.index("## Quality Engineer transport repair")
        section_intro = self.text[repair_idx:repair_idx + 1200]
        assert "never asks the Quality Engineer to redo verification" in " ".join(section_intro.split())
        assert "Edit`/`Write`/" in section_intro or "edit`/`write`/" in section_intro.lower()

    def test_wait_for_same_agent_completion_before_proceeding(self) -> None:
        repair_idx = self.text.index("## Quality Engineer transport repair")
        section = self.text[repair_idx:]
        assert "Wait for the completion notification from that same agent id" in " ".join(section.split())

    def test_exactly_one_correction_attempt_permitted_per_phase_not_per_turn(self) -> None:
        assert "exactly one transport-only correction attempt is permitted per verification phase" in self.flat_text.lower()
        assert "not one per turn" in self.flat_text
        repair_idx = self.text.index("## Quality Engineer transport repair")
        section_flat = " ".join(self.text[repair_idx:].split())
        assert "do **not** send a second correction request" in section_flat

    def test_still_malformed_or_invalid_after_correction_blocks_verification(self) -> None:
        repair_idx = self.text.index("## Quality Engineer transport repair")
        section = self.text[repair_idx:]
        assert '"verification_blocked"' in section
        assert "transport_repair_exhausted" in section

    def test_replacement_qe_fallback_forbidden(self) -> None:
        assert "Never spawn a replacement Quality Engineer" in self.flat_text
        repair_idx = self.text.index("## Quality Engineer transport repair")
        assert "fabricates continuity" in self.text[repair_idx:].lower()

    def test_content_failures_excluded_and_routed_to_rejected_reply_mechanism(self) -> None:
        repair_idx = self.text.index("## Quality Engineer transport repair")
        section = self.text[repair_idx:].lower()
        assert "content failure" in section
        assert "rejected_reply" in section
        assert "not eligible for this repair" in section

    def test_budget_shared_across_attempt_requested_and_final_report(self) -> None:
        repair_idx = self.text.index("## Quality Engineer transport repair")
        intro = " ".join(self.text[repair_idx:repair_idx + 600].split())
        assert "attempt_requested" in intro
        assert "final report" in intro.lower()


class TestTestRunnerForkedMediation:
    """Covers the fork-isolation repair adopted after run-20260804-riskband-002: the
    test-runner Skill now runs in an isolated forked context (context: fork,
    background: false) instead of inline, so its disallowed-tools restriction no
    longer leaks into the invoking turn or a resumed Engineer's own tool availability."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_test_runner_skill_mediation_required_not_direct_wrapper(self) -> None:
        assert "real `test-runner` Skill" in self.text
        assert "run_command.py" in self.text
        assert "never fall back to a direct `bash` call" in self.flat_text.lower()

    def test_forked_mediation_section_present(self) -> None:
        assert "Test-runner Skill mediation: forked isolation" in self.text

    def test_fork_frontmatter_referenced(self) -> None:
        assert "context: fork" in self.text
        assert "background: false" in self.text

    def test_inline_turn_wide_denial_no_longer_described_as_normal_path(self) -> None:
        # The old "avoid Write/Edit for the rest of the turn" workaround must no longer
        # be presented as the intended path for live Implementation.
        assert "is not the normal path for live implementation" in self.flat_text.lower()
        assert "do not reintroduce workarounds" in self.flat_text.lower()

    def test_waits_for_forked_result_before_continuing(self) -> None:
        assert "wait for its real result in this same turn" in self.flat_text.lower() or (
            "wait for" in self.flat_text.lower() and "before proceeding" in self.flat_text.lower()
        )
        assert "never proceed as if a result arrived when none has" in self.flat_text.lower()

    def test_fork_failure_or_malformed_result_blocks_phase(self) -> None:
        assert "test_runner_fork_failure" in self.text
        assert "block the phase" in self.flat_text.lower()
        assert "never fabricate a" in self.flat_text.lower()

    def test_no_direct_wrapper_fallback_on_fork_failure(self) -> None:
        assert "never fall back to a direct `bash` call" in self.flat_text.lower()

    def test_same_engineer_resumed_after_valid_pretest_result(self) -> None:
        assert "same engineer agent id" in self.flat_text.lower()
        assert "resume" in self.flat_text.lower()
        assert "edit`/`write` grant" in self.flat_text.lower() or "edit/write grant" in self.flat_text.lower()

    def test_bash_forbidden_from_editing_demo_repo_source(self) -> None:
        assert "must **never** be used to edit application source" in self.flat_text

    def test_only_engineer_may_modify_demo_repo(self) -> None:
        assert "Only the **Engineer** subagent" in self.flat_text

    def test_retains_blocked_run_as_evidence_for_the_fork(self) -> None:
        # Required: the blocked live run stays retained and cited as the reason the
        # fork exists, not silently dropped once the repair lands.
        assert "run-20260804-riskband-002" in self.text
        assert "retained evidence" in self.flat_text.lower()
        assert "not to be re-derived, edited, or deleted" in self.flat_text.lower()


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
