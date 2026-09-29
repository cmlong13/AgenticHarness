"""Deterministic Jira/Atlassian connector boundary for the orchestrator.

Distinct from harness/orchestrator/intake.py (mode parsing + normalization) and
harness/orchestrator/evidence_io.py (evidence persistence): this module answers "what
does Jira actually say about this issue right now," never "what did the caller assert."

No MCP Atlassian server is configured in this environment (confirmed at milestone start:
`claude mcp list` reports no servers, and no JIRA_*/ATLASSIAN_* environment variable is
set). Per ASSIGNMENT.md Part 8 ("use the actual Jira/Atlassian integration available in
this environment... do not write a fake connector and call it real integration") and the
documented precedent this repository already established for GitHub (a real CLI/REST-
style connector, not an MCP server, per PROJECT_SPEC.md's GitHub-skill milestone), the one
real connector here is a genuine Jira Cloud REST API v3 call
(`GET {base_url}/rest/api/3/issue/{key}`) made with the stdlib's `urllib.request` -- no
new dependency, no simulated success path. Credentials are read only from environment
variables (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN); none are stored in this repository.
When credentials are absent -- the case in this environment today -- `resolve_issue`
returns a genuine `connector_unavailable` result: this is an honest, real "no connector is
configured" outcome, not a fabricated success.

This module is Route B (REST, the kept route) of the dual-route Jira connector; the MCP
route (harness/mcp/jira_server.py) wraps `resolve_issue` from here, and
harness/orchestrator/connector_router.py selects between them for reads. The two
state-changing skill-pack procedures, `create_issue` (create-ticket) and `edit_issue`
(edit-ticket), live here too and are REST-only by design -- see connector_router's
JIRA_OPERATION_ROUTES for why a mutation never falls back to another route. Every field
either procedure reads or writes is enumerated in this module's field-reference
constants (READ_FIELDS / CREATE_FIELDS / EDIT_FIELDS / SUPPORTED_ISSUE_TYPES /
CUSTOM_FIELD_IDS), which .claude/skills/jira/FIELD_REFERENCE.md documents.

Every real HTTP call goes through a swappable `HttpTransport` (a plain
`Callable[[str, str, dict], HttpResult]`), exactly the seam
harness/orchestrator/github.py's `CommandRunner` already establishes for `git`/`gh`: a
test can inject a transport that returns a fabricated Jira response (a different issue
key, a 404, a malformed body) without ever contacting a real Jira instance, and confirm
the classification layer refuses to treat that as a resolved match. Production code
(harness/orchestrator/live_cli.py) always uses `DEFAULT_TRANSPORT`, the real
`urllib.request`-based call -- the simulated seam is never exposed through the live CLI
boundary, so a live `/work` run can never fabricate a Jira resolution the way a
deterministic test fixture deliberately can.

Rules this module exists to enforce, per ASSIGNMENT.md's Jira-skill requirements:
- Never guess a Jira issue key -- `resolve_issue` only ever looks up the key it was given.
- Never invent a custom-field id -- only Jira's own standard fields (summary, description,
  issuetype, status, project) are read; acceptance criteria are extracted, best-effort,
  from the standard `description` field's own text, never from a guessed
  `customfield_XXXXX` id (see `extract_acceptance_criteria`'s own docstring for the
  heuristic and its limits).
- Never treat a failed lookup as an empty issue -- every non-`resolved` outcome carries
  its own explicit classification and reason; none of them synthesize a placeholder issue.
- Never silently accept a mismatched issue -- the returned `key` (and, transitively, the
  project key embedded in it) is always compared against the requested key before a result
  is ever classified `resolved`.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

# A Jira Cloud project key: 2-10 characters, first character a letter, remaining
# characters alphanumeric (Jira's own default project-key constraint). The issue number
# is 1-10 digits with no leading zero. Bounding both lengths is what makes "overly long
# value" a distinct, deterministic negative case rather than an unbounded regex.
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,9}$")

RESOLUTION_STATUSES = frozenset(
    {"resolved", "not_found", "unauthorized", "connector_unavailable", "identity_mismatch", "invalid_issue_key", "invalid_response"}
)

# ---------------------------------------------------------------------------
# Field reference -- the single source of truth .claude/skills/jira/FIELD_REFERENCE.md
# documents (tests/test_orchestrator_jira_connector.py::TestFieldReference keeps the two
# in lockstep). Only Jira's own standard, system-defined fields appear here.
# ---------------------------------------------------------------------------

# Standard fields read-ticket requests (`?fields=` on GET /issue/{key}).
READ_FIELDS = ("summary", "description", "issuetype", "status", "project", "created", "updated")

# Caller-settable fields for create-ticket (inside the request's `fields` object; the
# project and issue type are separate, required request fields). `summary` is required.
CREATE_FIELDS = ("summary", "description")
CREATE_REQUIRED_FIELDS = ("summary",)

# Caller-settable fields for edit-ticket -- at least one must be supplied.
EDIT_FIELDS = ("summary", "description")

# Issue types create-ticket accepts: the standard Jira Software default types that need
# no parent issue and no custom field to create. Epic (Epic Name is a custom field on
# company-managed projects) and Sub-task (requires a parent) are deliberately excluded.
# Whether a given project actually enables one of these is still Jira's decision -- a
# project without it answers 400, classified `rejected`, never retried with another type.
SUPPORTED_ISSUE_TYPES = ("Task", "Bug", "Story")

# Configured custom-field ids (logical name -> "customfield_<digits>"). Deliberately
# empty: no custom field has been authorized for this harness, and an agent must never
# guess one. See FIELD_REFERENCE.md "Adding a custom field safely".
CUSTOM_FIELD_IDS: dict = {}

# Field names that are real Jira fields but that this skill deliberately does not set,
# each with the reason reported back in an `unsupported_field` refusal.
_UNSUPPORTED_FIELD_REASONS = {
    "status": "status changes need a workflow transition, which this skill does not perform",
    "resolution": "resolution is set by a workflow transition, which this skill does not perform",
    "assignee": "assignee is never set -- account ids are never guessed",
    "reporter": "reporter is never set -- account ids are never guessed",
    "labels": "labels are not supported by this skill",
    "priority": "priority is not supported by this skill",
    "components": "components are not supported by this skill",
    "fixVersions": "fix versions are not supported by this skill",
    "duedate": "due date is not supported by this skill",
    "parent": "parent links are not supported by this skill",
    "comment": "comments are not posted by this skill",
    "project": "project is a separate, required create-ticket field and can never be edited",
    "issuetype": "issue type is a separate, required create-ticket field and can never be edited",
}

SUMMARY_MAX_LEN = 255  # Jira's own summary limit.
DESCRIPTION_MAX_LEN = 32_000  # Under Jira's 32,767-character text-field limit.
AUTHORIZATION_SOURCE_MAX_LEN = 500

CREATE_STATUSES = frozenset(
    {
        "created", "created_unverified", "not_authorized", "invalid_input", "unsupported_issue_type",
        "unsupported_field", "connector_unavailable", "wrong_instance", "unauthorized", "rejected",
        "indeterminate", "invalid_response",
    }
)
EDIT_STATUSES = frozenset(
    {
        "updated", "updated_unverified", "not_authorized", "invalid_input", "invalid_issue_key",
        "unsupported_field", "connector_unavailable", "wrong_instance", "not_found", "unauthorized",
        "identity_mismatch", "rejected", "indeterminate", "invalid_response",
    }
)

_ENV_BASE_URL = "JIRA_BASE_URL"
_ENV_EMAIL = "JIRA_EMAIL"
_ENV_API_TOKEN = "JIRA_API_TOKEN"

_MAX_RETAINED_BODY_CHARS = 4000  # Part 4: retain a minimal auditable subset, not a dump.
_HTTP_TIMEOUT_SECONDS = 15


class JiraError(Exception):
    """A classified Jira failure. `code` is always one of RESOLUTION_STATUSES."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


