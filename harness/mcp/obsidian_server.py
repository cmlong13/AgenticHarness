"""Project-local Obsidian MCP server -- the narrow, read-only "docs connector" the
Architect is granted directly in its ``tools:`` frontmatter (ASSIGNMENT.md §2.2
"Read/grep/glob + your docs connectors").

Exposes exactly two read-only tools:

    search_notes(query: str)      -> JSON text (bounded, ranked matches)
    read_note(note_path: str)     -> JSON text (one note, bounded body + full identity)

Both wrap ``harness.orchestrator.obsidian_reader`` unchanged, so every path-safety and
containment guarantee that module already proves is inherited verbatim:

- ``OBSIDIAN_VAULT_PATH`` is read fresh from *this server process's* environment on
  every call (``obsidian_reader.resolve_vault``); a tool ``arguments`` object carrying
  ``vault_path`` / ``env`` / ``OBSIDIAN_VAULT_PATH`` is rejected outright.
- ``read_note`` rejects an absolute / drive-prefixed / ``..``-bearing / non-``.md`` /
  hidden-component path and any target that resolves outside the vault
  (``safe_note_target``); a structurally safe path naming no file is ``not_found``.
- ``search_notes`` walks only eligible ``.md`` files, skipping every ``.``-prefixed
  component (``.obsidian/``, ``.trash/``, hidden notes) and every file whose resolved
  path escapes the vault (``_eligible_md_files``); results are capped at
  ``MAX_SEARCH_RESULTS`` with bounded snippets -- the vault is never dumped.

What this tool grant does and does not do:

- It gives the Architect a real docs-connector capability in its own permission model
  (a named MCP tool in ``tools:``), read-only.
- It does NOT give the Architect ``Bash``, ``Edit``/``Write``, arbitrary filesystem
  access, the ``Agent`` tool, or any write path into the vault. There is no
  create/update/delete-note tool here and none will be added.
- Run identity, scope, and evidence retention still originate from the orchestrator:
  the Architect echoes any tool result it used back in its findings response, and the
  orchestrator retains the normalized read record (Option A in the milestone plan).
  A vault note remains historical/contextual evidence, never current repository truth.
"""
from __future__ import annotations

import json
import sys

from harness.orchestrator import obsidian_reader

from ._server_base import McpServer, Tool

SERVER_NAME = "obsidian"
SERVER_VERSION = "1.0.0"

_REJECTED_ARG_KEYS = frozenset({"vault_path", "env", "OBSIDIAN_VAULT_PATH", "root", "base_path"})

_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Whitespace-separated search terms. Matched case-insensitively against note text; "
            "results are ranked by term frequency and bounded.",
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}

_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "note_path": {
            "type": "string",
            "description": "A vault-relative path to a Markdown note, e.g. 'Design/Risk band calibration.md'. "
            "Must be relative, must end .md, must not contain '..' or a dot-prefixed component, "
            "and must resolve inside the configured vault.",
        }
    },
    "required": ["note_path"],
    "additionalProperties": False,
}


def _reject_smuggled(arguments: dict) -> None:
    smuggled = _REJECTED_ARG_KEYS.intersection(arguments)
    if smuggled:
        raise ValueError(
            f"the vault path is read from this server's OBSIDIAN_VAULT_PATH environment, never from tool "
            f"arguments (rejected: {sorted(smuggled)})"
        )


def _search_notes(arguments: dict) -> str:
    _reject_smuggled(arguments)
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("'query' must be a non-empty string")
    result = obsidian_reader.search(query=query)
    return json.dumps(
        {"route": "mcp", "connector": "obsidian", "tool": "search_notes", **result.as_dict()},
        indent=2,
    )


def _read_note(arguments: dict) -> str:
    _reject_smuggled(arguments)
    note_path = arguments.get("note_path")
    if not isinstance(note_path, str) or not note_path.strip():
        raise ValueError("'note_path' must be a non-empty string")
    result = obsidian_reader.read_note(note_path=note_path)
    return json.dumps(
        {"route": "mcp", "connector": "obsidian", "tool": "read_note", **result.as_dict()},
        indent=2,
    )


def build_server() -> McpServer:
    return McpServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        tools=[
            Tool(
                name="search_notes",
                description=(
                    "Search the configured Obsidian vault's Markdown notes for prior design context "
                    "(read-only, bounded). Returns ranked matches with a vault-relative path and a bounded "
                    "snippet each; hidden/system paths are never searched and the vault is never dumped."
                ),
                input_schema=_SEARCH_SCHEMA,
                handler=_search_notes,
            ),
            Tool(
                name="read_note",
                description=(
                    "Read one vault-relative Markdown note in full (read-only; body bounded to 64 KiB, "
                    "full-file SHA-256 + byte count always returned). Rejects any path that escapes the "
                    "configured vault or names a hidden/system entry."
                ),
                input_schema=_READ_SCHEMA,
                handler=_read_note,
            ),
        ],
    )


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
