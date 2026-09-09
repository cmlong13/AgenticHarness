"""Deterministic Obsidian vault READ boundary for Discovery/Research consultation.

Distinct from harness/orchestrator/obsidian.py (the run-summary WRITE-BACK /
publication boundary) on purpose: that module answers "was a concise run summary
actually published to the configured vault, and if not, why"; this one answers
"what does the vault already record that could inform Discovery or Research, and
can a given note be trusted as-is." A write claim and a read claim must never be
conflated -- they live in separate modules, with separate classifications, and
their cardinal rules point in opposite directions:

  publication cardinal rule (obsidian.py): a generated Markdown summary is not
    proof that the note was published to Obsidian.
  read cardinal rule (this module): **a vault note is historical / contextual
    evidence, never current repository truth.** Vault content may inform Discovery
    and Research, but it must never silently override the user request,
    ASSIGNMENT.md, repository evidence, Protected Paths, agent permissions, test
    evidence, current source code, or scope validation. A stale or contradictory
    note is historical/contextual evidence to be checked, not unquestioned fact.

ASSIGNMENT.md requirement served (§2.4 connector table): "Obsidian ... Used in
Discovery, Research ... The architect reads it" -- design notes, past decisions,
calibration docs. This module is the narrow, deterministic read/search boundary
the orchestrator drives on the Architect's behalf, consistent with this harness's
Option C architecture: every external boundary here is mediated by the main
session + live_cli.py, exactly as the Architect's own findings.json is written for
it and exactly as test execution is mediated for the Engineer -- the Architect
holds only Read/Grep/Glob and never touches this module directly.

Not in scope here, unchanged by this module: the MCP + REST dual-connector
requirement (§2.4 "for at least one connector"). ASSIGNMENT.md never names Obsidian
as that connector; this is deliberately a single filesystem-vault reader, and the
dual-connector requirement remains its own separate, still-open milestone.

Destination. The vault directory is named by OBSIDIAN_VAULT_PATH, read fresh from
the environment on every call -- never cached, never accepted from a request
payload, never hard-coded. OBSIDIAN_SUMMARY_DIR only scopes where the publication
side writes its own note and is irrelevant to reads; it is not consulted here.

Path safety. Rejected: no configured vault (connector_unavailable); a structurally
unsafe configured path -- not absolute, or containing ".." (invalid_destination);
a well-formed configured path that is not an existing directory
(destination_unavailable); a caller-supplied note path that is absolute,
drive-prefixed, contains "..", does not end ".md", targets a dot-directory /
dotfile, or whose fully-resolved target escapes the vault (invalid_note_path). A
note path that is structurally safe but names no existing file is not_found --
never a silent fall-through to a different file.

Eligible content. Only Markdown (".md") knowledge files under the vault. Any path
component beginning "." (".obsidian/", ".trash/", a hidden note) is skipped
entirely -- never searched, never read. Search results are bounded
(MAX_SEARCH_RESULTS, 5-10 band) and each snippet is bounded (SNIPPET_RADIUS); a
single note read is bounded too (_MAX_NOTE_RETURN_BYTES). The whole vault is never
dumped.

No credential or secret is read, stored, or retained -- a vault filesystem read
needs none.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_ENV_VAULT_PATH = "OBSIDIAN_VAULT_PATH"

MAX_SEARCH_RESULTS = 8  # 5-10 band per the milestone; a hard deterministic ceiling.
SNIPPET_RADIUS = 160  # characters each side of the first match -> a bounded excerpt.
_MAX_FILES_SCANNED = 5000  # a knowledge vault, not a monorepo.
_MAX_FILE_BYTES = 2 * 1024 * 1024  # skip a pathologically large "note" rather than load it.
_MAX_QUERY_LEN = 200
_MAX_NOTE_RETURN_BYTES = 64 * 1024  # a single note read is bounded; identity is always full-file.

READ_STATUSES = frozenset(
    {
        "read",
        "not_found",
        "connector_unavailable",
        "invalid_destination",
        "destination_unavailable",
        "invalid_note_path",
        "read_failed",
    }
)
SEARCH_STATUSES = frozenset(
    {
        "found",
        "no_matches",
        "connector_unavailable",
        "invalid_destination",
        "destination_unavailable",
        "invalid_query",
        "search_failed",
    }
)

_WS_RE = re.compile(r"\s+")


class ObsidianReadError(Exception):
    """A classified Obsidian read failure. `code` is always a non-affirmative member
    of READ_STATUSES / SEARCH_STATUSES (never `read` / `found`)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Destination resolution + path safety
