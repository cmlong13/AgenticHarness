---
name: jira
description: Jira/Atlassian procedure for ticket-mode intake -- real issue resolution,
  requested-key vs returned-key identity validation, and honest failure classification --
  plus the jira skill pack's three procedures read-ticket (read-only), create-ticket and
  edit-ticket (state-changing, explicitly authorized only), with FIELD_REFERENCE.md as
  the field reference. Invoked by the orchestrator (/work) before treating any
  ticket-shaped input as a real Jira ticket and before any Jira read or write; never
  grants an independent tool allowlist of its own.
---

# Purpose
Hold every Jira action `/work`'s ticket mode performs to one standard: never guess a
ticket, never invent a field, never treat a failed lookup as an empty issue, and never
let a resolved ticket's content bypass the same scope/policy checks a free-form prompt
would go through. This skill does not execute anything itself -- it is the procedure the
orchestrator (the only session that ever resolves a Jira ticket in this harness; see
`work/SKILL.md` standing rule 3) follows, using `harness/orchestrator/live_cli.py`'s
`resolve_jira_issue` operation (`harness/orchestrator/jira_connector.py`) for the one
real HTTP call and every piece of retained evidence.

**Skill pack index** (`ASSIGNMENT.md` §2.3). Details are in section 10.

| Procedure | `live_cli.py` operation | Kind |
|---|---|---|
| `read-ticket` | `read_ticket` (also `resolve_jira_issue` for ticket-mode intake, and `resolve_jira_issue_routed` for the MCP route) | read-only |
| `create-ticket` | `create_ticket` | state-changing, explicit authorization only |
| `edit-ticket` | `edit_ticket` | state-changing, explicit authorization only |
| field reference | [`FIELD_REFERENCE.md`](FIELD_REFERENCE.md) | the only fields any procedure reads or writes; no custom field ids are configured |

Sections 1-7 describe the read path that all three procedures share. Section 9 covers
routing and section 10 covers the procedures themselves.

# The cardinal rule
**A ticket ID is not a resolved ticket.** Corollaries, all non-negotiable:
- Never guess, invent, or auto-complete a Jira issue key, a project key, a custom-field
  id, or a transition/status id. `resolve_issue` only ever looks up the exact key it was
  given.
- A ticket-shaped string typed into `/work` is a *request* to resolve a ticket, not
  evidence that the ticket exists. Only a real (or, in a test, an injected fake --
  see `jira_connector.py`'s own docstring) HTTP response, checked against the requested
  key, may ever be treated as "this ticket is real."
- The returned issue's `key` (and the project key embedded in it) is always compared
  against the requested key before anything is ever classified `resolved` --
  `jira_connector.parse_issue_payload`. A mismatch is `identity_mismatch`, never silently
  accepted.
- A failed lookup (404, 401/403, malformed body, no connector configured) is never
  treated as an empty or placeholder issue -- every non-`resolved` outcome carries its
  own explicit classification and reason.
- Resolved ticket content (summary/description/acceptance criteria) is *input evidence*
  for Discovery, exactly like a free-form prompt -- it never bypasses the pre-dispatch
  checklist, the orchestrator's refusal authority, or the Protected Path list. See
  `work/SKILL.md`'s "Ticket-mode Jira resolution" section for how the resolved content
  actually feeds Discovery.

# 1. Issue-key shape validation (before any network call)
Normalize the caller's input with `jira_connector.normalize_issue_key` (strip + uppercase
only -- never reformat, reorder, or guess a project prefix), then let
`resolve_jira_issue` validate its shape (`jira_connector.is_valid_issue_key`, a bounded
`^[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,9}$`). A malformed key is classified
`invalid_issue_key` and **no network call is ever attempted** for it -- confirmed by
`tests/test_orchestrator_jira_connector.py::TestResolveIssue::
test_invalid_key_shape_never_attempts_network_call`.

