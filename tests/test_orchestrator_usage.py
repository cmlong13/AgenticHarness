"""Tests for harness/orchestrator/usage.py -- per-agent token usage capture,
identity mapping, Decimal pricing, and run usage summaries (usage/cost-accounting
milestone, 2026-08-14).

Covers: transcript parsing (exact structured categories, malformed-line skipping),
agent_id -> retained agent_dispatch identity matching (matched/unmatched/ambiguous),
Decimal-exact pricing (priced/unpriced), idempotent capture (duplicate/resumed
recompute-and-overwrite, never additive), quarantine of unmatched identities, the
PostToolUse:Agent corroboration sidecar, and run-usage-summary aggregation with its
explicit subagent-subtotal-vs-orchestrator-vs-full-pipeline coverage semantics.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from harness.orchestrator import evidence_io, usage

REAL_PRICING_PATH = Path(__file__).resolve().parent.parent / "harness" / "model-pricing.json"


def _assistant_line(
    *, model: str = "claude-sonnet-5", input_tokens: int = 2, output_tokens: int = 10,
    cache_creation_5m: int = 0, cache_creation_1h: int = 0, cache_read: int = 0,
    include_breakdown: bool = True, message_id: str | None = None,
) -> str:
    usage_obj = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
    }
    if include_breakdown:
        usage_obj["cache_creation_input_tokens"] = cache_creation_5m + cache_creation_1h
        usage_obj["cache_creation"] = {
            "ephemeral_5m_input_tokens": cache_creation_5m,
            "ephemeral_1h_input_tokens": cache_creation_1h,
        }
    else:
        usage_obj["cache_creation_input_tokens"] = cache_creation_5m + cache_creation_1h
    message = {"model": model, "usage": usage_obj}
    if message_id is not None:
        message["id"] = message_id
    return json.dumps({"type": "assistant", "agentId": "a1", "message": message})


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


class TestParseTranscriptUsage:
    def test_sums_across_multiple_assistant_lines(self, tmp_path):
        transcript = tmp_path / "agent-x.jsonl"
        transcript.write_text(
            "\n".join([
                _assistant_line(input_tokens=2, output_tokens=10, cache_creation_5m=1271, cache_read=19143),
                _assistant_line(input_tokens=6, output_tokens=112, cache_creation_5m=19143, cache_read=0),
            ]) + "\n",
            encoding="utf-8",
        )
        result = usage.parse_transcript_usage(transcript)
        assert result.message_count == 2
        assert result.counts.input_tokens == 8
        assert result.counts.output_tokens == 122
        assert result.counts.cache_creation_5m_tokens == 1271 + 19143
        assert result.counts.cache_read_tokens == 19143
        assert result.counts.cache_creation_input_tokens == 1271 + 19143
        assert result.models_seen == ["claude-sonnet-5"]

    def test_non_assistant_lines_are_not_anomalies(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            "\n".join([
                json.dumps({"type": "user", "message": {"content": "hi"}}),
                json.dumps({"type": "attachment", "attachment": {}}),
                _assistant_line(),
            ]) + "\n",
            encoding="utf-8",
        )
        result = usage.parse_transcript_usage(transcript)
        assert result.message_count == 1
        assert result.skipped_lines == []

    def test_malformed_json_line_is_skipped_with_reason(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("not json at all\n" + _assistant_line() + "\n", encoding="utf-8")
        result = usage.parse_transcript_usage(transcript)
        assert result.message_count == 1
        assert len(result.skipped_lines) == 1
        assert "malformed JSON" in result.skipped_lines[0]["reason"]

    def test_assistant_line_missing_usage_is_skipped(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-5"}}) + "\n",
            encoding="utf-8",
        )
        result = usage.parse_transcript_usage(transcript)
        assert result.message_count == 0
        assert len(result.skipped_lines) == 1
        assert "usage" in result.skipped_lines[0]["reason"]

    def test_assistant_line_missing_message_is_skipped(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(json.dumps({"type": "assistant"}) + "\n", encoding="utf-8")
        result = usage.parse_transcript_usage(transcript)
        assert result.message_count == 0
        assert "message" in result.skipped_lines[0]["reason"]

    def test_missing_transcript_file_raises(self, tmp_path):
        with pytest.raises(usage.TranscriptNotFoundError):
            usage.parse_transcript_usage(tmp_path / "does-not-exist.jsonl")

    def test_fallback_when_no_cache_creation_breakdown(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            _assistant_line(cache_creation_5m=500, include_breakdown=False) + "\n", encoding="utf-8",
        )
        result = usage.parse_transcript_usage(transcript)
        # Fallback attributes the whole cache_creation_input_tokens figure to the 5m bucket.
        assert result.counts.cache_creation_5m_tokens == 500
        assert result.counts.cache_creation_1h_tokens == 0

    def test_blank_lines_ignored(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("\n\n" + _assistant_line() + "\n\n", encoding="utf-8")
        result = usage.parse_transcript_usage(transcript)
        assert result.message_count == 1


# ---------------------------------------------------------------------------
# Identity mapping
# ---------------------------------------------------------------------------


class TestFindAgentDispatch:
    def _write_dispatch_event(self, run_directory, agent_id, *, phase="research", subagent_type="architect"):
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": phase, "subagent_type": subagent_type, "agent_id": agent_id, "dispatch_sequence": 1},
        )

    def test_unmatched_when_no_runs_directory(self, tmp_path):
        match = usage.find_agent_dispatch("agent-1", runs_root=tmp_path / "runs")
        assert match.status == "unmatched"

    def test_unmatched_when_no_run_dispatched_it(self, tmp_path):
        runs_root = tmp_path / "runs"
        self._write_dispatch_event(runs_root / "run-a", "some-other-agent")
        match = usage.find_agent_dispatch("agent-1", runs_root=runs_root)
        assert match.status == "unmatched"

    def test_matched_single_run(self, tmp_path):
        runs_root = tmp_path / "runs"
        self._write_dispatch_event(runs_root / "run-a", "agent-1", phase="implementation", subagent_type="engineer")
        match = usage.find_agent_dispatch("agent-1", runs_root=runs_root)
        assert match.status == "matched"
        assert match.run_id == "run-a"
        assert match.phase == "implementation"
        assert match.subagent_type == "engineer"
        assert match.dispatch_sequence == 1

    def test_ambiguous_across_two_runs(self, tmp_path):
        runs_root = tmp_path / "runs"
        self._write_dispatch_event(runs_root / "run-a", "agent-1")
        self._write_dispatch_event(runs_root / "run-b", "agent-1")
        match = usage.find_agent_dispatch("agent-1", runs_root=runs_root)
        assert match.status == "ambiguous"
        assert match.candidate_run_ids == ["run-a", "run-b"]

    def test_multiple_agents_same_role_stay_separate(self, tmp_path):
        runs_root = tmp_path / "runs"
        self._write_dispatch_event(runs_root / "run-a", "engineer-1", subagent_type="engineer")
        self._write_dispatch_event(runs_root / "run-a", "engineer-2", subagent_type="engineer")
        m1 = usage.find_agent_dispatch("engineer-1", runs_root=runs_root)
        m2 = usage.find_agent_dispatch("engineer-2", runs_root=runs_root)
        assert m1.status == "matched" and m2.status == "matched"
        assert m1.run_id == m2.run_id == "run-a"


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


class TestPricing:
    def test_loads_real_repository_pricing_file(self):
        pricing = usage.load_pricing(REAL_PRICING_PATH)
        assert "claude-sonnet-5" in pricing.models
        assert pricing.models["claude-sonnet-5"].input == Decimal("2.00")
        assert pricing.source
        assert pricing.verified_date == "2026-08-14"

    def test_price_usage_exact_decimal(self, tmp_path):
        pricing_path = tmp_path / "pricing.json"
        pricing_path.write_text(json.dumps({
            "_meta": {"source": "test", "verified_date": "2026-08-14"},
            "models": {"m1": {"input": "2.00", "output": "10.00", "cache_write_5m": "2.50", "cache_write_1h": "4.00", "cache_read": "0.20"}},
        }), encoding="utf-8")
        pricing = usage.load_pricing(pricing_path)
        counts = usage.TokenCounts(
            input_tokens=1_000_000, output_tokens=500_000,
            cache_creation_5m_tokens=1_000_000, cache_creation_1h_tokens=0, cache_read_tokens=2_000_000,
        )
        result = usage.price_usage("m1", counts, pricing)
        assert result.priced is True
        # 1*2.00 + 0.5*10.00 + 1*2.50 + 2*0.20 = 2.00 + 5.00 + 2.50 + 0.40 = 9.90
        assert result.amount == Decimal("9.90")

    def test_unknown_model_stays_unpriced(self, tmp_path):
        pricing_path = tmp_path / "pricing.json"
        pricing_path.write_text(json.dumps({"_meta": {}, "models": {}}), encoding="utf-8")
        pricing = usage.load_pricing(pricing_path)
        result = usage.price_usage("some-unrecognized-model", usage.TokenCounts(input_tokens=10), pricing)
        assert result.priced is False
        assert result.amount is None
        assert "unrecognized model" in result.reason

    def test_none_model_stays_unpriced(self):
        pricing = usage.PricingTable(models={}, source="", verified_date="")
        result = usage.price_usage(None, usage.TokenCounts(), pricing)
        assert result.priced is False
        assert "no model name" in result.reason

    def test_malformed_pricing_file_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(usage.PricingLoadError):
            usage.load_pricing(bad)

    def test_malformed_model_entry_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"_meta": {}, "models": {"m1": {"input": "2.00"}}}), encoding="utf-8")
        with pytest.raises(usage.PricingLoadError):
            usage.load_pricing(bad)


# ---------------------------------------------------------------------------
# capture_subagent_usage -- identity, idempotency, quarantine
# ---------------------------------------------------------------------------


class TestCaptureSubagentUsage:
    def _setup_run_with_dispatch(self, repo_root, run_id, agent_id):
        run_directory = evidence_io.run_dir(repo_root, run_id)
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": "research", "subagent_type": "architect", "agent_id": agent_id, "dispatch_sequence": 1},
        )
        return run_directory

    def _write_transcript(self, repo_root, name, lines):
        path = repo_root / "transcripts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _copy_real_pricing(self, repo_root):
        (repo_root / "harness").mkdir(parents=True, exist_ok=True)
        (repo_root / "harness" / "model-pricing.json").write_text(
            REAL_PRICING_PATH.read_text(encoding="utf-8"), encoding="utf-8",
        )

    def test_matched_identity_writes_priced_record(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        self._setup_run_with_dispatch(tmp_path, "run-1", "agent-1")
        transcript_path = self._write_transcript(tmp_path, "agent-1.jsonl", [
            _assistant_line(input_tokens=2, output_tokens=10, cache_creation_5m=1271, cache_read=19143),
        ])
        payload = {
            "hook_event_name": "SubagentStop", "agent_id": "agent-1",
            "agent_transcript_path": str(transcript_path), "agent_type": "architect",
        }
        result = usage.capture_subagent_usage(payload, repo_root=tmp_path)
        assert result["status"] == "captured"
        assert result["run_id"] == "run-1"

        record_path = usage.usage_record_path(evidence_io.run_dir(tmp_path, "run-1"), "agent-1")
        assert record_path.is_file()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["identity"]["match_status"] == "matched"
        assert record["identity"]["run_id"] == "run-1"
        assert record["identity"]["phase"] == "research"
        assert record["token_usage"]["input_tokens"] == 2
        assert record["token_usage"]["output_tokens"] == 10
        assert record["cost"]["priced"] is True

    def test_idempotent_duplicate_capture_produces_identical_record(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        self._setup_run_with_dispatch(tmp_path, "run-1", "agent-1")
        transcript_path = self._write_transcript(tmp_path, "agent-1.jsonl", [_assistant_line()])
        payload = {
            "hook_event_name": "SubagentStop", "agent_id": "agent-1", "agent_transcript_path": str(transcript_path),
        }
        r1 = usage.capture_subagent_usage(payload, repo_root=tmp_path, captured_at="2026-08-14T00:00:00Z")
        r2 = usage.capture_subagent_usage(payload, repo_root=tmp_path, captured_at="2026-08-14T00:00:00Z")
        record_path = usage.usage_record_path(evidence_io.run_dir(tmp_path, "run-1"), "agent-1")
        assert r1["status"] == r2["status"] == "captured"
        # Only one record file exists (overwritten, not appended/duplicated).
        assert len(list((evidence_io.run_dir(tmp_path, "run-1") / "usage").glob("*.json"))) == 1
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["token_usage"]["input_tokens"] == 2  # not doubled

    def test_resumed_agent_recomputes_full_total_not_additive(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        self._setup_run_with_dispatch(tmp_path, "run-1", "agent-1")
        transcript_path = self._write_transcript(tmp_path, "agent-1.jsonl", [
            _assistant_line(input_tokens=2, output_tokens=10),
        ])
        payload = {"hook_event_name": "SubagentStop", "agent_id": "agent-1", "agent_transcript_path": str(transcript_path)}
        usage.capture_subagent_usage(payload, repo_root=tmp_path)

        # Simulate a resume: the transcript file grows (append-only), a second
        # SubagentStop fires for the SAME agent_id.
        with transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(_assistant_line(input_tokens=6, output_tokens=112) + "\n")
        usage.capture_subagent_usage(payload, repo_root=tmp_path)

        record_path = usage.usage_record_path(evidence_io.run_dir(tmp_path, "run-1"), "agent-1")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        # Full recompute from the whole (now larger) file -- 2+6, 10+112 -- never
        # the first capture's total plus the second capture's total added again.
        assert record["token_usage"]["input_tokens"] == 8
        assert record["token_usage"]["output_tokens"] == 122
        assert record["message_count"] == 2
        # Still exactly one record for this agent_id -- no second agent record.
        assert len(list((evidence_io.run_dir(tmp_path, "run-1") / "usage").glob("*.json"))) == 1

    def test_two_real_agents_same_role_stay_separate_records(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        run_directory = self._setup_run_with_dispatch(tmp_path, "run-1", "engineer-1")
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": "implementation", "subagent_type": "engineer", "agent_id": "engineer-2", "dispatch_sequence": 1},
        )
        t1 = self._write_transcript(tmp_path, "e1.jsonl", [_assistant_line(input_tokens=1)])
        t2 = self._write_transcript(tmp_path, "e2.jsonl", [_assistant_line(input_tokens=99)])
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "engineer-1", "agent_transcript_path": str(t1)},
            repo_root=tmp_path,
        )
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "engineer-2", "agent_transcript_path": str(t2)},
            repo_root=tmp_path,
        )
        usage_dir = evidence_io.run_dir(tmp_path, "run-1") / "usage"
        assert len(list(usage_dir.glob("*.json"))) == 2
        r1 = json.loads((usage_dir / "engineer-1.json").read_text(encoding="utf-8"))
        r2 = json.loads((usage_dir / "engineer-2.json").read_text(encoding="utf-8"))
        assert r1["token_usage"]["input_tokens"] == 1
        assert r2["token_usage"]["input_tokens"] == 99

    def test_unmatched_identity_is_quarantined_not_dropped(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        transcript_path = self._write_transcript(tmp_path, "orphan.jsonl", [_assistant_line()])
        payload = {
            "hook_event_name": "SubagentStop", "agent_id": "orphan-agent", "agent_transcript_path": str(transcript_path),
        }
        result = usage.capture_subagent_usage(payload, repo_root=tmp_path, captured_at="2026-08-14T00:00:00Z")
        assert result["status"] == "quarantined"
        assert result["reason"] == "unmatched"
        quarantine_path = Path(result["path"])
        assert quarantine_path.is_file()
        record = json.loads(quarantine_path.read_text(encoding="utf-8"))
        assert record["identity"]["match_status"] == "unmatched"

    def test_ambiguous_identity_is_quarantined(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        self._setup_run_with_dispatch(tmp_path, "run-a", "dup-agent")
        self._setup_run_with_dispatch(tmp_path, "run-b", "dup-agent")
        transcript_path = self._write_transcript(tmp_path, "dup.jsonl", [_assistant_line()])
        result = usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "dup-agent", "agent_transcript_path": str(transcript_path)},
            repo_root=tmp_path, captured_at="2026-08-14T00:00:00Z",
        )
        assert result["status"] == "quarantined"
        assert result["reason"] == "ambiguous"

    def test_missing_agent_id_errors_without_raising(self, tmp_path):
        result = usage.capture_subagent_usage({"hook_event_name": "SubagentStop"}, repo_root=tmp_path)
        assert result["status"] == "error"

    def test_missing_transcript_path_errors_without_raising(self, tmp_path):
        result = usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "a"}, repo_root=tmp_path,
        )
        assert result["status"] == "error"

    def test_missing_transcript_file_errors_without_raising(self, tmp_path):
        result = usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_transcript_path": str(tmp_path / "nope.jsonl")},
            repo_root=tmp_path,
        )
        assert result["status"] == "error"

    def test_captured_agent_dispatch_event_is_retained(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        self._setup_run_with_dispatch(tmp_path, "run-1", "agent-1")
        transcript_path = self._write_transcript(tmp_path, "agent-1.jsonl", [_assistant_line()])
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "agent-1", "agent_transcript_path": str(transcript_path)},
            repo_root=tmp_path,
        )
        events_path = evidence_io.run_dir(tmp_path, "run-1") / "logs" / "policy-events.jsonl"
        kinds = [json.loads(l)["kind"] for l in events_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert "agent_usage_captured" in kinds


# ---------------------------------------------------------------------------
# PostToolUse:Agent corroboration
# ---------------------------------------------------------------------------


class TestCorroboration:
    def test_records_corroboration_sidecar(self, tmp_path):
        payload = {
            "hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": "toolu_01",
            "tool_response": {
                "agentId": "agent-1", "resolvedModel": "claude-sonnet-5",
                "usage": {"input_tokens": 2, "output_tokens": 10}, "totalTokens": 20426,
            },
        }
        result = usage.record_post_tool_use_corroboration(payload, repo_root=tmp_path, captured_at="2026-08-14T00:00:00Z")
        assert result["status"] == "recorded"
        path = usage.corroboration_record_path(tmp_path, "agent-1")
        assert path.is_file()
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["resolved_model"] == "claude-sonnet-5"
        assert record["reported_usage"]["input_tokens"] == 2
        # Never a source of authoritative billing -- field name says so explicitly.
        assert record["reported_total_tokens_UNBILLED_DO_NOT_USE"] == 20426

    def test_missing_tool_response_is_skipped(self, tmp_path):
        result = usage.record_post_tool_use_corroboration(
            {"hook_event_name": "PostToolUse", "tool_name": "Agent"}, repo_root=tmp_path,
        )
        assert result["status"] == "skipped"

    def test_missing_agent_id_is_skipped(self, tmp_path):
        result = usage.record_post_tool_use_corroboration(
            {"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_response": {}}, repo_root=tmp_path,
        )
        assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Run usage summary -- coverage semantics
# ---------------------------------------------------------------------------


class TestBuildRunUsageSummary:
    def _capture(self, repo_root, run_id, agent_id, phase, subagent_type, tokens_line):
        run_directory = evidence_io.run_dir(repo_root, run_id)
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": phase, "subagent_type": subagent_type, "agent_id": agent_id, "dispatch_sequence": 1},
        )
        (repo_root / "harness").mkdir(parents=True, exist_ok=True)
        (repo_root / "harness" / "model-pricing.json").write_text(
            REAL_PRICING_PATH.read_text(encoding="utf-8"), encoding="utf-8",
        )
        transcript_path = repo_root / "transcripts" / f"{agent_id}.jsonl"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(tokens_line + "\n", encoding="utf-8")
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": agent_id, "agent_transcript_path": str(transcript_path)},
            repo_root=repo_root,
        )

    def test_empty_run_has_zero_subtotal_and_partial_coverage(self, tmp_path):
        summary = usage.build_run_usage_summary("run-empty", repo_root=tmp_path)
        assert summary["subagent_subtotal"]["agent_count"] == 0
        assert summary["coverage_status"] == "partial"
        assert summary["full_pipeline_total"] is None
        assert summary["orchestrator"]["status"] == "not_measured"

    def test_aggregates_multiple_priced_agents(self, tmp_path):
        self._capture(tmp_path, "run-1", "arch-1", "research", "architect", _assistant_line(input_tokens=10, output_tokens=5))
        self._capture(tmp_path, "run-1", "eng-1", "implementation", "engineer", _assistant_line(input_tokens=20, output_tokens=15))
        summary = usage.build_run_usage_summary("run-1", repo_root=tmp_path)
        assert summary["subagent_subtotal"]["agent_count"] == 2
        assert summary["subagent_subtotal"]["token_usage"]["input_tokens"] == 30
        assert summary["subagent_subtotal"]["token_usage"]["output_tokens"] == 20
        assert summary["subagent_subtotal"]["cost"]["priced"] is True
        assert Decimal(summary["subagent_subtotal"]["cost"]["amount"]) > 0
        # Never presented as a full pipeline figure.
        assert summary["full_pipeline_total"] is None
        assert summary["coverage_status"] == "partial"

    def test_unpriced_agent_marks_subtotal_partial(self, tmp_path):
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": "research", "subagent_type": "architect", "agent_id": "arch-1", "dispatch_sequence": 1},
        )
        (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
        (tmp_path / "harness" / "model-pricing.json").write_text(
            json.dumps({"_meta": {}, "models": {}}), encoding="utf-8",
        )
        transcript_path = tmp_path / "t.jsonl"
        transcript_path.write_text(_assistant_line(model="some-unrecognized-model") + "\n", encoding="utf-8")
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "arch-1", "agent_transcript_path": str(transcript_path)},
            repo_root=tmp_path,
        )
        summary = usage.build_run_usage_summary("run-1", repo_root=tmp_path)
        assert summary["subagent_subtotal"]["cost"]["priced"] is False
        assert "arch-1" in summary["subagent_subtotal"]["cost"]["unpriced_agent_ids"]

    def test_write_run_usage_summary_writes_file(self, tmp_path):
        self._capture(tmp_path, "run-1", "arch-1", "research", "architect", _assistant_line())
        path = usage.write_run_usage_summary("run-1", repo_root=tmp_path)
        assert path.is_file()
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["run_id"] == "run-1"

    def test_write_run_usage_summary_is_idempotent_overwrite(self, tmp_path):
        self._capture(tmp_path, "run-1", "arch-1", "research", "architect", _assistant_line())
        path1 = usage.write_run_usage_summary("run-1", repo_root=tmp_path)
        path2 = usage.write_run_usage_summary("run-1", repo_root=tmp_path)
        assert path1 == path2
        assert path1.is_file()


# ---------------------------------------------------------------------------
# Reconciling a quarantined record once late-arriving dispatch evidence exists
# ---------------------------------------------------------------------------


class TestReconcileQuarantinedUsage:
    def _copy_real_pricing(self, repo_root):
        (repo_root / "harness").mkdir(parents=True, exist_ok=True)
        (repo_root / "harness" / "model-pricing.json").write_text(
            REAL_PRICING_PATH.read_text(encoding="utf-8"), encoding="utf-8",
        )

    def test_no_quarantine_record_is_reported_honestly(self, tmp_path):
        result = usage.reconcile_quarantined_usage("never-quarantined", repo_root=tmp_path)
        assert result["status"] == "no_quarantine_record"

    def test_reconciles_once_dispatch_evidence_arrives(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        transcript_path = tmp_path / "transcripts" / "agent-1.jsonl"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(_assistant_line(input_tokens=2, output_tokens=10) + "\n", encoding="utf-8")

        # Simulates the real observed ordering: SubagentStop fires (and quarantines)
        # BEFORE the orchestrator's own turn resumes to retain the agent_dispatch event.
        quarantine_result = usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "agent-1", "agent_transcript_path": str(transcript_path)},
            repo_root=tmp_path, captured_at="2026-08-14T00:00:00Z",
        )
        assert quarantine_result["status"] == "quarantined"
        quarantine_path = Path(quarantine_result["path"])
        assert quarantine_path.is_file()

        # The orchestrator's very next real step: retain the dispatch identity.
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        evidence_io.ensure_run_dirs(run_directory)
        evidence_io.retain_policy_event(
            run_directory, "agent_dispatch",
            {"phase": "research", "subagent_type": "architect", "agent_id": "agent-1", "dispatch_sequence": 1},
        )

        reconcile_result = usage.reconcile_quarantined_usage("agent-1", repo_root=tmp_path)
        assert reconcile_result["status"] == "captured"
        assert reconcile_result["run_id"] == "run-1"

        record_path = usage.usage_record_path(run_directory, "agent-1")
        assert record_path.is_file()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["identity"]["match_status"] == "matched"
        assert record["token_usage"]["input_tokens"] == 2

        # The original quarantine record is consumed (renamed, not deleted) --
        # evidence is preserved, but the stale unmatched copy no longer looks live.
        assert not quarantine_path.exists()
        assert Path(reconcile_result["reconciled_from_quarantine"]).is_file()

    def test_still_unmatched_leaves_quarantine_record_in_place(self, tmp_path):
        self._copy_real_pricing(tmp_path)
        transcript_path = tmp_path / "t.jsonl"
        transcript_path.write_text(_assistant_line() + "\n", encoding="utf-8")
        usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "orphan", "agent_transcript_path": str(transcript_path)},
            repo_root=tmp_path, captured_at="2026-08-14T00:00:00Z",
        )
        # No agent_dispatch event is ever written -- reconciliation should still fail.
        result = usage.reconcile_quarantined_usage("orphan", repo_root=tmp_path)
        assert result["status"] == "quarantined"
        quarantine_dir = tmp_path / "runs" / usage.UNMATCHED_USAGE_DIRNAME
        # The original quarantine record is untouched (never renamed/consumed on a
        # failed reconciliation) -- the retry attempt is retained as its own record
        # rather than overwriting it, per capture_subagent_usage's own retry contract.
        remaining = list(quarantine_dir.glob("orphan-*.json"))
        assert len(remaining) == 2
        assert all(not name.endswith(".reconciled") for name in (p.name for p in remaining))


# ---------------------------------------------------------------------------
# Accounting failure must never corrupt pipeline correctness (integration
# audit, 2026-08-14, requirement #3)
# ---------------------------------------------------------------------------


class TestAccountingFailureNeverCorruptsPipelineResult:
    """Usage/cost accounting is evidence, not verdict (work/SKILL.md standing rule 16).
    These tests prove -- at the code level, not just by prose assertion -- that a broken,
    quarantined, or errored usage capture leaves every canonical pipeline artifact
    (verification-report.json, run-summary.json's final_verdict, checkpoint.json)
    byte-for-byte untouched. usage.py never opens, parses, or writes any of them."""

    def _write_passing_verification_report(self, run_directory):
        run_directory.mkdir(parents=True, exist_ok=True)
        doc = {
            "schema_version": "1.0", "task_id": "T-1", "run_id": "run-1",
            "final_verdict": "pass", "attempts": [], "acceptance_criteria_results": [],
        }
        path = run_directory / "verification-report.json"
        raw = json.dumps(doc, indent=2)
        path.write_text(raw, encoding="utf-8")
        return path, raw

    def test_quarantined_capture_leaves_verification_report_untouched(self, tmp_path):
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        report_path, original_raw = self._write_passing_verification_report(run_directory)

        transcript_path = tmp_path / "t.jsonl"
        transcript_path.write_text(_assistant_line() + "\n", encoding="utf-8")
        (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
        (tmp_path / "harness" / "model-pricing.json").write_text(
            REAL_PRICING_PATH.read_text(encoding="utf-8"), encoding="utf-8",
        )
        # No agent_dispatch event exists for this run -- capture is guaranteed to quarantine.
        result = usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "never-dispatched", "agent_transcript_path": str(transcript_path)},
            repo_root=tmp_path,
        )
        assert result["status"] == "quarantined"
        assert report_path.read_text(encoding="utf-8") == original_raw

    def test_capture_error_leaves_verification_report_untouched(self, tmp_path):
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        report_path, original_raw = self._write_passing_verification_report(run_directory)

        # Missing transcript file -> capture_subagent_usage returns status "error".
        result = usage.capture_subagent_usage(
            {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_transcript_path": str(tmp_path / "missing.jsonl")},
            repo_root=tmp_path,
        )
        assert result["status"] == "error"
        assert report_path.read_text(encoding="utf-8") == original_raw

    def test_reconcile_failure_leaves_verification_report_untouched(self, tmp_path):
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        report_path, original_raw = self._write_passing_verification_report(run_directory)

        result = usage.reconcile_quarantined_usage("agent-with-no-quarantine-record", repo_root=tmp_path)
        assert result["status"] == "no_quarantine_record"
        assert report_path.read_text(encoding="utf-8") == original_raw

    def test_build_usage_summary_never_touches_canonical_artifacts(self, tmp_path):
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        report_path, original_raw = self._write_passing_verification_report(run_directory)
        summary_doc = {
            "schema_version": "1.0", "task_id": "T-1", "run_id": "run-1",
            "created_at": "2026-08-14T00:00:00Z", "objective_summary": "x",
            "artifact_refs": {"scope": "runs/run-1/scope.json"},
            "final_verdict": "pass", "phases_completed": ["discovery"],
        }
        run_summary_path = run_directory / "run-summary.json"
        run_summary_raw = json.dumps(summary_doc, indent=2)
        run_summary_path.write_text(run_summary_raw, encoding="utf-8")

        usage.build_run_usage_summary("run-1", repo_root=tmp_path)  # read-only variant
        assert report_path.read_text(encoding="utf-8") == original_raw
        assert run_summary_path.read_text(encoding="utf-8") == run_summary_raw

        usage.write_run_usage_summary("run-1", repo_root=tmp_path)  # writing variant
        assert report_path.read_text(encoding="utf-8") == original_raw
        assert run_summary_path.read_text(encoding="utf-8") == run_summary_raw
        # write_run_usage_summary writes only its own file, never final_verdict/run-summary.
        assert (run_directory / "usage-summary.json").is_file()

    def test_run_with_incomplete_accounting_can_still_be_a_genuine_pass(self, tmp_path):
        """A run whose usage accounting is incomplete (quarantined/ambiguous/error) must
        remain free to report final_verdict: "pass" -- accounting coverage and pipeline
        correctness are independent claims (work/SKILL.md standing rule 16). This test
        proves the aggregation function itself never inspects, requires, or reports on
        final_verdict/verification-report.json at all -- there is no code path by which
        it could veto a pass."""
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        self._write_passing_verification_report(run_directory)
        evidence_io.ensure_run_dirs(run_directory)
        # Retain a real usage_accounting_gap-shaped event, exactly as work/SKILL.md's
        # own "Per-agent usage accounting" protocol would for a still-quarantined agent.
        evidence_io.retain_policy_event(
            run_directory, "usage_accounting_gap",
            {"phase": "implementation", "agent_id": "eng-1", "status": "quarantined"},
        )
        summary = usage.build_run_usage_summary("run-1", repo_root=tmp_path)
        # The summary neither knows about nor reports on final_verdict -- confirmed by
        # its own key set never including it.
        assert "final_verdict" not in summary
        assert summary["coverage_status"] == "partial"


# ---------------------------------------------------------------------------
# Per-API-call deduplication (remediation, 2026-09-29): one API response is
# written as several assistant lines sharing a message.id
# ---------------------------------------------------------------------------


class TestPerMessageIdDeduplication:
    def _write(self, tmp_path, lines):
        path = tmp_path / "t.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_multi_block_response_is_one_call_last_line_wins(self, tmp_path):
        # Real shape: thinking, tool_use, tool_use lines of ONE call; input/cache
        # repeated on every line, output_tokens final only on the last line.
        path = self._write(tmp_path, [
            _assistant_line(message_id="msg_1", input_tokens=2, output_tokens=8, cache_creation_5m=14628),
            _assistant_line(message_id="msg_1", input_tokens=2, output_tokens=8, cache_creation_5m=14628),
            _assistant_line(message_id="msg_1", input_tokens=2, output_tokens=257, cache_creation_5m=14628),
        ])
        result = usage.parse_transcript_usage(path)
        assert result.message_count == 1
        assert result.assistant_line_count == 3
        assert result.counts.input_tokens == 2
        assert result.counts.output_tokens == 257
        assert result.counts.cache_creation_5m_tokens == 14628
        assert result.message_ids == ["msg_1"]

    def test_distinct_ids_are_separate_calls(self, tmp_path):
        path = self._write(tmp_path, [
            _assistant_line(message_id="msg_1", input_tokens=2, output_tokens=10, cache_read=100),
            _assistant_line(message_id="msg_2", input_tokens=3, output_tokens=20, cache_read=200),
        ])
        result = usage.parse_transcript_usage(path)
        assert result.message_count == 2
        assert result.counts.input_tokens == 5
        assert result.counts.output_tokens == 30
        assert result.counts.cache_read_tokens == 300

    def test_resumed_turn_appending_to_same_file_counts_each_call_once(self, tmp_path):
        # A route-back / SendMessage resume appends new calls to the same file; the
        # original calls must not be counted again.
        lines = [_assistant_line(message_id="msg_1", output_tokens=5), _assistant_line(message_id="msg_1", output_tokens=50)]
        first = usage.parse_transcript_usage(self._write(tmp_path, lines))
        lines += [_assistant_line(message_id="msg_2", output_tokens=7), _assistant_line(message_id="msg_2", output_tokens=70)]
        second = usage.parse_transcript_usage(self._write(tmp_path, lines))
        assert first.counts.output_tokens == 50
        assert second.counts.output_tokens == 120
        assert second.message_count == 2

    def test_lines_without_id_are_counted_and_flagged(self, tmp_path):
        path = self._write(tmp_path, [_assistant_line(), _assistant_line()])
        result = usage.parse_transcript_usage(path)
        assert result.message_count == 2
        assert result.lines_without_message_id == 2

    def test_synthetic_messages_are_not_api_calls(self, tmp_path):
        path = self._write(tmp_path, [
            _assistant_line(model=usage.SYNTHETIC_MODEL, message_id="syn", input_tokens=0, output_tokens=0),
            _assistant_line(message_id="msg_1"),
        ])
        result = usage.parse_transcript_usage(path)
        assert result.message_count == 1
        assert result.synthetic_message_count == 1
        assert result.models_seen == ["claude-sonnet-5"]

    def test_usage_missing_required_fields_is_skipped_not_zeroed(self, tmp_path):
        line = json.dumps({"type": "assistant", "message": {
            "id": "msg_1", "model": "claude-sonnet-5", "usage": {"cache_read_input_tokens": 10},
        }})
        result = usage.parse_transcript_usage(self._write(tmp_path, [line]))
        assert result.message_count == 0
        assert "input_tokens" in result.skipped_lines[0]["reason"]

    def test_counts_are_split_by_model(self, tmp_path):
        path = self._write(tmp_path, [
            _assistant_line(message_id="m1", model="claude-sonnet-5", input_tokens=1, output_tokens=2),
            _assistant_line(message_id="m2", model="claude-haiku-4-5-20251001", input_tokens=3, output_tokens=4),
        ])
        result = usage.parse_transcript_usage(path)
        assert result.counts_by_model["claude-sonnet-5"].output_tokens == 2
        assert result.counts_by_model["claude-haiku-4-5-20251001"].input_tokens == 3


class TestPerModelPricing:
    def _pricing(self, tmp_path, **extra):
        path = tmp_path / "pricing.json"
        path.write_text(json.dumps({
            "_meta": {"source": "test", "verified_date": "2026-09-29"},
            "models": {"m-a": {"input": "1", "output": "10", "cache_write_5m": "1.25", "cache_write_1h": "2", "cache_read": "0.1"}},
            **extra,
        }), encoding="utf-8")
        return usage.load_pricing(path)

    def test_each_model_priced_at_its_own_rate(self):
        pricing = usage.load_pricing(REAL_PRICING_PATH)
        cost = usage.price_counts_by_model({
            "claude-sonnet-5": usage.TokenCounts(input_tokens=1_000_000),
            "claude-haiku-4-5": usage.TokenCounts(input_tokens=1_000_000),
        }, pricing)
        assert cost.priced is True
        assert cost.amount == Decimal("3.00")  # $2 + $1

    def test_one_unknown_model_makes_whole_cost_unknown_not_partial(self):
        pricing = usage.load_pricing(REAL_PRICING_PATH)
        cost = usage.price_counts_by_model({
            "claude-sonnet-5": usage.TokenCounts(input_tokens=1_000_000),
            "mystery-model": usage.TokenCounts(input_tokens=1),
        }, pricing)
        assert cost.priced is False
        assert cost.amount is None
        assert "mystery-model" in cost.reason

    def test_alias_resolves_to_configured_key_only(self, tmp_path):
        pricing = self._pricing(tmp_path, aliases={"m-a-20250101": "m-a"})
        assert usage.price_usage("m-a-20250101", usage.TokenCounts(output_tokens=1_000_000), pricing).amount == Decimal("10")
        # No prefix/fuzzy matching.
        assert usage.price_usage("m-a-20990101", usage.TokenCounts(output_tokens=1), pricing).priced is False

    def test_alias_to_unknown_key_is_a_load_error(self, tmp_path):
        with pytest.raises(usage.PricingLoadError):
            self._pricing(tmp_path, aliases={"x": "not-a-model"})

    def test_real_pricing_prices_dated_haiku_id(self):
        pricing = usage.load_pricing(REAL_PRICING_PATH)
        # Reproduces the runtime's own costUSD observed 2026-09-29 for this exact usage.
        counts = usage.TokenCounts(input_tokens=10, output_tokens=37, cache_creation_5m_tokens=16625)
        assert usage.price_usage("claude-haiku-4-5-20251001", counts, pricing).amount == Decimal("0.02097625")


class TestLegacyRecordsExcludedFromSubtotal:
    def test_record_from_old_parser_is_listed_not_summed(self, tmp_path):
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        (run_directory / "usage").mkdir(parents=True)
        (run_directory / "usage" / "old.json").write_text(json.dumps({
            "record_kind": "agent_usage", "agent_id": "old",
            "token_usage": {"input_tokens": 999, "output_tokens": 999},
            "cost": {"priced": True, "amount": "9.99"},
        }), encoding="utf-8")
        summary = usage.build_run_usage_summary("run-1", repo_root=tmp_path)
        sub = summary["subagent_subtotal"]
        assert sub["agent_count"] == 0
        assert sub["token_usage"]["input_tokens"] == 0
        assert sub["cost"]["legacy_undeduplicated_agent_ids"] == ["old"]
        assert sub["cost"]["priced"] is False


# ---------------------------------------------------------------------------
# Full pipeline total over one dedicated headless session
# ---------------------------------------------------------------------------

SESSION_ID = "11111111-2222-3333-4444-555555555555"


class TestFinalizePipelineUsage:
    """Builds a fake dedicated session on disk: <sid>.jsonl (orchestrator) plus
    <sid>/subagents/agent-<id>.jsonl, a run with agent_dispatch events, and a
    runtime result whose modelUsage is supplied per test."""

    def _setup(self, tmp_path, *, agents, orchestrator_lines, dispatched=None):
        (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
        (tmp_path / "harness" / "model-pricing.json").write_text(
            REAL_PRICING_PATH.read_text(encoding="utf-8"), encoding="utf-8",
        )
        run_directory = evidence_io.run_dir(tmp_path, "run-1")
        evidence_io.ensure_run_dirs(run_directory)
        roles = {"arch": ("research", "architect"), "eng": ("implementation", "engineer"),
                 "qe": ("verification", "quality-engineer"), "qe2": ("verification", "quality-engineer")}
        for seq, agent_id in enumerate(dispatched if dispatched is not None else agents, start=1):
            phase, subagent_type = roles[agent_id]
            evidence_io.retain_policy_event(run_directory, "agent_dispatch", {
                "phase": phase, "subagent_type": subagent_type, "agent_id": agent_id, "dispatch_sequence": seq,
            })
        sessions = tmp_path / "sessions"
        main = sessions / f"{SESSION_ID}.jsonl"
        main.parent.mkdir(parents=True)
        main.write_text(
            json.dumps({"type": "user", "message": {"content": "/work Add a thing"}}) + "\n"
            + "\n".join(orchestrator_lines) + "\n", encoding="utf-8",
        )
        sub_dir = sessions / SESSION_ID / "subagents"
        sub_dir.mkdir(parents=True)
        for agent_id, lines in agents.items():
            (sub_dir / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return main

    @staticmethod
    def _runtime(model_usage, cost=None):
        return {"type": "result", "subtype": "success", "session_id": SESSION_ID,
                "total_cost_usd": cost, "modelUsage": model_usage}

    @staticmethod
    def _mu(i, o, cr, cc):
        return {"inputTokens": i, "outputTokens": o, "cacheReadInputTokens": cr, "cacheCreationInputTokens": cc}

    def _full_pipeline(self, tmp_path):
        """Orchestrator + Architect + Engineer (with a repair-round resume in the same
        file) + QE + a second QE dispatch (fresh retry identity)."""
        line = _assistant_line
        agents = {
            "arch": [line(message_id="a1", output_tokens=5), line(message_id="a1", output_tokens=10)],
            "eng": [line(message_id="e1", output_tokens=20),
                    line(message_id="e2", output_tokens=30)],  # e2 = repair-round turn, same agent id
            "qe": [line(message_id="q1", output_tokens=40)],
            "qe2": [line(message_id="q2", output_tokens=50)],
        }
        orch = [line(message_id="o1", input_tokens=4, output_tokens=100, cache_creation_1h=1000, cache_read=5000),
                line(message_id="o1", input_tokens=4, output_tokens=300, cache_creation_1h=1000, cache_read=5000)]
        main = self._setup(tmp_path, agents=agents, orchestrator_lines=orch)
        # Each subagent call: input 2; orchestrator: input 4.
        return main, self._mu(4 + 2 * 5, 300 + 10 + 20 + 30 + 40 + 50, 5000, 1000)

    def test_complete_session_reconciles_exactly_and_states_total(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                          runtime_result=self._runtime({"claude-sonnet-5": mu}))
        assert s["reconciliation"]["status"] == "exact"
        assert s["coverage_status"] == "complete"
        assert s["orchestrator"]["status"] == "measured"
        assert s["orchestrator"]["api_call_count"] == 1
        assert s["orchestrator"]["token_usage"]["output_tokens"] == 300
        roles = [(c["component"], c.get("agent_id"), c.get("api_call_count")) for c in s["components"]]
        assert roles == [("orchestrator", None, 1), ("architect", "arch", 1), ("engineer", "eng", 2),
                         ("quality-engineer", "qe", 1), ("quality-engineer", "qe2", 1)]
        total = s["full_pipeline_total"]
        assert total["token_usage"] == {"input_tokens": 14, "output_tokens": 450,
                                        "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 1000}
        # Decimal total equals the sum of the per-component costs, exactly.
        assert Decimal(total["cost"]["amount"]) == sum(Decimal(c["cost"]["amount"]) for c in s["components"])
        # 14*2 + 450*10 + 5000*0.2 + 1000*4 (1h write) per MTok
        assert Decimal(total["cost"]["amount"]) == Decimal("0.009528")
        assert total["cost"]["status"] == "priced"

    def test_corroboration_and_quarantine_records_never_add_to_total(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        before = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                               runtime_result=self._runtime({"claude-sonnet-5": mu}))
        # Hook-side sidecars for the same agents, plus a captured canonical record.
        for agent_id in ("arch", "eng"):
            usage.record_post_tool_use_corroboration(
                {"tool_response": {"agentId": agent_id, "usage": {"input_tokens": 999}}}, repo_root=tmp_path)
        quarantine = tmp_path / "runs" / usage.UNMATCHED_USAGE_DIRNAME / "arch-x.json"
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        quarantine.write_text(json.dumps({"record_kind": "agent_usage", "token_usage": {"input_tokens": 999}}))
        usage.capture_subagent_usage({"hook_event_name": "SubagentStop", "agent_id": "arch",
                                      "agent_transcript_path": str(main.with_suffix("") / "subagents" / "agent-arch.jsonl")},
                                     repo_root=tmp_path)
        after = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                              runtime_result=self._runtime({"claude-sonnet-5": mu}))
        assert after["full_pipeline_total"] == before["full_pipeline_total"]
        arch = next(c for c in after["components"] if c.get("agent_id") == "arch")
        assert arch["hook_record_matches"] is True

    def test_runtime_residual_without_cache_creation_is_priced_and_included(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main, runtime_result=self._runtime({
            "claude-sonnet-5": mu, "claude-haiku-4-5-20251001": self._mu(1000, 10, 0, 0),
        }))
        assert s["reconciliation"]["status"] == "runtime_residual"
        assert s["coverage_status"] == "complete"
        residual = s["components"][-1]
        assert residual["component"] == "runtime_unattributed"
        assert Decimal(residual["cost"]["amount"]) == Decimal("0.00105")  # 1000*$1 + 10*$5 per MTok
        assert s["full_pipeline_total"]["token_usage"]["input_tokens"] == 1014

    def test_residual_cache_creation_leaves_cost_unknown_not_zero(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main, runtime_result=self._runtime({
            "claude-sonnet-5": {**mu, "cacheCreationInputTokens": mu["cacheCreationInputTokens"] + 50},
        }))
        cost = s["full_pipeline_total"]["cost"]
        assert cost["status"] == "unknown"
        assert cost["amount"] is None
        assert "5m/1h" in cost["reason"]

    def test_transcripts_exceeding_runtime_withhold_total(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main, runtime_result=self._runtime({
            "claude-sonnet-5": {**mu, "outputTokens": mu["outputTokens"] - 1},
        }))
        assert s["reconciliation"]["status"] == "inconsistent"
        assert s["coverage_status"] == "incomplete"
        assert s["full_pipeline_total"] is None

    def test_dispatched_agent_without_transcript_withholds_total(self, tmp_path):
        agents = {"arch": [_assistant_line(message_id="a1")]}
        main = self._setup(tmp_path, agents=agents, orchestrator_lines=[_assistant_line(message_id="o1")],
                           dispatched=["arch", "eng"])
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                          runtime_result=self._runtime({"claude-sonnet-5": self._mu(4, 20, 0, 0)}))
        assert s["reconciliation"]["missing_agent_transcripts"] == ["eng"]
        assert s["full_pipeline_total"] is None

    def test_same_call_in_two_transcripts_withholds_total(self, tmp_path):
        agents = {"arch": [_assistant_line(message_id="dup")]}
        main = self._setup(tmp_path, agents=agents, orchestrator_lines=[_assistant_line(message_id="dup")])
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                          runtime_result=self._runtime({"claude-sonnet-5": self._mu(4, 20, 0, 0)}))
        assert s["reconciliation"]["cross_file_duplicate_message_ids"] == ["dup"]
        assert s["full_pipeline_total"] is None

    def test_unusable_assistant_line_withholds_total(self, tmp_path):
        agents = {"arch": [_assistant_line(message_id="a1"), json.dumps({"type": "assistant"})]}
        main = self._setup(tmp_path, agents=agents, orchestrator_lines=[_assistant_line(message_id="o1")])
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                          runtime_result=self._runtime({"claude-sonnet-5": self._mu(4, 20, 0, 0)}))
        assert s["coverage_status"] == "incomplete"
        assert s["full_pipeline_total"] is None

    def test_unknown_model_gives_unknown_total_cost(self, tmp_path):
        agents = {"arch": [_assistant_line(message_id="a1", model="mystery-model")]}
        main = self._setup(tmp_path, agents=agents, orchestrator_lines=[_assistant_line(message_id="o1")])
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main, runtime_result=self._runtime({
            "claude-sonnet-5": self._mu(2, 10, 0, 0), "mystery-model": self._mu(2, 10, 0, 0),
        }))
        assert s["reconciliation"]["status"] == "exact"
        assert s["full_pipeline_total"]["cost"]["status"] == "unknown"
        assert s["full_pipeline_total"]["cost"]["amount"] is None

    def test_unattributed_subagent_in_session_is_counted_and_labeled(self, tmp_path):
        agents = {"arch": [_assistant_line(message_id="a1")], "zzz": [_assistant_line(message_id="z1")]}
        main = self._setup(tmp_path, agents=agents, orchestrator_lines=[_assistant_line(message_id="o1")],
                           dispatched=["arch"])
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                          runtime_result=self._runtime({"claude-sonnet-5": self._mu(6, 30, 0, 0)}))
        assert [c["component"] for c in s["components"]] == ["orchestrator", "architect", "unattributed_subagent"]
        assert s["full_pipeline_total"]["token_usage"]["output_tokens"] == 30

    def test_session_mismatch_is_rejected(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        with pytest.raises(usage.PipelineUsageError):
            usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main, runtime_result={
                **self._runtime({"claude-sonnet-5": mu}), "session_id": "other-session",
            })

    def test_total_carries_the_runs_own_outcome(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        (evidence_io.run_dir(tmp_path, "run-1") / "run-summary.json").write_text(json.dumps({
            "final_verdict": "blocked", "phases_completed": ["discovery", "research"],
        }), encoding="utf-8")
        s = usage.finalize_pipeline_usage("run-1", repo_root=tmp_path, session_transcript=main,
                                          runtime_result=self._runtime({"claude-sonnet-5": mu}))
        assert s["full_pipeline_total"]["run_final_verdict"] == "blocked"
        assert s["full_pipeline_total"]["run_phases_completed"] == ["discovery", "research"]

    def test_write_is_idempotent(self, tmp_path):
        main, mu = self._full_pipeline(tmp_path)
        kwargs = dict(repo_root=tmp_path, session_transcript=main, runtime_result=self._runtime({"claude-sonnet-5": mu}))
        p1, s1 = usage.write_pipeline_usage_summary("run-1", **kwargs)
        p2, s2 = usage.write_pipeline_usage_summary("run-1", **kwargs)
        assert p1 == p2
        assert s1["full_pipeline_total"] == s2["full_pipeline_total"]
