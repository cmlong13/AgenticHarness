"""Per-agent token usage capture, identity mapping, pricing, and run usage summaries.

Per PROJECT_SPEC.md's Option C, this module is the Python side of usage/cost
accounting -- exactly as memory.py is the Python side of the memory loop and
checkpoint.py is the Python side of checkpointing: it never reasons about
which agent to dispatch or when: it only parses, validates, identity-matches,
prices, and persists real runtime evidence that the hooks/orchestrator hand
it. `.claude/hooks/record_agent_usage.py` is the thin script that calls into
this module on the real `SubagentStop`/`PostToolUse:Agent` hook events;
`harness/orchestrator/live_cli.py` exposes `build_usage_summary` as the one
thin operation the `/work` skill calls to fold a run's usage into its
reporting.

Architecture, grounded in the retained diagnostic evidence
(docs/usage-hook-signal-verification.md, runs/hooks-diagnostic/
usage-hook-diagnostic-log.jsonl -- see that doc for the full empirical
account this module implements):

- Capture hook: `SubagentStop` is authoritative. It fires on every stop of a
  staged agent (the initial dispatch AND every `SendMessage` resume),
  confirmed live: two `SubagentStop` events for one `Agent` dispatch plus one
  resume, versus exactly one `PostToolUse:Agent` event. `PostToolUse:Agent`'s
  own `tool_response.usage` is real structured data but would silently miss
  every resumed turn if used as the sole source -- it is retained here only
  as a non-authoritative corroboration record (see
  `record_post_tool_use_corroboration` below), never as the source of a
  persisted per-agent total.
- Token source: the subagent's own transcript file, named by the hook
  payload's `agent_transcript_path`. Confirmed live (this milestone, reading
  the retained transcript from the original diagnostic session) that each
  `type: "assistant"` line carries `message.model` and a `message.usage`
  object with `input_tokens`/`output_tokens`/`cache_creation_input_tokens`/
  `cache_read_input_tokens` individually, plus a `cache_creation` sub-object
  splitting `ephemeral_5m_input_tokens`/`ephemeral_1h_input_tokens` -- never
  the rounded `totalTokens` convenience figure, which this module never reads
  for accounting.
- Aggregation semantics: `usage` is per-API-call, not cumulative -- but one
  API call is written as several `assistant` lines (one per content block)
  sharing a `message.id`, so usage is counted once per `message.id` (see
  `parse_transcript_usage`); the transcript file itself is cumulative and append-only
  across every turn/resume that agent has taken. Every capture therefore
  recomputes the FULL sum from the entire current file and OVERWRITES (never
  adds to) the stored per-agent record -- this is what makes a duplicate or
  repeated `SubagentStop` for the same `agent_id` naturally idempotent
  (same total recomputed twice) rather than a source of double-counting, and
  what correctly folds a route-back/SendMessage resume's usage into the
  SAME agent record rather than creating a second one.
- Identity: `agent_id` is matched against every run's own retained
  `agent_dispatch` policy event (`runs/<run_id>/logs/policy-events.jsonl`).
  Exactly one matching run -> the record belongs to that run. Zero or more
  than one matching run -> quarantined (`runs/_unmatched_usage/`), never
  guessed.
- Coverage: during the run only subagent usage is final -- the orchestrator's
  own usage is still growing while the run summary is written, so the in-run
  `build_run_usage_summary` is always "partial" with `full_pipeline_total: null`.
  A full total exists only after the session ends, and only for a run executed
  as one dedicated headless session: `finalize_pipeline_usage` then accounts the
  orchestrator transcript and every subagent transcript of that session and
  reconciles them against the runtime's own session `modelUsage`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import evidence_io

PRICING_FILENAME = "model-pricing.json"
USAGE_DIRNAME = "usage"
UNMATCHED_USAGE_DIRNAME = "_unmatched_usage"
# Marks a record produced by the per-message-id deduplicating parser. Records
# without it were produced by the earlier every-line parser, which double-counted
# multi-block API responses (see parse_transcript_usage).
ACCOUNTING_METHOD = "per_api_message_id"

# The four exact structured token categories this module ever persists or
# prices -- never a rounded/precomputed UI figure such as `totalTokens`.
_TOKEN_CATEGORIES = (
    "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Transcript parsing (Part: exact structured token categories)
# ---------------------------------------------------------------------------


@dataclass
class TokenCounts:
    """The exact structured categories captured from a real transcript --
    never rounded, never a UI convenience figure. `cache_creation_5m_tokens`/
    `cache_creation_1h_tokens` are kept separate (not merely summed) because
    they price at different rates (1.25x vs 2x base input) -- see
    `price_usage`. `cache_creation_input_tokens` is their sum, exposed as a
    property so a caller that only wants the four canonical categories the
    diagnostic evidence names (input/output/cache_creation/cache_read) can
    read it directly without knowing about the 5m/1h split underneath."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def cache_creation_input_tokens(self) -> int:
        return self.cache_creation_5m_tokens + self.cache_creation_1h_tokens

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_tokens,
            "cache_creation_breakdown": {
                "ephemeral_5m_input_tokens": self.cache_creation_5m_tokens,
                "ephemeral_1h_input_tokens": self.cache_creation_1h_tokens,
            },
        }

    def __add__(self, other: "TokenCounts") -> "TokenCounts":
        return TokenCounts(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_5m_tokens=self.cache_creation_5m_tokens + other.cache_creation_5m_tokens,
            cache_creation_1h_tokens=self.cache_creation_1h_tokens + other.cache_creation_1h_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
        )