# 2. Connector availability
Credentials (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) are read fresh from the
real environment on every call (`jira_connector.JiraCredentials.from_env`) -- never
cached, never accepted as a request field, never assumed present. When any of the three
is missing or blank, the outcome is a genuine, honest `connector_unavailable` --
**not a fabricated success and not a silent fallback to free-form mode.** No network call
is attempted in this case either.

# 3. Real issue resolution (the one real HTTP call)
When credentials are present, `resolve_jira_issue` issues exactly one real
`GET {base_url}/rest/api/3/issue/{key}?fields=summary,description,issuetype,status,project,created,updated`
via `jira_connector.fetch_issue` -- standard fields only, never a guessed
`customfield_XXXXX` id. Authentication is Basic auth over the email/API-token pair
(`jira_connector.build_auth_header`); the header is never retained in any evidence file
(confirmed by `tests/test_orchestrator_live_cli.py::TestResolveJiraIssue::
test_resolved_issue_retained_and_reported`'s no-leakage assertions).

# 4. Requested-key vs returned-key identity validation
Before anything is classified `resolved`, `jira_connector.parse_issue_payload` requires
the response body's own `key` field to exactly equal the requested (normalized) key, and
the response's `fields.project.key` (when present) to match the project-key component
embedded in that same issue key. Either mismatch is `identity_mismatch` /
`invalid_response` -- a different ticket, or an internally inconsistent Jira response,
is never silently accepted as the one requested.

# 5. Acceptance-criteria extraction (best-effort, never authoritative)
`jira_connector.extract_acceptance_criteria` looks for a line matching "Acceptance
Criteria" (case-insensitive, optional colon) in the issue's plain-text description
(`jira_connector.adf_to_text`, a deterministic Atlassian-Document-Format walk) and
returns the non-empty, bullet-stripped lines that follow it, up to the next blank line.
Returns `None` -- never an empty list -- when no such heading exists at all; this is a
heuristic input to Discovery's own reasoning, never a substitute for Discovery actually
authoring `scope.json`'s own `acceptance_criteria`.

# 6. Retained evidence (unconditional, every classification)
Every call to `resolve_jira_issue` retains `runs/<run_id>/jira/issue-resolution.json`
(`evidence_io.retain_jira_evidence`, collision-guarded -- never silently overwrites a
prior resolution for the same run) and a matching `jira_issue_resolution` policy event,
**regardless of the outcome** -- a `not_found` or `unauthorized` result is retained just
as faithfully as a `resolved` one. The retained document never contains the
`Authorization` header or the raw API token.

# 7. Failure classification (every outcome is one of these seven)
| status | meaning |
|---|---|
| `resolved` | the requested key was found and the returned key matches it exactly -- the only status that may ever be reported as "this ticket exists" |
| `not_found` | Jira returned 404 for the requested key |
| `unauthorized` | Jira returned 401/403 -- the configured credentials cannot read this issue |
| `connector_unavailable` | no real Jira connector is configured in this environment (missing env var) -- no network call was attempted |
| `identity_mismatch` | Jira's response named a different issue key (or an internally inconsistent project key) than the one requested |
| `invalid_issue_key` | the caller's input does not have a valid Jira issue-key shape -- no network call was attempted |
| `invalid_response` | a transport failure, a non-200/404/401/403 status, or a response body that isn't valid JSON or is missing required fields |

Report the exact classification and `reason` to the user for anything other than
`resolved` -- never soften a `not_found`/`unauthorized`/`connector_unavailable` into "the
ticket might exist" or silently retry with a guessed key.

# 8. What this skill does not do
- **No status transitions.** A status change needs a workflow transition id, which varies
  by workflow and is never guessed. `edit_ticket` refuses `status` and `resolution` with
  `unsupported_field`. The completion-comment and status-transition uses in
  `ASSIGNMENT.md` §2.4's connector table are therefore **not implemented**.
- **No comments.** No procedure posts a comment. `comment` is refused with
  `unsupported_field`.
- **No other fields.** Assignee, reporter, labels, priority, components, fix versions, due
  date, parent, and every custom field are refused. See `FIELD_REFERENCE.md`.
- **No MCP writes.** The MCP server exposes only the read-only `get_issue` tool (section 9).
- **No retries.** A failed or `indeterminate` write is reported, never retried
  automatically.
- **No hand-rolled calls.** Never call the Jira REST API by hand (`curl`, `Invoke-RestMethod`,
  a `python -c` `urllib` call). `.claude/hooks/skill_enforcement.py` blocks a `Bash`
  command that pairs a Jira `/rest/api/` path with an HTTP client in command position
  (`raw_jira_api_bypass`). This is a substring heuristic, not a sandbox.

# 9. Connector routing -- MCP route + REST fallback (ASSIGNMENT.md §2.4)
`ASSIGNMENT.md` §2.4 requires that, "for at least one connector, implement **both** the
MCP route and a REST-skill fallback, and document ... which one you kept and why." **Jira
is that connector** -- the assignment's own example ("the reference setup abandoned the
Atlassian MCP server in favor of curl-based skills because raw REST calls were more
reliable and debuggable").

**Route B -- REST (kept).** Everything in sections 1-8 above: a direct
`jira_connector.resolve_issue` call to `GET /rest/api/3/issue/{key}`, reached through
`resolve_jira_issue`. This is the route this harness keeps as its production Jira
connector, and the route ticket-mode `/work` uses, unchanged.

**Route A -- MCP.** `harness/mcp/jira_server.py`, a real project-local JSON-RPC 2.0 stdio
MCP server exposing one read-only tool, `get_issue(issue_key)`, that itself calls the
same `jira_connector.resolve_issue`. It is registered in the project-root `.mcp.json`.
`harness/orchestrator/mcp_client.py` drives it (`initialize` ->
`notifications/initialized` -> `tools/call`), classifying every MCP-layer failure
(`mcp_unavailable` / `mcp_transport_error` / `mcp_protocol_error` /
`mcp_invalid_response` / `mcp_tool_error`).

**Route selection** is `harness/orchestrator/connector_router.py`, reached through
`live_cli.py`'s `resolve_jira_issue_routed`:
- `policy: "rest_first"` (default): REST first; an authoritative outcome (`resolved` /
  `not_found` / `unauthorized` / `identity_mismatch` / `invalid_issue_key`) is final and
  the MCP route is not run; `connector_unavailable` runs the MCP route only to
  corroborate; a transport-class `invalid_response` falls back to MCP.
- `policy: "mcp_first"`: the mirror image, used to exercise/prefer MCP.
- **Fallback is forbidden past a real `unauthorized` or `identity_mismatch`** -- a
  fallback route must never mask an authorization failure or silently accept a different
  ticket.

**Route identity / success proof.** Each route returns a normalized `RouteResult`; the
final `RouteOutcome` records the primary route, whether fallback ran and why, the
`final_route`, and one `ROUTE_STATUSES` value. `resolved_via_rest` / `resolved_via_mcp` /
`rest_failed_mcp_resolved` / `mcp_failed_rest_resolved` are the only affirmative ones,
and only when a route genuinely returned a matching, identity-checked issue -- never
because fallback code exists.

**Evidence.** `runs/<run_id>/jira/routing/route-<n>.json` (structurally distinct from
`issue-resolution.json`) + a `connector_routing` policy event, retained on every outcome.
No `Authorization` header or raw HTTP body is ever in it.

**Secret handling / least privilege.** The MCP server reads `JIRA_*` fresh from its own
process environment; a `get_issue` argument carrying `base_url`/`email`/`api_token`/`env`
is rejected outright. The tool result carries only the connector's classification plus a
whitelist of issue fields.

**Routes per procedure** (`connector_router.JIRA_OPERATION_ROUTES`, deterministic):
`read-ticket` may use REST and MCP as described above. `create-ticket` and `edit-ticket`
are **REST only, with no fallback** (`connector_router.select_mutation_route`). There are
two reasons. First, a write is not idempotent: a REST write that timed out may already
have been applied, so repeating it over MCP could create or change the issue twice.
Second, a write tool on the MCP server would be callable directly as an `mcp__jira__*`
tool, which would bypass `live_cli.py`'s authorization check and evidence retention.
Each mutation's evidence file records this routing decision (`allowed_routes`,
`selected_route`, `fallback_attempted: false`, and the reason).

