"""Project-local Jira MCP server -- Route A ("the MCP route") of the ASSIGNMENT.md
§2.4 dual-route requirement for the Jira connector.

Exposes exactly one read-only tool:

    get_issue(issue_key: str) -> JSON text

It wraps ``harness.orchestrator.jira_connector.resolve_issue`` -- the same
identity-checked Jira Cloud REST API v3 resolution Route B uses -- so both routes hit
the same authoritative source, but through genuinely different transports: this route
adds an MCP stdio subprocess + JSON-RPC protocol boundary, Route B is a direct
in-process call. ``harness.orchestrator.connector_router`` runs the MCP route first,
classifies its outcome, and falls back to Route B on defined MCP failure/unavailability
conditions -- never masking a real ``unauthorized`` / ``identity_mismatch`` into a
success.

Boundaries (enforced here, not just documented):

- Read-only. There is no create/edit/transition/comment tool, and none will be added
  here speculatively (ASSIGNMENT.md and the Jira milestone both forbid Jira writes).
- No caller-supplied base URL or credential. ``JIRA_BASE_URL`` / ``JIRA_EMAIL`` /
  ``JIRA_API_TOKEN`` are read fresh from *this server process's* environment via
  ``JiraCredentials.from_env`` on every call; a ``get_issue`` ``arguments`` object
  carrying ``base_url`` / ``email`` / ``api_token`` / ``env`` is rejected outright.
- No secret in the result. The tool returns the connector's own ``status`` /
  ``reason`` plus whitelisted issue fields (key, project, summary, description,
  acceptance criteria, type, status, url) -- never the ``Authorization`` header, the
  raw HTTP body, or the API token.
- Honest unavailability. With no credentials configured (the state in this
  environment today), ``get_issue`` returns ``status: "connector_unavailable"`` --
  a real "no connector" outcome, never a fabricated issue.
"""
from __future__ import annotations

import json
import sys

from harness.orchestrator import jira_connector

from ._server_base import McpServer, Tool

SERVER_NAME = "jira"
SERVER_VERSION = "1.0.0"

_REJECTED_ARG_KEYS = frozenset({"base_url", "email", "api_token", "env", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"})

_GET_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "issue_key": {
            "type": "string",
            "description": "A Jira issue key, e.g. 'PROJ-123'. Looked up exactly as given (normalized to "
            "upper-case only); never guessed, reformatted, or auto-completed.",
        }
    },
    "required": ["issue_key"],
    "additionalProperties": False,
}

# Fields the tool result is allowed to carry from a resolved issue -- a deliberate
# whitelist so a future jira_connector change can never widen what this MCP boundary
# leaks. `raw` (which holds the HTTP request/response echo) is intentionally absent.
_ISSUE_RESULT_FIELDS = (
    "issue_key",
    "project_key",
    "summary",
    "description",
    "acceptance_criteria",
    "issue_type",
    "status",
    "url",
)


def _get_issue(arguments: dict) -> str:
    smuggled = _REJECTED_ARG_KEYS.intersection(arguments)
    if smuggled:
        raise ValueError(
            f"credentials/endpoint are read from this server's environment, never from tool arguments "
            f"(rejected: {sorted(smuggled)})"
        )
    raw_key = arguments.get("issue_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise ValueError("'issue_key' must be a non-empty string")

    normalized = jira_connector.normalize_issue_key(raw_key)
    credentials = jira_connector.JiraCredentials.from_env()
    resolution = jira_connector.resolve_issue(normalized, credentials)

    payload: dict = {
        "route": "mcp",
        "connector": "jira",
        "tool": "get_issue",
        "requested_issue_key": normalized,
        "status": resolution.status,
        "reason": resolution.reason,
        "issue": None,
    }
    if resolution.issue is not None:
        issue_dict = resolution.issue.as_dict()
        payload["issue"] = {k: issue_dict.get(k) for k in _ISSUE_RESULT_FIELDS}
    return json.dumps(payload, indent=2)


def build_server() -> McpServer:
    return McpServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        tools=[
            Tool(
                name="get_issue",
                description=(
                    "Read a single Jira issue by key (read-only). Returns the connector's own "
                    "classification (resolved / not_found / unauthorized / connector_unavailable / "
                    "identity_mismatch / invalid_issue_key / invalid_response) plus whitelisted issue "
                    "fields on success. Never creates, edits, transitions, or comments."
                ),
                input_schema=_GET_ISSUE_SCHEMA,
                handler=_get_issue,
            )
        ],
    )


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
