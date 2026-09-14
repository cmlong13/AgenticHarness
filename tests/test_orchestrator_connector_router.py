"""Tests for harness/orchestrator/connector_router.py -- deterministic route selection
for ASSIGNMENT.md §2.4's dual-route Jira connector.

Route callers are injected as canned ``RouteResult`` factories so every branch of the
selection matrix is exercised without a real Jira or a real subprocess. Two tests wire
the real ``run_rest_route`` / ``run_mcp_route`` against the no-credentials environment
to prove the real integration classifies honestly.
"""
from __future__ import annotations

import pytest

from harness.orchestrator import connector_router as cr
from harness.orchestrator.connector_router import RouteResult, RoutesPolicy


def _rest(outcome, issue=None, reason="r"):
    return lambda key: RouteResult(route="rest", transport="jira_rest_v3", outcome=outcome, reason=reason, issue=issue)


def _mcp(outcome, issue=None, reason="r"):
    return lambda key: RouteResult(route="mcp", transport="mcp_stdio", outcome=outcome, reason=reason, issue=issue)


_ISSUE = {"issue_key": "PROJ-1", "summary": "x"}


class TestRestFirstPolicy:
    def test_rest_resolves_mcp_not_called(self):
        called = {"mcp": False}

        def mcp_caller(key):
            called["mcp"] = True
            return RouteResult("mcp", "mcp_stdio", "resolved", "r", _ISSUE)

        out = cr.resolve_issue_routed("PROJ-1", rest_caller=_rest("resolved", _ISSUE), mcp_caller=mcp_caller)
        assert out.status == "resolved_via_rest"
        assert out.final_route == "rest"
        assert out.fallback_attempted is False
        assert called["mcp"] is False
        assert out.issue == _ISSUE

    @pytest.mark.parametrize("authoritative", ["not_found", "unauthorized", "identity_mismatch", "invalid_issue_key"])
    def test_authoritative_rest_negative_blocks_fallback(self, authoritative):
        called = {"mcp": False}

        def mcp_caller(key):
            called["mcp"] = True
            return RouteResult("mcp", "mcp_stdio", "resolved", "r", _ISSUE)

        out = cr.resolve_issue_routed("PROJ-1", rest_caller=_rest(authoritative), mcp_caller=mcp_caller)
        assert out.status == authoritative
        assert out.fallback_attempted is False
        assert called["mcp"] is False, f"a fallback must never run past an authoritative {authoritative}"
        assert out.issue is None

    def test_unauthorized_is_never_converted_to_success_by_fallback(self):
        # Even if the MCP route somehow "resolved", the REST unauthorized is final.
        out = cr.resolve_issue_routed("PROJ-1", rest_caller=_rest("unauthorized"), mcp_caller=_mcp("resolved", _ISSUE))
        assert out.status == "unauthorized"
        assert not out.is_affirmative

    def test_connector_unavailable_corroborated_by_mcp(self):
        out = cr.resolve_issue_routed(
            "PROJ-1", rest_caller=_rest("connector_unavailable"), mcp_caller=_mcp("connector_unavailable")
        )
        assert out.status == "connector_unavailable"
        assert out.fallback_attempted is True
        assert out.final_route == "none"
        assert "independently report no Jira connector" in out.reason

    def test_connector_unavailable_rest_but_mcp_reaches_jira_and_resolves(self):
        out = cr.resolve_issue_routed(
            "PROJ-1", rest_caller=_rest("connector_unavailable"), mcp_caller=_mcp("resolved", _ISSUE)
        )
        assert out.status == "rest_failed_mcp_resolved"
        assert out.final_route == "mcp"
        assert out.issue == _ISSUE

    def test_rest_transport_failure_falls_back_to_mcp_which_resolves(self):
        out = cr.resolve_issue_routed(
            "PROJ-1", rest_caller=_rest("invalid_response"), mcp_caller=_mcp("resolved", _ISSUE)
        )
        assert out.status == "rest_failed_mcp_resolved"
        assert out.final_route == "mcp"
        assert out.fallback_attempted is True
        assert out.primary_result["outcome"] == "invalid_response"

    def test_rest_transport_failure_then_mcp_authoritative_negative(self):
        out = cr.resolve_issue_routed(
            "PROJ-1", rest_caller=_rest("invalid_response"), mcp_caller=_mcp("not_found")
        )
        assert out.status == "not_found"
        assert out.fallback_attempted is True
        assert out.final_route == "mcp"

    def test_both_routes_fail_non_authoritatively(self):
        out = cr.resolve_issue_routed(
            "PROJ-1", rest_caller=_rest("invalid_response"), mcp_caller=_mcp("route_error", reason="mcp_transport_error")
        )
        assert out.status == "both_routes_failed"
        assert out.final_route == "none"
        assert not out.is_affirmative

    def test_no_double_success_claim(self):
        out = cr.resolve_issue_routed("PROJ-1", rest_caller=_rest("resolved", _ISSUE), mcp_caller=_mcp("resolved", _ISSUE))
        d = out.as_dict()
        # exactly one affirmative status, and the fallback result is absent because it was never run
        assert d["status"] == "resolved_via_rest"
        assert d["fallback_result"] is None


