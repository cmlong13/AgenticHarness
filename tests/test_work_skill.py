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


class TestCheckpointingAndResume:
    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_resume_is_the_one_and_only_syntax(self) -> None:
        assert "--resume <run_id>" in self.text
        assert "one and only resume syntax" in self.flat_text

    def test_resume_never_combined_with_dry_run_or_ticket_mode(self) -> None:
        assert "never combined with `--dry-run` or ticket mode" in self.flat_text

    def test_progress_checkpoint_written_after_each_of_three_phases(self) -> None:
        for phrase in (
            'completed_phases: ["discovery"]',
            'completed_phases: ["discovery", "research"]',
            'completed_phases: ["discovery", "research", "implementation"]',
        ):
            assert phrase in self.flat_text, phrase

    def test_no_checkpoint_for_discovery_refused_or_invalid(self) -> None:
        assert "No checkpoint at all" in self.flat_text or "without dispatching the Architect and **without** writing a checkpoint" in self.text

    def test_evaluate_resume_performs_full_precondition_pipeline(self) -> None:
        assert "evaluate_resume" in self.text
        for code in (
            "no_checkpoint", "invalid_checkpoint", "run_id_mismatch", "terminal_complete",
            "terminal_failed", "run_already_terminal", "target_repo_path_invalid",
            "missing_artifact", "invalid_artifact", "artifact_identity_mismatch",
        ):
            assert code in self.text, code

    def test_terminal_runs_never_restarted(self) -> None:
        assert "never restart it" in self.flat_text.lower()

    def test_completed_phases_reused_without_redispatch(self) -> None:
        assert "reused" in self.flat_text.lower()
        assert "not redispatched" in self.flat_text.lower() or "must not be redispatched" in self.flat_text.lower()
        assert "the Architect must not run again" in self.flat_text
        assert "the Engineer must not run again" in self.flat_text

    def test_restarted_phase_gets_a_fresh_agent_not_a_resumed_handle(self) -> None:
        assert "dispatch a brand-new agent instance" in self.flat_text
        assert "no live handle from whatever process wrote the checkpoint" in self.flat_text
        assert "never described as" in self.flat_text.lower()

    def test_existing_canonical_artifact_never_silently_overwritten_on_resume(self) -> None:
        assert "never silently overwrite existing evidence" in self.flat_text.lower()

    def test_run_summary_records_reused_and_restarted_phases(self) -> None:
        assert "phases_reused" in self.text
        assert "phases_restarted" in self.text

    def test_resume_policy_events_documented(self) -> None:
        for kind in (
            "resume_requested", "checkpoint_validated", "phase_reused", "phase_restarted",
            "resume_refused", "resume_completed", "checkpoint_written",
        ):
            assert kind in self.text, kind

    def test_reporting_states_checkpoint_path_and_resume_status(self) -> None:
        assert "checkpoint.json" in self.text
        assert "which phases were reused" in self.flat_text.lower() or "which single phase was restarted" in self.flat_text.lower()
        assert "not evidence" in self.flat_text.lower()


