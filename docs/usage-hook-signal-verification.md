# Usage-accounting milestone: live hook signal verification (2026-08-06)

Before designing `.claude/hooks/record_agent_usage.py` or `harness/orchestrator/usage.py`,
this milestone needed real answers to the questions Part 1 of the assignment requires:
which hook event fires after an agent turn, whether it carries `agent_id`, whether it
carries a transcript reference, and whether structured per-category token usage is
present anywhere reachable from that event. These were answered empirically, following
the same throwaway-diagnostic-hook method `docs/hooks-signal-verification.md` used for
`skill_enforcement.py`, not assumed or recalled from training data.

## Method

1. A throwaway diagnostic hook (`.claude/hooks/_diag_usage_capture.py`, deleted before
   this milestone was reported done) was registered in `.claude/settings.json` for
   `SubagentStop` (no matcher — this event has none) and `PostToolUse` (matcher `Agent`),
   appending every raw hook JSON payload it received to a local log file. It never
   blocked anything (always exited 0).
2. One real, isolated `Agent` dispatch (`subagent_type: "general-purpose"`,
   `run_in_background: false`) was made, reading only `README.md` — no `demo-repo/`
   contact, no historical run evidence touched. This confirmed the *initial* dispatch's
   `PostToolUse:Agent` and `SubagentStop` payloads.
3. The same agent was then resumed once via `SendMessage` (a second, unrelated read-only
   instruction) to observe whether a **staged continuation** re-fires either hook event —
   directly relevant to Part 5's "per message / cumulative / per API call / per agent
   session" question and to the harness's own staged Engineer/Quality-Engineer
   continuation protocol.
4. The captured payloads were compared field-by-field, and separately, the two involved
   transcript files were read directly: the subagent's own transcript
   (`agent_transcript_path` from the `SubagentStop` payload) and the main session's own
   transcript (`transcript_path`).

## Result

### Which hook fires, and when

- **`PostToolUse` (matcher `Agent`) fires exactly once per `Agent` tool call** — i.e.
  once for the *initial* dispatch only. It does **not** fire again when the same agent
  is later resumed via `SendMessage`, because that is a different tool
  (`SendMessage`, not `Agent`).
- **`SubagentStop` fires every time the named agent stops** — once after the initial
  dispatch's own turn, and again after the `SendMessage`-triggered resume's turn
  completed (confirmed: three total log entries in this diagnostic — one
  `PostToolUse:Agent`, two `SubagentStop`, matching one dispatch + one resume). This
  makes `SubagentStop` the only hook event that reliably covers a **staged, multi-turn**
  agent (the Engineer/Quality-Engineer continuation protocol `work/SKILL.md` already
  documents) — `PostToolUse:Agent` alone would silently miss every resumed turn's usage.

**Decision: `SubagentStop` is the capture hook**, not `PostToolUse:Agent`. It is the only
one of the two that fires on every turn a staged agent takes, matching real harness
dispatch shape, not just the single-shot Architect case this diagnostic happened to use.

### Real captured `SubagentStop` payload (initial dispatch)

```json
{
  "session_id": "c24e8ec2-5705-45b6-a6d0-22aeb23f60bf",
  "transcript_path": "C:\\Users\\caleb\\.claude\\projects\\...\\c24e8ec2-....jsonl",
  "cwd": "C:\\Users\\caleb\\Documents\\Projects\\AgenticHarness",
  "prompt_id": "0ef4ff13-ec9c-4563-98d8-39bf2a245424",
  "permission_mode": "acceptEdits",
  "agent_id": "ab6584484cc49effa",
  "agent_type": "general-purpose",
  "effort": {"level": "high"},
  "hook_event_name": "SubagentStop",
  "stop_hook_active": false,
  "agent_transcript_path": "C:\\Users\\caleb\\.claude\\projects\\...\\c24e8ec2-...\\subagents\\agent-ab6584484cc49effa.jsonl",
  "last_assistant_message": "# Agentic Harness",
  "background_tasks": [ ... ],
  "session_crons": []
}
```

Real captured `SubagentStop` payload after the `SendMessage`-triggered resume is
identical in shape, `agent_id`/`agent_transcript_path` unchanged, only
`last_assistant_message` and `background_tasks` differ. Full raw lines (three, trimmed
of unrelated background-task noise) retained verbatim at
`runs/hooks-diagnostic/usage-hook-diagnostic-log.jsonl`.

**`SubagentStop` does not itself carry structured token usage.** It carries
`agent_id`, `agent_type`, and — critically — `agent_transcript_path`, an absolute path
to that agent's own transcript file. Structured usage must be read from there.

### Real captured `PostToolUse:Agent` payload (initial dispatch only)