def is_valid_issue_key(value: object) -> bool:
    """True only for a genuine, bounded Jira issue-key shape. Case-sensitive on purpose --
    callers wanting case-insensitive matching (Jira itself accepts lowercase input in
    practice) must normalize with `normalize_issue_key` first, so "was this exactly the
    shape Jira expects" and "did the caller type it in a different case" stay distinguishable."""
    return isinstance(value, str) and bool(ISSUE_KEY_RE.match(value))


def normalize_issue_key(value: str) -> str:
    """Uppercases and strips only -- never reformats, reorders, or guesses a project
    prefix. Callers must still check `is_valid_issue_key` on the result."""
    return value.strip().upper()


def project_key_from_issue_key(issue_key: str) -> str:
    """The project-key component is always the text before the final '-NUMBER' suffix --
    valid only for an already-`is_valid_issue_key`-confirmed key."""
    return issue_key.rsplit("-", 1)[0]


@dataclass(frozen=True)
class HttpResult:
    """The command-result boundary this module's tests inject a fake value at (see the
    module docstring) -- mirrors github.py's CommandResult in spirit."""

    status_code: int
    body: str
    reason: str = ""

    def as_dict(self) -> dict:
        body = self.body
        truncated = len(body) > _MAX_RETAINED_BODY_CHARS
        if truncated:
            body = body[:_MAX_RETAINED_BODY_CHARS]
        return {
            "status_code": self.status_code,
            "body": body,
            "body_truncated": truncated,
            "reason": self.reason,
        }