# ---------------------------------------------------------------------------


def _is_absolute(raw: str) -> bool:
    """True for a POSIX-absolute path or a Windows drive-absolute path -- the vault
    may legitimately be configured on either OS, and `os.path.isabs` alone is
    platform-dependent. Mirrors harness/orchestrator/obsidian.py's own helper; kept
    local here so the proven publication path is not touched by read-side work."""
    if raw.startswith("/"):
        return True
    if len(raw) >= 3 and raw[1] == ":" and raw[0].isalpha() and raw[2] in ("/", "\\"):
        return True
    return os.path.isabs(raw)


def resolve_vault(env: dict | None = None) -> Path:
    """Resolves the configured Obsidian vault directory from the environment, fresh
    on every call -- never cached, never accepted from a request payload. Raises
    ObsidianReadError with `connector_unavailable` (OBSIDIAN_VAULT_PATH unset/blank
    -- an honest "no connector" outcome, exactly like the Jira connector's
    missing-credentials case), `invalid_destination` (not absolute, or contains
    ".."), or `destination_unavailable` (structurally fine but not an existing
    directory)."""
    source = env if env is not None else os.environ
    raw = (source.get(_ENV_VAULT_PATH) or "").strip()
    if not raw:
        raise ObsidianReadError(
            f"no Obsidian vault is configured in this environment ({_ENV_VAULT_PATH} is not set)",
            "connector_unavailable",
        )
    if not _is_absolute(raw):
        raise ObsidianReadError(
            f"{_ENV_VAULT_PATH} must be an absolute path, got {raw!r}", "invalid_destination"
        )
    if ".." in Path(raw).parts:
        raise ObsidianReadError(f"{_ENV_VAULT_PATH} must not contain '..': {raw!r}", "invalid_destination")
    try:
        vault = Path(raw).resolve(strict=True)
    except OSError as exc:
        raise ObsidianReadError(
            f"{_ENV_VAULT_PATH} does not resolve to an existing path: {exc}", "destination_unavailable"
        )
    if not vault.is_dir():
        raise ObsidianReadError(f"{_ENV_VAULT_PATH} ({vault}) is not a directory", "destination_unavailable")
    return vault


def _relpath(vault: Path, target: Path) -> str:
    return target.resolve().relative_to(vault.resolve()).as_posix()


def safe_note_target(vault: Path, note_path: str) -> Path:
    """Returns the absolute filesystem path a vault-relative Markdown note would
    resolve to, after confirming it is structurally safe and genuinely inside
    `vault`. Raises ObsidianReadError(`invalid_note_path`) otherwise -- never
    follows a caller-controlled path outside the configured vault, and never
    resolves into a hidden/system directory."""
    if not isinstance(note_path, str) or not note_path.strip():
        raise ObsidianReadError("a vault-relative note path is required", "invalid_note_path")
    normalized = note_path.strip().replace("\\", "/")
    if normalized.startswith("/"):
        raise ObsidianReadError(f"note path must be vault-relative, not absolute: {note_path!r}", "invalid_note_path")
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        raise ObsidianReadError(f"note path must not be drive-prefixed: {note_path!r}", "invalid_note_path")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        raise ObsidianReadError(f"note path resolves to the vault root: {note_path!r}", "invalid_note_path")
    if ".." in parts:
        raise ObsidianReadError(f"note path must not contain '..': {note_path!r}", "invalid_note_path")
    if any(p.startswith(".") for p in parts):
        raise ObsidianReadError(
            f"note path must not reference a hidden/system entry (e.g. .obsidian/): {note_path!r}",
            "invalid_note_path",
        )
    if not parts[-1].lower().endswith(".md"):
        raise ObsidianReadError(f"only Markdown (.md) knowledge notes may be read: {note_path!r}", "invalid_note_path")
    target = (vault / "/".join(parts)).resolve()
    vault_resolved = vault.resolve()
    if not (target == vault_resolved or target.is_relative_to(vault_resolved)):
        raise ObsidianReadError(
            f"resolved note path {target} escapes the configured vault {vault_resolved}", "invalid_note_path"
        )
    return target