```json
{
  "hook_event_name": "PostToolUse",
  "tool_name": "Agent",
  "tool_input": {"description": "...", "prompt": "...", "subagent_type": "general-purpose", "run_in_background": false},
  "tool_response": {
    "status": "completed",
    "agentId": "ab6584484cc49effa",
    "agentType": "general-purpose",
    "content": [{"type": "text", "text": "# Agentic Harness"}],
    "resolvedModel": "claude-sonnet-5",
    "totalDurationMs": 3836,
    "totalTokens": 20426,
    "totalToolUseCount": 1,
    "usage": {
      "input_tokens": 2,
      "cache_creation_input_tokens": 1271,
      "cache_read_input_tokens": 19143,
      "output_tokens": 10,
      "cache_creation": {"ephemeral_5m_input_tokens": 1271, "ephemeral_1h_input_tokens": 0},
      "service_tier": "standard",
      "iterations": [{"input_tokens": 2, "output_tokens": 10, "cache_read_input_tokens": 19143, "cache_creation_input_tokens": 1271, "type": "message"}]
    },
    "toolStats": {"readCount": 1, "searchCount": 0, "bashCount": 0, "editFileCount": 0, "linesAdded": 0, "linesRemoved": 0, "otherToolCount": 0}
  },
  "tool_use_id": "toolu_01HKVxSysLvVohYybetyT4qP"
}
```

`PostToolUse:Agent`'s `tool_response.usage` *is* structured (input/output/cache-read/
cache-creation, individually) and even carries a `resolvedModel`, but since it never
fires on a staged resume it is not usable as this milestone's sole capture point. It
remains useful only as corroborating identity evidence (`tool_use_id`) — not adopted as
the primary mechanism.

### Structured usage in the subagent's own transcript file

`agent_transcript_path` (from `SubagentStop`) points at
`~/.claude/projects/<project-slug>/<session_id>/subagents/agent-<agent_id>.jsonl` — a
JSONL file, one JSON object per line, that accumulates **every** turn that agent has
taken so far (initial dispatch's messages, then the resumed turn's messages appended
after it — confirmed: 3 lines after the first `SubagentStop`, 13 lines after the second).
Each `type: "assistant"` line carries its own `message.model` (e.g. `"claude-sonnet-5"`)
and its own `message.usage` object:

```json
{
  "input_tokens": 2,
  "cache_creation_input_tokens": 19143,
  "cache_read_input_tokens": 0,
  "cache_creation": {"ephemeral_5m_input_tokens": 19143, "ephemeral_1h_input_tokens": 0},
  "output_tokens": 112,
  "service_tier": "standard",
  "iterations": [ ... ]
}
```

This is **per-message** (per real Anthropic API call), not cumulative within the file —
each `assistant`-type line has its own independent `usage`. The file as a whole is
**cumulative across every turn the agent has taken to date** (append-only, growing with
each resume). `input_tokens`/`output_tokens`/`cache_creation_input_tokens`/
`cache_read_input_tokens` are individually present on every observed message; a
sub-object `cache_creation` further splits creation into `ephemeral_5m_input_tokens`/
`ephemeral_1h_input_tokens`. No single field reports a runtime-computed dollar cost
anywhere in this transcript format — cost must be calculated from tokens.

A companion `agent-<agent_id>.meta.json` file (not a JSONL, one JSON object) carries
`agentType`, `description`, and `toolUseId` — the same id as `PostToolUse:Agent`'s
`tool_use_id` — establishing the link between the dispatch tool call and this specific
subagent conversation, independent of anything a hook payload claims.

### Aggregation semantics (Part 5's required statement)

- Per-message usage inside one agent's transcript file: **per API call**, not cumulative
  with prior messages in the same file.
- The transcript file itself: **cumulative across every turn/resume of that one agent**
  (append-only; a resume's turns are appended after the original dispatch's).
- `SubagentStop` firing multiple times for the same `agent_id` (once per stop, including
  once per resume) does **not** mean multiple agents — it means the same staged agent's
  transcript grew. The correct rule, and the one implemented in `usage.py`: on every
  `SubagentStop` capture, **recompute the full cumulative total from the entire current
  transcript file** and treat the result as this agent's authoritative running total —
  never sum multiple `SubagentStop`-triggered snapshots additively for the same
  `agent_id`, since each snapshot already contains everything the previous one did, plus
  the messages the new turn added.
