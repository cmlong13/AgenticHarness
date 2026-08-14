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
- Aggregation semantics: each `assistant` line's `usage` is per-API-call, not
  cumulative; the transcript file itself is cumulative and append-only
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
- Coverage: only subagent usage is capturable this milestone -- the
  orchestrator's own final token usage is not knowable while the run summary
  is still being written in the same turn (the `Stop` event that would
  reveal it fires only once this turn ends). `coverage_status` is therefore
  always "partial" and a genuine `full_pipeline_total` is never computed;
  only an explicitly labeled `subagent_subtotal` is reported. See
  `build_run_usage_summary`.
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
    """The full result of parsing one subagent transcript file at capture
    time -- always a fresh, full recomputation (see module docstring), never
    an incremental update against a prior capture."""

    counts: TokenCounts = field(default_factory=TokenCounts)
    message_count: int = 0
    models_seen: list[str] = field(default_factory=list)
    skipped_lines: list[dict] = field(default_factory=list)  # {"line_no":..., "reason":...}


class TranscriptNotFoundError(Exception):
    pass


def parse_transcript_usage(transcript_path: Path) -> TranscriptUsage:
    """Reads a subagent transcript JSONL file end to end and sums the exact
    structured usage categories across every `type: "assistant"` line.
    Per-message usage is per-API-call (confirmed empirically, see module
    docstring) -- summing every assistant line in the file at capture time is
    therefore the correct full-recompute aggregation, not a double count.

    A line that isn't `type: "assistant"` is not part of usage accounting at
    all and is silently skipped (not reported as an anomaly) -- only an
    `assistant`-type line that is malformed (bad JSON, missing `message`,
    missing `message.usage`) is recorded in `skipped_lines`, never allowed to
    block a later valid line in the same file from being summed."""
    if not transcript_path.is_file():
        raise TranscriptNotFoundError(f"transcript file not found: {transcript_path}")

    result = TranscriptUsage()
    seen_models: list[str] = []

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
        usage = message.get("usage")
        if not isinstance(usage, dict):
            result.skipped_lines.append({"line_no": line_no, "reason": "message missing 'usage' object"})
            continue

        model = message.get("model")
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
        result.counts = result.counts + line_counts
        result.message_count += 1

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

    return PricingTable(
        models=models,
        source=str(meta.get("source", "")),
        verified_date=str(meta.get("verified_date", "")),
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
    rates = pricing.models.get(model)
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
        "schema_version": "1.0",
        "record_kind": "agent_usage",
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
        "models_seen": transcript.models_seen,
        "primary_model": model,
        "token_usage": transcript.counts.as_dict(),
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
        model = transcript.models_seen[0] if transcript.models_seen else None
        cost = price_usage(model, transcript.counts, pricing)
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
    for record in agents:
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
            "agent_count": len(agents),
            "token_usage": subtotal_counts.as_dict(),
            "cost": {
                "priced": any_priced and not unpriced_agent_ids,
                "amount": str(priced_amount) if any_priced else None,
                "currency": "USD",
                "unpriced_agent_ids": unpriced_agent_ids,
                "note": (
                    "partial: one or more agents are unpriced" if unpriced_agent_ids
                    else ("complete over captured agents" if any_priced else "no priced agents")
                ),
            },
        },
        "orchestrator": {
            "status": "not_measured",
            "reason": (
                "The main orchestrator session's own token usage is not observable while "
                "this run's own artifacts are still being written -- the Stop hook that "
                "would reveal it fires only at the very end of the turn, after any "
                "run-summary this milestone writes. Not captured this milestone; see "
                "PROJECT_SPEC.md."
            ),
            "token_usage": None,
            "cost": None,
        },
        "coverage_status": "partial",
        "full_pipeline_total": None,
        "full_pipeline_total_reason": (
            "orchestrator usage was not captured this milestone -- a subagent subtotal is "
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