def _eligible_md_files(vault: Path) -> list[Path]:
    """Every eligible Markdown knowledge file under the vault, deterministically
    ordered. Skips: any path with a dot-prefixed component (.obsidian/, .trash/, a
    hidden note); anything that is not a regular ``.md`` file; and any file whose
    fully-resolved path escapes the configured vault (a symlink/junction planted in
    the vault is never opened -- the search walk applies the same containment check
    `safe_note_target` applies to a single-note read, so a vault-wide search can
    never inspect a file outside the configured vault)."""
    vault_resolved = vault.resolve()
    out: list[Path] = []
    for candidate in vault.rglob("*"):
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        rel = candidate.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if candidate.suffix.lower() != ".md":
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not (resolved == vault_resolved or resolved.is_relative_to(vault_resolved)):
            continue
        out.append(candidate)
    out.sort(key=lambda p: p.relative_to(vault).as_posix())
    return out[:_MAX_FILES_SCANNED]


def _read_text_bounded(path: Path) -> str:
    data = path.read_bytes()[:_MAX_FILE_BYTES]
    return data.decode("utf-8", errors="replace")


def _snippet(text: str, offset: int) -> str:
    start = max(0, offset - SNIPPET_RADIUS)
    end = min(len(text), offset + SNIPPET_RADIUS)
    fragment = text[start:end]
    collapsed = _WS_RE.sub(" ", fragment).strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{collapsed}{suffix}"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchMatch:
    note_path: str  # vault-relative, e.g. "Design/Risk band calibration.md"
    score: int
    matched_terms: list[str]
    line: int
    snippet: str

    def as_dict(self) -> dict:
        return {
            "note_path": self.note_path,
            "score": self.score,
            "matched_terms": list(self.matched_terms),
            "line": self.line,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class SearchResult:
    status: str  # one of SEARCH_STATUSES
    query: str
    terms: list[str]
    reason: str
    matches: list[SearchMatch] = field(default_factory=list)
    total_match_count: int = 0
    files_scanned: int = 0
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "query": self.query,
            "terms": list(self.terms),
            "reason": self.reason,
            "returned_count": len(self.matches),
            "total_match_count": self.total_match_count,
            "files_scanned": self.files_scanned,
            "truncated": self.truncated,
            "matches": [m.as_dict() for m in self.matches],
        }


def search(*, query: str, env: dict | None = None, max_results: int = MAX_SEARCH_RESULTS) -> SearchResult:
    """Deterministic bounded text search across eligible ``.md`` notes under the
    configured vault. Every failure mode is classified, never raised. Only `found`
    is an affirmative retrieval; `no_matches` is a real, honest "nothing there" --
    not a connector failure."""
    raw_query = query if isinstance(query, str) else ""
    stripped = raw_query.strip()
    if not stripped:
        return SearchResult(status="invalid_query", query=raw_query, terms=[], reason="query is empty")
    if len(stripped) > _MAX_QUERY_LEN:
        return SearchResult(
            status="invalid_query", query=stripped[:_MAX_QUERY_LEN], terms=[],
            reason=f"query exceeds the {_MAX_QUERY_LEN}-character ceiling",
        )
    terms = [t for t in stripped.lower().split() if t]
    if not terms:
        return SearchResult(status="invalid_query", query=stripped, terms=[], reason="query has no searchable terms")

    try:
        vault = resolve_vault(env)
    except ObsidianReadError as exc:
        return SearchResult(status=exc.code, query=stripped, terms=terms, reason=exc.message)

    max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
    scored: list[SearchMatch] = []
    files_scanned = 0
    try:
        for note in _eligible_md_files(vault):
            files_scanned += 1
            try:
                text = _read_text_bounded(note)
            except OSError:
                continue
            lowered = text.lower()
            score = sum(lowered.count(term) for term in terms)
            if score == 0:
                continue
            offsets = [lowered.find(term) for term in terms if term in lowered]
            first_offset = min(offsets)
            scored.append(
                SearchMatch(
                    note_path=note.relative_to(vault).as_posix(),
                    score=score,
                    matched_terms=[t for t in terms if t in lowered],
                    line=text.count("\n", 0, first_offset) + 1,
                    snippet=_snippet(text, first_offset),
                )
            )
    except OSError as exc:
        return SearchResult(status="search_failed", query=stripped, terms=terms, reason=f"vault walk failed: {exc}")

    scored.sort(key=lambda m: (-m.score, m.line, m.note_path))
    returned = scored[:max_results]
    if not returned:
        return SearchResult(
            status="no_matches", query=stripped, terms=terms,
            reason="no eligible vault note matched the query", files_scanned=files_scanned,
        )
    return SearchResult(
        status="found", query=stripped, terms=terms,
        reason=f"{len(scored)} eligible note(s) matched; returning the top {len(returned)}",
        matches=returned, total_match_count=len(scored), files_scanned=files_scanned,
        truncated=len(scored) > len(returned),
    )