class TestMemoryLoop:
    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_canonical_order_stated(self) -> None:
        assert "memory load -> Discovery -> Research -> Implementation -> Verification -> terminal memory append" in self.flat_text

    def test_memory_loaded_before_discovery(self) -> None:
        assert "# Phase 0: Memory load" in self.text
        assert self.text.index("# Phase 0: Memory load") < self.text.index("# Phase 1: Discovery")

    def test_load_memory_operation_documented_and_retains_event(self) -> None:
        assert "`load_memory`" in self.text
        assert "memory_loaded" in self.text
        assert "retains its own `memory_loaded` policy event automatically" in self.flat_text

    def test_memory_is_context_not_authority(self) -> None:
        assert "never authority" in self.flat_text
        assert "never overrides the Protected Path list" in self.flat_text or "never overrides" in self.flat_text

    def test_loaded_versus_applied_distinction_documented(self) -> None:
        assert "Memory: loaded vs. applied" in self.text
        assert "record_memory_applied" in self.text
        assert "Never call `record_memory_applied` merely because" in self.text

    def test_memory_applied_requires_real_evidence_path(self) -> None:
        assert "evidence_path" in self.text
        assert "refuses" in self.flat_text.lower()

    def test_terminal_memory_append_documented(self) -> None:
        assert "## Terminal memory append" in self.text
        assert "Runs exactly once per run" in self.flat_text

    def test_lesson_cap_of_five_documented(self) -> None:
        assert "max_new" in self.text or "cap" in self.flat_text.lower()
        assert "5" in self.text

    def test_memory_never_appended_on_interruption(self) -> None:
        assert "Memory is never appended on an interruption" in self.flat_text

    def test_summarize_memory_feeds_run_summary_before_write(self) -> None:
        assert "`summarize_memory`" in self.text
        assert "memory_influenced_run" in self.text
        assert "memory_refs_used" in self.text

    def test_reporting_states_loaded_versus_applied(self) -> None:
        assert "these are different claims" in self.flat_text.lower()

    def test_fresh_invocation_load_is_scoped_to_one_process_not_one_run_id(self) -> None:
        assert "one load per process invocation, not one load per run_id" in self.flat_text

    def test_resumed_invocation_loads_memory_again(self) -> None:
        assert "## Loading memory on a resumed invocation" in self.text
        assert "Call `load_memory`" in self.text
        assert "second, independent `memory_loaded` event under the same `run_id`" in self.flat_text

    def test_resumed_memory_load_step_precedes_reused_and_restarted_phase_handling(self) -> None:
        resume_section = self.text[self.text.index("## Resuming an interrupted run"):]
        load_memory_idx = resume_section.index("Call `load_memory`")
        reused_idx = resume_section.index("this phase is **reused**")
        restarted_idx = resume_section.index("dispatch a brand-new agent instance")
        assert load_memory_idx < reused_idx < restarted_idx

    def test_resumed_memory_never_reinterprets_reused_phases(self) -> None:
        assert "must never be used to revisit, second-guess, or reinterpret a" in self.flat_text
        assert "not reopened because the" in self.flat_text.lower() or "not a draft" in self.flat_text.lower()

    def test_never_claim_single_load_for_resumed_run(self) -> None:
        assert 'never report a resumed run as having "loaded memory once"' in self.flat_text.lower()

    def test_terminal_append_relies_on_resume_refusal_not_only_duplicate_suppression(self) -> None:
        assert "is refused outright by `evaluate_resume`" in self.text
        assert "secondary safety" in self.flat_text.lower()

    def test_reporting_states_two_loads_on_resume(self) -> None:
        assert "memory was loaded twice" in self.flat_text.lower()


