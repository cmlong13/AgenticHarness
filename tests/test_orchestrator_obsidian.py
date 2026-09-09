"""Tests for harness/orchestrator/obsidian.py -- deterministic Obsidian run-summary
publication.

Mirrors tests/test_orchestrator_github.py / tests/test_orchestrator_jira_connector.py:
a fake `VaultWriter` is injected at the exact seam `publish` accepts (per the module's
own docstring) -- no real vault is ever written by these tests except the ones that
deliberately point `OBSIDIAN_VAULT_PATH` at a pytest `tmp_path`. `DEFAULT_WRITER` (the
real filesystem write) is exercised only against those throwaway temp directories,
never a personal vault.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from harness.orchestrator import obsidian


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeWriter:
    """A controlled fake VaultWriter -- the exact seam `publish` names. Records every
    call and returns a scripted WriteOutcome."""

    def __init__(self, outcome: obsidian.WriteOutcome):
        self.outcome = outcome
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, target, content, overwrite) -> obsidian.WriteOutcome:
        self.calls.append((str(target), content, overwrite))
        return self.outcome


def _vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _destination(tmp_path, summary_dir: str = obsidian.DEFAULT_SUMMARY_DIR) -> obsidian.Destination:
    return obsidian.Destination(vault_path=_vault(tmp_path).resolve(), summary_dir=summary_dir)


_RUN_SUMMARY = {
    "schema_version": "1.0",
    "task_id": "T-1",
    "run_id": "run-1",
    "created_at": "2026-09-08T00:00:00Z",
    "objective_summary": "Fix the trailing-newline bug in validate_reference_id.",
    "artifact_refs": {
        "scope": "runs/run-1/scope.json",
        "findings": "runs/run-1/findings.json",
        "implementation_report": "runs/run-1/implementation-report.json",
        "verification_report": "runs/run-1/verification-report.json",
        "checkpoint": "runs/run-1/checkpoint.json",
    },
    "final_verdict": "pass",
    "phases_completed": ["discovery", "research", "implementation", "verification"],
}


# ---------------------------------------------------------------------------
# Destination resolution
# ---------------------------------------------------------------------------


class TestResolveDestination:
    def test_unset_vault_is_connector_unavailable(self):
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={})
        assert exc.value.code == "connector_unavailable"

    def test_blank_vault_is_connector_unavailable(self):
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": "   "})
        assert exc.value.code == "connector_unavailable"

    def test_relative_vault_path_is_invalid_destination(self):
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": "some/relative/dir"})
        assert exc.value.code == "invalid_destination"

    def test_traversal_in_vault_path_is_invalid_destination(self, tmp_path):
        target = f"{tmp_path}/../{tmp_path.name}"
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": target})
        assert exc.value.code == "invalid_destination"

    def test_nonexistent_vault_is_destination_unavailable(self, tmp_path):
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(tmp_path / "nope")})
        assert exc.value.code == "destination_unavailable"

    def test_file_not_dir_is_destination_unavailable(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(f)})
        assert exc.value.code == "destination_unavailable"

    def test_valid_vault_default_summary_dir(self, tmp_path):
        v = _vault(tmp_path)
        dest = obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(v)})
        assert dest.vault_path == v.resolve()
        assert dest.summary_dir == obsidian.DEFAULT_SUMMARY_DIR

    def test_custom_summary_dir_is_honored(self, tmp_path):
        v = _vault(tmp_path)
        dest = obsidian.resolve_destination(
            env={"OBSIDIAN_VAULT_PATH": str(v), "OBSIDIAN_SUMMARY_DIR": "Runs/Harness"}
        )
        assert dest.summary_dir == "Runs/Harness"

    def test_absolute_summary_dir_is_invalid_destination(self, tmp_path):
        v = _vault(tmp_path)
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(v), "OBSIDIAN_SUMMARY_DIR": "/etc"})
        assert exc.value.code == "invalid_destination"

    def test_traversal_summary_dir_is_invalid_destination(self, tmp_path):
        v = _vault(tmp_path)
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(v), "OBSIDIAN_SUMMARY_DIR": "../escape"})
        assert exc.value.code == "invalid_destination"

    def test_resolution_reads_fresh_env_each_call(self, tmp_path):
        v = _vault(tmp_path)
        env = {"OBSIDIAN_VAULT_PATH": str(v)}
        assert obsidian.resolve_destination(env=env).summary_dir == obsidian.DEFAULT_SUMMARY_DIR
        env["OBSIDIAN_SUMMARY_DIR"] = "Later"
        assert obsidian.resolve_destination(env=env).summary_dir == "Later"


# ---------------------------------------------------------------------------
# Note-name / path safety
# ---------------------------------------------------------------------------


class TestNoteNameAndPathSafety:
    def test_build_note_name_is_canonical(self):
        assert obsidian.build_note_name("run-20260908-obs-001") == "run-summary-run-20260908-obs-001.md"

    def test_safe_note_target_inside_vault(self, tmp_path):
        dest = _destination(tmp_path)
        target = obsidian.safe_note_target(dest, "run-summary-run-1.md")
        assert target.is_relative_to(dest.vault_path)
        assert target.name == "run-summary-run-1.md"

    @pytest.mark.parametrize(
        "bad_name",
        ["../evil.md", "sub/dir/note.md", "note.txt", ".hidden.md", "..md", "", "a" * 200 + ".md"],
    )
    def test_unsafe_note_names_rejected(self, tmp_path, bad_name):
        dest = _destination(tmp_path)
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.safe_note_target(dest, bad_name)
        assert exc.value.code == "invalid_destination"

    def test_summary_dir_traversal_caught_by_containment(self, tmp_path):
        # A Destination constructed directly (bypassing resolve_destination's lexical
        # check) with a traversal summary_dir is still caught by the resolved-path
        # containment check in safe_note_target.
        dest = obsidian.Destination(vault_path=_vault(tmp_path).resolve(), summary_dir="../outside")
        with pytest.raises(obsidian.ObsidianError) as exc:
            obsidian.safe_note_target(dest, "run-summary-run-1.md")
        assert exc.value.code == "invalid_destination"


# ---------------------------------------------------------------------------
# publish() -- classification via the fake writer seam
# ---------------------------------------------------------------------------


class TestPublish:
    def test_published_when_writer_succeeds(self, tmp_path):
        dest = _destination(tmp_path)
        content = "# note\nbody\n"
        writer = FakeWriter(obsidian.WriteOutcome(ok=True, absolute_path="/x/y.md", bytes_written=len(content.encode())))
        result = obsidian.publish(destination=dest, note_name="run-summary-run-1.md", content=content, writer=writer)
        assert result.status == "published"
        assert result.content_sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert result.note_relpath == f"{obsidian.DEFAULT_SUMMARY_DIR}/run-summary-run-1.md"
        assert writer.calls and writer.calls[0][2] is False  # overwrite defaulted False

    def test_invalid_destination_short_circuits_before_writer(self, tmp_path):
        dest = _destination(tmp_path)
        writer = FakeWriter(obsidian.WriteOutcome(ok=True, absolute_path="/x", bytes_written=1))
        result = obsidian.publish(destination=dest, note_name="../evil.md", content="x", writer=writer)
        assert result.status == "invalid_destination"
        assert writer.calls == []

    def test_destination_unavailable_when_vault_dir_missing(self, tmp_path):
        dest = obsidian.Destination(vault_path=tmp_path / "gone", summary_dir="d")
        writer = FakeWriter(obsidian.WriteOutcome(ok=True, absolute_path="/x", bytes_written=1))
        result = obsidian.publish(destination=dest, note_name="run-summary-run-1.md", content="x", writer=writer)
        assert result.status == "destination_unavailable"
        assert writer.calls == []

    def test_collision_when_note_exists_and_no_overwrite(self, tmp_path):
        dest = _destination(tmp_path)
        target = dest.vault_path / dest.summary_dir / "run-summary-run-1.md"
        target.parent.mkdir(parents=True)
        target.write_text("existing")
        writer = FakeWriter(obsidian.WriteOutcome(ok=True, absolute_path=str(target), bytes_written=1))
        result = obsidian.publish(destination=dest, note_name="run-summary-run-1.md", content="new", writer=writer)
        assert result.status == "collision"
        assert writer.calls == []
        assert target.read_text() == "existing"  # untouched

    def test_overwrite_true_allows_replacement(self, tmp_path):
        dest = _destination(tmp_path)
        target = dest.vault_path / dest.summary_dir / "run-summary-run-1.md"
        target.parent.mkdir(parents=True)
        target.write_text("existing")
        writer = FakeWriter(obsidian.WriteOutcome(ok=True, absolute_path=str(target), bytes_written=3))
        result = obsidian.publish(
            destination=dest, note_name="run-summary-run-1.md", content="new", writer=writer, overwrite=True
        )
        assert result.status == "published"
        assert writer.calls[0][2] is True

    def test_write_failed_on_writer_error(self, tmp_path):
        dest = _destination(tmp_path)
        writer = FakeWriter(obsidian.WriteOutcome(ok=False, absolute_path="/x", bytes_written=0, error="Permission denied"))
        result = obsidian.publish(destination=dest, note_name="run-summary-run-1.md", content="x", writer=writer)
        assert result.status == "write_failed"
        assert "Permission denied" in result.reason

    def test_writer_reported_collision_is_classified_collision(self, tmp_path):
        dest = _destination(tmp_path)
        writer = FakeWriter(obsidian.WriteOutcome(ok=False, absolute_path="/x", bytes_written=0, error="collision"))
        result = obsidian.publish(destination=dest, note_name="run-summary-run-1.md", content="x", writer=writer)
        assert result.status == "collision"

    def test_oversized_note_is_write_failed_before_writer(self, tmp_path):
        dest = _destination(tmp_path)
        writer = FakeWriter(obsidian.WriteOutcome(ok=True, absolute_path="/x", bytes_written=1))
        result = obsidian.publish(
            destination=dest, note_name="run-summary-run-1.md", content="x" * (64 * 1024 + 1), writer=writer
        )
        assert result.status == "write_failed"
        assert writer.calls == []


# ---------------------------------------------------------------------------
# DEFAULT_WRITER -- the real filesystem write, against a throwaway temp dir
# ---------------------------------------------------------------------------


class TestDefaultWriter:
    def test_real_write_into_temp_vault(self, tmp_path):
        dest = obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(_vault(tmp_path))})
        content = "# real note\n\n- ünïcödé ✅\n"
        result = obsidian.publish(destination=dest, note_name=obsidian.build_note_name("run-1"), content=content)
        assert result.status == "published"
        written = (dest.vault_path / dest.summary_dir / "run-summary-run-1.md").read_text(encoding="utf-8")
        assert written == content
        assert result.bytes_written == len(content.encode("utf-8"))

    def test_real_write_refuses_silent_clobber(self, tmp_path):
        dest = obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(_vault(tmp_path))})
        name = obsidian.build_note_name("run-1")
        assert obsidian.publish(destination=dest, note_name=name, content="one").status == "published"
        second = obsidian.publish(destination=dest, note_name=name, content="two")
        assert second.status == "collision"
        assert (dest.vault_path / dest.summary_dir / name).read_text() == "one"

    def test_default_writer_is_the_production_seam(self):
        # publish() with no writer= uses DEFAULT_WRITER -- the real filesystem write.
        # There is no request-level parameter anywhere that swaps it for a simulated one
        # outside a test that monkeypatches the module attribute directly.
        assert obsidian.DEFAULT_WRITER is obsidian._default_writer


# ---------------------------------------------------------------------------
# render_run_summary_note -- content, from evidence only
# ---------------------------------------------------------------------------


class TestRenderRunSummaryNote:
    def _render(self, **over):
        kw = dict(
            run_id="run-1", task_id="T-1", run_summary=dict(_RUN_SUMMARY),
            generated_at="2026-09-08T12:00:00Z",
        )
        kw.update(over)
        return obsidian.render_run_summary_note(**kw)

    def test_core_identity_and_verdict_present(self):
        note = self._render(scope={"source": {"type": "prompt"}})
        assert "run_id: run-1" in note
        assert "task_id: T-1" in note
        assert "**Final verdict:** pass" in note
        assert "**Source mode:** prompt" in note
        assert "Fix the trailing-newline bug" in note
        assert "discovery, research, implementation, verification" in note

    def test_source_mode_jira_when_scope_says_so(self):
        assert "**Source mode:** jira" in self._render(scope={"source": {"type": "jira"}})

    def test_source_mode_unknown_when_scope_absent(self):
        assert "**Source mode:** unknown" in self._render()

    def test_artifact_refs_rendered(self):
        note = self._render()
        assert "runs/run-1/findings.json" in note
        assert "runs/run-1/verification-report.json" in note

    def test_missing_optional_fields_do_not_fabricate(self):
        minimal = {
            "schema_version": "1.0", "task_id": "T-1", "run_id": "run-1",
            "created_at": "2026-09-08T00:00:00Z", "objective_summary": "x",
            "artifact_refs": {"scope": "runs/run-1/scope.json"},
            "final_verdict": "blocked", "phases_completed": ["discovery"],
        }
        note = self._render(run_summary=minimal)
        assert "no verification-report.json retained" in note
        assert "Memory influenced this run: no" in note
        assert "(no usage summary retained)" in note
        # nothing invented for jira / git
        assert "## Jira source" not in note
        assert "## Git delivery" not in note

    def test_tests_section_from_verification_report(self):
        vr = {
            "final_verdict": "pass",
            "attempts": [
                {"requested_command": {"command": "python -m pytest tests/unit/test_x.py -q"},
                 "exit_code": 0, "classification": None},
            ],
            "acceptance_criteria_results": [
                {"id": "AC-1", "status": "pass"}, {"id": "AC-2", "status": "pass"},
            ],
        }
        note = self._render(verification_report=vr)
        assert "python -m pytest tests/unit/test_x.py -q" in note
        assert "2/2 passed" in note
        assert "Verification verdict: pass" in note

    def test_jira_section_only_for_resolved_ticket_runs(self):
        note = self._render(
            jira_resolution={"status": "resolved", "issue": {"issue_key": "PROJ-7", "url": "https://x/browse/PROJ-7"}}
        )
        assert "## Jira source" in note
        assert "PROJ-7" in note
        # a non-resolved jira evidence doc adds nothing
        assert "## Jira source" not in self._render(jira_resolution={"status": "not_found", "issue": None})

    def test_git_section_only_when_push_verification_present(self):
        note = self._render(
            push_verification={"status": "verified", "observed_sha": "a" * 40, "expected_sha": "a" * 40}
        )
        assert "## Git delivery" in note
        assert "verified" in note
        assert "## Git delivery" not in self._render()

    def test_usage_section_notes_null_full_pipeline_total(self):
        note = self._render(
            run_summary={**_RUN_SUMMARY, "usage_summary_ref": "runs/run-1/usage-summary.json"},
            usage_summary={
                "subagent_subtotal": {"total_tokens": 12345, "cost_usd": "0.0421"},
                "coverage_status": "partial", "full_pipeline_total": None,
            },
        )
        assert "runs/run-1/usage-summary.json" in note
        assert "12345 tokens" in note
        assert "subtotal only" in note
        assert "Full pipeline total: None" in note
        assert "Orchestrator-side usage was not measured" in note

    def test_memory_influence_reported_from_summary_fields(self):
        note = self._render(
            run_summary={**_RUN_SUMMARY, "memory_influenced_run": True, "memory_refs_used": ["L-20260806-TRANSPORT-BUDGET"]}
        )
        assert "Memory influenced this run: yes" in note
        assert "L-20260806-TRANSPORT-BUDGET" in note

    def test_deterministic_output(self):
        assert self._render(scope={"source": {"type": "prompt"}}) == self._render(scope={"source": {"type": "prompt"}})

    def test_unicode_safe(self):
        note = self._render(run_summary={**_RUN_SUMMARY, "objective_summary": "Résumé du 日本語 — ✅"})
        assert "Résumé du 日本語 — ✅" in note
        note.encode("utf-8")  # must not raise

    def test_multiline_objective_collapsed_to_one_bullet_line(self):
        note = self._render(run_summary={**_RUN_SUMMARY, "objective_summary": "line one\nline two\nline three"})
        assert "**Objective:** line one line two line three" in note

    def test_no_secret_shaped_content_leaks(self):
        # Even if an upstream evidence doc somehow carried a raw/headers blob, the
        # renderer only reads whitelisted scalar fields -- it never walks a raw blob.
        note = self._render(
            jira_resolution={
                "status": "resolved",
                "issue": {"issue_key": "PROJ-7", "url": "https://x/browse/PROJ-7"},
                "raw": {"response": {"headers": {"Authorization": "Basic c2VjcmV0OnRva2Vu"}}},
            }
        )
        assert "Authorization" not in note
        assert "c2VjcmV0OnRva2Vu" not in note


# ---------------------------------------------------------------------------
# describe_destination -- evidence identity
# ---------------------------------------------------------------------------


class TestDescribeDestination:
    def test_success_identity_is_fully_described(self, tmp_path):
        dest = obsidian.resolve_destination(env={"OBSIDIAN_VAULT_PATH": str(_vault(tmp_path))})
        ident = obsidian.describe_destination("run-summary-run-1.md", destination=dest, env={"OBSIDIAN_VAULT_PATH": str(dest.vault_path)})
        d = ident.as_dict()
        assert d["configured"] is True
        assert d["vault_path"] == str(dest.vault_path)
        assert d["note_relpath"].endswith("run-summary-run-1.md")

    def test_failure_identity_records_raw_value_without_fabrication(self):
        ident = obsidian.describe_destination("run-summary-run-1.md", destination=None, env={"OBSIDIAN_VAULT_PATH": "/some/where"})
        d = ident.as_dict()
        assert d["configured"] is True
        assert d["raw_env_value"] == "/some/where"
        assert d["vault_path"] is None  # not fabricated

    def test_unconfigured_identity(self):
        d = obsidian.describe_destination("n.md", destination=None, env={}).as_dict()
        assert d["configured"] is False
        assert d["raw_env_value"] is None