**The kept route: REST.** Fewer moving parts (no subprocess, no second protocol surface),
directly debuggable (one retained request/response pair), minimal deterministic evidence,
no `.mcp.json`-approval / server-health dependency, and identical authority (both routes
hit the same endpoint and the same identity check). MCP is retained as an implemented,
live-exercised alternate so "implement both" is genuinely met and this decision rests on
real evidence.

# 10. Jira skill pack: read-ticket, create-ticket, edit-ticket
Each procedure is one `live_cli.py` operation (`python -m harness.orchestrator.live_cli
--request-file <file>`; see `work/SKILL.md`). The HTTP call and all validation are in
`harness/orchestrator/jira_connector.py`. Exit `0` means an affirmative result, exit `1` a
well-formed negative result (never evidence that anything changed in Jira), exit `2` a
request-construction error.

## `read-ticket` -- operation `read_ticket` (read-only)
Reads one issue by key. The logic is the same as sections 1-7: key-shape check, then
credentials from the environment, then one `GET`, then an identity check. Fields: `run_id`,
`task_id`, `issue_key`, optional `filename` (default `read-ticket-1.json`; increment it for
another read in the same run). It returns only the standard fields in
`FIELD_REFERENCE.md` and one of the seven statuses in section 7. It retains
`runs/<run_id>/jira/<filename>` and a `jira_read_ticket` policy event. It sends only
`GET`.
- Ticket-mode intake keeps using `resolve_jira_issue`, which runs the same read and
  retains `issue-resolution.json`.
