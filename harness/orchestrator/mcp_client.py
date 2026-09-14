"""Deterministic MCP stdio client -- drives a project-local (or, if one were
configured, external) MCP server exactly as any real MCP client would: JSON-RPC 2.0
over newline-delimited stdio, ``initialize`` -> ``notifications/initialized`` ->
``tools/list`` -> ``tools/call``.

This is the client half of Route A ("the MCP route") in ``ASSIGNMENT.md`` §2.4's
dual-route requirement. ``harness.orchestrator.connector_router`` calls
``call_tool`` and, on a defined MCP failure/unavailability, falls back to the direct
REST route.

Every exchange goes through a swappable ``Transport`` -- exactly the seam
``github.py``'s ``CommandRunner`` and ``jira_connector.py``'s ``HttpTransport``
already establish:

- ``StdioTransport`` (the production default): spawns ``python -m <server_module>``
  as a real subprocess and pipes real JSON-RPC. This is what a live ``/work`` run and
  the live proof use.
- ``InProcessTransport``: routes messages straight into a real
  ``harness.mcp._server_base.McpServer`` instance in the same process -- the real
  server + real client protocol code, no subprocess. Tests use this for the happy
  path and the honest ``connector_unavailable`` path.
- ``ScriptedTransport``: replays a fixed list of server responses (or raises) so a
  test can inject a JSON-RPC error, a malformed ``tools/call`` result, a dead pipe,
  or a hang -- the negative MCP classifications.

The production path never exposes ``InProcessTransport`` / ``ScriptedTransport``: a
live run always spawns the real server, so it can never fabricate an MCP success the
way a deterministic fixture deliberately can.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import paths

REPO_ROOT = paths.REPO_ROOT

_CLIENT_NAME = "agentic-harness-connector-router"
_CLIENT_VERSION = "1.0.0"
_PROTOCOL_VERSION = "2025-06-18"
_DEFAULT_TIMEOUT_SECONDS = 20

# The MCP-route outcome vocabulary. connector_router maps these onto the run-level
# route model; none of them is ever a bare boolean.
MCP_STATUSES = frozenset(
    {
        "mcp_ok",
        "mcp_unavailable",  # the server could not be started / is not configured
        "mcp_transport_error",  # pipe broke, process died, timed out, no reply
        "mcp_protocol_error",  # JSON-RPC error, failed handshake, wrong id
        "mcp_invalid_response",  # a result whose shape is not what the MCP spec requires
        "mcp_tool_error",  # a well-formed tool result with isError: true
    }
)


class McpClientError(Exception):
    """A classified MCP client failure. ``code`` is always a non-ok member of
    ``MCP_STATUSES``."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class Transport:
    """send one JSON-RPC object, receive one (or None for a notification)."""

    def request(self, message: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def notify(self, message: dict) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        pass


class StdioTransport(Transport):
    """Spawn ``python -m <server_module>`` and speak newline-delimited JSON-RPC to it.
    The one real subprocess entry point -- ``shell=False``, a fixed argv, a bounded
    timeout on every read."""

    def __init__(self, server_module: str, *, env: dict | None = None, timeout: float = _DEFAULT_TIMEOUT_SECONDS):
        self.server_module = server_module
        self.timeout = timeout
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", server_module],
                cwd=str(REPO_ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=env,
                bufsize=1,
            )
        except OSError as exc:
            raise McpClientError(f"could not launch MCP server {server_module!r}: {exc}", "mcp_unavailable")

    def _send(self, message: dict) -> None:
        if self._proc.poll() is not None or self._proc.stdin is None:
            raise McpClientError(f"MCP server {self.server_module!r} exited before a request could be sent", "mcp_transport_error")
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpClientError(f"MCP server {self.server_module!r} pipe broke: {exc}", "mcp_transport_error")

    def request(self, message: dict) -> dict:
        self._send(message)
        try:
            self._proc.stdout  # type: ignore[union-attr]
            line = _readline_with_timeout(self._proc, self.timeout)
        except TimeoutError:
            raise McpClientError(f"MCP server {self.server_module!r} did not reply within {self.timeout}s", "mcp_transport_error")
        if not line:
            raise McpClientError(f"MCP server {self.server_module!r} closed the connection with no reply", "mcp_transport_error")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpClientError(f"MCP server {self.server_module!r} sent a non-JSON line: {exc}", "mcp_protocol_error")

    def notify(self, message: dict) -> None:
        self._send(message)

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def _readline_with_timeout(proc: subprocess.Popen, timeout: float) -> str:
    """Read one line from ``proc.stdout`` or raise ``TimeoutError``. Uses a thread so
    the behaviour is identical on Windows and POSIX (``select`` does not work on
    Windows pipes)."""
    import queue
    import threading

    result: "queue.Queue[object]" = queue.Queue(maxsize=1)

    def _reader() -> None:
        try:
            result.put(proc.stdout.readline() if proc.stdout is not None else "")
        except Exception as exc:  # pragma: no cover - defensive
            result.put(exc)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    try:
        value = result.get(timeout=timeout)
    except Exception:
        raise TimeoutError
    if isinstance(value, Exception):
        raise McpClientError(f"reading from the MCP server failed: {value}", "mcp_transport_error")
    return str(value)


class InProcessTransport(Transport):
    """Route messages straight into a real ``McpServer`` instance -- real server + real
    client protocol logic, no subprocess. Never used in production."""

    def __init__(self, server):
        self._server = server

    def request(self, message: dict) -> dict:
        response = self._server.handle_message(message)
        if response is None:
            raise McpClientError("MCP server returned no response to a request", "mcp_protocol_error")
        return response

    def notify(self, message: dict) -> None:
        self._server.handle_message(message)


@dataclass
class ScriptedTransport(Transport):
    """Replay a fixed list of responses. An entry that is an ``Exception`` is raised
    when reached; a ``dict`` is returned as-is. Never used in production."""

    responses: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    _i: int = 0

    def _next(self) -> dict:
        if self._i >= len(self.responses):
            raise McpClientError("scripted transport exhausted", "mcp_transport_error")
        item = self.responses[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return item

    def request(self, message: dict) -> dict:
        self.sent.append(message)
        return self._next()

    def notify(self, message: dict) -> None:
        self.sent.append(message)


TransportFactory = Callable[[], Transport]


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpToolResult:
    status: str  # one of MCP_STATUSES
    server: str
    tool: str
    reason: str
    text: str | None = None  # raw tool text content, on mcp_ok
    parsed: dict | None = None  # json.loads(text), when text is a JSON object
    is_error: bool = False

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "server": self.server,
            "tool": self.tool,
            "reason": self.reason,
            "text": self.text,
            "parsed": self.parsed,
            "is_error": self.is_error,
        }


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------


def _default_transport_factory(server_module: str) -> TransportFactory:
    return lambda: StdioTransport(server_module)


def call_tool(
    *,
    server: str,
    server_module: str,
    tool: str,
    arguments: dict,
    transport_factory: TransportFactory | None = None,
) -> McpToolResult:
    """Open one MCP session, perform the handshake, call ``tool`` once, close, and
    classify. Never raises for a connector-level problem -- every outcome is a
    classified ``McpToolResult``."""
    factory = transport_factory or _default_transport_factory(server_module)
    try:
        conn = factory()
    except McpClientError as exc:
        return McpToolResult(status=exc.code, server=server, tool=tool, reason=exc.message)
    except OSError as exc:
        return McpToolResult(status="mcp_unavailable", server=server, tool=tool, reason=f"could not start MCP server: {exc}")

    try:
        _handshake(conn, server)
        raw = _call(conn, tool, arguments)
        return _classify_tool_result(raw, server, tool)
    except McpClientError as exc:
        return McpToolResult(status=exc.code, server=server, tool=tool, reason=exc.message)
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            pass


def _handshake(conn: Transport, server: str) -> None:
    response = conn.request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION},
            },
        }
    )
    _require_jsonrpc_result(response, expected_id=1, context=f"{server} initialize")
    result = response["result"]
    if not isinstance(result, dict) or "serverInfo" not in result:
        raise McpClientError(f"{server} initialize result is missing serverInfo", "mcp_invalid_response")
    conn.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})