# ---------------------------------------------------------------------------
# Read one note
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadResult:
    status: str  # one of READ_STATUSES
    note_path: str
    reason: str
    absolute_path: str | None = None
    content: str | None = None  # bounded to _MAX_NOTE_RETURN_BYTES
    byte_count: int | None = None  # full file, always
    line_count: int | None = None
    content_sha256: str | None = None  # full file, always -- lets a caller independently verify
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "note_path": self.note_path,
            "reason": self.reason,
            "absolute_path": self.absolute_path,
            "content": self.content,
            "byte_count": self.byte_count,
            "line_count": self.line_count,
            "content_sha256": self.content_sha256,
            "truncated": self.truncated,
        }


def read_note(*, note_path: str, env: dict | None = None) -> ReadResult:
    """Reads a single vault-relative Markdown note. Every failure mode is
    classified, never raised. Retains note identity (path + full-file SHA-256 +
    byte count) even when the returned body is bounded; never silently reads a
    different file if the requested note is missing (`not_found`)."""
    requested = note_path if isinstance(note_path, str) else ""
    try:
        vault = resolve_vault(env)
    except ObsidianReadError as exc:
        return ReadResult(status=exc.code, note_path=requested.strip(), reason=exc.message)
    try:
        target = safe_note_target(vault, requested)
    except ObsidianReadError as exc:
        return ReadResult(status=exc.code, note_path=requested.strip(), reason=exc.message)

    rel = target.relative_to(vault).as_posix()
    if not target.is_file():
        return ReadResult(status="not_found", note_path=rel, reason=f"no note exists at {rel!r} in the configured vault")
    try:
        data = target.read_bytes()
    except OSError as exc:
        return ReadResult(status="read_failed", note_path=rel, reason=f"vault read failed: {exc}")
    try:
        full_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ReadResult(status="read_failed", note_path=rel, reason=f"note is not valid UTF-8: {exc}")

    body = full_text
    truncated = False
    if len(data) > _MAX_NOTE_RETURN_BYTES:
        body = data[:_MAX_NOTE_RETURN_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    return ReadResult(
        status="read", note_path=rel, reason="note read from the configured Obsidian vault",
        absolute_path=str(target), content=body, byte_count=len(data),
        line_count=full_text.count("\n") + 1, content_sha256=hashlib.sha256(data).hexdigest(),
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Evidence-identity helper
# ---------------------------------------------------------------------------


def describe_vault(env: dict | None = None) -> dict:
    """Best-effort vault identity for a retained read-evidence file. Records the
    raw configured value (a vault path is not a credential) even on failure so the
    evidence is auditable, but fabricates nothing."""
    source = env if env is not None else os.environ
    raw = (source.get(_ENV_VAULT_PATH) or "").strip() or None
    resolved: str | None = None
    try:
        resolved = str(resolve_vault(env))
    except ObsidianReadError:
        resolved = None
    return {"configured": bool(raw), "vault_path": resolved, "raw_env_value": raw}
