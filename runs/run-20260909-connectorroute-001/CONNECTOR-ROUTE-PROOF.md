# Live proof — MCP + REST dual-route connector, and the Architect docs connector

Run id: `run-20260909-connectorroute-001` · task `CONNECTOR-ROUTE-001` · 2026-09-09

This is a dedicated `live_cli.py` / `mcp_client` demonstration (the same substitution the
memory-loop, usage-accounting, and Obsidian milestones used for their own live proofs) —
**not** a full four-phase `/work` pipeline run. It closes two `ASSIGNMENT.md` requirements:

1. **§2.4** — "for at least one connector, implement **both** the MCP route and a
   REST-skill fallback, and document ... which one you kept and why." Connector: **Jira**.
2. **§2.2** — the Architect's "docs connectors" tool. Connector: **Obsidian** (read-only).

---

## 1. Jira dual route — both transports live-exercised

No `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` is set in this environment and no
Atlassian MCP server is configured, so a *resolved issue* cannot be live-proven through
either route without inventing a ticket (which `ASSIGNMENT.md` Part 8 forbids). What is
live-proven here is that **both transports genuinely run and classify honestly**, and that
the router's fallback/corroboration policy behaves as designed.

### `route-1.json` — `policy: "rest_first"` (exit code 1)

- Primary route **rest** (`jira_connector.resolve_issue`, `GET /rest/api/3/issue/{key}`):
  `connector_unavailable`.
- Because the primary was `connector_unavailable`, the router ran the **mcp** route to
  corroborate: a real `python -m harness.mcp.jira_server` subprocess was spawned, the
  JSON-RPC 2.0 handshake (`initialize` → `notifications/initialized`) completed,
  `tools/call get_issue` returned `mcp_ok`, and the tool payload's own `status` was
  `connector_unavailable`.
- `final_route: "none"`, `status: "connector_unavailable"`, reason: *"both the REST and
  MCP routes independently report no Jira connector is configured."*

### `route-2.json` — `policy: "mcp_first"` (exit code 1)

- Primary route **mcp** first (real subprocess, real protocol): `connector_unavailable`.
- Fallback route **rest**: `connector_unavailable`. Same corroborated final result.

### What this proves / does not prove

- **Proven live:** the MCP transport (real subprocess, real JSON-RPC 2.0, real
  `tools/call`) and the REST transport both run end-to-end; the router's deterministic
  policy (primary → authoritative-stop / corroborate / fallback) behaves as designed; a
  fallback never masked an authoritative negative (there was none to mask here); the
  retained routing record (`jira/routing/route-*.json`) distinguishes primary vs fallback
  vs final route and carries no `Authorization` header or raw HTTP body.
- **Not proven live:** a `resolved` issue through either route (no Jira credentials / no
  legitimate issue). That path is deterministic-test-proven only
  (`tests/test_orchestrator_connector_router.py`,
  `tests/test_orchestrator_mcp_client.py`), against injected fakes — never a real Jira.

### Kept route: **REST (Route B)**

Documented in `.claude/skills/jira/SKILL.md` §9 and `work/SKILL.md` "# Connector routing".
Rationale, matching the reference setup's own conclusion and this run's evidence: fewer
moving parts, directly debuggable (one retained request/response pair vs. a multi-step
cross-process exchange), minimal deterministic evidence, no `.mcp.json`-approval /
subprocess-health dependency, and identical authority (both routes hit the same endpoint
and identity check). MCP is retained as an implemented, live-exercised alternate.

---

## 2. Architect docs connector — Obsidian MCP read, live-proven affirmative

`OBSIDIAN_VAULT_PATH` was set locally (never committed) to the real configured vault
`C:\Users\caleb\Documents\Obsidian Vault`. One `mcp__obsidian__read_note` call was driven
through a real `python -m harness.mcp.obsidian_server` subprocess (real JSON-RPC 2.0),
against **exactly one harness-owned note** — no vault-wide search, no write:

- note: `Harness Run Summaries/run-summary-run-20260908-obsidianlive-001.md`
- tool status: `read` · `byte_count: 1552` · `line_count: 39`
- `content_sha256: 5e017d2de6e3aea81aad60001061d75739584e44fd1e9d7c4dfcf25e0c198679`
- **independently verified** outside the MCP boundary (`hashlib.sha256` of the raw file):
  identical SHA-256 and byte count. Also identical to the SHA-256 the 2026-09-08 Obsidian
  write-back milestone recorded for that note.
- corroboration: the non-MCP `read_obsidian_note` `live_cli.py` operation
  (`obsidian/read/read-1.json`) returned the identical SHA-256 / byte count for the same
  note — the MCP boundary adds transport, not authority.
- path-safety live-checked through the real subprocess: `read_note` with `note_path:
  "../../escape.md"` → `invalid_note_path` (retained in `logs/policy-events.jsonl`).

Evidence: `obsidian/read/mcp-read-1.json`, `obsidian/read/read-1.json`,
`logs/policy-events.jsonl` (three `obsidian_read` events).

The vault's existing personal content was untouched; no file was written, no `.tmp`
artifact created, no directory walked.

---

## 3. What was NOT done

- No commit, no push (`git rev-list --left-right --count origin/main...main` = `0 0`).
- No Jira write of any kind; no Jira issue invented.
- No Obsidian vault-wide search against the real vault; no Obsidian write.
- No full four-phase `/work` pipeline run — this is a targeted `live_cli.py` / `mcp_client`
  demonstration.
- The `.mcp.json` servers are registered but Claude-Code-side approval is a one-time user
  action; the servers here were driven directly by `harness/orchestrator/mcp_client.py`
  (which is exactly how a real MCP client drives them) and by `python -m` subprocess.
