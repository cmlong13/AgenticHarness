"""Static structural checks on the same-run logic-failure route-back protocol (Part 2 of
this milestone), spread across `.claude/agents/engineer.md`, `.claude/agents/quality-engineer.md`,
and `.claude/skills/work/SKILL.md`.

Same convention as tests/test_work_skill.py: these check for the *presence of required
concepts*, not exact paragraph wording -- prose is free to be rephrased without breaking
these tests, as long as the underlying requirement is still actually expressed somewhere.

The engine-level behavior itself (bounded repair, classification, same-handle continuity,
policy events) is covered by tests/test_orchestrator_core.py -- this file only proves the
three markdown contracts a *live* run depends on actually document the same protocol.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINEER_PATH = REPO_ROOT / ".claude" / "agents" / "engineer.md"
QE_PATH = REPO_ROOT / ".claude" / "agents" / "quality-engineer.md"
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "work" / "SKILL.md"


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


class TestEngineerRouteBackProtocol:
    def setup_method(self) -> None:
        self.text = ENGINEER_PATH.read_text(encoding="utf-8")
        self.flat_text = _flat(ENGINEER_PATH)

    def test_route_back_section_present(self) -> None:
        assert "Route-back repair protocol" in self.text

    def test_not_a_new_dispatch(self) -> None:
        assert "not** a new dispatch" in self.text or "not a new dispatch" in self.flat_text.lower()

    def test_route_back_message_type_documented(self) -> None:
        assert '"message_type": "verification_failed"' in self.text
        assert "repair_attempt" in self.text
        assert "failing_attempts" in self.text
        assert "acceptance_criteria_failed" in self.text

    def test_broken_handoff_detection_applies_to_route_back(self) -> None:
        section_idx = self.text.index("Route-back repair protocol")
        section = self.text[section_idx:]
        assert "broken handoff" in section.lower() or "continuity broke" in section.lower()

    def test_evidence_never_fabricated_or_summarized(self) -> None:
        assert "never fabricated" in self.flat_text.lower()

    def test_write_failing_regression_test_first(self) -> None:
        section_idx = self.text.index("Route-back repair protocol")
        section = " ".join(self.text[section_idx:].split())
        assert "failing regression test" in section.lower()
        assert "before you touch production code" in section.lower()

    def test_fresh_command_id_required_never_reused(self) -> None:
        section_idx = self.text.index("Route-back repair protocol")
        section = " ".join(self.text[section_idx:].split())
        assert "never reuse" in section.lower()
        assert "C-3" in section

    def test_repair_final_report_is_ordinary_schema_conformant_report(self) -> None:
        section_idx = self.text.index("Route-back repair protocol")
        section = " ".join(self.text[section_idx:].split())
        assert "implementation-report.schema.json" in section
        assert "no relaxed" in section.lower() or "nothing in this protocol relaxes" in section.lower()

    def test_minimal_change_ladder_reapplied(self) -> None:
        section_idx = self.text.index("Route-back repair protocol")
        section = " ".join(self.text[section_idx:].split())
        assert "minimal-change ladder" in section.lower()


class TestQualityEngineerReVerificationProtocol:
    def setup_method(self) -> None:
        self.text = QE_PATH.read_text(encoding="utf-8")
        self.flat_text = _flat(QE_PATH)

    def test_reverification_section_present(self) -> None:
        assert "Re-verification protocol" in self.text

    def test_not_a_new_dispatch(self) -> None:
        assert "not** a new dispatch" in self.text or "not a new dispatch" in self.flat_text.lower()

    def test_reverification_message_type_documented(self) -> None:
        assert '"message_type": "implementation_updated"' in self.text
        assert "repair_attempt" in self.text
        assert "implementation_ref" in self.text

    def test_broken_handoff_detection_applies_to_reverification(self) -> None:
        section_idx = self.text.index("Re-verification protocol")
        section = self.text[section_idx:]
        assert "continuity broke" in section.lower()

    def test_reruns_full_verification_steps_against_updated_report(self) -> None:
        section_idx = self.text.index("Re-verification protocol")
        section = " ".join(self.text[section_idx:].split())
        assert "protected path" in section.lower()
        assert "fresh" in section.lower()
        assert "V-2" in self.text

    def test_second_failure_reported_honestly_not_softened(self) -> None:
        section_idx = self.text.index("Re-verification protocol")
        section = " ".join(self.text[section_idx:].split())
        assert "not something to soften" in section.lower() or "not softened" in section.lower()

    def test_reverification_report_is_ordinary_schema_conformant_report(self) -> None:
        section_idx = self.text.index("Re-verification protocol")
        section = " ".join(self.text[section_idx:].split())
        assert "verification-report.schema.json" in section
        assert "no relaxed rules" in section.lower()


class TestWorkSkillRouteBackOrchestration:
    """Covers the live orchestrator-side half of the protocol: classification,
    same-handle resume, bounded repair count, honest terminal failure."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = _flat(SKILL_PATH)

    def test_route_back_section_present(self) -> None:
        assert "Same-run logic-failure route-back to the Engineer" in self.text

    def test_four_classification_categories_documented(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split()).lower()
        for phrase in ["genuine logic bug", "infrastructure flake", "environment failure", "malformed or insufficient evidence"]:
            assert phrase in section, f"expected classification category {phrase!r} in SKILL.md route-back section"

    def test_only_logic_bug_is_eligible_for_route_back(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "only case eligible for route-back" in section.lower() or "only eligible" in section.lower()

    def test_infrastructure_flake_never_routed(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "never re-route an infrastructure flake" in section.lower() or "never route" in section.lower()

    def test_environment_never_retried_or_routed(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "never route this to the engineer and never retry it" in section.lower()

    def test_bounded_repair_count_documented(self) -> None:
        assert "MAX_LOGIC_REPAIR_ATTEMPTS = 1" in self.text

    def test_same_engineer_agent_id_resumed_via_sendmessage(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "same engineer agent id" in section.lower()
        assert "SendMessage" in self.text
        assert "never a new `agent` call" in section.lower()

    def test_same_quality_engineer_agent_id_resumed_via_sendmessage(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "same quality engineer agent id" in section.lower()
        assert "never a new `agent` call" in section.lower()

    def test_real_evidence_never_summarized_or_redacted(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "never summarize, soften, or partially redact" in section.lower()

    def test_test_first_ordering_enforced_for_repair(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "mandatory tdd orchestration check" in section.lower()

    def test_repair_mediated_through_real_forked_test_runner_skill(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "real, forked" in section.lower() and "test-runner` skill" in section.lower()

    def test_repair_artifacts_promoted_to_new_canonical_paths_not_overwritten(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "implementation-report.repair-1.json" in section
        assert "verification-report.repair-1.json" in section
        assert "never delete or rewrite the original" in section.lower()

    def test_broken_continuity_during_repair_blocks_never_replaces_agent(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "broken agent continuity" in section.lower()
        assert "never dispatch a replacement engineer" in section.lower()
        assert "never dispatch a replacement quality engineer" in section.lower()

    def test_exhaustion_ends_run_honestly(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "route_back_exhaustion" in self.text
        assert "budget" in section.lower() and "exhausted" in section.lower()
        assert "do not attempt a second repair cycle" in section.lower()

    def test_repaired_pass_reported_with_repair_context(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "one logic-bug repair cycle" in section.lower()

    def test_repaired_run_re_verified_with_same_scrutiny(self) -> None:
        section_idx = self.text.index("Same-run logic-failure route-back")
        section = " ".join(self.text[section_idx:].split())
        assert "no less scrutiny than a" in section.lower()


class TestWorkSkillOrchestratorCommandIdentity:
    """Part 1: every orchestrator-owned test-runner request (ORCH-1, ORCH-2, ...), not
    just staged-agent-requested commands, must be identity-checked before its result is
    trusted."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = _flat(SKILL_PATH)

    def test_standing_rule_requires_orch_identity_check(self) -> None:
        assert "orchestrator-owned test-runner request" in self.flat_text.lower()
        assert "ORCH-1" in self.text and "ORCH-2" in self.text

    def test_references_the_documented_evidence_gap(self) -> None:
        assert "run-20260804-riskband-003" in self.text
        assert "check_command_identity" in self.text

    def test_mismatch_blocks_completion(self) -> None:
        assert "mismatch on an orch-* result blocks completion" in self.flat_text.lower() or (
            "blocks completion" in self.flat_text.lower() and "orch" in self.flat_text.lower()
        )

    def test_mismatch_policy_event_kind_documented(self) -> None:
        assert "orchestrator_command_identity_mismatch" in self.text

    def test_never_fabricate_or_locally_repair_mismatch(self) -> None:
        assert "never fabricate a matching result or locally repair a mismatch" in self.flat_text.lower()

    def test_phase_4_reverification_calls_check_command_identity_for_orch_commands(self) -> None:
        phase4_idx = self.text.index("# Phase 4")
        route_back_idx = self.text.index("## Same-run logic-failure route-back")
        phase4_section = " ".join(self.text[phase4_idx:route_back_idx].split())
        assert "orch-1" in phase4_section.lower()
        assert "orch-2" in phase4_section.lower()
        assert "check_command_identity" in phase4_section.lower()


class TestWorkSkillCompletionGuardrailMarker:
    """Part 3: the /work skill must write the marker completion_guardrail.py depends on
    before ever reporting a run complete."""

    def setup_method(self) -> None:
        self.text = SKILL_PATH.read_text(encoding="utf-8")
        self.flat_text = _flat(SKILL_PATH)

    def test_marker_section_present(self) -> None:
        assert "Completion-guardrail marker" in self.text

    def test_marker_filename_and_shape_documented(self) -> None:
        assert ".completion_claim.json" in self.text
        assert '{"task_id": "<task_id>", "run_id": "<run_id>"}' in self.text

    def test_marker_written_before_reporting_complete(self) -> None:
        section_idx = self.text.index("Completion-guardrail marker")
        section = " ".join(self.text[section_idx:].split())
        assert "before reporting a run as complete" in section.lower()

    def test_hook_block_treated_as_seriously_as_other_checks(self) -> None:
        section_idx = self.text.index("Completion-guardrail marker")
        section = " ".join(self.text[section_idx:].split())
        assert "not actually complete" in section.lower()

    def test_never_hand_edit_marker_to_bypass(self) -> None:
        section_idx = self.text.index("Completion-guardrail marker")
        section = " ".join(self.text[section_idx:].split())
        assert "do not delete or hand-edit this marker" in section.lower()
