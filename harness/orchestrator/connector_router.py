"""Deterministic dual-route connector selection for Jira -- the ``ASSIGNMENT.md`` §2.4
requirement that, "for at least one connector, implement both the MCP route and a
REST-skill fallback, and document ... which one you kept and why."

The connector built both ways is **Jira** (the assignment's own motivating example:
"The reference setup abandoned the Atlassian MCP server in favor of curl-based skills
because raw REST calls were more reliable and debuggable"). Both routes reach the same
authoritative source -- a Jira Cloud REST API v3 ``GET /rest/api/3/issue/{key}`` -- but
through genuinely different transports:

- **Route B, REST** (``harness.orchestrator.jira_connector.resolve_issue``): a direct,
  in-process, identity-checked call. **This is the route this harness keeps** (see
  ``KEPT_ROUTE`` and the write-up in ``.claude/skills/jira/SKILL.md`` §"Connector
  routing"). Rationale, matching the reference setup's own conclusion: fewer moving
  parts (no subprocess, no second protocol surface), directly debuggable (one HTTP
  exchange, retained verbatim), deterministic evidence, and no dependence on an MCP
  server process being installed / approved / healthy.
- **Route A, MCP** (``harness.mcp.jira_server`` via ``harness.orchestrator.mcp_client``):
  the same resolution reached over a real JSON-RPC 2.0 stdio MCP boundary. Implemented
  and live-exercised so "implement both" is genuinely met -- and so the *evidence*
  behind the kept-route decision is real, not asserted.

Route-selection policy (deterministic, no reasoning):

- ``RoutesPolicy.REST_FIRST`` (the default / production policy): try REST; on an
  authoritative outcome (resolved / not_found / unauthorized / identity_mismatch /
  invalid_issue_key) stop -- never let a fallback mask an authorization failure or an
  identity mismatch; on ``connector_unavailable`` run the MCP route too, purely to
  *corroborate* that the connector really is unconfigured (both must agree); on a
  non-authoritative transport-class failure (``invalid_response``) fall back to MCP.
- ``RoutesPolicy.MCP_FIRST``: the mirror image -- try MCP first, fall back to REST.
  Used by the parity tests and the live proof so both transports are genuinely
  exercised end to end.

Every routed call produces one ``RouteOutcome`` recording, separately: connector,
operation, requested key, policy, primary route, fallback route, primary result,
whether fallback was attempted and why, fallback result, the final authoritative
route, the final status, a reason, and a timestamp. ``live_cli.py``'s
``resolve_jira_issue_routed`` operation retains it under
``runs/<run_id>/jira/routing/`` alongside a ``connector_routing`` policy event. A
route never reports success merely because fallback code exists: only a route that
actually returned a matching, identity-checked issue is ``resolved``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from . import jira_connector, mcp_client

# The route this harness keeps as its production connector for Jira (ASSIGNMENT.md
# §2.4 / §5 "document ... which one you kept and why").
KEPT_ROUTE = "rest"

JIRA_MCP_SERVER = "jira"
JIRA_MCP_SERVER_MODULE = "harness.mcp.jira_server"
JIRA_MCP_TOOL = "get_issue"

# Normalized per-route outcome vocabulary -- the same seven jira_connector already uses,
# plus `route_error` for an MCP-transport/protocol failure that never reached Jira's own
# classification layer.
AUTHORITATIVE_OUTCOMES = frozenset({"resolved", "not_found", "unauthorized", "identity_mismatch", "invalid_issue_key"})
FALLBACK_OUTCOMES = frozenset({"invalid_response", "route_error"})
CORROBORATE_OUTCOMES = frozenset({"connector_unavailable"})

# The final RouteOutcome.status vocabulary.
ROUTE_STATUSES = frozenset(
    {
        "resolved_via_rest",
        "resolved_via_mcp",
        "rest_failed_mcp_resolved",
        "mcp_failed_rest_resolved",
        "connector_unavailable",
        "not_found",
        "unauthorized",
        "identity_mismatch",
        "invalid_issue_key",
        "both_routes_failed",
    }
)
# Note: this design never emits a "route_disagreement" status -- a fallback only ever runs
# when the primary route was non-authoritative or unavailable, so there is no
# authoritative-vs-authoritative conflict to surface. The concept is covered instead by
# `both_routes_failed` (neither route produced an authoritative answer).

_AFFIRMATIVE_STATUSES = frozenset(
    {"resolved_via_rest", "resolved_via_mcp", "rest_failed_mcp_resolved", "mcp_failed_rest_resolved"}
)


class RoutesPolicy(str, Enum):
    REST_FIRST = "rest_first"
    MCP_FIRST = "mcp_first"


@dataclass(frozen=True)
class RouteResult:
    """One route's normalized attempt."""

    route: str  # "rest" | "mcp"
    transport: str  # "jira_rest_v3" | "mcp_stdio"
    outcome: str  # resolved / not_found / unauthorized / connector_unavailable / identity_mismatch / invalid_issue_key / invalid_response / route_error
    reason: str
    issue: dict | None = None
    detail: dict = field(default_factory=dict)  # the raw sub-result, credential-free

    def as_dict(self) -> dict:
        return {
            "route": self.route,
            "transport": self.transport,
            "outcome": self.outcome,
            "reason": self.reason,
            "issue": self.issue,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RouteOutcome:
    connector: str
    operation: str
    requested_issue_key: str
    policy: str
    primary_route: str
    fallback_route: str
    primary_result: dict
    fallback_attempted: bool
    fallback_reason: str
    fallback_result: dict | None
    final_route: str  # "rest" | "mcp" | "none"
    status: str  # one of ROUTE_STATUSES
    reason: str
    issue: dict | None
    attempted_at: str

    @property
    def is_affirmative(self) -> bool:
        return self.status in _AFFIRMATIVE_STATUSES

    def as_dict(self) -> dict:
        return {
            "connector": self.connector,
            "operation": self.operation,
            "requested_issue_key": self.requested_issue_key,
            "policy": self.policy,
            "primary_route": self.primary_route,
            "fallback_route": self.fallback_route,
            "primary_result": self.primary_result,
            "fallback_attempted": self.fallback_attempted,
            "fallback_reason": self.fallback_reason,
            "fallback_result": self.fallback_result,
            "final_route": self.final_route,
            "status": self.status,
            "reason": self.reason,
            "issue": self.issue,
            "attempted_at": self.attempted_at,
        }


# ---------------------------------------------------------------------------
# Per-route callers -- each returns a normalized RouteResult, never raises.
# ---------------------------------------------------------------------------


def run_rest_route(issue_key: str, *, credentials_factory=None, transport=None) -> RouteResult:
    """Route B: a direct jira_connector.resolve_issue call. ``credentials_factory`` and
    ``transport`` exist only so a test can inject a fake Jira; production passes
    neither, so credentials are read fresh from the environment and the real HTTP
    transport is used."""
    creds = (credentials_factory or jira_connector.JiraCredentials.from_env)()
    resolution = jira_connector.resolve_issue(issue_key, creds, transport)
    detail = resolution.as_dict()
    detail.pop("raw", None)  # request/response echo -- retained by op_resolve_jira_issue's own evidence, not here
    return RouteResult(
        route="rest",
        transport="jira_rest_v3",
        outcome=resolution.status,
        reason=resolution.reason,
        issue=resolution.issue.as_dict() if resolution.issue else None,
        detail=detail,
    )


def run_mcp_route(issue_key: str, *, transport_factory=None) -> RouteResult:
    """Route A: the project-local Jira MCP server, driven over a real stdio JSON-RPC
    boundary by ``mcp_client``. ``transport_factory`` is the test seam; production
    passes nothing, so a real ``python -m harness.mcp.jira_server`` subprocess is
    spawned."""
    tool_result = mcp_client.call_tool(
        server=JIRA_MCP_SERVER,
        server_module=JIRA_MCP_SERVER_MODULE,
        tool=JIRA_MCP_TOOL,
        arguments={"issue_key": issue_key},
        transport_factory=transport_factory,
    )
    detail = tool_result.as_dict()
    if tool_result.status == "mcp_ok" and isinstance(tool_result.parsed, dict) and "status" in tool_result.parsed:
        parsed = tool_result.parsed
        jira_status = parsed.get("status")
        if jira_status not in jira_connector.RESOLUTION_STATUSES:
            return RouteResult(
                route="mcp", transport="mcp_stdio", outcome="invalid_response",
                reason=f"MCP tool returned an unrecognized Jira status {jira_status!r}", detail=detail,
            )
        return RouteResult(
            route="mcp", transport="mcp_stdio", outcome=jira_status,
            reason=str(parsed.get("reason") or ""), issue=parsed.get("issue"), detail=detail,
        )
    if tool_result.status == "mcp_ok":
        return RouteResult(
            route="mcp", transport="mcp_stdio", outcome="invalid_response",
            reason="MCP tool result was not the expected JSON object with a 'status' field", detail=detail,
        )
    return RouteResult(
        route="mcp", transport="mcp_stdio", outcome="route_error",
        reason=f"MCP route failed before reaching Jira's own classification: {tool_result.status} -- {tool_result.reason}",
        detail=detail,
    )


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _authoritative_final_status(route: str, outcome: str) -> str:
    """Map an AUTHORITATIVE_OUTCOMES value from a single route onto the final status.
    Only ever called with a member of AUTHORITATIVE_OUTCOMES."""
    if outcome == "resolved":
        return "resolved_via_rest" if route == "rest" else "resolved_via_mcp"
    return outcome  # not_found / unauthorized / identity_mismatch / invalid_issue_key, verbatim


def resolve_issue_routed(
    issue_key: str,
    *,
    policy: RoutesPolicy = RoutesPolicy.REST_FIRST,
    rest_caller=run_rest_route,
    mcp_caller=run_mcp_route,
) -> RouteOutcome:
    """Resolve one Jira issue through the dual-route policy. ``rest_caller`` /
    ``mcp_caller`` are injectable only for tests; production calls with neither, so
    both routes run for real."""
    attempted_at = _now()
    if policy is RoutesPolicy.MCP_FIRST:
        primary_route, fallback_route = "mcp", "rest"
        run_primary, run_fallback = (lambda: mcp_caller(issue_key)), (lambda: rest_caller(issue_key))
    else:
        primary_route, fallback_route = "rest", "mcp"
        run_primary, run_fallback = (lambda: rest_caller(issue_key)), (lambda: mcp_caller(issue_key))

    primary = run_primary()

    # An authoritative primary outcome is final -- never fall back past a real
    # resolved / not_found / unauthorized / identity_mismatch / invalid_issue_key.
    if primary.outcome in AUTHORITATIVE_OUTCOMES:
        return RouteOutcome(
            connector="jira", operation="resolve_issue", requested_issue_key=issue_key, policy=policy.value,
            primary_route=primary_route, fallback_route=fallback_route, primary_result=primary.as_dict(),
            fallback_attempted=False,
            fallback_reason="primary route returned an authoritative result; a fallback could only repeat or mask it",
            fallback_result=None, final_route=primary_route,
            status=_authoritative_final_status(primary_route, primary.outcome),
            reason=primary.reason, issue=primary.issue, attempted_at=attempted_at,
        )

    # connector_unavailable -> corroborate with the other route (both must agree).
    if primary.outcome in CORROBORATE_OUTCOMES:
        fallback = run_fallback()
        if fallback.outcome == "connector_unavailable":
            return RouteOutcome(
                connector="jira", operation="resolve_issue", requested_issue_key=issue_key, policy=policy.value,
                primary_route=primary_route, fallback_route=fallback_route, primary_result=primary.as_dict(),
                fallback_attempted=True,
                fallback_reason="primary route reported connector_unavailable; the other route was run to corroborate",
                fallback_result=fallback.as_dict(), final_route="none", status="connector_unavailable",
                reason="both the REST and MCP routes independently report no Jira connector is configured",
                issue=None, attempted_at=attempted_at,
            )
        # The other route reached Jira after all -> treat its outcome as authoritative.
        return _resolve_from_fallback(
            issue_key, policy, primary_route, fallback_route, primary, fallback, attempted_at,
            fallback_reason="primary route reported connector_unavailable but the other route reached Jira",
        )

    # A non-authoritative transport-class failure -> fall back.
    fallback = run_fallback()
    return _resolve_from_fallback(
        issue_key, policy, primary_route, fallback_route, primary, fallback, attempted_at,
        fallback_reason=f"primary route failed non-authoritatively ({primary.outcome}: {primary.reason})",
    )


def _resolve_from_fallback(
    issue_key, policy, primary_route, fallback_route, primary, fallback, attempted_at, *, fallback_reason
) -> RouteOutcome:
    if fallback.outcome == "resolved":
        status = "rest_failed_mcp_resolved" if fallback_route == "mcp" else "mcp_failed_rest_resolved"
        return RouteOutcome(
            connector="jira", operation="resolve_issue", requested_issue_key=issue_key, policy=policy.value,
            primary_route=primary_route, fallback_route=fallback_route, primary_result=primary.as_dict(),
            fallback_attempted=True, fallback_reason=fallback_reason, fallback_result=fallback.as_dict(),
            final_route=fallback_route, status=status,
            reason=f"fallback {fallback_route} route resolved the issue after the {primary_route} route did not",
            issue=fallback.issue, attempted_at=attempted_at,
        )
    # `resolved` is handled above, so here a member of AUTHORITATIVE_OUTCOMES is always
    # one of the negative classifications, reported verbatim as the final status.
    if fallback.outcome in AUTHORITATIVE_OUTCOMES or fallback.outcome == "connector_unavailable":
        return RouteOutcome(
            connector="jira", operation="resolve_issue", requested_issue_key=issue_key, policy=policy.value,
            primary_route=primary_route, fallback_route=fallback_route, primary_result=primary.as_dict(),
            fallback_attempted=True, fallback_reason=fallback_reason, fallback_result=fallback.as_dict(),
            final_route=fallback_route,
            status=fallback.outcome,
            reason=f"fallback {fallback_route} route returned an authoritative {fallback.outcome}",
            issue=None, attempted_at=attempted_at,
        )
    return RouteOutcome(
        connector="jira", operation="resolve_issue", requested_issue_key=issue_key, policy=policy.value,
        primary_route=primary_route, fallback_route=fallback_route, primary_result=primary.as_dict(),
        fallback_attempted=True, fallback_reason=fallback_reason, fallback_result=fallback.as_dict(),
        final_route="none", status="both_routes_failed",
        reason=f"neither route resolved the issue (primary {primary.outcome}, fallback {fallback.outcome})",
        issue=None, attempted_at=attempted_at,
    )