# (method, url, headers) for a GET; write calls pass a fourth positional argument, the
# encoded JSON request body. A read-only fake may therefore keep the 3-argument shape.
HttpTransport = Callable[..., HttpResult]


def _default_transport(method: str, url: str, headers: dict, body: bytes | None = None) -> HttpResult:
    """The one real HTTP entry point in this module. Never raises for a normal HTTP
    error status (Jira's own 404/401/403 responses are legitimate, classified
    HttpResults, not exceptions) -- only a genuine transport failure (DNS, timeout,
    connection refused) is caught and folded into status_code -1."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return HttpResult(status_code=resp.status, body=resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return HttpResult(status_code=exc.code, body=exc.read().decode("utf-8", errors="replace"), reason=str(exc.reason))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return HttpResult(status_code=-1, body="", reason=str(exc))


DEFAULT_TRANSPORT: HttpTransport = _default_transport


@dataclass(frozen=True)
class JiraCredentials:
    base_url: str
    email: str
    api_token: str

    @classmethod
    def from_env(cls, env: dict | None = None) -> "JiraCredentials | None":
        """Returns None -- never raises -- when any of the three required environment
        variables is missing or empty. A missing-credentials state is a legitimate,
        expected `connector_unavailable` outcome in this environment, not a bug."""
        import os

        source = env if env is not None else os.environ
        base_url = source.get(_ENV_BASE_URL, "").strip()
        email = source.get(_ENV_EMAIL, "").strip()
        api_token = source.get(_ENV_API_TOKEN, "").strip()
        if not base_url or not email or not api_token:
            return None
        return cls(base_url=base_url.rstrip("/"), email=email, api_token=api_token)


def build_auth_header(credentials: JiraCredentials) -> dict:
    """Basic auth, per Jira Cloud's own documented REST API v3 authentication scheme.
    Never retained in evidence -- see fetch_issue's caller (live_cli.py's
    op_jira_resolve_issue), which logs only method/url, never headers."""
    token = base64.b64encode(f"{credentials.email}:{credentials.api_token}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


def issue_url(base_url: str, issue_key: str) -> str:
    return f"{base_url}/rest/api/3/issue/{issue_key}"


def browse_url(base_url: str, issue_key: str) -> str:
    return f"{base_url}/browse/{issue_key}"


def fetch_issue(issue_key: str, credentials: JiraCredentials, transport: HttpTransport | None = None) -> HttpResult:
    """One real (or, in a test, fake -- see module docstring) HTTP GET against Jira's
    standard-fields issue endpoint. Requests only standard fields -- never a guessed
    custom-field id."""
    url = issue_url(credentials.base_url, issue_key) + "?fields=" + ",".join(READ_FIELDS)
    headers = build_auth_header(credentials)
    return (transport or DEFAULT_TRANSPORT)("GET", url, headers)


# ---------------------------------------------------------------------------
# Atlassian Document Format -> plain text (best-effort, deterministic)
# ---------------------------------------------------------------------------


def adf_to_text(node: object) -> str:
    """Deterministically walks a Jira description's Atlassian Document Format (ADF)
    tree into plain text: block-level nodes (paragraph/heading/listItem/...) are
    separated by newlines, inline text nodes within a block are concatenated. Best-
    effort by design -- unrecognized node types are walked generically via their own
    `content` array rather than dropped, so no real text is silently lost, but exact
    Jira rendering (tables, mentions, emoji) is not reproduced. A `None`/non-ADF
    description (a plain string, as some older Jira instances still return) is passed
    through unchanged; anything else unparseable yields an empty string rather than
    raising."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text", ""))
    children = node.get("content", [])
    if not isinstance(children, list):
        return ""
    parts = [adf_to_text(child) for child in children]
    parts = [p for p in parts if p != ""]
    node_type = node.get("type")
    if node_type in ("doc", "paragraph", "heading", "listItem", "bulletList", "orderedList", "codeBlock", None):
        return "\n".join(parts)
    return "".join(parts)