def _call(conn: Transport, tool: str, arguments: dict) -> dict:
    response = conn.request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    )
    _require_jsonrpc_result(response, expected_id=2, context=f"tools/call {tool}")
    return response["result"]


def _require_jsonrpc_result(response: object, *, expected_id: int, context: str) -> None:
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        raise McpClientError(f"{context}: reply is not a JSON-RPC 2.0 object", "mcp_protocol_error")
    if "error" in response:
        err = response["error"]
        code = err.get("code") if isinstance(err, dict) else "?"
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise McpClientError(f"{context}: server returned JSON-RPC error {code}: {message}", "mcp_protocol_error")
    if response.get("id") != expected_id:
        raise McpClientError(f"{context}: reply id {response.get('id')!r} != request id {expected_id}", "mcp_protocol_error")
    if "result" not in response:
        raise McpClientError(f"{context}: reply has neither 'result' nor 'error'", "mcp_protocol_error")


def _classify_tool_result(result: object, server: str, tool: str) -> McpToolResult:
    if not isinstance(result, dict):
        return McpToolResult(status="mcp_invalid_response", server=server, tool=tool, reason="tools/call result is not an object")
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return McpToolResult(status="mcp_invalid_response", server=server, tool=tool, reason="tools/call result has no content")
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text" or not isinstance(first.get("text"), str):
        return McpToolResult(status="mcp_invalid_response", server=server, tool=tool, reason="tools/call result content[0] is not a text block")
    text = first["text"]
    parsed: dict | None = None
    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            parsed = candidate
    except json.JSONDecodeError:
        parsed = None
    is_error = bool(result.get("isError"))
    if is_error:
        return McpToolResult(
            status="mcp_tool_error", server=server, tool=tool,
            reason=f"tool reported an execution error: {text[:300]}", text=text, parsed=parsed, is_error=True,
        )
    return McpToolResult(
        status="mcp_ok", server=server, tool=tool, reason="tool returned a result", text=text, parsed=parsed, is_error=False,
    )


def default_server_module(server: str) -> str:
    """Map a connector name to its project-local MCP server module -- the same names
    the project-root ``.mcp.json`` registers."""
    return {"jira": "harness.mcp.jira_server", "obsidian": "harness.mcp.obsidian_server"}[server]