class TestUsageAccounting:
    """Structural checks for the per-agent usage/cost-accounting integration into /work
    (integration audit, 2026-08-14): every fresh Agent dispatch is followed by
    reconcile_quarantined_usage, resumed/staged turns never repeat that call, terminal
    reporting builds and references a usage summary, and accounting coverage is kept
    strictly separate from pipeline correctness."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def test_per_agent_usage_accounting_section_exists(self) -> None:
        assert "# Per-agent usage accounting" in self.text

    def test_reconcile_quarantined_usage_operation_documented(self) -> None:
        assert "`reconcile_quarantined_usage`" in self.text
        assert "no_quarantine_record" in self.text
        assert "ambiguous" in self.text

    def test_build_usage_summary_operation_documented(self) -> None:
        assert "`build_usage_summary`" in self.text

    def test_standing_rules_cover_dispatch_time_reconciliation_and_coverage_semantics(self) -> None:
        assert "15. After every fresh `Agent` dispatch's own" in self.text
        assert "16. Per-agent token/cost accounting is evidence, not verdict." in self.text

    def _phase_slice(self, start_marker: str, end_marker: str) -> str:
        start = self.text.index(start_marker)
        end = self.text.index(end_marker, start)
        return self.text[start:end]

    def test_architect_dispatch_is_followed_by_reconciliation(self) -> None:
        phase = self._phase_slice("# Phase 2: Research", "# Dry-run boundary")
        dispatch_idx = phase.index('"agent_id": "<the returned id>"')
        reconcile_idx = phase.index("reconcile_quarantined_usage")
        assert dispatch_idx < reconcile_idx

    def test_engineer_dispatch_is_followed_by_reconciliation(self) -> None:
        phase = self._phase_slice("# Phase 3: Implementation", "## Engineer transport repair")
        dispatch_idx = phase.index('`kind: "agent_dispatch"`,\n   `phase: "implementation"`')
        reconcile_idx = phase.index("reconcile_quarantined_usage")
        assert dispatch_idx < reconcile_idx

    def test_quality_engineer_dispatch_is_followed_by_reconciliation(self) -> None:
        phase = self._phase_slice("# Phase 4: Verification", "## Same-run logic-failure route-back")
        dispatch_idx = phase.index("capture and retain the agent id as a policy")
        reconcile_idx = phase.index("reconcile_quarantined_usage")
        assert dispatch_idx < reconcile_idx

    def test_restarted_fresh_engineer_after_resume_is_reconciled_as_a_new_identity(self) -> None:
        resume_section = self._phase_slice("## Resuming an interrupted run", "## Loading memory on a resumed invocation")
        assert "reconcile_quarantined_usage" in resume_section
        assert "wholly separate agent record" in resume_section

    def test_resumed_identities_never_repeat_the_dispatch_time_sequence(self) -> None:
        usage_section = self._phase_slice("# Per-agent usage accounting", "# Phase 0: Memory load")
        assert "Resumed identities never repeat this sequence" in usage_section
        assert "never gets its own" in usage_section

    def test_same_run_route_back_is_documented_as_a_resume_not_a_fresh_dispatch(self) -> None:
        # The route-back section itself never calls a fresh Agent tool for the Engineer
        # (SendMessage to the same agent id only) -- this is what keeps repair-cycle usage
        # attributed to one identity; asserted structurally via the existing route-back
        # prose plus the usage-accounting section's own governing rule.
        route_back = self._phase_slice("## Same-run logic-failure route-back", "## Quality Engineer transport repair")
        assert "never a new `Agent` call" in route_back
        assert "`SendMessage` to that **exact same Engineer agent id**" in route_back

    def test_engineer_transport_repair_is_documented_as_a_resume(self) -> None:
        repair = self._phase_slice("## Engineer transport repair", "# Phase 4: Verification")
        assert "SendMessage to that exact agent id" in repair or "same Engineer agent id" in repair

    def test_terminal_usage_summary_section_exists_and_runs_before_write_run_summary(self) -> None:
        assert "## Terminal usage summary" in self.text
        terminal_usage = self._phase_slice("## Terminal usage summary", "# Reporting")
        build_idx = terminal_usage.index("Call `build_usage_summary`")
        ref_idx = terminal_usage.index("usage_summary_ref")
        assert build_idx < ref_idx

    def test_terminal_usage_summary_applies_to_every_write_run_summary_call(self) -> None:
        terminal_usage = self._phase_slice("## Terminal usage summary", "# Reporting")
        assert "discovery_invalid" in terminal_usage
        assert "discovery_refused" in terminal_usage
        assert "no exception" in terminal_usage.lower()

    def test_usage_summary_ref_is_a_pointer_never_hand_authored(self) -> None:
        assert "never hand-authored token/cost totals" in self.flat_text.lower() or "never hand-author usage totals" in self.flat_text.lower()

    def test_full_pipeline_total_stays_null_and_is_reported_as_such(self) -> None:
        assert "full_pipeline_total` is `null` for every run this milestone" in self.flat_text

    def test_subagent_subtotal_never_described_as_total_pipeline_cost(self) -> None:
        assert "never describe" in self.flat_text.lower() and "total pipeline cost" in self.flat_text.lower()
        assert "never call the" in self.flat_text.lower() and "subagent_subtotal" in self.flat_text

    def test_accounting_gaps_never_change_pipeline_verdict(self) -> None:
        assert "never let their presence change this run's own" in self.flat_text
        assert "final_verdict" in self.text

    def test_accounting_and_pipeline_correctness_are_independent_claims(self) -> None:
        assert "the two are independent claims, always" in self.flat_text.lower()

    def test_never_fabricate_or_redispatch_solely_for_accounting(self) -> None:
        usage_section = self._phase_slice("# Per-agent usage accounting", "# Phase 0: Memory load")
        assert "never fabricate a usage record" in usage_section.lower()
        assert "never dispatch a" in usage_section.lower()

    def test_reporting_states_usage_accounting_picture(self) -> None:
        assert "Per-agent usage/cost accounting" in self.text
        assert "usage_accounting_gap" in self.text


class TestGitHubDelivery:
    """Structural checks for the GitHub/Git-delivery milestone: the github Skill is
    required before any commit/push, every push is followed by independent
    verification, and only a "verified" verify_push classification may ever be reported
    as a successful push."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def _phase_slice(self, start_marker: str, end_marker: str) -> str:
        start = self.text.index(start_marker)
        end = self.text.index(end_marker, start)
        return self.text[start:end]

    def test_github_delivery_section_exists(self) -> None:
        assert "# GitHub / Git delivery" in self.text

    def test_standing_rule_17_requires_the_github_skill_before_commit_or_push(self) -> None:
        assert '17. Any Git commit or push this session performs' in self.text
        assert 'skill: "github"' in self.text

    def test_standing_rule_12_cross_references_standing_rule_17(self) -> None:
        rule_12 = self._phase_slice("12. Never commit or push.", "13. Every orchestrator-owned")
        assert "standing rule 17" in rule_12

    def test_all_five_new_operations_documented_in_operations_table(self) -> None:
        for op in (
            "`git_repo_identity`", "`retain_commit_evidence`", "`retain_push_attempt`",
            "`verify_push`", "`gh_repo_metadata`",
        ):
            assert op in self.text, f"expected operation {op!r} documented in the live_cli operations table"

    def test_all_six_verification_classifications_documented(self) -> None:
        for status in ("verified", "mismatch", "remote_ref_missing", "command_failed", "invalid_output", "wrong_repository"):
            assert status in self.text

    def test_github_skill_invocation_is_step_one(self) -> None:
        section = self._phase_slice("# GitHub / Git delivery", "# Phase 0: Memory load")
        skill_idx = section.index('skill: "github"')
        commit_idx = section.index("git commit -m")
        push_idx = section.index("git push <remote> <branch>")
        verify_idx = section.index("Mandatory, every time, no exception")
        assert skill_idx < commit_idx < push_idx < verify_idx

    def test_commit_evidence_precedes_push_attempt_precedes_verification(self) -> None:
        section = self._phase_slice("# GitHub / Git delivery", "# Phase 0: Memory load")
        commit_evidence_idx = section.index("`retain_commit_evidence`")
        push_attempt_idx = section.index("`retain_push_attempt`")
        verify_idx = section.index("`verify_push`")
        assert commit_evidence_idx < push_attempt_idx < verify_idx

    def test_only_verified_status_may_be_reported_as_success(self) -> None:
        section = self._phase_slice("# GitHub / Git delivery", "# Phase 0: Memory load")
        assert 'if, and only if, the returned status is exactly `"verified"`' in section

    def test_never_force_push_or_rewrite_history(self) -> None:
        section = self._phase_slice("# GitHub / Git delivery", "# Phase 0: Memory load")
        assert "--force" in section
        assert "destructive `reset`" in section

    def test_git_delivery_failure_never_falsifies_test_evidence(self) -> None:
        section = self._phase_slice("# GitHub / Git delivery", "# Phase 0: Memory load")
        assert "never rewritten, reinterpreted" in section
        assert "never becomes an excuse to fabricate or soften a real test result" in section

    def test_gh_never_substitutes_for_git_ls_remote(self) -> None:
        assert "never a substitute for" in self.flat_text.lower() or "never in place of step 8" in self.flat_text

    def test_github_skill_file_exists(self) -> None:
        github_skill_path = REPO_ROOT / ".claude" / "skills" / "github" / "SKILL.md"
        assert github_skill_path.is_file()

    def test_reporting_states_git_delivery_status(self) -> None:
        assert "Git delivery status" in self.text
        assert "commit-evidence.json" in self.text
        assert "push-verification.json" in self.text