_AC_HEADING_RE = re.compile(r"(?im)^\s*acceptance\s+criteria\s*:?\s*$")
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def extract_acceptance_criteria(description_text: str) -> list | None:
    """Best-effort, heuristic extraction only -- never authoritative, never invented.
    Looks for a line matching exactly "Acceptance Criteria" (case-insensitive, optional
    trailing colon) and returns the non-empty lines that follow it, up to the next blank
    line or another heading-shaped line, with any leading bullet/number marker stripped.
    Returns None (never an empty list standing in for "none found") when no such heading
    exists at all -- distinguishing "Jira genuinely has no AC section" from "this issue's
    AC list happens to be empty" is not something this heuristic can honestly claim, so it
    only ever returns None or a non-empty list."""
    if not description_text:
        return None
    lines = description_text.splitlines()
    heading_idx = None
    for idx, line in enumerate(lines):
        if _AC_HEADING_RE.match(line):
            heading_idx = idx
            break
    if heading_idx is None:
        return None
    collected: list = []
    for line in lines[heading_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            break
        collected.append(_BULLET_PREFIX_RE.sub("", stripped).strip())
    return collected or None


# ---------------------------------------------------------------------------
# Response parsing / classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedIssue:
    issue_key: str
    project_key: str
    summary: str
    description: str
    acceptance_criteria: list | None
    issue_type: str | None
    status: str | None
    url: str

    def as_dict(self) -> dict:
        return {
            "issue_key": self.issue_key,
            "project_key": self.project_key,
            "summary": self.summary,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "issue_type": self.issue_type,
            "status": self.status,
            "url": self.url,
        }


def parse_issue_payload(requested_issue_key: str, raw_json: dict, *, base_url: str) -> ResolvedIssue:
    """Parses an already-decoded Jira issue JSON body into a ResolvedIssue. Raises
    JiraError(code="invalid_response") for a structurally broken payload (missing `key`,
    missing `fields`, non-dict `fields`) and JiraError(code="identity_mismatch") when the
    returned key does not exactly equal the requested key. Never invents a field the
    response did not actually provide -- summary/description/issue_type/status default to
    "" / None only when genuinely absent from the response, not guessed."""
    if not isinstance(raw_json, dict):
        raise JiraError("Jira response body is not a JSON object", "invalid_response")
    returned_key = raw_json.get("key")
    if not isinstance(returned_key, str) or not returned_key:
        raise JiraError("Jira response is missing a string 'key' field", "invalid_response")
    if returned_key != requested_issue_key:
        raise JiraError(
            f"requested issue {requested_issue_key!r} but Jira returned issue {returned_key!r}", "identity_mismatch"
        )
    fields = raw_json.get("fields")
    if not isinstance(fields, dict):
        raise JiraError("Jira response is missing a 'fields' object", "invalid_response")

    project = fields.get("project") or {}
    project_key = project.get("key") if isinstance(project, dict) else None
    expected_project_key = project_key_from_issue_key(returned_key)
    if project_key is not None and project_key != expected_project_key:
        raise JiraError(
            f"issue {returned_key!r}'s reported project key {project_key!r} does not match "
            f"the project key embedded in its own issue key {expected_project_key!r}",
            "invalid_response",
        )

    summary = fields.get("summary")
    if not isinstance(summary, str):
        summary = ""
    description = adf_to_text(fields.get("description"))
    issue_type_obj = fields.get("issuetype") or {}
    issue_type = issue_type_obj.get("name") if isinstance(issue_type_obj, dict) else None
    status_obj = fields.get("status") or {}
    status = status_obj.get("name") if isinstance(status_obj, dict) else None

    return ResolvedIssue(
        issue_key=returned_key,
        project_key=project_key or expected_project_key,
        summary=summary,
        description=description,
        acceptance_criteria=extract_acceptance_criteria(description),
        issue_type=issue_type,
        status=status,
        url=browse_url(base_url, returned_key),
    )


@dataclass(frozen=True)
class JiraResolution:
    status: str  # one of RESOLUTION_STATUSES
    requested_issue_key: str
    issue: ResolvedIssue | None
    reason: str
    raw: dict  # {"request": {...}, "response": HttpResult.as_dict()} -- no credentials.

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "requested_issue_key": self.requested_issue_key,
            "issue": self.issue.as_dict() if self.issue else None,
            "reason": self.reason,
            "raw": self.raw,
        }