- To read over the MCP route, use `resolve_jira_issue_routed` (section 9).

## Rules shared by create-ticket and edit-ticket (state-changing)
- **Explicit authorization comes first.** `authorized` must be the JSON boolean `true`.
  The string `"true"`, `1`, and other truthy values are refused. `authorization_source`
  must name where the authorization came from: the ticket key that requests the
  mutation, or the user's instruction quoted. These fields *record* permission the user
  or orchestrator already granted; setting them never grants permission. Only the
  orchestrator sets them (`work/SKILL.md` standing rule 22). Without them the result is
  `not_authorized`, and nothing else happens: no validation, no environment read, no
  network call.
- **Validation happens before any call.** Issue and project keys are checked for exact
  shape, the issue type against the allowlist, field names against `FIELD_REFERENCE.md`,
  and field values for length and control characters. Failures return `invalid_input` /
  `invalid_issue_key` / `unsupported_issue_type` / `unsupported_field`, and nothing is
  sent. Any `customfield_...` name is refused. No project key, issue type, assignee,
  label, or workflow state is ever filled in for you.
- **The instance must be the expected one.** `expected_base_url` is required and must
  equal the configured `JIRA_BASE_URL` (compared case-insensitively on scheme and host,
  ignoring a trailing slash). A mismatch returns `wrong_instance`, and nothing is sent.
- **Unavailable means unavailable.** Missing `JIRA_*` returns `connector_unavailable`,
  nothing is sent, and the result is exit `1`. It is never reported as success.
- **Routes: REST only, no fallback.** `route` may be omitted or `"rest"`. Anything else
  returns `unsupported_route`. See section 9 for why a mutation never uses MCP or falls
  back.
- **Verify, don't trust.** Jira's own `201`/`204` is never the success signal. The issue is
  re-read independently with the same identity-checked `GET`, and each requested value is
  compared with what Jira now holds. Only a full match is `created` / `updated`. A 2xx
  response followed by a failed or mismatched re-read is `created_unverified` /
  `updated_unverified`. Report it as "Jira accepted the request; the result was not
  verified", never as success.
- **Indeterminate is its own outcome.** A transport failure or a `5xx` on the write means
  the change may or may not have been applied. That is `indeterminate`. Re-read with
  `read_ticket` before deciding anything, and never retry blindly.
