"""Tests for the project-local MCP servers (harness/mcp/) -- the real, stdlib-only
JSON-RPC 2.0 stdio servers behind ASSIGNMENT.md §2.4's Jira "MCP route" and §2.2's
Architect docs connector.

Every test drives the server through its own ``handle_message`` (the pure protocol
layer) or through a real ``python -m harness.mcp.<server>`` subprocess -- exactly as a
real MCP client would. No secret is ever set; the Jira server's honest
``connector_unavailable`` path is the one exercised here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.mcp import _server_base, jira_server, obsidian_server

REPO_ROOT = Path(__file__).resolve().parent.parent

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}


class TestServerBaseProtocol:
    def _server(self):
        return _server_base.McpServer(
            name="x", version="1", tools=[_server_base.Tool("echo", "echo", {"type": "object"}, lambda a: json.dumps(a))]
        )

    def test_initialize_echoes_protocol_version_and_advertises_tools(self):
        resp = self._server().handle_message(_INIT)
        assert resp["jsonrpc"] == "2.0" and resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2025-06-18"
        assert resp["result"]["capabilities"]["tools"] == {"listChanged": False}
        assert resp["result"]["serverInfo"] == {"name": "x", "version": "1"}

    def test_initialize_falls_back_to_default_protocol_version(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}}
        resp = self._server().handle_message(msg)
        assert resp["result"]["protocolVersion"] == _server_base.DEFAULT_PROTOCOL_VERSION

    def test_initialized_notification_gets_no_response(self):
        assert self._server().handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_ping_returns_empty_result(self):
        srv = self._server()
        srv.handle_message(_INIT)
        assert srv.handle_message({"jsonrpc": "2.0", "id": 5, "method": "ping"})["result"] == {}

    def test_tools_list_returns_name_description_input_schema(self):
        srv = self._server()
        srv.handle_message(_INIT)
        tools = srv.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        assert tools == [{"name": "echo", "description": "echo", "inputSchema": {"type": "object"}}]

    def test_tools_call_wraps_handler_text_in_a_content_block(self):
        srv = self._server()
        srv.handle_message(_INIT)
        resp = srv.handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"a": 1}}}
        )
        assert resp["result"]["isError"] is False
        assert resp["result"]["content"][0] == {"type": "text", "text": json.dumps({"a": 1})}

    def test_tool_handler_exception_is_an_iserror_result_not_a_crash(self):
        def boom(_a):
            raise ValueError("nope")

        srv = _server_base.McpServer(name="x", version="1", tools=[_server_base.Tool("b", "b", {}, boom)])
        srv.handle_message(_INIT)
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "b", "arguments": {}}})
        assert resp["result"]["isError"] is True
        assert "ValueError: nope" in resp["result"]["content"][0]["text"]

    def test_unknown_method_is_jsonrpc_method_not_found(self):
        srv = self._server()
        srv.handle_message(_INIT)
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
        assert resp["error"]["code"] == -32601

    def test_unknown_tool_is_invalid_params(self):
        srv = self._server()
        srv.handle_message(_INIT)
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "ghost"}})
        assert resp["error"]["code"] == -32602

    def test_non_object_message_is_invalid_request(self):
        assert self._server().handle_message([1, 2, 3])["error"]["code"] == -32600

    def test_serve_loop_handles_newline_delimited_json_and_bad_lines(self):
        import io

        srv = self._server()
        stdin = io.StringIO(
            json.dumps(_INIT) + "\n"
            + "not json\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
        )
        stdout = io.StringIO()
        srv.serve(stdin, stdout)
        lines = [json.loads(x) for x in stdout.getvalue().splitlines() if x]
        # initialize result, parse-error for the bad line, tools/list result (no line for the notification)
        assert lines[0]["id"] == 1
        assert lines[1]["error"]["code"] == -32700
        assert lines[2]["id"] == 2 and "tools" in lines[2]["result"]


class TestJiraServer:
    def _call(self, arguments, monkeypatch=None):
        srv = jira_server.build_server()
        srv.handle_message(_INIT)
        return srv.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_issue", "arguments": arguments}}
        )

    def test_exactly_one_read_only_tool(self):
        srv = jira_server.build_server()
        srv.handle_message(_INIT)
        tools = srv.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        assert [t["name"] for t in tools] == ["get_issue"]
        names = " ".join(t["name"] for t in tools).lower()
        for banned in ("create", "update", "delete", "transition", "comment", "edit", "write"):
            assert banned not in names

    def test_missing_credentials_is_honest_connector_unavailable(self, monkeypatch):
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        resp = self._call({"issue_key": "PROJ-1"})
        assert resp["result"]["isError"] is False
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["status"] == "connector_unavailable"
        assert payload["route"] == "mcp" and payload["issue"] is None

    def test_smuggled_credential_argument_is_rejected(self):
        resp = self._call({"issue_key": "PROJ-1", "base_url": "https://evil.example"})
        assert resp["result"]["isError"] is True
        assert "never from tool" in resp["result"]["content"][0]["text"]

    def test_blank_issue_key_is_a_tool_error(self):
        resp = self._call({"issue_key": "   "})
        assert resp["result"]["isError"] is True

    def test_result_never_carries_raw_http_echo_or_headers(self, monkeypatch):
        from harness.orchestrator import jira_connector as jc

        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "a@example.invalid")
        monkeypatch.setenv("JIRA_API_TOKEN", "s3cr3t-token")
        body = json.dumps(
            {"key": "PROJ-9", "fields": {"summary": "s", "description": None, "issuetype": {"name": "Bug"},
                                          "status": {"name": "To Do"}, "project": {"key": "PROJ"}}}
        )
        monkeypatch.setattr(jc, "DEFAULT_TRANSPORT", lambda m, u, h: jc.HttpResult(status_code=200, body=body))
        resp = self._call({"issue_key": "PROJ-9"})
        text = resp["result"]["content"][0]["text"]
        assert "s3cr3t-token" not in text
        assert "Authorization" not in text and "Basic " not in text
        payload = json.loads(text)
        assert payload["status"] == "resolved"
        assert set(payload["issue"]) == {
            "issue_key", "project_key", "summary", "description", "acceptance_criteria", "issue_type", "status", "url"
        }

    def test_real_subprocess_round_trip(self, monkeypatch):
        env = {k: v for k, v in _clean_env().items()}
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            env.pop(k, None)
        proc = subprocess.Popen(
            [sys.executable, "-m", "harness.mcp.jira_server"],
            cwd=str(REPO_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
        )
        try:
            msgs = (
                json.dumps(_INIT) + "\n"
                + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
                + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "get_issue", "arguments": {"issue_key": "ABC-1"}}}) + "\n"
            )
            out, _ = proc.communicate(msgs, timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        lines = [json.loads(x) for x in out.splitlines() if x.strip()]
        call_result = [m for m in lines if m.get("id") == 2][0]["result"]
        assert call_result["isError"] is False
        assert json.loads(call_result["content"][0]["text"])["status"] == "connector_unavailable"


class TestObsidianServer:
    @pytest.fixture()
    def vault(self, tmp_path, monkeypatch):
        v = tmp_path / "vault"
        (v / "Design").mkdir(parents=True)
        (v / "Design" / "Risk band calibration.md").write_text(
            "The risk band cutoff was set to 0.42 after the March incident review.", encoding="utf-8"
        )
        (v / ".obsidian").mkdir()
        (v / ".obsidian" / "workspace.md").write_text("SECRET WORKSPACE", encoding="utf-8")
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(v))
        return v

    def _call(self, tool, arguments):
        srv = obsidian_server.build_server()
        srv.handle_message(_INIT)
        return srv.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        )

    def test_exactly_two_read_only_tools(self):
        srv = obsidian_server.build_server()
        srv.handle_message(_INIT)
        tools = srv.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        assert sorted(t["name"] for t in tools) == ["read_note", "search_notes"]
        names = " ".join(t["name"] for t in tools).lower()
        for banned in ("write", "create", "update", "delete", "publish", "rename"):
            assert banned not in names

    def test_search_returns_bounded_matches_and_never_touches_hidden_dirs(self, vault):
        resp = self._call("search_notes", {"query": "risk band cutoff"})
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["status"] == "found"
        paths = [m["note_path"] for m in payload["matches"]]
        assert paths == ["Design/Risk band calibration.md"]
        assert not any(".obsidian" in p for p in paths)

    def test_read_note_returns_full_identity(self, vault):
        resp = self._call("read_note", {"note_path": "Design/Risk band calibration.md"})
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["status"] == "read"
        assert len(payload["content_sha256"]) == 64 and payload["byte_count"] > 0

    @pytest.mark.parametrize(
        "bad_path",
        ["../escape.md", "/etc/passwd", "C:/Windows/system32/x.md", ".obsidian/workspace.md", "Design/notes.txt"],
    )
    def test_unsafe_note_paths_are_classified_invalid_never_read(self, vault, bad_path):
        payload = json.loads(self._call("read_note", {"note_path": bad_path})["result"]["content"][0]["text"])
        assert payload["status"] == "invalid_note_path"
        assert "SECRET WORKSPACE" not in json.dumps(payload)

    def test_smuggled_vault_path_argument_is_rejected(self, vault, tmp_path):
        resp = self._call("read_note", {"note_path": "x.md", "vault_path": str(tmp_path)})
        assert resp["result"]["isError"] is True
        assert "never from tool" in resp["result"]["content"][0]["text"]

    def test_connector_unavailable_when_vault_unset(self, monkeypatch):
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        payload = json.loads(self._call("search_notes", {"query": "anything"})["result"]["content"][0]["text"])
        assert payload["status"] == "connector_unavailable"


def _clean_env():
    import os

    return dict(os.environ)