class TestJiraTicketMode:
    """Structural checks for the Jira ticket-mode-intake milestone: the jira Skill is
    required before any resolution attempt, resolution happens before Discovery, only a
    genuine `resolved` classification may proceed, every other classification stops the
    run honestly before Discovery with no silent fallback to free-form mode, and
    free-form mode remains fully supported alongside ticket mode."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def _phase_slice(self, start_marker: str, end_marker: str) -> str:
        start = self.text.index(start_marker)
        end = self.text.index(end_marker, start)
        return self.text[start:end]

    def _flat_slice(self, start_marker: str, end_marker: str) -> str:
        return " ".join(self._phase_slice(start_marker, end_marker).split())

    def test_ticket_mode_section_exists(self) -> None:
        assert "# Ticket-mode Jira resolution" in self.text

    def test_ticket_mode_is_documented_as_implemented_not_deferred(self) -> None:
        # The old milestone explicitly refused ticket-shaped input ("not implemented in
        # this milestone"); that phrase must not survive now that ticket mode is real.
        assert "not implemented in this milestone" not in self.flat_text
        assert "ticket-mode candidate" in self.flat_text

    def test_a_whole_first_token_selects_ticket_mode(self) -> None:
        section = self._phase_slice("# Parsing $ARGUMENTS", "# Run identity")
        assert "tokens[0]" in section
        assert "whole-token Jira-ticket shape" in section
        # Prose text containing a ticket-shaped substring later in the sentence must not
        # accidentally select ticket mode -- only a match at the very first token does.
        assert "never attempt Jira resolution for input that does not" in section

    def test_extra_arguments_after_the_ticket_id_are_a_usage_error(self) -> None:
        section = self._phase_slice("# Parsing $ARGUMENTS", "# Run identity")
        assert "usage error" in section
        flat_section = self._flat_slice("# Parsing $ARGUMENTS", "# Run identity")
        assert "do not proceed to Discovery, do not guess which mode was intended" in flat_section

    def test_standing_rule_18_requires_the_jira_skill_before_resolution(self) -> None:
        rule_18 = self._phase_slice("18. Any ticket-mode", "# Parsing $ARGUMENTS")
        assert 'skill: "jira"' in rule_18
        flat_rule_18 = self._flat_slice("18. Any ticket-mode", "# Parsing $ARGUMENTS")
        assert "before Phase 0 memory load and before any Discovery reasoning begins" in flat_rule_18

    def test_jira_skill_invocation_is_step_one_of_resolution(self) -> None:
        section = self._phase_slice("# Ticket-mode Jira resolution", "# The live bridge")
        skill_idx = section.index('skill: "jira"')
        build_request_idx = section.index("2. Build a")
        assert skill_idx < build_request_idx

    def test_resolution_happens_before_discovery_and_before_memory_load(self) -> None:
        section = self._phase_slice("# Ticket-mode Jira resolution", "# The live bridge")
        assert "before" in section
        assert "Phase 0 memory load and before any Discovery reasoning" in section

    def test_only_resolved_status_may_proceed(self) -> None:
        section = self._phase_slice("# Ticket-mode Jira resolution", "# The live bridge")
        assert '`status: "resolved"` (exit code 0) is the only outcome' in section
        assert 'that may ever be treated as "this ticket exists."' in section

    def test_every_other_classification_stops_before_discovery(self) -> None:
        section = self._phase_slice("# Ticket-mode Jira resolution", "# The live bridge")
        assert "stops the run **before** Discovery and" in section
        for status in (
            "not_found", "unauthorized", "connector_unavailable",
            "identity_mismatch", "invalid_issue_key", "invalid_response",
        ):
            assert status in section

    def test_no_silent_fallback_to_free_form_mode(self) -> None:
        section = self._flat_slice("# Ticket-mode Jira resolution", "# The live bridge")
        assert "never silently fall back to free-form mode on the caller's behalf" in section
        assert "the caller decides whether to re-run in free-form mode" in section

    def test_resolved_content_is_input_evidence_not_trusted_scope(self) -> None:
        section = self._flat_slice("# Ticket-mode Jira resolution", "# The live bridge")
        assert "it is input evidence, nothing more" in section
        assert "Discovery still performs its own full reasoning over it" in section
        assert "never let a resolved ticket's content exempt this run from the pre-dispatch checklist" in section

    def test_free_form_mode_still_supported_alongside_ticket_mode(self) -> None:
        section = self._phase_slice("# Parsing $ARGUMENTS", "# Run identity")
        assert "free-form request" in section
        assert "proceed straight to" in section

    def test_resolve_jira_issue_documented_in_operations_table(self) -> None:
        assert "`resolve_jira_issue`" in self.text

    def test_no_checkpoint_written_for_pre_discovery_resolution_failure(self) -> None:
        section = self._phase_slice("# Ticket-mode Jira resolution", "# The live bridge")
        assert "No `checkpoint.json` is written for this outcome" in section


class TestObsidianRunSummaryPublication:
    """Structural checks for the Obsidian run-summary publication milestone: the
    obsidian Skill is invoked before publishing, publication happens after
    write_run_summary, and an Obsidian delivery failure is a separate claim that never
    changes the run verdict."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def _phase_slice(self, start_marker: str, end_marker: str) -> str:
        start = self.text.index(start_marker)
        end = self.text.index(end_marker, start)
        return self.text[start:end]

    def test_section_exists(self) -> None:
        assert "# Obsidian run-summary publication" in self.text

    def test_standing_rule_19_present(self) -> None:
        assert "19. Every terminal run publishes its concise run summary" in self.text
        assert 'skill: "obsidian"' in self.text

    def test_publication_happens_after_write_run_summary(self) -> None:
        section = self._phase_slice("# Obsidian run-summary publication", "# Reporting")
        assert "immediately after `write_run_summary`" in section or "after** the terminal run-summary" in section
        # explicit ordering: run summary written, THEN skill invoked, THEN publish
        write_idx = section.index("`write_run_summary`")
        skill_idx = section.index('skill: "obsidian"')
        publish_idx = section.index("publish_run_summary` request")
        assert write_idx < skill_idx < publish_idx

    def test_operation_documented_in_table(self) -> None:
        assert "`publish_run_summary`" in self.text

    def test_all_six_classifications_documented(self) -> None:
        for status in (
            "published", "connector_unavailable", "invalid_destination",
            "destination_unavailable", "collision", "write_failed",
        ):
            assert status in self.text

    def test_only_published_counts_as_delivered(self) -> None:
        assert 'only a `publish_run_summary` result of exactly `status: "published"`' in self.flat_text

    def test_obsidian_failure_never_changes_verdict(self) -> None:
        section = self._phase_slice("# Obsidian run-summary publication", "# Reporting")
        assert "never** changes `final_verdict`" in self.flat_text
        assert "still genuinely" in section and 'final_verdict: "pass"' in section

    def test_separate_from_git_and_jira_and_pipeline(self) -> None:
        assert "separate external-delivery claim" in self.flat_text

    def test_kept_separate_from_memory_loop(self) -> None:
        section = self._phase_slice("# Obsidian run-summary publication", "# Reporting")
        assert "independent of the memory loop" in section
        assert "Terminal memory append" in section
        assert "neither is derived from it" in section

    def test_evidence_path_documented(self) -> None:
        assert "runs/<run_id>/obsidian/summary-publication.json" in self.text

    def test_reporting_states_obsidian_delivery(self) -> None:
        assert "Obsidian run-summary delivery" in self.text
        assert "summary-publication.json" in self.text

    def test_obsidian_skill_file_exists(self) -> None:
        assert (REPO_ROOT / ".claude" / "skills" / "obsidian" / "SKILL.md").is_file()

    def test_no_hardcoded_personal_vault_path(self) -> None:
        assert "Obsidian Vault" not in self.text
        assert r"C:\Users" not in self.text
        assert "OBSIDIAN_VAULT_PATH" in self.text