def resolve_issue(
    requested_issue_key: str, credentials: JiraCredentials | None, transport: HttpTransport | None = None
) -> JiraResolution:
    """The one issue-resolution entry point. `requested_issue_key` must already be
    `is_valid_issue_key`-confirmed and normalized -- an invalid shape is classified
    `invalid_issue_key` without ever attempting a network call. Missing credentials are
    classified `connector_unavailable`, likewise before any network call. Every other
    outcome (`resolved`/`not_found`/`unauthorized`/`identity_mismatch`/`invalid_response`)
    reflects a real (or, in a test, fake) HTTP exchange."""
    if not is_valid_issue_key(requested_issue_key):
        return JiraResolution(
            status="invalid_issue_key", requested_issue_key=requested_issue_key, issue=None,
            reason=f"{requested_issue_key!r} is not a valid Jira issue key shape", raw={},
        )
    if credentials is None:
        return JiraResolution(
            status="connector_unavailable", requested_issue_key=requested_issue_key, issue=None,
            reason=(
                f"no Jira connector is configured in this environment "
                f"({_ENV_BASE_URL}/{_ENV_EMAIL}/{_ENV_API_TOKEN} are not all set)"
            ),
            raw={},
        )

    url = issue_url(credentials.base_url, requested_issue_key)
    http_result = fetch_issue(requested_issue_key, credentials, transport)
    raw = {"request": {"method": "GET", "url": url}, "response": http_result.as_dict()}

    if http_result.status_code == -1:
        return JiraResolution(
            status="invalid_response", requested_issue_key=requested_issue_key, issue=None,
            reason=f"Jira request failed before receiving a response: {http_result.reason}", raw=raw,
        )
    if http_result.status_code == 404:
        return JiraResolution(
            status="not_found", requested_issue_key=requested_issue_key, issue=None,
            reason=f"Jira returned 404 for issue {requested_issue_key!r}", raw=raw,
        )
    if http_result.status_code in (401, 403):
        return JiraResolution(
            status="unauthorized", requested_issue_key=requested_issue_key, issue=None,
            reason=f"Jira returned {http_result.status_code} for issue {requested_issue_key!r}", raw=raw,
        )
    if http_result.status_code != 200:
        return JiraResolution(
            status="invalid_response", requested_issue_key=requested_issue_key, issue=None,
            reason=f"Jira returned unexpected status {http_result.status_code} for issue {requested_issue_key!r}",
            raw=raw,
        )
    try:
        payload = json.loads(http_result.body)
    except json.JSONDecodeError as exc:
        return JiraResolution(
            status="invalid_response", requested_issue_key=requested_issue_key, issue=None,
            reason=f"Jira response body is not valid JSON: {exc}", raw=raw,
        )
    try:
        resolved = parse_issue_payload(requested_issue_key, payload, base_url=credentials.base_url)
    except JiraError as exc:
        return JiraResolution(status=exc.code, requested_issue_key=requested_issue_key, issue=None, reason=exc.message, raw=raw)

    return JiraResolution(
        status="resolved", requested_issue_key=requested_issue_key, issue=resolved,
        reason="requested issue key matches returned issue key", raw=raw,
    )


# ---------------------------------------------------------------------------
# create-ticket / edit-ticket -- the two state-changing procedures
# ---------------------------------------------------------------------------
#
# Both follow the same order, stopping at the first failure:
#   1. explicit authorization (`authorized is True` + a named authorization_source) --
#      before anything else, so an unauthorized request never reaches validation, the
#      environment, or the network;
#   2. input validation (keys, issue type, field names, field values);
#   3. connector availability (credentials from the environment, never the request);
#   4. instance identity (`expected_base_url` must equal the configured JIRA_BASE_URL);
#   5. the one mutating HTTP call;
#   6. an independent re-read (`resolve_issue`) compared field-by-field against what was
#      requested. The mutating call's own 201/204 is never the success signal -- a
#      mismatch or failed re-read is `created_unverified` / `updated_unverified`.
# Neither procedure retries, falls back to another route, transitions, or comments.

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def is_valid_project_key(value: object) -> bool:
    """Exact shape only -- never upper-cased or otherwise corrected on the caller's behalf."""
    return isinstance(value, str) and bool(PROJECT_KEY_RE.match(value))


