---
name: jira
description: Jira/Atlassian procedure for ticket-mode intake -- real issue resolution,
  requested-key vs returned-key identity validation, and honest failure classification.
  Invoked by the orchestrator (/work) before treating any ticket-shaped input as a real
  Jira ticket; never grants an independent tool allowlist of its own.
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

# 8. What this skill does not do (explicitly out of scope this milestone)
Only read-only ticket resolution (`GET /rest/api/3/issue/{key}`) is implemented and wired
into `/work`. **No create-ticket, edit-ticket, status-transition, or comment-posting
capability exists in this codebase** -- `ASSIGNMENT.md` §2.3/§2.4 names those as part of
the eventual `jira/` skill pack and connector table, but this milestone's own explicit
instructions forbid any real Jira mutation and forbid speculative write support ahead of
an actual requirement. Never fabricate a mutation capability by hand (a raw `curl`/POST
against the Jira REST API bypassing this skill) -- if a future ticket genuinely requires
writes, they belong here, behind the same identity-checked, evidence-retaining pattern
this file already establishes for reads, not as an ad hoc bypass.

# Safe operations (apply to every step above)
- Never write, transition, comment on, or otherwise mutate a real Jira issue.
- Never store `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` (or any credential derived
  from them, including the Basic-auth header) in this repository or in any retained
  evidence file.
- Never invent a Jira issue key, project key, custom-field id, or transition/status id.
- Never treat a `connector_unavailable`/`not_found`/`unauthorized`/`invalid_response`
  result as license to silently fall back to free-form mode on the caller's behalf --
  report the failure and stop; the caller decides whether to re-run in free-form mode.

# Boundaries -- this skill must not
- Decide whether ticket-mode input should be trusted over free-form input -- that is
  `work/SKILL.md`'s own "Parsing $ARGUMENTS" and "Ticket-mode Jira resolution" sections.
- Fabricate, soften, or infer a resolution result -- only a real `resolve_jira_issue`
  call's own classification may ever be reported.
- Duplicate the test-runner skill's role (test execution), the code-craftsmanship
  skill's role (change-quality review), or the github skill's role (Git/GitHub
  delivery) -- this skill covers Jira ticket intake only.