- **Other failure statuses:** `unauthorized` (401/403), `not_found` (404), `rejected` (400,
  with Jira's own error message), and `invalid_response`.
- **Evidence.** Before anything is sent, `live_cli.py` checks the evidence path
  `runs/<run_id>/jira/<filename>`. If it already exists, the result is `blocked` and
  nothing is sent. Afterwards it always retains that file and a policy event,
  refusals included. The file records the requested operation and values, `authorized`,
  `authorization_source`, the routing decision, the issue key, the mutation request
  (method, URL, JSON body) and the response (status and bounded body), the independent
  verification record (expected, observed, mismatches), the final status, and the
  reason. It never contains the `Authorization` header or the API token.

## `create-ticket` -- operation `create_ticket`
Fields: `run_id`, `task_id`, `project_key`, `issue_type` (`Task` / `Bug` / `Story`),
`fields` (`summary` required, `description` optional, nothing else), `expected_base_url`,
`authorized`, `authorization_source`, optional `filename` (default `create-ticket.json`),
optional `route`. Sends one `POST /rest/api/3/issue`. The key Jira returns must be in the
requested project. The re-read must then show the requested project, issue type, summary,
and description. Statuses: `created` / `created_unverified` / `not_authorized` /
`invalid_input` / `unsupported_issue_type` / `unsupported_field` / `unsupported_route` /
`connector_unavailable` / `wrong_instance` / `unauthorized` / `rejected` /
`indeterminate` / `invalid_response`. Policy event: `jira_create_ticket`.

## `edit-ticket` -- operation `edit_ticket`
Fields: `run_id`, `task_id`, `issue_key`, `fields` (any of `summary`, `description`; at
least one), `expected_base_url`, `authorized`, `authorization_source`, optional `filename`
(default `edit-ticket.json`), optional `route`. The issue is pre-read first. If the
pre-read returns anything other than `resolved`, that status is returned (`not_found`,
`unauthorized`, `identity_mismatch`, ...) and nothing is sent. Then one
`PUT /rest/api/3/issue/{key}` sends **only** the supplied fields, and the issue is re-read
and compared. Statuses: `updated` / `updated_unverified` / `not_authorized` /
`invalid_input` / `invalid_issue_key` / `unsupported_field` / `unsupported_route` /
`connector_unavailable` / `wrong_instance` / `not_found` / `unauthorized` /
`identity_mismatch` / `rejected` / `indeterminate` / `invalid_response`. Policy event:
`jira_edit_ticket`. It never changes status and never comments.

# Safe operations (apply to every step above)
- Never create, edit, transition, comment on, or otherwise mutate a real Jira issue except
  through `create_ticket` / `edit_ticket` with explicit authorization (section 10).
- Never store `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` (or any credential derived
  from them, including the Basic-auth header) in this repository or in any retained
  evidence file.
- Never invent a Jira issue key, project key, issue type, custom-field id, or
  transition/status id. `FIELD_REFERENCE.md` lists every field; anything else does not
  exist for this skill.
- Never treat a `connector_unavailable`/`not_found`/`unauthorized`/`invalid_response`
  result as license to silently fall back to free-form mode on the caller's behalf --
  report the failure and stop; the caller decides whether to re-run in free-form mode.

# Boundaries -- this skill must not
- Decide whether ticket-mode input should be trusted over free-form input -- that is
  `work/SKILL.md`'s own "Parsing $ARGUMENTS" and "Ticket-mode Jira resolution" sections.
- Fabricate, soften, or infer a resolution result -- only a real `resolve_jira_issue`
  call's own classification may ever be reported. The same applies to `create_ticket` /
  `edit_ticket`: only `created` / `updated` may be reported as a change made.
- Decide whether a ticket mutation is authorized -- that is `work/SKILL.md`'s standing
  rule 22. `authorized` / `authorization_source` only record and enforce an authorization
  that already exists.
- Duplicate the test-runner skill's role (test execution), the code-craftsmanship
  skill's role (change-quality review), or the github skill's role (Git/GitHub
  delivery) -- this skill covers Jira ticket intake and the jira skill pack only.
