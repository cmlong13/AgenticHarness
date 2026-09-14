"""Project-local MCP (Model Context Protocol) servers for the Agentic Harness.

Two real, stdlib-only stdio MCP servers live here:

- ``harness.mcp.jira_server`` -- exposes exactly one read-only tool, ``get_issue``,
  wrapping ``harness.orchestrator.jira_connector.resolve_issue``. This is Route A
  ("the MCP route") of the ``ASSIGNMENT.md`` §2.4 dual-route requirement for the Jira
  connector; Route B ("the REST-skill fallback") is the same ``jira_connector``
  module reached directly through ``live_cli.py``'s ``resolve_jira_issue`` operation.
  ``harness.orchestrator.connector_router`` selects between them and documents which
  route is kept.

- ``harness.mcp.obsidian_server`` -- exposes two read-only tools, ``search_notes``
  and ``read_note``, wrapping ``harness.orchestrator.obsidian_reader``. This is the
  narrow, read-only "docs connector" the Architect is granted directly in its
  ``tools:`` frontmatter (``ASSIGNMENT.md`` §2.2 "Read/grep/glob + your docs
  connectors"), without giving it ``Bash``, arbitrary filesystem access, or any
  write authority.

Both servers speak genuine JSON-RPC 2.0 over stdio (newline-delimited messages,
``initialize`` / ``notifications/initialized`` / ``tools/list`` / ``tools/call`` /
``ping``), so Claude Code connects to them like any other MCP server -- see the
project-root ``.mcp.json``. They are not a protocol facade: ``harness.orchestrator.
mcp_client`` drives them exactly as an external MCP client would, and the servers can
be pointed at by ``claude mcp`` directly.

Design rules shared by both servers:

- Read-only. No tool creates, updates, deletes, transitions, or comments on anything.
- No caller-supplied root path, base URL, or credential. The Jira server reads
  ``JIRA_*`` fresh from its own environment; the Obsidian server reads
  ``OBSIDIAN_VAULT_PATH`` fresh from its own environment. A tool ``arguments`` object
  can never smuggle a vault path, an endpoint, or an auth token.
- Path containment / hidden-path exclusion for Obsidian is delegated entirely to
  ``obsidian_reader`` (``safe_note_target`` / ``_eligible_md_files``) -- the server
  adds no second, weaker check and removes none.
- No secret is ever echoed into a tool result. The Jira tool returns the connector's
  own classification plus whitelisted issue fields; it never returns the request
  headers or the raw HTTP body.
- Every server failure is a classified result, never an unhandled crash: a tool
  handler exception becomes an ``isError`` tool result, a malformed request becomes a
  JSON-RPC error response.
"""
