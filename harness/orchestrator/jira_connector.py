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

This is deliberately NOT the MCP-vs-REST fallback ASSIGNMENT.md Part 8/§2.4 describes for
"at least one connector" -- there is exactly one Jira connector here, REST, because no
Jira MCP server exists in this environment to compare against or fall back from. See
PROJECT_SPEC.md's Jira-skill milestone entry for the full accounting of what remains open.

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


HttpTransport = Callable[[str, str, dict], HttpResult]


def _default_transport(method: str, url: str, headers: dict) -> HttpResult:
    """The one real HTTP entry point in this module. Never raises for a normal HTTP
    error status (Jira's own 404/401/403 responses are legitimate, classified
    HttpResults, not exceptions) -- only a genuine transport failure (DNS, timeout,
    connection refused) is caught and folded into status_code -1."""
    request = urllib.request.Request(url, method=method, headers=headers)
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
    url = issue_url(credentials.base_url, issue_key) + "?fields=summary,description,issuetype,status,project,created,updated"
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