class TestMcpFirstPolicy:
    def test_mcp_resolves_rest_not_called(self):
        called = {"rest": False}

        def rest_caller(key):
            called["rest"] = True
            return RouteResult("rest", "jira_rest_v3", "resolved", "r", _ISSUE)

        out = cr.resolve_issue_routed(
            "PROJ-1", policy=RoutesPolicy.MCP_FIRST, rest_caller=rest_caller, mcp_caller=_mcp("resolved", _ISSUE)
        )
        assert out.status == "resolved_via_mcp"
        assert out.primary_route == "mcp"
        assert called["rest"] is False

    def test_mcp_unavailable_falls_back_to_rest(self):
        out = cr.resolve_issue_routed(
            "PROJ-1", policy=RoutesPolicy.MCP_FIRST,
            mcp_caller=_mcp("route_error", reason="mcp_unavailable"), rest_caller=_rest("resolved", _ISSUE),
        )
        assert out.status == "mcp_failed_rest_resolved"
        assert out.final_route == "rest"
        assert out.primary_result["outcome"] == "route_error"

    def test_mcp_malformed_then_rest_resolves(self):
        out = cr.resolve_issue_routed(
            "PROJ-1", policy=RoutesPolicy.MCP_FIRST,
            mcp_caller=_mcp("invalid_response"), rest_caller=_rest("resolved", _ISSUE),
        )
        assert out.status == "mcp_failed_rest_resolved"

    def test_mcp_authoritative_unauthorized_blocks_rest_fallback(self):
        called = {"rest": False}

        def rest_caller(key):
            called["rest"] = True
            return RouteResult("rest", "jira_rest_v3", "resolved", "r", _ISSUE)

        out = cr.resolve_issue_routed(
            "PROJ-1", policy=RoutesPolicy.MCP_FIRST, mcp_caller=_mcp("unauthorized"), rest_caller=rest_caller
        )
        assert out.status == "unauthorized"
        assert called["rest"] is False


class TestRecordShape:
    def test_route_outcome_records_every_required_field(self):
        out = cr.resolve_issue_routed("PROJ-1", rest_caller=_rest("invalid_response"), mcp_caller=_mcp("resolved", _ISSUE))
        d = out.as_dict()
        for field in (
            "connector", "operation", "requested_issue_key", "policy", "primary_route", "fallback_route",
            "primary_result", "fallback_attempted", "fallback_reason", "fallback_result", "final_route",
            "status", "reason", "issue", "attempted_at",
        ):
            assert field in d
        assert d["connector"] == "jira" and d["operation"] == "resolve_issue"
        assert d["status"] in cr.ROUTE_STATUSES

    def test_kept_route_is_rest(self):
        assert cr.KEPT_ROUTE == "rest"


class TestRealIntegrationNoCredentials:
    def test_real_rest_route_reports_connector_unavailable(self, monkeypatch):
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        r = cr.run_rest_route("ABC-1")
        assert r.route == "rest" and r.outcome == "connector_unavailable"
        assert "raw" not in r.detail  # credential-free

    def test_real_mcp_route_spawns_server_and_reports_connector_unavailable(self, monkeypatch):
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        r = cr.run_mcp_route("ABC-1")
        assert r.route == "mcp" and r.transport == "mcp_stdio"
        assert r.outcome == "connector_unavailable"

    def test_real_routed_call_both_transports_agree(self, monkeypatch):
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        out = cr.resolve_issue_routed("ABC-1", policy=RoutesPolicy.REST_FIRST)
        assert out.status == "connector_unavailable"
        assert out.fallback_attempted is True
        assert out.primary_result["outcome"] == "connector_unavailable"
        assert out.fallback_result["outcome"] == "connector_unavailable"