def _authorization_refusal(authorized: object, authorization_source: object) -> str | None:
    if authorized is not True:
        return "authorized must be the boolean true (a string such as \"true\" is not authorization)"
    if not isinstance(authorization_source, str) or not authorization_source.strip():
        return "authorization_source must name where the explicit authorization came from"
    if len(authorization_source) > AUTHORIZATION_SOURCE_MAX_LEN or _CONTROL_CHARS_RE.search(authorization_source):
        return f"authorization_source must be at most {AUTHORIZATION_SOURCE_MAX_LEN} printable characters"
    return None


def _normalized_instance(url: object) -> tuple | None:
    """(scheme, host, path) for an http(s) base URL, lower-cased where Jira is
    case-insensitive; None for anything that is not a plain base URL."""
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.lower() not in ("https", "http") or not parsed.netloc or parsed.query or parsed.fragment:
        return None
    if "@" in parsed.netloc:
        return None
    return (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"))


def _unsupported_field_reason(name: object) -> str:
    if isinstance(name, str) and name.startswith("customfield_"):
        return (
            f"{name!r} is a custom field id; none is configured (CUSTOM_FIELD_IDS is empty) and "
            "custom field ids are never guessed -- see FIELD_REFERENCE.md"
        )
    if isinstance(name, str) and name in _UNSUPPORTED_FIELD_REASONS:
        return f"{name!r}: {_UNSUPPORTED_FIELD_REASONS[name]}"
    return f"{name!r} is not a field this skill supports -- see FIELD_REFERENCE.md"


def _validate_fields(fields: object, allowed: tuple, required: tuple) -> tuple[str, str] | None:
    """Returns (status, reason) for the first problem, or None when `fields` is valid."""
    if not isinstance(fields, dict) or not fields:
        return "invalid_input", "fields must be a non-empty object"
    for name in fields:
        if name not in allowed:
            return "unsupported_field", _unsupported_field_reason(name)
    for name in required:
        if name not in fields:
            return "invalid_input", f"fields.{name} is required"
    if "summary" in fields:
        summary = fields["summary"]
        if (
            not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_MAX_LEN
            or "\n" in summary or "\r" in summary or _CONTROL_CHARS_RE.search(summary)
        ):
            return "invalid_input", f"fields.summary must be one non-empty line of at most {SUMMARY_MAX_LEN} characters"
    if "description" in fields:
        description = fields["description"]
        if not isinstance(description, str) or len(description) > DESCRIPTION_MAX_LEN or _CONTROL_CHARS_RE.search(description):
            return "invalid_input", f"fields.description must be plain text of at most {DESCRIPTION_MAX_LEN} characters"
    return None


def description_lines(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip()]


def description_to_adf(text: str) -> dict:
    """Plain text -> the Atlassian Document Format Jira REST v3 requires for
    `description`: one paragraph per non-blank line. Blank lines are not preserved;
    `description_lines` applies the same rule so verification compares like with like."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in description_lines(text)],
    }


def _build_jira_fields(fields: dict) -> dict:
    out: dict = {}
    if "summary" in fields:
        out["summary"] = fields["summary"].strip()
    if "description" in fields:
        out["description"] = description_to_adf(fields["description"])
    return out


def _expected_state(fields: dict) -> dict:
    expected: dict = {}
    if "summary" in fields:
        expected["summary"] = fields["summary"].strip()
    if "description" in fields:
        expected["description_lines"] = description_lines(fields["description"])
    return expected


def _jira_error_summary(body: str) -> str:
    """Jira's own validation message (errorMessages / errors), bounded -- never the whole body."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    parts = [str(m) for m in parsed.get("errorMessages") or [] if m]
    errors = parsed.get("errors")
    if isinstance(errors, dict):
        parts.extend(f"{k}: {v}" for k, v in errors.items())
    return "; ".join(parts)[:500]


def _write_headers(credentials: JiraCredentials) -> dict:
    return {**build_auth_header(credentials), "Content-Type": "application/json"}


def _classify_write_failure(http_result: HttpResult, action: str) -> tuple[str, str]:
    """(status, reason) for a mutating call that did not return its success code."""
    code = http_result.status_code
    if code == -1:
        return "indeterminate", (
            f"{action} request failed before a response arrived ({http_result.reason}); Jira may or may not "
            "have applied it -- re-read with read-ticket before any retry"
        )
    if code >= 500:
        return "indeterminate", (
            f"Jira returned {code} for the {action} request; it may or may not have been applied -- "
            "re-read with read-ticket before any retry"
        )
    if code in (401, 403):
        return "unauthorized", f"Jira returned {code} for the {action} request"
    if code == 404:
        return "not_found", f"Jira returned 404 for the {action} request"
    if code == 400:
        detail = _jira_error_summary(http_result.body)
        return "rejected", f"Jira rejected the {action} request (400){': ' + detail if detail else ''}"
    return "invalid_response", f"Jira returned unexpected status {code} for the {action} request"


def _verify(issue_key: str, credentials: JiraCredentials, expected: dict, transport: HttpTransport | None) -> dict:
    """Independent re-read of `issue_key`, compared against `expected`. `verified` is
    true only when the re-read resolved and every expected value matched."""
    reread = resolve_issue(issue_key, credentials, transport)
    record = {
        "method": "independent re-read: GET /rest/api/3/issue/{key}",
        "reread_status": reread.status,
        "reread_reason": reread.reason,
        "expected": expected,
        "observed": reread.issue.as_dict() if reread.issue else None,
        "mismatches": [],
        "verified": False,
    }
    if reread.issue is not None:
        observed = {
            "project_key": reread.issue.project_key,
            "issue_type": reread.issue.issue_type,
            "summary": reread.issue.summary,
            "description_lines": description_lines(reread.issue.description),
        }
        record["mismatches"] = [
            {"field": name, "expected": value, "observed": observed[name]}
            for name, value in expected.items()
            if observed[name] != value
        ]
        record["verified"] = not record["mismatches"]
    return record


def _preflight(expected_base_url: object, credentials: JiraCredentials | None) -> tuple[str, str] | None:
    """Steps 3-4, shared by create and edit. (status, reason) for a refusal, else None."""
    if credentials is None:
        return "connector_unavailable", (
            f"no Jira connector is configured in this environment "
            f"({_ENV_BASE_URL}/{_ENV_EMAIL}/{_ENV_API_TOKEN} are not all set); nothing was sent"
        )
    configured = _normalized_instance(credentials.base_url)
    if configured is None or configured != _normalized_instance(expected_base_url):
        return "wrong_instance", (
            f"expected_base_url {expected_base_url!r} does not match the configured {_ENV_BASE_URL}; nothing was sent"
        )
    return None


def create_issue(
    *, project_key: object, issue_type: object, fields: object, authorized: object = False,
    authorization_source: object = None, expected_base_url: object = None,
    credentials: JiraCredentials | None, transport: HttpTransport | None = None,
) -> dict:
    """create-ticket: POST /rest/api/3/issue, then an independent re-read. Returns a
    credential-free dict whose `status` is one of CREATE_STATUSES; only `created` means
    the issue exists and holds exactly the requested project, type, summary and
    description."""
    base = {
        "operation": "create_ticket", "route": "rest", "issue_key": None,
        "requested": {"project_key": project_key, "issue_type": issue_type, "fields": fields},
        "authorization_source": authorization_source if isinstance(authorization_source, str) else None,
        "mutation": None, "verification": None,
    }

    def done(status: str, reason: str, **extra) -> dict:
        return {**base, **extra, "status": status, "reason": reason}

    refusal = _authorization_refusal(authorized, authorization_source)
    if refusal:
        return done("not_authorized", f"{refusal}; nothing was validated or sent")
    if not is_valid_project_key(project_key):
        return done("invalid_input", f"project_key {project_key!r} is not a valid Jira project key (e.g. 'PROJ')")
    if not isinstance(issue_type, str) or issue_type not in SUPPORTED_ISSUE_TYPES:
        return done(
            "unsupported_issue_type",
            f"issue_type {issue_type!r} is not one of {list(SUPPORTED_ISSUE_TYPES)} -- see FIELD_REFERENCE.md",
        )
    problem = _validate_fields(fields, CREATE_FIELDS, CREATE_REQUIRED_FIELDS)
    if problem:
        return done(*problem)
    blocked = _preflight(expected_base_url, credentials)
    if blocked:
        return done(*blocked)

    payload = {"fields": {"project": {"key": project_key}, "issuetype": {"name": issue_type}, **_build_jira_fields(fields)}}
    url = f"{credentials.base_url}/rest/api/3/issue"
    http_result = (transport or DEFAULT_TRANSPORT)("POST", url, _write_headers(credentials), json.dumps(payload).encode("utf-8"))
    base["mutation"] = {"request": {"method": "POST", "url": url, "body": payload}, "response": http_result.as_dict()}

    if http_result.status_code != 201:
        return done(*_classify_write_failure(http_result, "create"))
    try:
        created = json.loads(http_result.body)
    except json.JSONDecodeError:
        created = None
    created_key = created.get("key") if isinstance(created, dict) else None
    if not is_valid_issue_key(created_key):
        return done("created_unverified", "Jira answered 201 but its body carried no valid issue key to re-read")
    base["issue_key"] = created_key
    if project_key_from_issue_key(created_key) != project_key:
        return done(
            "created_unverified",
            f"Jira answered 201 with issue {created_key!r}, which is not in the requested project {project_key!r}",
        )

    expected = {"project_key": project_key, "issue_type": issue_type, **_expected_state(fields)}
    verification = _verify(created_key, credentials, expected, transport)
    if not verification["verified"]:
        return done(
            "created_unverified",
            f"Jira answered 201 for {created_key!r} but the independent re-read did not confirm it "
            f"({verification['reread_status']}; mismatched: {[m['field'] for m in verification['mismatches']]})",
            verification=verification,
        )
    return done("created", f"{created_key!r} created and confirmed by an independent re-read", verification=verification)


def edit_issue(
    *, issue_key: object, fields: object, authorized: object = False, authorization_source: object = None,
    expected_base_url: object = None, credentials: JiraCredentials | None, transport: HttpTransport | None = None,
) -> dict:
    """edit-ticket: pre-read, PUT /rest/api/3/issue/{key} with only the supplied
    fields, then an independent re-read. Returns a credential-free dict whose `status`
    is one of EDIT_STATUSES; only `updated` means the issue now holds exactly the
    requested values. Never transitions status and never comments."""
    normalized = normalize_issue_key(issue_key) if isinstance(issue_key, str) else issue_key
    base = {
        "operation": "edit_ticket", "route": "rest", "issue_key": normalized,
        "requested": {"issue_key": issue_key, "fields": fields},
        "authorization_source": authorization_source if isinstance(authorization_source, str) else None,
        "pre_read": None, "mutation": None, "verification": None,
    }

    def done(status: str, reason: str, **extra) -> dict:
        return {**base, **extra, "status": status, "reason": reason}

    refusal = _authorization_refusal(authorized, authorization_source)
    if refusal:
        return done("not_authorized", f"{refusal}; nothing was validated or sent")
    if not is_valid_issue_key(normalized):
        return done("invalid_issue_key", f"{issue_key!r} is not a valid Jira issue key shape")
    problem = _validate_fields(fields, EDIT_FIELDS, ())
    if problem:
        return done(*problem)
    blocked = _preflight(expected_base_url, credentials)
    if blocked:
        return done(*blocked)

    # Pre-read: the issue must exist and be the one requested before anything is sent.
    pre = resolve_issue(normalized, credentials, transport)
    base["pre_read"] = {"status": pre.status, "reason": pre.reason, "issue": pre.issue.as_dict() if pre.issue else None}
    if pre.status != "resolved":
        return done(pre.status, f"pre-read did not resolve {normalized!r} ({pre.reason}); nothing was sent")

    payload = {"fields": _build_jira_fields(fields)}
    url = issue_url(credentials.base_url, normalized)
    http_result = (transport or DEFAULT_TRANSPORT)("PUT", url, _write_headers(credentials), json.dumps(payload).encode("utf-8"))
    base["mutation"] = {"request": {"method": "PUT", "url": url, "body": payload}, "response": http_result.as_dict()}
    if http_result.status_code not in (200, 204):
        return done(*_classify_write_failure(http_result, "edit"))

    verification = _verify(normalized, credentials, _expected_state(fields), transport)
    if not verification["verified"]:
        return done(
            "updated_unverified",
            f"Jira accepted the edit of {normalized!r} but the independent re-read did not confirm it "
            f"({verification['reread_status']}; mismatched: {[m['field'] for m in verification['mismatches']]})",
            verification=verification,
        )
    return done("updated", f"{normalized!r} updated and confirmed by an independent re-read", verification=verification)
