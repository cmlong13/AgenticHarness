"""Structural checks on the project-root .mcp.json -- the ASSIGNMENT.md §2.4 "wire up
MCP servers" configuration for the Jira MCP route and the Architect's Obsidian docs
connector.

Claude Code's supported project-scoped MCP config is a project-root ``.mcp.json`` with
an ``mcpServers`` object; each entry names a stdio ``command`` + ``args``. The tool a
server exposes is addressed from an agent's ``tools:`` frontmatter as
``mcp__<server-name>__<tool-name>``. These tests pin that mapping so the Architect's
frontmatter grant and the server registration cannot silently drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_JSON = REPO_ROOT / ".mcp.json"


def _config() -> dict:
    return json.loads(MCP_JSON.read_text(encoding="utf-8"))


def test_mcp_json_exists_and_is_valid_json() -> None:
    assert MCP_JSON.is_file(), ".mcp.json must exist at the project root"
    assert isinstance(_config().get("mcpServers"), dict)


def test_registers_exactly_the_jira_and_obsidian_servers() -> None:
    servers = _config()["mcpServers"]
    assert set(servers) == {"jira", "obsidian"}


def test_each_server_is_a_stdio_python_module_invocation() -> None:
    servers = _config()["mcpServers"]
    assert servers["jira"]["command"] == "python"
    assert servers["jira"]["args"] == ["-m", "harness.mcp.jira_server"]
    assert servers["obsidian"]["command"] == "python"
    assert servers["obsidian"]["args"] == ["-m", "harness.mcp.obsidian_server"]


def test_no_server_carries_a_committed_credential() -> None:
    blob = MCP_JSON.read_text(encoding="utf-8").lower()
    for secret_ish in ("token", "api_key", "apikey", "password", "secret", "bearer", "sk-ant", "ghp_"):
        assert secret_ish not in blob
    for server in _config()["mcpServers"].values():
        assert server.get("env", {}) == {}


def test_referenced_server_modules_exist() -> None:
    assert (REPO_ROOT / "harness" / "mcp" / "jira_server.py").is_file()
    assert (REPO_ROOT / "harness" / "mcp" / "obsidian_server.py").is_file()


def test_architect_frontmatter_mcp_tools_map_to_a_registered_server() -> None:
    text = (REPO_ROOT / ".claude" / "agents" / "architect.md").read_text(encoding="utf-8")
    tools_line = next(line for line in text.splitlines() if line.startswith("tools:"))
    mcp_tools = [t.strip() for t in tools_line.split(":", 1)[1].split(",") if t.strip().startswith("mcp__")]
    assert mcp_tools == ["mcp__obsidian__search_notes", "mcp__obsidian__read_note"]
    servers = _config()["mcpServers"]
    for tool in mcp_tools:
        _, server_name, tool_name = tool.split("__", 2)
        assert server_name in servers
        assert server_name == "obsidian"  # the Architect is granted the docs connector only, never jira
        assert tool_name in {"search_notes", "read_note"}
