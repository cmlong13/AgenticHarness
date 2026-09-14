"""Tests for harness/orchestrator/mcp_client.py -- the deterministic MCP stdio client
that drives Route A of ASSIGNMENT.md §2.4's dual-route Jira connector.

The happy path and the honest ``connector_unavailable`` path go through
``InProcessTransport`` (real server + real client protocol code, no subprocess). Every
negative MCP classification goes through ``ScriptedTransport``, which injects a
JSON-RPC error, a malformed result, a dead pipe, etc. One test uses a real subprocess
to prove ``StdioTransport`` genuinely speaks the protocol.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.mcp import _server_base, jira_server
from harness.orchestrator import mcp_client

REPO_ROOT = Path(__file__).resolve().parent.parent


def _echo_server(reply_text="ok", is_error=False):
    return _server_base.McpServer(
        name="x", version="1",
        tools=[_server_base.Tool("t", "t", {"type": "object"}, lambda a: reply_text)],
    )


def _inproc(server):
    return lambda: mcp_client.InProcessTransport(server)


def _scripted(*responses):
    return lambda: mcp_client.ScriptedTransport(responses=list(responses))


_INIT_OK = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "x", "version": "1"}}}


class TestHappyPath:
    def test_inprocess_tool_call_succeeds_and_parses_json_text(self):
        srv = jira_server.build_server()
        r = mcp_client.call_tool(
            server="jira", server_module="m", tool="get_issue",
            arguments={"issue_key": "ABC-1"}, transport_factory=_inproc(srv),
        )
        assert r.status == "mcp_ok"
        assert r.parsed["status"] == "connector_unavailable"
        assert r.is_error is False

    def test_non_json_tool_text_is_still_mcp_ok_with_parsed_none(self):
        r = mcp_client.call_tool(
            server="x", server_module="m", tool="t", arguments={}, transport_factory=_inproc(_echo_server("plain text")),
        )
        assert r.status == "mcp_ok" and r.parsed is None and r.text == "plain text"

    def test_real_subprocess_stdio_transport(self):
        r = mcp_client.call_tool(
            server="jira", server_module="harness.mcp.jira_server", tool="get_issue",
            arguments={"issue_key": "ABC-1"},
        )
        assert r.status == "mcp_ok"
        assert r.parsed["status"] in mcp_client.MCP_STATUSES or r.parsed["status"] == "connector_unavailable"


class TestNegativeClassifications:
    def test_server_that_will_not_launch_is_mcp_unavailable(self):
        def explode():
            raise mcp_client.McpClientError("no such module", "mcp_unavailable")

        r = mcp_client.call_tool(server="jira", server_module="m", tool="t", arguments={}, transport_factory=explode)
        assert r.status == "mcp_unavailable"

    def test_jsonrpc_error_on_initialize_is_protocol_error(self):
        err = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
        r = mcp_client.call_tool(server="x", server_module="m", tool="t", arguments={}, transport_factory=_scripted(err))
        assert r.status == "mcp_protocol_error"

    def test_jsonrpc_error_on_tools_call_is_protocol_error(self):
        err = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "bad tool"}}
        r = mcp_client.call_tool(server="x", server_module="m", tool="t", arguments={}, transport_factory=_scripted(_INIT_OK, err))
        assert r.status == "mcp_protocol_error"

    def test_wrong_reply_id_is_protocol_error(self):
        bad = {"jsonrpc": "2.0", "id": 99, "result": {"content": [{"type": "text", "text": "x"}]}}
        r = mcp_client.call_tool(server="x", server_module="m", tool="t", arguments={}, transport_factory=_scripted(_INIT_OK, bad))
        assert r.status == "mcp_protocol_error"

    def test_missing_serverinfo_is_invalid_response(self):
        bad_init = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}
        r = mcp_client.call_tool(server="x", server_module="m", tool="t", arguments={}, transport_factory=_scripted(bad_init))
        assert r.status == "mcp_invalid_response"

    def test_tool_result_without_content_is_invalid_response(self):
        bad = {"jsonrpc": "2.0", "id": 2, "result": {"isError": False}}
        r = mcp_client.call_tool(server="x", server_module="m", tool="t", arguments={}, transport_factory=_scripted(_INIT_OK, bad))
        assert r.status == "mcp_invalid_response"

    def test_iserror_true_result_is_mcp_tool_error(self):
        bad = {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "kaboom"}], "isError": True}}
        r = mcp_client.call_tool(server="x", server_module="m", tool="t", arguments={}, transport_factory=_scripted(_INIT_OK, bad))
        assert r.status == "mcp_tool_error" and r.is_error is True

    def test_transport_that_raises_midway_is_transport_error(self):
        r = mcp_client.call_tool(
            server="x", server_module="m", tool="t", arguments={},
            transport_factory=_scripted(_INIT_OK, mcp_client.McpClientError("pipe broke", "mcp_transport_error")),
        )
        assert r.status == "mcp_transport_error"

    def test_close_is_always_called_even_on_failure(self):
        class TrackingTransport(mcp_client.ScriptedTransport):
            closed = False

            def close(self):
                TrackingTransport.closed = True

        tt = TrackingTransport(responses=[{"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "x"}}])
        mcp_client.call_tool(server="x", server_module="m", tool="t", arguments={}, transport_factory=lambda: tt)
        assert TrackingTransport.closed is True


class TestStdioTransportRealDeadServer:
    def test_a_server_module_that_exits_immediately_is_transport_error(self, tmp_path):
        # A module that prints nothing and exits -> the client's first read gets EOF.
        r = mcp_client.call_tool(
            server="x", server_module="this_module_does_not_exist_anywhere", tool="t", arguments={},
        )
        assert r.status in {"mcp_unavailable", "mcp_transport_error"}


class TestServerModuleMapping:
    def test_known_connectors_map_to_project_local_servers(self):
        assert mcp_client.default_server_module("jira") == "harness.mcp.jira_server"
        assert mcp_client.default_server_module("obsidian") == "harness.mcp.obsidian_server"
