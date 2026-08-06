"""Deterministic tests for harness/orchestrator/memory.py -- the persistent factual
memory and lessons-learned feedback loop (ASSIGNMENT.md Section 2.6, PROJECT_SPEC.md's
memory-loop milestone).

These exercise memory.py directly, against pytest's tmp_path -- no live agent, no
Claude Code tool call. Delegation-level proof that live_cli.py's load_memory/
append_memory/record_memory_applied/summarize_memory operations call these functions
correctly (rather than reimplementing any of this logic) lives in
tests/test_orchestrator_live_cli.py's TestMemoryOperations.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.orchestrator import memory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo_root(tmp_path):
    """A fake repo root with one real evidence file every fact/lesson in these tests
    can legitimately cite -- evidence_ref must resolve to something that really exists."""
    evidence_dir = tmp_path / "runs" / "run-source-1" / "logs"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "policy-events.jsonl").write_text(
        json.dumps({"kind": "transport_parse_failure", "detail": "fenced Architect reply"}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def memory_dir(repo_root):
    return repo_root / "memory"


EVIDENCE_REF = "runs/run-source-1/logs/policy-events.jsonl"


def _fact(**overrides) -> dict:
    base = {
        "id": "FACT-0001",
        "content": "The test-runner Skill must run forked (context: fork) or its disallowed-tools leak into the resumed Engineer's own turn.",
        "source_run_id": "run-source-1",
        "evidence_ref": EVIDENCE_REF,
        "recorded_at": "2026-08-06",
        "status": "confirmed",
        "tags": ["test-runner", "fork", "isolation"],
    }
    base.update(overrides)
    return base


def _lesson(**overrides) -> dict:
    base = {
        "id": "L-0001",
        "text": "Strict JSON transport failures (a fenced or prose-wrapped agent reply) must be classified and repaired before any semantic validation is attempted, and consume a bounded, one-per-phase correction budget.",
        "source_run_id": "run-source-1",
        "evidence_ref": EVIDENCE_REF,
        "date": "2026-08-06",
        "tags": ["transport", "validation", "json"],
    }
    base.update(overrides)
    return base


def _write_facts_jsonl(memory_dir, lines: list[str]) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory.facts_path(memory_dir).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _retain_memory_loaded(
    run_directory, *, relevant_fact_ids: list[str] | None = None, relevant_lesson_ids: list[str] | None = None
) -> None:
    """Test helper standing in for op_load_memory's own retained event -- appends a
    genuine "memory_loaded" policy event to run_directory's log, exactly shaped as
    live_cli.py's op_load_memory writes it, so validate_memory_applied's run-scoped
    selection check (Audit 1) has something real to check against."""
    events_path = run_directory / "logs" / "policy-events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "kind": "memory_loaded",
        "relevant_fact_ids": relevant_fact_ids or [],
        "relevant_lesson_ids": relevant_lesson_ids or [],
    }
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Fact validation
# ---------------------------------------------------------------------------


class TestFactValidation:
    def test_valid_fact_loads(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, [json.dumps(_fact())])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert [f["id"] for f in result.valid] == ["FACT-0001"]
        assert result.skipped == []

    def test_provenance_is_required(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, [json.dumps(_fact(source_run_id=""))])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert result.valid == []
        assert len(result.skipped) == 1
        assert "source_run_id" in result.skipped[0].reason

    def test_source_evidence_field_is_required(self, memory_dir, repo_root):
        candidate = _fact()
        del candidate["evidence_ref"]
        _write_facts_jsonl(memory_dir, [json.dumps(candidate)])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert result.valid == []
        assert "evidence_ref" in result.skipped[0].reason

    def test_missing_evidence_file_is_skipped_visibly(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, [json.dumps(_fact(evidence_ref="runs/does-not-exist/foo.json"))])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert result.valid == []
        assert len(result.skipped) == 1
        assert "does not resolve to an existing file" in result.skipped[0].reason

    def test_malformed_jsonl_line_is_skipped_visibly(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, ["{not valid json"])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert result.valid == []
        assert len(result.skipped) == 1
        assert "malformed JSON" in result.skipped[0].reason

    def test_empty_content_is_rejected(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, [json.dumps(_fact(content="   "))])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert result.valid == []
        assert "content" in result.skipped[0].reason

    def test_duplicate_facts_are_suppressed(self, memory_dir, repo_root):
        first = _fact()
        second = _fact(id="FACT-0002")  # same content, different id -> normalized-content duplicate
        _write_facts_jsonl(memory_dir, [json.dumps(first), json.dumps(second)])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert [f["id"] for f in result.valid] == ["FACT-0001"]
        assert len(result.skipped) == 1
        assert "duplicate" in result.skipped[0].reason

    def test_secret_shaped_content_is_rejected(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, [json.dumps(_fact(content="the API_KEY is sk-abcdefghijklmnopqrstuvwxyz"))])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert result.valid == []
        assert "secret" in result.skipped[0].reason

    def test_secret_shaped_tag_is_rejected(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, [json.dumps(_fact(tags=["password: hunter2"]))])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert result.valid == []

    def test_valid_entries_after_a_malformed_line_still_load(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, ["{broken", json.dumps(_fact())])
        result = memory.load_facts(memory_dir, repo_root=repo_root)
        assert [f["id"] for f in result.valid] == ["FACT-0001"]
        assert len(result.skipped) == 1
        assert result.skipped[0].line_no == 1

    def test_append_fact_is_append_safe_and_does_not_rewrite_prior_lines(self, memory_dir, repo_root):
        first_result = memory.append_fact(memory_dir, _fact(), repo_root=repo_root)
        assert first_result.status == "appended"
        raw_before = memory.facts_path(memory_dir).read_text(encoding="utf-8")

        second = _fact(id="FACT-0002", content="A distinct second fact about checkpoint resume behavior.")
        second_result = memory.append_fact(memory_dir, second, repo_root=repo_root)
        assert second_result.status == "appended"
        raw_after = memory.facts_path(memory_dir).read_text(encoding="utf-8")

        assert raw_after.startswith(raw_before)  # the first line was never rewritten
        loaded = memory.load_facts(memory_dir, repo_root=repo_root)
        assert {f["id"] for f in loaded.valid} == {"FACT-0001", "FACT-0002"}

    def test_append_fact_rejects_duplicate(self, memory_dir, repo_root):
        memory.append_fact(memory_dir, _fact(), repo_root=repo_root)
        result = memory.append_fact(memory_dir, _fact(id="FACT-9999"), repo_root=repo_root)
        assert result.status == "duplicate"
        assert result.appended is None

    def test_append_fact_rejects_missing_evidence(self, memory_dir, repo_root):
        result = memory.append_fact(memory_dir, _fact(evidence_ref="runs/nope/x.json"), repo_root=repo_root)
        assert result.status == "rejected"


# ---------------------------------------------------------------------------
# Lesson behavior
# ---------------------------------------------------------------------------


class TestLessonBehavior:
    def test_lessons_file_is_created_when_absent(self, memory_dir, repo_root):
        assert not memory.lessons_path(memory_dir).exists()
        memory.ensure_lessons_file(memory_dir)
        assert memory.lessons_path(memory_dir).exists()
        assert "Lessons Learned" in memory.lessons_path(memory_dir).read_text(encoding="utf-8")

    def test_ensure_lessons_file_never_overwrites_existing_content(self, memory_dir, repo_root):
        memory.ensure_lessons_file(memory_dir)
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        before = memory.lessons_path(memory_dir).read_text(encoding="utf-8")
        memory.ensure_lessons_file(memory_dir)
        after = memory.lessons_path(memory_dir).read_text(encoding="utf-8")
        assert before == after

    def test_no_more_than_five_lessons_appended_per_run(self, memory_dir, repo_root):
        candidates = [
            _lesson(
                id=f"L-{n:04d}",
                text=f"Lesson number {n} about harness orchestration and staged-protocol validation behavior.",
            )
            for n in range(7)
        ]
        result = memory.append_lessons(memory_dir, candidates, repo_root=repo_root)
        assert len(result.appended) == 5
        assert len(result.skipped) == 2
        assert all("cap reached" in s["reason"] for s in result.skipped)

    def test_fewer_than_five_is_permitted(self, memory_dir, repo_root):
        candidates = [
            _lesson(id="L-0001", text="First harness validation lesson about staged protocol continuity."),
            _lesson(id="L-0002", text="Second harness validation lesson about checkpoint resume ordering."),
        ]
        result = memory.append_lessons(memory_dir, candidates, repo_root=repo_root)
        assert len(result.appended) == 2
        assert result.skipped == []

    def test_zero_is_permitted(self, memory_dir, repo_root):
        candidates = [_lesson(text="Applicants prefer a shorter loan application form.")]  # no workflow keyword
        result = memory.append_lessons(memory_dir, candidates, repo_root=repo_root)
        assert result.appended == []
        assert len(result.skipped) == 1

    def test_exact_duplicates_are_suppressed(self, memory_dir, repo_root):
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        result = memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        assert result.appended == []
        assert "duplicate" in result.skipped[0]["reason"]

    def test_materially_equivalent_duplicates_are_suppressed_via_normalization(self, memory_dir, repo_root):
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        reworded = _lesson(
            id="L-0002",
            text=_lesson()["text"].upper().replace(".", "!!!").replace(",", " ,, "),
        )
        result = memory.append_lessons(memory_dir, [reworded], repo_root=repo_root)
        assert result.appended == []
        assert "duplicate" in result.skipped[0]["reason"]

    def test_task_specific_trivia_is_rejected_when_the_contract_can_detect_it(self, memory_dir, repo_root):
        trivia = _lesson(text="Applicants under 25 should get a slightly lower risk band by default.")
        result = memory.append_lessons(memory_dir, [trivia], repo_root=repo_root)
        assert result.appended == []
        assert "task-specific product trivia" in result.skipped[0]["reason"]

    def test_every_lesson_retains_source_run_provenance(self, memory_dir, repo_root):
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        loaded = memory.load_lessons(memory_dir, repo_root=repo_root)
        assert loaded.valid[0]["source_run_id"] == "run-source-1"

    def test_lesson_with_unresolvable_evidence_is_rejected(self, memory_dir, repo_root):
        result = memory.append_lessons(
            memory_dir, [_lesson(evidence_ref="runs/ghost/nope.json")], repo_root=repo_root
        )
        assert result.appended == []

    def test_malformed_lesson_line_is_skipped_but_does_not_block_a_later_valid_one(self, memory_dir, repo_root):
        memory.ensure_lessons_file(memory_dir)
        path = memory.lessons_path(memory_dir)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("- [not a valid lesson line at all\n")
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        loaded = memory.load_lessons(memory_dir, repo_root=repo_root)
        assert [lesson["id"] for lesson in loaded.valid] == ["L-0001"]
        assert len(loaded.skipped) == 1


# ---------------------------------------------------------------------------
# Load order and evidence (Part 3)
# ---------------------------------------------------------------------------


class TestLoadMemory:
    def test_relevant_and_irrelevant_entries_are_distinguished(self, memory_dir, repo_root):
        memory.append_fact(memory_dir, _fact(), repo_root=repo_root)  # tags: test-runner, fork, isolation
        memory.append_fact(
            memory_dir,
            _fact(id="FACT-0002", content="Checkpoint resume revalidates every completed phase's artifact.",
                  tags=["checkpoint", "resume"]),
            repo_root=repo_root,
        )
        result = memory.load_memory(memory_dir, raw_prompt="Fix the test-runner fork isolation bug", repo_root=repo_root)
        assert result.valid_fact_count == 2
        assert [f["id"] for f in result.relevant_facts] == ["FACT-0001"]

    def test_empty_memory_does_not_block_a_run(self, memory_dir, repo_root):
        result = memory.load_memory(memory_dir, raw_prompt="fix a typo", repo_root=repo_root)
        assert result.valid_fact_count == 0
        assert result.valid_lesson_count == 0
        assert result.relevant_facts == []
        assert result.relevant_lessons == []
        assert result.skipped == []

    def test_malformed_memory_is_visible_but_does_not_poison_valid_memory(self, memory_dir, repo_root):
        _write_facts_jsonl(memory_dir, ["not json at all", json.dumps(_fact())])
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        result = memory.load_memory(memory_dir, raw_prompt="transport json validation", repo_root=repo_root)
        assert result.valid_fact_count == 1
        assert result.valid_lesson_count == 1
        assert any(s["source"] == "facts" for s in result.skipped)

    def test_load_memory_has_no_scope_or_protected_path_side_effect(self, memory_dir, repo_root):
        """memory.load_memory is read-only context, never authority over scope -- it must
        never create, modify, or reference any canonical artifact (scope.json etc.)."""
        memory.append_fact(memory_dir, _fact(), repo_root=repo_root)
        run_dir = repo_root / "runs" / "run-current"
        run_dir.mkdir(parents=True)
        memory.load_memory(memory_dir, raw_prompt="anything", repo_root=repo_root)
        assert not (run_dir / "scope.json").exists()
        assert not any(run_dir.iterdir())  # load_memory itself writes nothing into the run directory


# ---------------------------------------------------------------------------
# Actual-use proof (Part 4)
# ---------------------------------------------------------------------------


class TestActualUseProof:
    def test_reading_a_lesson_alone_does_not_set_memory_influenced_run(self, memory_dir, repo_root, tmp_path):
        run_directory = repo_root / "runs" / "run-b"
        run_directory.mkdir(parents=True)
        # Simulate what op_load_memory would retain: a memory_loaded event only.
        events_path = run_directory / "logs" / "policy-events.jsonl"
        events_path.parent.mkdir(parents=True)
        events_path.write_text(json.dumps({"kind": "memory_loaded", "relevant_lesson_ids": ["L-0001"]}) + "\n", encoding="utf-8")

        summary = memory.summarize_memory_events(run_directory)
        assert summary["memory_loaded"] is True
        assert summary["memory_influenced_run"] is False
        assert summary["memory_refs_used"] == []

    def test_a_valid_memory_applied_event_sets_memory_influenced_run(self, memory_dir, repo_root):
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        run_directory = repo_root / "runs" / "run-b"
        run_directory.mkdir(parents=True)
        _retain_memory_loaded(run_directory, relevant_lesson_ids=["L-0001"])
        decision_evidence = run_directory / "scope.json"
        decision_evidence.write_text(
            json.dumps({"constraints": ["Apply lesson L-0001: validate transport before parsing."]}),
            encoding="utf-8",
        )

        payload = {
            "entry_id": "L-0001", "entry_type": "lesson", "source_run_id": "run-source-1",
            "current_run_id": "run-b", "phase": "discovery",
            "decision": "Discovery constraints require transport-format validation before content parsing.",
            "evidence_path": "runs/run-b/scope.json",
        }
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert errors == []

        events_path = run_directory / "logs" / "policy-events.jsonl"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "memory_applied", **payload}) + "\n")

        summary = memory.summarize_memory_events(run_directory)
        assert summary["memory_influenced_run"] is True
        assert summary["memory_refs_used"] == ["L-0001"]

    def test_applied_record_cites_exact_entry_and_evidence(self, memory_dir, repo_root):
        memory.append_fact(memory_dir, _fact(), repo_root=repo_root)
        run_directory = repo_root / "runs" / "run-b"
        _retain_memory_loaded(run_directory, relevant_fact_ids=["FACT-0001"])
        (run_directory / "attempts").mkdir(parents=True)
        (run_directory / "attempts" / "implementation-1.raw.txt").write_text(
            "Applying FACT-0001: required the forked test-runner Skill before dispatching Engineer.",
            encoding="utf-8",
        )

        payload = {
            "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
            "current_run_id": "run-b", "phase": "implementation",
            "decision": "Required test-runner Skill to run forked before dispatching Engineer.",
            "evidence_path": "runs/run-b/attempts/implementation-1.raw.txt",
        }
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert errors == []

    def test_missing_decision_evidence_blocks_the_applied_claim(self, memory_dir, repo_root):
        memory.append_fact(memory_dir, _fact(), repo_root=repo_root)
        run_directory = repo_root / "runs" / "run-b"
        _retain_memory_loaded(run_directory, relevant_fact_ids=["FACT-0001"])
        payload = {
            "entry_id": "FACT-0001", "entry_type": "fact", "source_run_id": "run-source-1",
            "current_run_id": "run-b", "phase": "implementation",
            "decision": "Some decision.",
            "evidence_path": "runs/run-b/does-not-exist.json",
        }
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert errors
        assert any("does not resolve to an existing, regular file" in e for e in errors)

    def test_applied_claim_rejects_unknown_entry_id(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        _retain_memory_loaded(run_directory)
        (run_directory / "scope.json").write_text("FACT-9999 mentioned here", encoding="utf-8")
        payload = {
            "entry_id": "FACT-9999", "entry_type": "fact", "source_run_id": "run-source-1",
            "current_run_id": "run-b", "phase": "discovery", "decision": "x",
            "evidence_path": "runs/run-b/scope.json",
        }
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("does not resolve to a currently-valid fact entry" in e for e in errors)

    def test_run_summary_accurately_distinguishes_loaded_from_applied(self, memory_dir, repo_root):
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        run_directory = repo_root / "runs" / "run-b"
        run_directory.mkdir(parents=True)
        events_path = run_directory / "logs" / "policy-events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)

        # Load only -- no apply yet.
        events_path.write_text(json.dumps({"kind": "memory_loaded", "relevant_lesson_ids": ["L-0001"]}) + "\n", encoding="utf-8")
        loaded_only = memory.summarize_memory_events(run_directory)
        assert loaded_only == {"memory_loaded": True, "memory_influenced_run": False, "memory_refs_used": []}

        # Now a genuine apply happens later in the same run.
        (run_directory / "scope.json").write_text("{}", encoding="utf-8")
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "kind": "memory_applied", "entry_id": "L-0001", "entry_type": "lesson",
                "source_run_id": "run-source-1", "current_run_id": "run-b", "phase": "discovery",
                "decision": "used it", "evidence_path": "runs/run-b/scope.json",
            }) + "\n")
        loaded_and_applied = memory.summarize_memory_events(run_directory)
        assert loaded_and_applied == {"memory_loaded": True, "memory_influenced_run": True, "memory_refs_used": ["L-0001"]}


# ---------------------------------------------------------------------------
# Audit 1 -- memory_applied must come from THIS run's own loaded selection
# ---------------------------------------------------------------------------


class TestMemoryAppliedRunScopedSelection:
    def _payload(self, **overrides) -> dict:
        base = {
            "entry_id": "L-0001", "entry_type": "lesson", "source_run_id": "run-source-1",
            "current_run_id": "run-b", "phase": "discovery",
            "decision": "Applied lesson L-0001 to require early transport validation.",
            "evidence_path": "runs/run-b/scope.json",
        }
        base.update(overrides)
        return base

    def _seed(self, memory_dir, repo_root, run_directory, *, evidence_text: str) -> None:
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        memory.append_fact(memory_dir, _fact(), repo_root=repo_root)
        run_directory.mkdir(parents=True, exist_ok=True)
        (run_directory / "scope.json").write_text(evidence_text, encoding="utf-8")

    def test_a_selected_relevant_lesson_can_be_applied(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites L-0001 here")
        _retain_memory_loaded(run_directory, relevant_lesson_ids=["L-0001"])
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert errors == []

    def test_a_selected_relevant_fact_can_be_applied(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites FACT-0001 here")
        _retain_memory_loaded(run_directory, relevant_fact_ids=["FACT-0001"])
        payload = self._payload(entry_id="FACT-0001", entry_type="fact")
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert errors == []

    def test_existing_but_non_selected_entry_is_rejected(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites L-0001 here")
        # memory_loaded happened, but this lesson was never in its relevant set.
        _retain_memory_loaded(run_directory, relevant_lesson_ids=[])
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("was not selected as relevant in this run's own" in e for e in errors)

    def test_entry_selected_by_another_run_is_rejected(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites L-0001 here")
        # A DIFFERENT run's own memory_loaded event selected L-0001 -- irrelevant to run-b.
        other_run_directory = repo_root / "runs" / "run-other"
        _retain_memory_loaded(other_run_directory, relevant_lesson_ids=["L-0001"])
        # run-b itself never loaded memory at all.
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("no memory_loaded event found for this run" in e for e in errors)

    def test_fact_cannot_be_claimed_as_a_lesson(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites FACT-0001 here")
        # FACT-0001 was selected as a relevant FACT, never as a relevant LESSON.
        _retain_memory_loaded(run_directory, relevant_fact_ids=["FACT-0001"], relevant_lesson_ids=[])
        payload = self._payload(entry_id="FACT-0001", entry_type="lesson", evidence_path="runs/run-b/scope.json")
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        # Rejected on two independent grounds: FACT-0001 isn't a valid lesson id at all,
        # and even if it were, it was never in this run's relevant_lesson_ids.
        assert any("does not resolve to a currently-valid lesson entry" in e for e in errors)

    def test_lesson_cannot_be_claimed_as_a_fact(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites L-0001 here")
        _retain_memory_loaded(run_directory, relevant_lesson_ids=["L-0001"], relevant_fact_ids=[])
        payload = self._payload(entry_id="L-0001", entry_type="fact", evidence_path="runs/run-b/scope.json")
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("does not resolve to a currently-valid fact entry" in e for e in errors)

    def test_no_memory_loaded_event_means_application_is_rejected(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites L-0001 here")
        # No _retain_memory_loaded call at all -- the run never loaded memory.
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("no memory_loaded event found for this run" in e for e in errors)

    def test_a_second_memory_loaded_event_under_the_same_run_widens_the_eligible_set(self, memory_dir, repo_root):
        """A resumed invocation retains a SECOND memory_loaded event under the same
        run_id (work/SKILL.md's "Resuming an interrupted run" contract) -- an entry
        surfaced only by that second load is still eligible, since both loads genuinely
        happened within this run."""
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory, evidence_text="cites L-0001 here")
        _retain_memory_loaded(run_directory, relevant_lesson_ids=[])  # fresh invocation: nothing relevant
        _retain_memory_loaded(run_directory, relevant_lesson_ids=["L-0001"])  # resumed invocation's own load
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert errors == []


# ---------------------------------------------------------------------------
# Audit 2 -- decision evidence must cite the applied entry, not merely exist
# ---------------------------------------------------------------------------


class TestMemoryAppliedEvidenceCitesEntry:
    def _payload(self, **overrides) -> dict:
        base = {
            "entry_id": "L-0001", "entry_type": "lesson", "source_run_id": "run-source-1",
            "current_run_id": "run-b", "phase": "discovery",
            "decision": "Applied lesson L-0001 to require early transport validation.",
            "evidence_path": "runs/run-b/scope.json",
        }
        base.update(overrides)
        return base

    def _seed(self, memory_dir, repo_root, run_directory) -> None:
        memory.append_lessons(memory_dir, [_lesson()], repo_root=repo_root)
        run_directory.mkdir(parents=True, exist_ok=True)
        _retain_memory_loaded(run_directory, relevant_lesson_ids=["L-0001"])

    def test_evidence_containing_the_exact_id_passes(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        (run_directory / "scope.json").write_text(
            'constraints: ["Apply lesson L-0001 before Discovery."]', encoding="utf-8"
        )
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert errors == []

    def test_unrelated_existing_evidence_file_fails(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        (run_directory / "scope.json").write_text('{"objective": "totally unrelated"}', encoding="utf-8")
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("does not contain entry_id" in e for e in errors)

    def test_missing_evidence_file_fails(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        payload = self._payload(evidence_path="runs/run-b/never-written.json")
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("does not resolve to an existing, regular file" in e for e in errors)

    def test_directory_path_is_rejected_not_read_as_a_file(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        (run_directory / "a-directory").mkdir()
        payload = self._payload(evidence_path="runs/run-b/a-directory")
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("does not resolve to an existing, regular file" in e for e in errors)

    def test_outside_repository_path_fails(self, memory_dir, repo_root):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        payload = self._payload(evidence_path="../outside-repo.json")
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("safe, repo-relative path" in e for e in errors)

    def test_absolute_path_fails(self, memory_dir, repo_root, tmp_path):
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        outside_file = tmp_path / "outside.json"
        outside_file.write_text("L-0001", encoding="utf-8")
        payload = self._payload(evidence_path=str(outside_file))
        errors = memory.validate_memory_applied(
            payload, memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("safe, repo-relative path" in e for e in errors)

    def test_incomplete_id_substring_does_not_incorrectly_pass(self, memory_dir, repo_root):
        """entry_id "L-0001" must not be satisfied by evidence that only contains a
        longer id sharing it as a prefix (e.g. "L-00010") -- boundary-matched, not a
        bare substring search."""
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        (run_directory / "scope.json").write_text("mentions L-00010 but not the real id", encoding="utf-8")
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("does not contain entry_id" in e for e in errors)

    def test_id_as_prefix_of_evidence_word_does_not_incorrectly_pass(self, memory_dir, repo_root):
        """The reverse boundary case: entry_id "L-0001" must not be satisfied merely
        because it is a PREFIX of a longer token in the evidence file."""
        run_directory = repo_root / "runs" / "run-b"
        self._seed(memory_dir, repo_root, run_directory)
        (run_directory / "scope.json").write_text("mentions L-0001-EXTRA but not the exact id", encoding="utf-8")
        errors = memory.validate_memory_applied(
            self._payload(), memory_dir=memory_dir, repo_root=repo_root, run_directory=run_directory
        )
        assert any("does not contain entry_id" in e for e in errors)


# ---------------------------------------------------------------------------
# Checkpoint/resume interaction (Part 7)
# ---------------------------------------------------------------------------


class TestCheckpointResumeInteraction:
    def test_checkpoint_module_never_calls_memory_append_functions(self):
        """Structural proof that an interruption or a terminal-failure checkpoint write
        can never itself trigger a memory append: checkpoint.py has no reference to
        memory.py's append_fact/append_lessons at all -- appending is exclusively
        something the live /work skill's own terminal step calls, never a side effect
        of checkpoint persistence."""
        import inspect

        from harness.orchestrator import checkpoint

        source = inspect.getsource(checkpoint)
        assert "append_fact" not in source
        assert "append_lessons" not in source
        assert "import memory" not in source and "from . import memory" not in source

    def test_resumed_terminal_finalization_appends_lessons_at_most_once(self, memory_dir, repo_root):
        """Simulates a resumed process re-attempting the same terminal memory-append step
        (e.g. after a retried finalization) with the identical candidate list: duplicate
        suppression means the second attempt appends nothing new."""
        candidates = [_lesson()]
        first = memory.append_lessons(memory_dir, candidates, repo_root=repo_root)
        assert len(first.appended) == 1

        second = memory.append_lessons(memory_dir, candidates, repo_root=repo_root)
        assert second.appended == []
        assert "duplicate" in second.skipped[0]["reason"]

        loaded = memory.load_lessons(memory_dir, repo_root=repo_root)
        assert len(loaded.valid) == 1  # never duplicated on disk

    def test_retrying_finalization_does_not_duplicate_lessons_across_three_attempts(self, memory_dir, repo_root):
        candidates = [_lesson(id=f"L-{n:04d}", text=f"Retry-safe lesson {n} about harness checkpoint validation ordering.") for n in range(3)]
        totals = []
        for _ in range(3):
            result = memory.append_lessons(memory_dir, candidates, repo_root=repo_root)
            totals.append(len(result.appended))
        assert totals == [3, 0, 0]
        loaded = memory.load_lessons(memory_dir, repo_root=repo_root)
        assert len(loaded.valid) == 3

    def test_memory_load_events_remain_auditable_across_a_simulated_resume(self, memory_dir, repo_root):
        """Two separate load_memory calls against the same run directory (an original run
        and a later resumed process) must both remain visible in the retained event log --
        retain_policy_event is append-only and never overwrites a prior entry."""
        run_directory = repo_root / "runs" / "run-b"
        run_directory.mkdir(parents=True)
        events_path = run_directory / "logs" / "policy-events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)

        for attempt in ("original", "resumed"):
            with events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "memory_loaded", "attempt": attempt}) + "\n")

        lines = events_path.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
        assert [e["attempt"] for e in events if e["kind"] == "memory_loaded"] == ["original", "resumed"]


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------


class TestDeterministicHelpers:
    def test_normalize_text_is_case_and_punctuation_insensitive(self):
        assert memory.normalize_text("Hello,  World!!") == memory.normalize_text("hello world")

    def test_derive_keywords_is_deterministic_and_order_stable(self):
        text = "Transport JSON validation must occur before semantic Transport parsing."
        assert memory.derive_keywords(text) == memory.derive_keywords(text)
        keywords = memory.derive_keywords(text)
        assert keywords.count("transport") == 1  # de-duplicated, first-seen order

    def test_contains_secret_like_content_detects_known_patterns(self):
        assert memory.contains_secret_like_content("AWS key AKIAABCDEFGHIJKLMNOP") is True
        assert memory.contains_secret_like_content("a perfectly normal sentence") is False


# ---------------------------------------------------------------------------
# Regression proof: Run B's real, retained demonstration still validates under the
# stricter Audit 1/2 contract -- read-only against the real repository, never writes
# to or modifies runs/run-20260806-memoryloop-002/ or memory/ (retained evidence).
# ---------------------------------------------------------------------------


class TestRunBRealEvidenceStillValidatesUnderStricterContract:
    """runs/run-20260806-memoryloop-002/ is retained evidence from an earlier session --
    immutable, never rewritten here. This class only *reads* it and the real memory/
    directory to confirm the exact payload that run's own resp-05-record-memory-applied.json
    used would still be accepted by today's stricter validate_memory_applied (Audit 1's
    run-scoped selection check, Audit 2's evidence-cites-entry check) -- proving the
    contract tightening did not silently invalidate the milestone's own demonstration."""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    RUN_B_DIR = REPO_ROOT / "runs" / "run-20260806-memoryloop-002"
    MEMORY_DIR = REPO_ROOT / "memory"

    def test_real_repo_fixtures_present(self):
        """Guards the rest of this class: if these retained files were ever moved or
        deleted, fail loudly here rather than have the real proof below silently pass
        for the wrong reason (e.g. a vacuously-true check against missing files)."""
        assert self.RUN_B_DIR.is_dir(), f"expected retained evidence at {self.RUN_B_DIR}"
        assert (self.RUN_B_DIR / "scope.json").is_file()
        assert (self.RUN_B_DIR / "logs" / "policy-events.jsonl").is_file()
        assert (self.MEMORY_DIR / "lessons-learned.md").is_file()

    def test_run_bs_real_memory_applied_payload_still_validates(self):
        payload = {
            "entry_id": "L-20260806-TRANSPORT-BUDGET",
            "entry_type": "lesson",
            "source_run_id": "run-20260803-riskband-001",
            "current_run_id": "run-20260806-memoryloop-002",
            "phase": "discovery",
            "decision": (
                "scope.json's constraints and AC-1 require that a new staged agent's raw JSON "
                "transport format (no fence, no prose) is validated and, if malformed, handled as "
                "a transport failure BEFORE any semantic/schema validation of its content is "
                "attempted -- adopted directly from lesson L-20260806-TRANSPORT-BUDGET rather than "
                "decided independently."
            ),
            "evidence_path": "runs/run-20260806-memoryloop-002/scope.json",
        }
        errors = memory.validate_memory_applied(
            payload, memory_dir=self.MEMORY_DIR, repo_root=self.REPO_ROOT, run_directory=self.RUN_B_DIR
        )
        assert errors == [], f"Run B's real demonstration would now be rejected: {errors}"
