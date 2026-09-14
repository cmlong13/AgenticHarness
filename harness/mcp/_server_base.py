"""A minimal, correct, stdlib-only JSON-RPC 2.0 stdio MCP server.

This is a real implementation of the MCP stdio transport, not a facade: messages are
newline-delimited JSON-RPC 2.0 objects on stdin/stdout, and the four methods a
read-only tool server needs are handled per the MCP spec --

    initialize                -> capabilities + serverInfo (protocolVersion echoed)
    notifications/initialized -> (notification, no response)
    ping                      -> {} result
    tools/list                -> {"tools": [...]}  (name / description / inputSchema)
    tools/call                -> {"content": [{"type": "text", "text": ...}],
                                  "isError": bool}

Anything else returns a JSON-RPC ``-32601 Method not found`` error. A request that is
not a JSON object, is missing ``method``, or is not valid JSON returns
``-32700`` / ``-32600``. A tool handler that raises is reported as an ``isError``
tool result (an execution error the client can see), never as a server crash.

The server loop is deliberately synchronous and single-threaded: one request in, one
response out, in order. That is all an MCP stdio server is required to be, and it
keeps this dependency-free.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, TextIO

# The MCP revision this server implements. `initialize` echoes the client's requested
# version when the client sends one (per the spec's version-negotiation rule); this is
# only the fallback for a client that omits it.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


@dataclass(frozen=True)
class Tool:
    """One read-only MCP tool. `handler` takes the parsed ``arguments`` object and
    returns the tool's text output; if it raises, the server reports an ``isError``
    tool result carrying ``str(exc)`` -- it never lets the exception escape the loop."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


class McpServer:
    def __init__(self, *, name: str, version: str, tools: Iterable[Tool]):
        self.name = name
        self.version = version
        self._tools = {t.name: t for t in tools}
        self._initialized = False

    # -- message handling -------------------------------------------------

    def handle_message(self, message: object) -> dict | None:
        """Returns the JSON-RPC response object for ``message``, or ``None`` when the
        message is a notification (no ``id``) that needs no reply. Pure: no I/O."""
        if not isinstance(message, dict):
            return _error(None, _INVALID_REQUEST, "request must be a JSON-RPC 2.0 object")
        msg_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(msg_id, _INVALID_REQUEST, "request is missing a string 'method'")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(msg_id, _INVALID_PARAMS, "'params' must be an object")

        is_notification = "id" not in message

        if method == "initialize":
            self._initialized = True
            requested = params.get("protocolVersion")
            protocol_version = requested if isinstance(requested, str) and requested else DEFAULT_PROTOCOL_VERSION
            return _result(
                msg_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            )
        if method == "notifications/initialized":
            return None
        if is_notification:
            # Any other notification (e.g. notifications/cancelled) is accepted silently.
            return None
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": [t.spec() for t in self._tools.values()]})
        if method == "tools/call":
            return self._handle_tools_call(msg_id, params)
        return _error(msg_id, _METHOD_NOT_FOUND, f"method {method!r} is not supported by this server")

    def _handle_tools_call(self, msg_id: object, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or name not in self._tools:
            return _error(msg_id, _INVALID_PARAMS, f"unknown tool {name!r}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(msg_id, _INVALID_PARAMS, "'arguments' must be an object")
        try:
            text = self._tools[name].handler(arguments)
        except Exception as exc:  # a tool execution error, surfaced to the client
            return _result(
                msg_id,
                {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True},
            )
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})

    # -- transport ------------------------------------------------------

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
        """Run the newline-delimited JSON-RPC loop until stdin closes. Returns 0."""
        stdin = stdin if stdin is not None else sys.stdin
        stdout = stdout if stdout is not None else sys.stdout
        for raw_line in stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _write(stdout, _error(None, _PARSE_ERROR, "message is not valid JSON"))
                continue
            response = self.handle_message(message)
            if response is not None:
                _write(stdout, response)
        return 0


def _result(msg_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _write(stdout: TextIO, obj: dict) -> None:
    stdout.write(json.dumps(obj) + "\n")
    stdout.flush()