- Two separate `Agent` dispatches (two different `agent_id` values, even for the same
  `subagent_type` in the same run, e.g. a repair-cycle's second Engineer... except this
  harness's own route-back protocol resumes the *same* agent id — see
  `work/SKILL.md`'s "Same-run logic-failure route-back") remain fully separate
  transcript files and fully separate usage records — confirmed structurally: each
  `agent-<id>.jsonl` file is keyed by the harness's own generated `agent_id`, and no
  cross-file aggregation ever happens implicitly.

### Main-orchestrator (non-subagent) transcript

The main session's own transcript file (`transcript_path`, e.g.
`c24e8ec2-....jsonl`, the file this very conversation is being recorded to) was also
inspected directly. Confirmed structurally: it contains **zero** `isSidechain: true`
entries and **zero** `attributionAgent` fields anywhere in this session — subagent
turns are never duplicated inline into the main transcript; they live exclusively under
`.../  <session_id>/subagents/agent-<id>.jsonl`. This means summing every `type:
"assistant"` entry's `message.usage` in the main transcript file gives a clean
orchestrator-only total with **no double-counting risk** against subagent totals, so
long as subagent totals are read only from their own separate files.

The registered `Stop` hook (`completion_guardrail.py`) already receives a payload with
this same `transcript_path` field (confirmed via its own docstring/existing tests
referencing `session_id`/`transcript_path`-shaped `Stop` payloads); a second `Stop` hook
entry can read it the same way `SubagentStop` reads `agent_transcript_path`, without
weakening the existing completion-guardrail's own logic.

### Human-readable summary line (explicitly not used for billing)

The diagnostic dispatch's own terminal summary line was also observed, unprompted, in
this session:

```
<usage>subagent_tokens: 20426 tool_uses: 1 duration_ms: 3836</usage>
```

`20426` is `PostToolUse:Agent`'s own `tool_response.totalTokens` field, a precomputed
sum Claude Code itself already reports — not a value this milestone invented by parsing
rounded UI text. Per the assignment's explicit prohibition, this rounded/human-readable
line (and `totalTokens`, its structured twin) is **never** used for the authoritative
per-agent token record or for billing math — only the individually-typed
`input_tokens`/`output_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`
fields read from the transcript are used, per Part 1's stated evidence-preference order.

## Retained evidence

The raw captured log lines (three total, one per hook fire in this diagnostic) are
preserved verbatim at `runs/hooks-diagnostic/usage-hook-diagnostic-log.jsonl` —
committed, immutable demonstration evidence, matching the existing convention
`docs/hooks-signal-verification.md` established. The diagnostic hook script
(`.claude/hooks/_diag_usage_capture.py`) and its `SubagentStop`/`PostToolUse:Agent`
registrations in `.claude/settings.json` were deleted immediately after this evidence was
captured — confirmed via `git diff .claude/settings.json` showing no change from the
committed version. No file under `demo-repo/` or `runs/` (other than the new evidence
file itself) was touched by this diagnostic.

## Pricing evidence (Part 6)

Separately, the same "verify before hardcoding" discipline was applied to pricing: rather
than trust the `claude-api` skill's own bundled, dated-but-cached (`cached: 2026-06-24`)
pricing table, the live, current official pricing page
(`https://platform.claude.com/docs/en/about-claude/pricing`) was fetched directly via
`WebFetch` on 2026-08-06 (this milestone's own working date) and its per-model USD rates
recorded into `harness/model-pricing.json` with that URL and fetch date as
`source`/`verified_date`, `externally_verified: true`. See that file's own
`_meta`/`_pricing_note` fields for the full citation and the Claude Sonnet 5
introductory-vs-standard pricing split (`$2`/`$10` per MTok through 2026-08-31, `$3`/`$15`
after).

## Architecture this evidence supports (smallest, per Part 1)

- Capture hook: `SubagentStop` only (no `PostToolUse:Agent` capture needed — its identity
  data is redundant with `SubagentStop` + the subagent's own transcript, and it would
  miss staged resumes entirely).
- Identity: `agent_id` from the hook payload, cross-referenced against this run's own
  retained `agent_dispatch` policy events (`runs/<run_id>/logs/policy-events.jsonl`)
  across every run directory — an `agent_id` that matches zero or more than one run's
  `agent_dispatch` event is quarantined, never guessed.
- Token source: the subagent's own transcript file at `agent_transcript_path`, summed
  per-category across every `type: "assistant"` entry present at capture time — always a
  full recomputation from the whole file, never an incremental add against a prior
  capture, so a duplicate or repeated `SubagentStop` for the same `agent_id` is naturally
  idempotent (same total recomputed twice) rather than a source of double-counting.
- Orchestrator (main-session) usage: the same summation technique applied to the main
  session's own `transcript_path`, captured from a second `Stop`-hook branch, retained
  under a distinct `component: "orchestrator"` record — never merged into the per-agent
  JSONL.
- Cost: `harness/model-pricing.json`, `decimal.Decimal` arithmetic, keyed by the exact
  `message.model` string observed in the transcript (`"claude-sonnet-5"`, etc.) — an
  unrecognized model string leaves cost `null`/unpriced rather than guessing a nearby
  model's rate.