@dataclass
class TranscriptUsage:
    """The full result of parsing one transcript file at capture time --
    always a fresh, full recomputation (see module docstring), never an
    incremental update against a prior capture. `message_count` counts
    distinct API calls (distinct `message.id`s), not transcript lines."""

    counts: TokenCounts = field(default_factory=TokenCounts)
    counts_by_model: dict[str, TokenCounts] = field(default_factory=dict)
    message_count: int = 0
    message_ids: list[str] = field(default_factory=list)
    assistant_line_count: int = 0
    lines_without_message_id: int = 0
    synthetic_message_count: int = 0
    models_seen: list[str] = field(default_factory=list)
    skipped_lines: list[dict] = field(default_factory=list)  # {"line_no":..., "reason":...}


class TranscriptNotFoundError(Exception):
    pass


# Client-generated assistant entries (e.g. an interrupted-turn placeholder) carry
# this model string and are not API calls -- never counted or priced.
SYNTHETIC_MODEL = "<synthetic>"


def parse_transcript_usage(transcript_path: Path) -> TranscriptUsage:
    """Reads a transcript JSONL file end to end and sums the exact structured
    usage categories once per real API call.

    Claude Code writes one API response as SEVERAL `type: "assistant"` lines --
    one per content block (thinking / text / tool_use) -- all sharing the same
    `message.id`, each repeating that call's input/cache usage, with
    `output_tokens` growing as the stream progresses and final on the last
    line (verified across every locally retained transcript on 2026-09-29:
    2,640 message ids in 93 files, input/cache identical within every id,
    output monotonic with the last line maximal). Summing every line therefore
    double-counts; this parser keeps exactly one usage per `message.id` -- the
    last line seen for that id. A line without a `message.id` cannot be
    deduplicated and is counted as its own call; `lines_without_message_id`
    records how many, so a session-level reconciliation against the runtime's
    own totals (see `finalize_pipeline_usage`) can expose any overcount.

    A line that isn't `type: "assistant"` is not part of usage accounting at
    all and is silently skipped (not reported as an anomaly) -- only an
    `assistant`-type line that is malformed (bad JSON, missing `message`,
    missing `message.usage`, missing `input_tokens`/`output_tokens`) is
    recorded in `skipped_lines`, never allowed to block a later valid line in
    the same file from being summed."""
    if not transcript_path.is_file():
        raise TranscriptNotFoundError(f"transcript file not found: {transcript_path}")

    result = TranscriptUsage()
    seen_models: list[str] = []
    calls: dict[str, tuple[str | None, TokenCounts]] = {}  # message key -> (model, counts); insertion-ordered

    for line_no, raw_line in enumerate(transcript_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError as exc:
            result.skipped_lines.append({"line_no": line_no, "reason": f"malformed JSON: {exc}"})
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue  # not an accounting-relevant line -- not an anomaly

        message = entry.get("message")
        if not isinstance(message, dict):
            result.skipped_lines.append({"line_no": line_no, "reason": "assistant entry missing 'message' object"})
            continue
        model = message.get("model")
        if model == SYNTHETIC_MODEL:
            result.synthetic_message_count += 1
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            result.skipped_lines.append({"line_no": line_no, "reason": "message missing 'usage' object"})
            continue
        missing = [k for k in ("input_tokens", "output_tokens") if k not in usage]
        if missing:
            result.skipped_lines.append({"line_no": line_no, "reason": f"usage missing field(s): {', '.join(missing)}"})
            continue

        result.assistant_line_count += 1
        if isinstance(model, str) and model and model not in seen_models:
            seen_models.append(model)

        cache_creation = usage.get("cache_creation")
        if isinstance(cache_creation, dict):
            cache_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
            cache_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
        else:
            # Documented fallback: no sub-breakdown present -- attribute the
            # whole of cache_creation_input_tokens to the 5m (default TTL)
            # bucket rather than dropping it, since every real transcript
            # line observed so far carries the breakdown and this path is a
            # defensive fallback, not the expected case.
            cache_5m = usage.get("cache_creation_input_tokens", 0)
            cache_1h = 0

        line_counts = TokenCounts(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_creation_5m_tokens=int(cache_5m or 0),
            cache_creation_1h_tokens=int(cache_1h or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        )
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id:
            key = message_id
            calls.pop(key, None)  # last line for this id wins (see docstring)
        else:
            result.lines_without_message_id += 1
            key = f"\0line-{line_no}"
        calls[key] = (model if isinstance(model, str) and model else None, line_counts)

    for key, (model, counts) in calls.items():
        result.counts = result.counts + counts
        model_key = model or "unknown"
        result.counts_by_model[model_key] = result.counts_by_model.get(model_key, TokenCounts()) + counts
        if not key.startswith("\0"):
            result.message_ids.append(key)
    result.message_count = len(calls)
    result.models_seen = seen_models
    return result


# ---------------------------------------------------------------------------
# Identity mapping (Part: agent_id -> retained agent_dispatch identity)
# ---------------------------------------------------------------------------


@dataclass
class AgentDispatchMatch:
    status: str  # "matched" | "unmatched" | "ambiguous"
    run_id: str | None = None
    phase: str | None = None
    subagent_type: str | None = None
    dispatch_sequence: int | None = None
    candidate_run_ids: list[str] = field(default_factory=list)


def find_agent_dispatch(agent_id: str, *, runs_root: Path) -> AgentDispatchMatch:
    """Scans every run directory's own retained policy-events.jsonl for an
    `agent_dispatch` event naming this exact `agent_id`. An `agent_id` that
    matches zero runs, or more than one distinct run, is never guessed --
    the caller must quarantine it (see `capture_subagent_usage`). Reused
    (route-back / SendMessage) turns never create a second `agent_dispatch`
    event for the same agent_id (per work/SKILL.md's own staged-continuation
    protocol), so a genuine single match is expected for every real,
    controlled dispatch."""
    if not runs_root.is_dir():
        return AgentDispatchMatch(status="unmatched")

    matches: dict[str, dict] = {}  # run_id -> first matching event payload
    for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        events_path = run_dir / "logs" / "policy-events.jsonl"
        if not events_path.is_file():
            continue
        for raw_line in events_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("kind") != "agent_dispatch":
                continue
            if event.get("agent_id") != agent_id:
                continue
            matches.setdefault(run_dir.name, event)
            break  # one match per run is enough to prove this run dispatched it

    if not matches:
        return AgentDispatchMatch(status="unmatched")
    if len(matches) > 1:
        return AgentDispatchMatch(status="ambiguous", candidate_run_ids=sorted(matches))

    (run_id, event), = matches.items()
    return AgentDispatchMatch(
        status="matched",
        run_id=run_id,
        phase=event.get("phase"),
        subagent_type=event.get("subagent_type"),
        dispatch_sequence=event.get("dispatch_sequence"),
        candidate_run_ids=[run_id],
    )


# ---------------------------------------------------------------------------
# Pricing (Part: Decimal-based cost, unknown models stay unpriced)
# ---------------------------------------------------------------------------


@dataclass
class ModelRates:
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal


@dataclass
class PricingTable:
    models: dict[str, ModelRates]
    source: str
    verified_date: str
    aliases: dict[str, str] = field(default_factory=dict)

    def rates_for(self, model: str | None) -> ModelRates | None:
        """Exact key first, then an explicitly configured alias (e.g. a dated
        model id the runtime records) -- never a fuzzy/prefix match."""
        if not model:
            return None
        return self.models.get(model) or self.models.get(self.aliases.get(model, ""))


class PricingLoadError(Exception):
    pass


def load_pricing(path: Path) -> PricingTable:
    """Loads harness/model-pricing.json. Every rate is parsed as Decimal from
    its JSON string representation -- never float -- per the assignment's
    explicit "Decimal for billing math" requirement. Raises PricingLoadError
    on a malformed file rather than silently pricing at $0; callers treat
    that identically to "no pricing available" (see `price_usage`)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PricingLoadError(f"cannot load pricing file {path}: {exc}") from exc

    meta = raw.get("_meta", {})
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict):
        raise PricingLoadError(f"pricing file {path} has no 'models' object")

    models: dict[str, ModelRates] = {}
    for model_id, rates in models_raw.items():
        try:
            models[model_id] = ModelRates(
                input=Decimal(str(rates["input"])),
                output=Decimal(str(rates["output"])),
                cache_write_5m=Decimal(str(rates["cache_write_5m"])),
                cache_write_1h=Decimal(str(rates["cache_write_1h"])),
                cache_read=Decimal(str(rates["cache_read"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PricingLoadError(f"pricing file {path}: malformed entry for {model_id!r}: {exc}") from exc

    aliases = raw.get("aliases", {})
    if not isinstance(aliases, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or v not in models for k, v in aliases.items()
    ):
        raise PricingLoadError(f"pricing file {path}: 'aliases' must map model ids to keys of 'models'")

    return PricingTable(
        models=models,
        source=str(meta.get("source", "")),
        verified_date=str(meta.get("verified_date", "")),
        aliases=dict(aliases),
    )


@dataclass
class CostResult:
    priced: bool
    amount: Decimal | None
    currency: str = "USD"
    reason: str | None = None
    pricing_source: str | None = None
    pricing_verified_date: str | None = None

    def as_dict(self) -> dict:
        return {
            "priced": self.priced,
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "reason": self.reason,
            "pricing_source": self.pricing_source,
            "pricing_verified_date": self.pricing_verified_date,
        }


_MTOK = Decimal(1_000_000)


def price_usage(model: str | None, counts: TokenCounts, pricing: PricingTable) -> CostResult:
    """Decimal-exact cost for one agent's summed usage, priced against
    `pricing`'s per-model rates. A model that is None, empty, or not present
    in the pricing table stays visibly unpriced (`priced: False`, `amount:
    None`, an explicit `reason`) -- never guessed from a nearby model's rate
    and never defaulted to $0. Cache-creation cost uses the real 5m/1h split
    captured in `counts`, priced at each bucket's own distinct rate, rather
    than collapsing both into a single "cache write" rate."""
    if not model:
        return CostResult(priced=False, amount=None, reason="no model name recorded in transcript")
    rates = pricing.rates_for(model)
    if rates is None:
        return CostResult(priced=False, amount=None, reason=f"unrecognized model {model!r} -- not in pricing table")

    amount = (
        (Decimal(counts.input_tokens) * rates.input)
        + (Decimal(counts.output_tokens) * rates.output)
        + (Decimal(counts.cache_creation_5m_tokens) * rates.cache_write_5m)
        + (Decimal(counts.cache_creation_1h_tokens) * rates.cache_write_1h)
        + (Decimal(counts.cache_read_tokens) * rates.cache_read)
    ) / _MTOK

    return CostResult(
        priced=True,
        amount=amount,
        pricing_source=pricing.source,
        pricing_verified_date=pricing.verified_date,
    )


def price_counts_by_model(counts_by_model: dict[str, TokenCounts], pricing: PricingTable) -> CostResult:
    """Prices each model's tokens at that model's own rates and sums them. If
    ANY model is unpriced the whole result is unpriced (`amount: None`) -- a
    partially priced sum is never presented as the cost."""
    total = Decimal(0)
    unpriced: list[str] = []
    for model, counts in counts_by_model.items():
        cost = price_usage(model, counts, pricing)
        if cost.priced:
            total += cost.amount
        else:
            unpriced.append(cost.reason or model)
    if unpriced:
        return CostResult(priced=False, amount=None, reason="; ".join(unpriced))
    return CostResult(
        priced=True, amount=total, pricing_source=pricing.source, pricing_verified_date=pricing.verified_date,
    )


# ---------------------------------------------------------------------------
# Per-agent usage record capture (the SubagentStop path)
# ---------------------------------------------------------------------------


def _default_pricing_path(repo_root: Path) -> Path:
    return repo_root / "harness" / PRICING_FILENAME


def usage_record_path(run_directory: Path, agent_id: str) -> Path:
    return run_directory / USAGE_DIRNAME / f"{agent_id}.json"


def quarantine_record_path(repo_root: Path, agent_id: str, *, captured_at: str) -> Path:
    # Timestamp in the filename: an agent_id can legitimately be captured
    # (and remain unmatched) more than once across retries -- each attempt
    # is retained, never silently overwriting a prior quarantine record.
    safe_ts = captured_at.replace(":", "").replace("-", "")
    return repo_root / "runs" / UNMATCHED_USAGE_DIRNAME / f"{agent_id}-{safe_ts}.json"


def build_usage_record(
    *, agent_id: str, hook_payload: dict, transcript: TranscriptUsage, match: AgentDispatchMatch,
    cost: CostResult, captured_at: str,
) -> dict:
    model = transcript.models_seen[0] if transcript.models_seen else None
    return {
        "schema_version": "1.1",
        "record_kind": "agent_usage",
        "accounting_method": ACCOUNTING_METHOD,
        "agent_id": agent_id,
        "identity": {
            "match_status": match.status,
            "run_id": match.run_id,
            "task_id": None,
            "phase": match.phase,
            "subagent_type": match.subagent_type,
            "dispatch_sequence": match.dispatch_sequence,
            "candidate_run_ids": match.candidate_run_ids,
        },
        "source": {
            "hook_event_name": hook_payload.get("hook_event_name"),
            "agent_transcript_path": hook_payload.get("agent_transcript_path"),
            "agent_type": hook_payload.get("agent_type"),
        },
        "captured_at": captured_at,
        "message_count": transcript.message_count,
        "assistant_line_count": transcript.assistant_line_count,
        "lines_without_message_id": transcript.lines_without_message_id,
        "synthetic_message_count": transcript.synthetic_message_count,
        "models_seen": transcript.models_seen,
        "primary_model": model,
        "token_usage": transcript.counts.as_dict(),
        "token_usage_by_model": {m: c.as_dict() for m, c in transcript.counts_by_model.items()},
        "skipped_transcript_lines": transcript.skipped_lines,
        "cost": cost.as_dict(),
    }


def capture_subagent_usage(hook_payload: dict, *, repo_root: Path, captured_at: str | None = None) -> dict:
    """The primary, authoritative usage-capture entry point -- invoked by
    `.claude/hooks/record_agent_usage.py` on a real `SubagentStop` event.

    Always recomputes the full per-agent record from the agent's own current
    transcript file and OVERWRITES (never appends to) the stored record for
    this exact `agent_id` -- see the module docstring's "Aggregation
    semantics" for why this is what keeps a duplicate hook firing, and a
    resumed/route-back turn, from ever double-counting or fabricating a
    second agent record. Never raises on a malformed or missing payload --
    returns a `status: "error"` result instead, since a usage-capture hook
    must never be the thing that blocks a real dispatch (mirrors
    pre_dispatch_check.py's own "fail open on our own errors" contract,
    except this hook was never a guardrail to begin with)."""
    captured_at = captured_at or _utc_now_iso()

    agent_id = hook_payload.get("agent_id")
    transcript_path_str = hook_payload.get("agent_transcript_path")
    if not isinstance(agent_id, str) or not agent_id:
        return {"status": "error", "reason": "hook payload missing 'agent_id'"}
    if not isinstance(transcript_path_str, str) or not transcript_path_str:
        return {"status": "error", "reason": "hook payload missing 'agent_transcript_path'"}

    try:
        transcript = parse_transcript_usage(Path(transcript_path_str))
    except TranscriptNotFoundError as exc:
        return {"status": "error", "reason": str(exc)}

    match = find_agent_dispatch(agent_id, runs_root=repo_root / "runs")

    try:
        pricing = load_pricing(_default_pricing_path(repo_root))
        cost = price_counts_by_model(transcript.counts_by_model, pricing)
    except PricingLoadError as exc:
        cost = CostResult(priced=False, amount=None, reason=f"pricing unavailable: {exc}")

    record = build_usage_record(
        agent_id=agent_id, hook_payload=hook_payload, transcript=transcript,
        match=match, cost=cost, captured_at=captured_at,
    )

    if match.status == "matched":
        run_directory = evidence_io.run_dir(repo_root, match.run_id)
        evidence_io.ensure_run_dirs(run_directory)
        record["identity"]["task_id"] = _task_id_for_run(run_directory)
        path = usage_record_path(run_directory, agent_id)
        evidence_io.atomic_write(path, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
        evidence_io.retain_policy_event(
            run_directory, "agent_usage_captured",
            {
                "agent_id": agent_id, "phase": match.phase, "subagent_type": match.subagent_type,
                "message_count": transcript.message_count, "priced": cost.priced,
            },
        )
        return {"status": "captured", "run_id": match.run_id, "path": str(path)}

    path = quarantine_record_path(repo_root, agent_id, captured_at=captured_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    evidence_io.atomic_write(path, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    return {"status": "quarantined", "reason": match.status, "path": str(path)}


def _task_id_for_run(run_directory: Path) -> str | None:
    scope_path = run_directory / "scope.json"
    if not scope_path.is_file():
        return None
    try:
        doc = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    task_id = doc.get("task_id")
    return task_id if isinstance(task_id, str) else None


# ---------------------------------------------------------------------------
# PostToolUse:Agent corroboration (secondary, non-authoritative signal)
# ---------------------------------------------------------------------------


def corroboration_record_path(repo_root: Path, agent_id: str) -> Path:
    return repo_root / "runs" / "_usage_corroboration" / f"{agent_id}.json"


def record_post_tool_use_corroboration(hook_payload: dict, *, repo_root: Path, captured_at: str | None = None) -> dict:
    """Retains `PostToolUse:Agent`'s own structured `tool_response.usage` (and
    `resolvedModel`/`tool_use_id`) as supplementary, non-authoritative
    corroborating evidence -- exactly as docs/usage-hook-signal-verification.md's
    own architecture section describes ("remains useful only as corroborating
    identity evidence"). This is NEVER read by `capture_subagent_usage` to
    compute or override the authoritative per-agent record; it exists purely
    so the real `PostToolUse:Agent` signal this milestone's evidence
    identified is itself retained, satisfying the demonstration's own
    requirement to prove real `PostToolUse:Agent` capture happened, without
    making it load-bearing for the numbers. Ordering with `SubagentStop` is
    not guaranteed (confirmed empirically -- see the diagnostic doc); this
    function only ever writes its own small sidecar file and never touches
    the authoritative per-agent usage record."""
    captured_at = captured_at or _utc_now_iso()
    tool_response = hook_payload.get("tool_response")
    if not isinstance(tool_response, dict):
        return {"status": "skipped", "reason": "no tool_response in payload"}
    agent_id = tool_response.get("agentId")
    if not isinstance(agent_id, str) or not agent_id:
        return {"status": "skipped", "reason": "tool_response missing agentId"}

    record = {
        "schema_version": "1.0",
        "record_kind": "post_tool_use_corroboration",
        "agent_id": agent_id,
        "captured_at": captured_at,
        "tool_use_id": hook_payload.get("tool_use_id"),
        "resolved_model": tool_response.get("resolvedModel"),
        "reported_usage": tool_response.get("usage"),
        "reported_total_tokens_UNBILLED_DO_NOT_USE": tool_response.get("totalTokens"),
    }
    path = corroboration_record_path(repo_root, agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    evidence_io.atomic_write(path, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    return {"status": "recorded", "agent_id": agent_id, "path": str(path)}


# ---------------------------------------------------------------------------
# Run usage summary (Part: explicit coverage semantics, never a subtotal
# presented as the full pipeline total)
# ---------------------------------------------------------------------------


def build_run_usage_summary(run_id: str, *, repo_root: Path) -> dict:
    """Aggregates every per-agent usage record retained under
    runs/<run_id>/usage/*.json into one run-level summary. Explicitly
    distinguishes:

    - `agents`: the individual per-agent records (identity, tokens, cost).
    - `subagent_subtotal`: the sum across every captured agent record for
      this run -- labeled a SUBTOTAL, never the pipeline total (assignment
      requirement: "Do not call a subagent subtotal the total pipeline
      cost").
    - `orchestrator`: always `{"status": "not_measured", ...}` this
      milestone -- the main session's own usage cannot be safely captured
      mid-run (the run summary is written before the session's own `Stop`
      event could ever reveal it), so `coverage_status` is always
      `"partial"` and `full_pipeline_total` is always `null` with an
      explicit reason. See the module docstring.
    """
    run_directory = evidence_io.run_dir(repo_root, run_id)
    usage_dir = run_directory / USAGE_DIRNAME

    agents: list[dict] = []
    if usage_dir.is_dir():
        for record_path in sorted(usage_dir.glob("*.json")):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and record.get("record_kind") == "agent_usage":
                agents.append(record)

    subtotal_counts = TokenCounts()
    priced_amount = Decimal(0)
    any_priced = False
    unpriced_agent_ids: list[str] = []
    legacy_agent_ids: list[str] = []
    for record in agents:
        if record.get("accounting_method") != ACCOUNTING_METHOD:
            # Produced by the earlier every-line parser, which double-counted
            # multi-block API responses -- never summed into a figure.
            legacy_agent_ids.append(record.get("agent_id"))
            continue
        tu = record.get("token_usage", {})
        breakdown = tu.get("cache_creation_breakdown", {})
        subtotal_counts = subtotal_counts + TokenCounts(
            input_tokens=int(tu.get("input_tokens", 0) or 0),
            output_tokens=int(tu.get("output_tokens", 0) or 0),
            cache_creation_5m_tokens=int(breakdown.get("ephemeral_5m_input_tokens", 0) or 0),
            cache_creation_1h_tokens=int(breakdown.get("ephemeral_1h_input_tokens", 0) or 0),
            cache_read_tokens=int(tu.get("cache_read_input_tokens", 0) or 0),
        )
        cost = record.get("cost", {})
        if cost.get("priced"):
            any_priced = True
            priced_amount += Decimal(str(cost["amount"]))
        else:
            unpriced_agent_ids.append(record.get("agent_id"))

    summary = {
        "schema_version": "1.0",
        "record_kind": "run_usage_summary",
        "run_id": run_id,
        "generated_at": _utc_now_iso(),
        "agents": agents,
        "subagent_subtotal": {
            "agent_count": len(agents) - len(legacy_agent_ids),
            "token_usage": subtotal_counts.as_dict(),
            "cost": {
                "priced": any_priced and not unpriced_agent_ids and not legacy_agent_ids,
                "amount": str(priced_amount) if any_priced else None,
                "currency": "USD",
                "unpriced_agent_ids": unpriced_agent_ids,
                "legacy_undeduplicated_agent_ids": legacy_agent_ids,
                "note": (
                    "partial: one or more agents are unpriced" if unpriced_agent_ids
                    else "partial: legacy (double-counting) agent records excluded" if legacy_agent_ids
                    else ("complete over captured agents" if any_priced else "no priced agents")
                ),
            },
        },
        "orchestrator": {
            "status": "not_measured",
            "reason": (
                "The main orchestrator session is still running while this summary is "
                "written, so its own usage is not final yet. It is measured only after the "
                "session ends, by finalize_pipeline_usage, and only for a run executed as "
                "one dedicated headless session (the pipeline boundary)."
            ),
            "token_usage": None,
            "cost": None,
        },
        "coverage_status": "partial",
        "full_pipeline_total": None,
        "full_pipeline_total_reason": (
            "orchestrator usage is not final until the session ends -- a subagent subtotal is "
            "never reported as the full pipeline cost"
        ),
    }
    return summary


def reconcile_quarantined_usage(agent_id: str, *, repo_root: Path) -> dict:
    """Re-attempts identity matching for an agent_id that was previously quarantined
    -- the expected, honest outcome of a real single-shot dispatch, per this module's
    own timing finding: `SubagentStop` fires (and record_agent_usage.py's capture runs)
    BEFORE the orchestrator's own turn resumes to write that dispatch's `agent_dispatch`
    policy event (confirmed live, see the milestone's own retained demonstration
    evidence) -- so the very first capture attempt for a fresh, non-staged dispatch has
    no matching identity yet, by construction, not by error.

    Called by the orchestrator immediately after `retain_policy_event(kind:
    "agent_dispatch")` -- once that event exists, this re-derives the record from the
    SAME transcript path the original quarantine record already retained (never a
    fresh/re-guessed path), and, if it now matches, writes the correctly attributed
    record under the real run and marks the quarantine record consumed by renaming it
    to a `.reconciled` suffix (content unchanged) -- mirroring this repo's existing
    completion_claim.json.consumed convention for deactivating a stale marker without
    destroying the evidence. A quarantine record that still doesn't match (e.g. the
    agent_id genuinely belongs to no controlled dispatch) is left in place, unconsumed."""
    quarantine_dir = repo_root / "runs" / UNMATCHED_USAGE_DIRNAME
    if not quarantine_dir.is_dir():
        return {"status": "no_quarantine_record", "agent_id": agent_id}
    candidates = sorted(quarantine_dir.glob(f"{agent_id}-*.json"))
    if not candidates:
        return {"status": "no_quarantine_record", "agent_id": agent_id}
    latest = candidates[-1]  # most recent quarantine attempt for this agent_id

    try:
        quarantined = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "reason": f"cannot read quarantine record {latest}: {exc}"}

    source = quarantined.get("source", {}) if isinstance(quarantined, dict) else {}
    hook_payload = {
        "hook_event_name": source.get("hook_event_name", "SubagentStop"),
        "agent_id": agent_id,
        "agent_transcript_path": source.get("agent_transcript_path"),
        "agent_type": source.get("agent_type"),
    }
    result = capture_subagent_usage(hook_payload, repo_root=repo_root)
    if result.get("status") == "captured":
        consumed_path = latest.with_name(latest.name + ".reconciled")
        latest.rename(consumed_path)
        result["reconciled_from_quarantine"] = str(consumed_path)
    return result


def usage_summary_path(run_directory: Path) -> Path:
    return run_directory / "usage-summary.json"


def write_run_usage_summary(run_id: str, *, repo_root: Path) -> Path:
    run_directory = evidence_io.run_dir(repo_root, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    summary = build_run_usage_summary(run_id, repo_root=repo_root)
    path = usage_summary_path(run_directory)
    evidence_io.atomic_write(path, (json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    return path


# ---------------------------------------------------------------------------
# Full pipeline total (post-session finalization over one dedicated session)
# ---------------------------------------------------------------------------
#
# Pipeline boundary: the run is executed as ONE dedicated headless Claude Code
# session (`claude -p "/work ..." --output-format json`), so every model call made
# while executing the pipeline belongs to that session. Once the process has
# exited, both of the runtime's own records of that session are final:
#
# - per-call usage in the main transcript (`<session_id>.jsonl`, the orchestrator)
#   and in each subagent transcript (`<session_id>/subagents/agent-<id>.jsonl`);
# - the runtime's session-wide `modelUsage` (per model: input/output/cache-read/
#   cache-creation tokens) in the headless `result` JSON.
#
# The transcripts give attribution (orchestrator / Architect / Engineer / QE); the
# runtime's `modelUsage` is the completeness check. Verified live on 2026-09-29
# that the two agree exactly for a headless session with one subagent. Any
# difference is reported, never absorbed: runtime tokens that no transcript
# accounts for become an explicit `runtime_unattributed` component, and
# transcript tokens the runtime does not report make the reconciliation
# `inconsistent`, in which case no full total is stated.

_RUNTIME_CATEGORIES = (
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("cacheReadInputTokens", "cache_read_input_tokens"),
    ("cacheCreationInputTokens", "cache_creation_input_tokens"),
)


class PipelineUsageError(Exception):
    pass


def _flat_counts(counts: TokenCounts) -> dict[str, int]:
    return {
        "input_tokens": counts.input_tokens,
        "output_tokens": counts.output_tokens,
        "cache_read_input_tokens": counts.cache_read_tokens,
        "cache_creation_input_tokens": counts.cache_creation_input_tokens,
    }


def _run_agent_dispatches(run_directory: Path) -> dict[str, dict]:
    events_path = run_directory / "logs" / "policy-events.jsonl"
    dispatches: dict[str, dict] = {}
    if not events_path.is_file():
        return dispatches
    for raw_line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("kind") == "agent_dispatch" and isinstance(event.get("agent_id"), str):
            dispatches.setdefault(event["agent_id"], event)
    return dispatches


def _first_user_prompt(transcript_path: Path) -> str | None:
    for raw_line in transcript_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "user":
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                return content[:300]
            if isinstance(content, list):
                texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                if texts:
                    return texts[0][:300]
    return None


def _session_component(role: str, path: Path, transcript: TranscriptUsage, pricing: PricingTable, **identity) -> dict:
    return {
        "component": role,
        **identity,
        "transcript_path": str(path),
        "api_call_count": transcript.message_count,
        "assistant_line_count": transcript.assistant_line_count,
        "lines_without_message_id": transcript.lines_without_message_id,
        "synthetic_message_count": transcript.synthetic_message_count,
        "skipped_transcript_lines": transcript.skipped_lines,
        "models_seen": transcript.models_seen,
        "token_usage": transcript.counts.as_dict(),
        "token_usage_by_model": {m: c.as_dict() for m, c in transcript.counts_by_model.items()},
        "cost": price_counts_by_model(transcript.counts_by_model, pricing).as_dict(),
    }


def _price_residual(model: str, residual: dict[str, int], pricing: PricingTable) -> CostResult:
    """Prices runtime-reported tokens no transcript accounts for. The runtime's
    `modelUsage` does not split cache creation into 5m/1h, and those price
    differently, so a residual with cache-creation tokens stays unpriced."""
    rates = pricing.rates_for(model)
    if rates is None:
        return CostResult(priced=False, amount=None, reason=f"unrecognized model {model!r} -- not in pricing table")
    if residual["cache_creation_input_tokens"]:
        return CostResult(
            priced=False, amount=None,
            reason=f"{model}: runtime does not report the 5m/1h split of unattributed cache-creation tokens",
        )
    amount = (
        Decimal(residual["input_tokens"]) * rates.input
        + Decimal(residual["output_tokens"]) * rates.output
        + Decimal(residual["cache_read_input_tokens"]) * rates.cache_read
    ) / _MTOK
    return CostResult(
        priced=True, amount=amount, pricing_source=pricing.source, pricing_verified_date=pricing.verified_date,
    )


def finalize_pipeline_usage(
    run_id: str, *, repo_root: Path, session_transcript: Path, runtime_result: dict, runtime_result_ref: str | None = None,
) -> dict:
    """Builds the full-pipeline usage summary for a run executed as one dedicated
    headless session, after that session has exited. See the section comment
    above for the boundary and reconciliation rules. Raises PipelineUsageError if
    the inputs do not describe the same finished session."""
    if runtime_result.get("type") != "result" or not isinstance(runtime_result.get("modelUsage"), dict):
        raise PipelineUsageError("runtime_result is not a headless 'result' document with a 'modelUsage' object")
    session_id = runtime_result.get("session_id")
    if not isinstance(session_id, str) or session_transcript.stem != session_id:
        raise PipelineUsageError(
            f"session transcript {session_transcript.name!r} does not belong to runtime session {session_id!r}"
        )
    if not session_transcript.is_file():
        raise PipelineUsageError(f"session transcript not found: {session_transcript}")

    pricing = load_pricing(_default_pricing_path(repo_root))
    run_directory = evidence_io.run_dir(repo_root, run_id)
    dispatches = _run_agent_dispatches(run_directory)

    parsed: list[tuple[dict, TranscriptUsage]] = []
    orchestrator_usage = parse_transcript_usage(session_transcript)
    parsed.append((_session_component("orchestrator", session_transcript, orchestrator_usage, pricing), orchestrator_usage))
    subagent_dir = session_transcript.with_suffix("") / "subagents"
    seen_agent_ids: list[str] = []
    for path in sorted(subagent_dir.glob("agent-*.jsonl")) if subagent_dir.is_dir() else []:
        agent_id = path.stem[len("agent-"):]
        seen_agent_ids.append(agent_id)
        dispatch = dispatches.get(agent_id)
        transcript = parse_transcript_usage(path)
        identity = {
            "agent_id": agent_id,
            "phase": dispatch.get("phase") if dispatch else None,
            "subagent_type": dispatch.get("subagent_type") if dispatch else None,
            "dispatch_sequence": dispatch.get("dispatch_sequence") if dispatch else None,
        }
        role = dispatch.get("subagent_type") if dispatch else "unattributed_subagent"
        parsed.append((_session_component(role or "unattributed_subagent", path, transcript, pricing, **identity), transcript))

    components = [c for c, _ in parsed]
    problems: list[str] = []

    # One API call is counted once across the whole session, not just within a file.
    owners: dict[str, int] = {}
    cross_file_duplicates: list[str] = []
    for index, (_, transcript) in enumerate(parsed):
        for message_id in transcript.message_ids:
            if owners.setdefault(message_id, index) != index:
                cross_file_duplicates.append(message_id)
    if cross_file_duplicates:
        problems.append(f"{len(cross_file_duplicates)} message id(s) appear in more than one transcript")

    missing_agent_transcripts = sorted(set(dispatches) - set(seen_agent_ids))
    if missing_agent_transcripts:
        problems.append(f"dispatched agent(s) with no transcript in this session: {missing_agent_transcripts}")
    for component, transcript in parsed:
        if transcript.skipped_lines:
            who = " ".join(filter(None, [component["component"], component.get("agent_id")]))
            problems.append(f"{who}: {len(transcript.skipped_lines)} unusable assistant line(s)")
        if component.get("agent_id"):
            # Corroboration only: the SubagentStop hook's record for the same agent,
            # compared, never summed.
            record_path = usage_record_path(run_directory, component["agent_id"])
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = None
            component["hook_record_matches"] = (
                record.get("token_usage") == component["token_usage"]
                if isinstance(record, dict) and record.get("accounting_method") == ACCOUNTING_METHOD else None
            )

    # Reconcile per model and category against the runtime's own session totals.
    transcript_by_model: dict[str, TokenCounts] = {}
    for _, transcript in parsed:
        for model, counts in transcript.counts_by_model.items():
            transcript_by_model[model] = transcript_by_model.get(model, TokenCounts()) + counts
    runtime_by_model: dict[str, dict[str, int]] = {}
    for model, mu in runtime_result["modelUsage"].items():
        if not isinstance(mu, dict):
            raise PipelineUsageError(f"runtime modelUsage entry for {model!r} is not an object")
        runtime_by_model[model] = {ours: int(mu.get(theirs, 0) or 0) for theirs, ours in _RUNTIME_CATEGORIES}

    per_model: dict[str, dict] = {}
    residual_by_model: dict[str, dict[str, int]] = {}
    inconsistent = False
    for model in sorted(set(transcript_by_model) | set(runtime_by_model)):
        ours = _flat_counts(transcript_by_model.get(model, TokenCounts()))
        theirs = runtime_by_model.get(model, {k: 0 for _, k in _RUNTIME_CATEGORIES})
        residual = {k: theirs[k] - ours[k] for k in ours}
        per_model[model] = {"transcripts": ours, "runtime": theirs, "runtime_minus_transcripts": residual}
        if any(v < 0 for v in residual.values()):
            inconsistent = True
            problems.append(f"{model}: transcripts report more tokens than the runtime ({residual})")
        elif any(residual.values()):
            residual_by_model[model] = residual

    if inconsistent:
        reconciliation_status = "inconsistent"
    elif residual_by_model:
        reconciliation_status = "runtime_residual"
    else:
        reconciliation_status = "exact"

    if residual_by_model:
        residual_costs = {m: _price_residual(m, r, pricing) for m, r in residual_by_model.items()}
        components.append({
            "component": "runtime_unattributed",
            "note": (
                "Tokens the runtime reports for this session that appear in no transcript "
                "(e.g. runtime-internal auxiliary calls). Counted in the total; not attributable to a phase."
            ),
            "token_usage_by_model": residual_by_model,
            "cost_by_model": {m: c.as_dict() for m, c in residual_costs.items()},
            "cost": (
                CostResult(
                    priced=True, amount=sum((c.amount for c in residual_costs.values()), Decimal(0)),
                    pricing_source=pricing.source, pricing_verified_date=pricing.verified_date,
                )
                if all(c.priced for c in residual_costs.values())
                else CostResult(priced=False, amount=None, reason="; ".join(
                    c.reason for c in residual_costs.values() if not c.priced
                ))
            ).as_dict(),
        })

    total_tokens = {k: sum(r[k] for r in runtime_by_model.values()) for _, k in _RUNTIME_CATEGORIES}
    unpriced = [c for c in components if not c["cost"]["priced"]]
    total_cost = (
        {"status": "priced", "amount": str(sum((Decimal(c["cost"]["amount"]) for c in components), Decimal(0))),
         "currency": "USD", "pricing_source": pricing.source, "pricing_verified_date": pricing.verified_date}
        if not unpriced else
        {"status": "unknown", "amount": None, "currency": "USD",
         "reason": "; ".join(f"{c['component']}: {c['cost'].get('reason')}" for c in unpriced)}
    )
    complete = not problems

    # Accounting coverage and pipeline completion are separate claims: a complete
    # accounting of a run that stopped early is that run's total, not the cost of a
    # full four-phase pipeline. The run's own outcome is carried alongside.
    try:
        run_summary = json.loads((run_directory / "run-summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        run_summary = {}
    run_outcome = {
        "final_verdict": run_summary.get("final_verdict"),
        "phases_completed": run_summary.get("phases_completed"),
    }

    runtime_cost = runtime_result.get("total_cost_usd")
    summary = build_run_usage_summary(run_id, repo_root=repo_root)
    summary.update({
        "schema_version": "2.0",
        "pipeline_boundary": {
            "kind": "dedicated_headless_session",
            "definition": (
                "Every model call made by the one headless Claude Code session that executed this "
                "run: the main (orchestrator) transcript, every subagent transcript under that "
                "session, and any runtime-reported usage no transcript accounts for."
            ),
            "session_id": session_id,
            "session_transcript_path": str(session_transcript),
            "subagent_transcript_dir": str(subagent_dir),
            "runtime_result_ref": runtime_result_ref,
            "runtime_result_subtype": runtime_result.get("subtype"),
            "runtime_result_is_error": runtime_result.get("is_error"),
            "first_user_prompt_excerpt": _first_user_prompt(session_transcript),
        },
        "run_outcome": run_outcome,
        "orchestrator": {"status": "measured", **components[0]},
        "components": components,
        "reconciliation": {
            "status": reconciliation_status,
            "per_model": per_model,
            "cross_file_duplicate_message_ids": cross_file_duplicates,
            "missing_agent_transcripts": missing_agent_transcripts,
            "problems": problems,
            "runtime_reported_total_cost_usd": None if runtime_cost is None else str(runtime_cost),
            "runtime_reported_cost_by_model_usd": {
                m: str(mu.get("costUSD")) for m, mu in runtime_result["modelUsage"].items()
            },
            "note": (
                "Runtime-reported costUSD is the runtime's own float estimate, retained for "
                "corroboration only; the stated cost is computed with Decimal from "
                "harness/model-pricing.json."
            ),
        },
        "coverage_status": "complete" if complete else "incomplete",
        "full_pipeline_total": (
            {"token_usage": total_tokens, "token_usage_by_model": runtime_by_model, "cost": total_cost,
             "run_final_verdict": run_outcome["final_verdict"],
             "run_phases_completed": run_outcome["phases_completed"]}
            if complete else None
        ),
        "full_pipeline_total_reason": (
            None if complete else "full total withheld: " + "; ".join(problems)
        ),
    })
    return summary


def write_pipeline_usage_summary(
    run_id: str, *, repo_root: Path, session_transcript: Path, runtime_result: dict, runtime_result_ref: str | None = None,
) -> tuple[Path, dict]:
    summary = finalize_pipeline_usage(
        run_id, repo_root=repo_root, session_transcript=session_transcript,
        runtime_result=runtime_result, runtime_result_ref=runtime_result_ref,
    )
    run_directory = evidence_io.run_dir(repo_root, run_id)
    evidence_io.ensure_run_dirs(run_directory)
    path = usage_summary_path(run_directory)
    evidence_io.atomic_write(path, (json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    return path, summary