class TestObsidianDiscoveryResearchRead:
    """Structural checks for the Obsidian Discovery/Research READ milestone (2026-09-08):
    the orchestrator may consult the vault during Discovery/Research, mediated through
    live_cli.py, treating every retrieved word as historical/contextual evidence -- never
    repository truth -- with the obsidian Skill invoked first and evidence retained
    separately from the publication path."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = " ".join(self.text.split())

    def _phase_slice(self, start_marker: str, end_marker: str) -> str:
        start = self.text.index(start_marker)
        end = self.text.index(end_marker, start)
        return self.text[start:end]

    def _flat_slice(self, start_marker: str, end_marker: str) -> str:
        return " ".join(self._phase_slice(start_marker, end_marker).split())

    def test_dedicated_section_exists(self) -> None:
        assert "# Obsidian Discovery/Research consultation" in self.text

    def test_standing_rule_20_present(self) -> None:
        assert "20. The Obsidian vault may be **read** during Discovery and Research" in self.text
        rule_20 = self._flat_slice("20. The Obsidian vault may be", "# Parsing $ARGUMENTS")
        assert 'skill: "obsidian"' in rule_20
        assert "A vault note is historical/contextual evidence, never current repository truth" in rule_20
        assert "never becomes acceptance criteria unless" in rule_20

    def test_operations_documented_in_table(self) -> None:
        assert "`search_obsidian`" in self.text
        assert "`read_obsidian_note`" in self.text

    def test_consultation_is_conditional_not_forced(self) -> None:
        section = self._flat_slice("# Obsidian Discovery/Research consultation", "# Phase 1: Discovery")
        assert "not every run" in section
        assert "materially" in section
        assert "no unconditional per-phase read" in section

    def test_obsidian_skill_invoked_before_first_read(self) -> None:
        section = self._phase_slice("# Obsidian Discovery/Research consultation", "# Phase 1: Discovery")
        skill_idx = section.index('skill: "obsidian"')
        search_req_idx = section.index("Build a `search_obsidian` request")
        assert skill_idx < search_req_idx

    def test_read_evidence_retained_and_separate_from_publication(self) -> None:
        section = self._flat_slice("# Obsidian Discovery/Research consultation", "# Phase 1: Discovery")
        assert "runs/<run_id>/obsidian/read/" in section
        assert "obsidian_read" in section
        assert "distinct direction of the same connector" in section

    def test_vault_note_never_overrides_authoritative_evidence(self) -> None:
        section = self._flat_slice("# Obsidian Discovery/Research consultation", "# Phase 1: Discovery")
        for authority in ("user request", "ASSIGNMENT.md", "Protected Paths", "scope validation"):
            assert authority in section
        assert "historical/contextual evidence, never repository truth" in section

    def test_discovery_and_research_subsections_present(self) -> None:
        section = self.text[self.text.index("# Obsidian Discovery/Research consultation"):]
        assert "## In Discovery" in section
        assert "## In Research" in section

    def test_research_injects_evidence_into_architect_prompt(self) -> None:
        section = self._flat_slice("# Obsidian Discovery/Research consultation", "# Phase 1: Discovery")
        assert "before dispatching the Architect" in section
        assert "Obsidian historical/contextual evidence" in section
        assert "corroborated / stale / contradicted / context-only" in section
        assert "Architect never reads the vault itself" in section

    def test_phase_1_points_at_consultation(self) -> None:
        phase1 = self._phase_slice("# Phase 1: Discovery", "# Phase 2: Research")
        assert "Obsidian Discovery/Research consultation" in phase1
        assert 'phase: "discovery"' in phase1

    def test_phase_2_step_0_consultation_before_dispatch(self) -> None:
        phase2 = self._phase_slice("# Phase 2: Research", "# Dry-run boundary")
        step0_idx = phase2.index("Optional Obsidian consultation, before dispatch")
        dispatch_idx = phase2.index("Dispatch with the `Agent` tool")
        assert step0_idx < dispatch_idx
        assert 'phase: "research"' in phase2

    def test_reporting_states_obsidian_read_consultation(self) -> None:
        assert "Obsidian vault **read** consultation" in self.text
        reporting = self.text[self.text.index("## What to state, every time"):]
        assert "search_obsidian" in reporting
        assert "historical/contextual evidence, not" in reporting

    def test_read_failure_is_not_a_pipeline_failure(self) -> None:
        assert "never a pipeline failure" in self.flat_text or "never a pipeline\n    failure" in self.text

    def test_kept_distinct_from_write_back(self) -> None:
        section = self._flat_slice("# Obsidian Discovery/Research consultation", "# Phase 1: Discovery")
        assert "separate direction" in section or "distinct direction" in section
        assert "never affects the run verdict" in section
