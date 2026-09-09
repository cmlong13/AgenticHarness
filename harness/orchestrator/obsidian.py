"""Deterministic Obsidian run-summary publication boundary for the orchestrator.

Distinct from harness/orchestrator/memory.py (the lessons-learned / facts feedback
loop -- a different ASSIGNMENT.md requirement entirely) and from
harness/orchestrator/evidence_io.py (run-directory evidence persistence): this module
answers "was a concise run summary actually published to the configured Obsidian
vault, and if not, exactly why," never "what did the caller assert about it."

ASSIGNMENT.md requirement this module serves (§2.4 connector table; §4 acceptance
checklist): "the orchestrator writes summaries back to [the Obsidian vault]" /
"Obsidian vault receives a run summary." The Architect *reads* the vault during
Discovery/Research; only the orchestrator ever *writes*, and only a concise run
summary, after a run reaches a terminal state and its own run-summary.json is final.
ASSIGNMENT.md gives `mcp-obsidian` only as an example ("e.g."), not a mandate, and its
own §2.4 caveat is that an MCP server is not automatically the right tool -- this
repository already made GitHub and Jira real REST/CLI connectors rather than MCP
servers for exactly that reason. There is no `mcp-obsidian` server configured in this
environment (confirmed at milestone start: `claude mcp list` reports none, and no
`.mcp.json` exists). The MCP-vs-REST dual-implementation requirement (§2.4, "for at
least one connector") is deliberately NOT discharged here -- it remains its own future
milestone, since ASSIGNMENT.md never names Obsidian as the connector that must be
built both ways.

THE CARDINAL RULE: a generated Markdown summary is not proof that the note was
published to Obsidian. `render_run_summary_note` produces a string; only a real
`publish` call whose `VaultWriter` genuinely wrote a file inside the configured vault
directory -- and returned `status="published"` -- may ever be reported as an Obsidian
delivery. Never claim publication from having built the note text, from writing a
temporary local file, or from any local folder that is not the configured vault.

Connector boundary. The one real publication mechanism is a filesystem write of a
Markdown note into the vault directory named by the `OBSIDIAN_VAULT_PATH` environment
variable (optionally under a subfolder named by `OBSIDIAN_SUMMARY_DIR`, default
"Harness Run Summaries"). That is exactly what an `mcp-obsidian` server would do too --
a vault is a directory of Markdown files. The write goes through a swappable
`VaultWriter` seam (`DEFAULT_WRITER` in production -- a real, atomic temp-file+replace
write; a fake writer only ever used by tests, to inject `published` / `collision` /
`write_failed` outcomes without touching a real vault). Production code
(harness/orchestrator/live_cli.py) always uses `DEFAULT_WRITER`; the simulated seam is
never exposed through the live CLI boundary, so a live `/work` run can never fabricate
a publication the way a deterministic test fixture deliberately can.

Destination / path safety. The system rejects: a missing destination
(`connector_unavailable`); a structurally unsafe configured path -- not absolute,
containing `..`, or a summary subfolder / note name that would escape the vault
(`invalid_destination`); a configured path that does not resolve to an existing
directory (`destination_unavailable`); and a note name collision when overwrite is not
explicitly requested (`collision`). A genuine writer failure (permission denied, disk
error) is `write_failed`. Only `published` is ever an affirmative outcome.

No credential or secret is read, stored, or retained by this module -- an Obsidian
vault filesystem write needs none. The note renderer draws strictly from a run's own
already-retained evidence and never emits credentials, tokens, authorization headers,
hidden agent transcripts, or raw logs.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_SUMMARY_DIR = "Harness Run Summaries"

_ENV_VAULT_PATH = "OBSIDIAN_VAULT_PATH"
_ENV_SUMMARY_DIR = "OBSIDIAN_SUMMARY_DIR"

# A safe Obsidian note filename: starts alphanumeric, then word chars / dash / dot /
# space, ends in ".md", bounded length. No path separators, no leading dot, no "..".
_NOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.md$")

PUBLICATION_STATUSES = frozenset(
    {
        "published",
        "connector_unavailable",
        "invalid_destination",
        "destination_unavailable",
        "collision",
        "write_failed",
    }
)

_MAX_NOTE_BYTES = 64 * 1024  # a run summary is concise by contract -- never a log dump.


class ObsidianError(Exception):
    """A classified Obsidian publication failure. `code` is always one of
    PUBLICATION_STATUSES (never `published`)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# Destination resolution + path safety
# ---------------------------------------------------------------------------


def _is_absolute(raw: str) -> bool:
    """True for a POSIX-absolute path or a Windows drive-absolute path. `os.path.isabs`
    alone is platform-dependent (it rejects "C:\\..." on POSIX), so a drive-letter shape
    is checked explicitly -- the vault may legitimately be configured on either OS."""
    if raw.startswith("/"):
        return True
    if len(raw) >= 3 and raw[1] == ":" and raw[0].isalpha() and raw[2] in ("/", "\\"):
        return True
    return os.path.isabs(raw)


@dataclass(frozen=True)
class Destination:
    """A validated Obsidian publication destination. `vault_path` is a resolved,
    existing directory; `summary_dir` is a vault-relative, containment-checked
    subfolder (possibly "")."""

    vault_path: Path
    summary_dir: str

    def note_relpath(self, note_name: str) -> str:
        parts = [p for p in (self.summary_dir, note_name) if p]
        return "/".join(parts)


def resolve_destination(env: dict | None = None) -> Destination:
    """Resolves the configured Obsidian vault destination from the environment, fresh
    on every call -- never cached, never accepted from a request payload. Raises
    ObsidianError with:
      - `connector_unavailable` when OBSIDIAN_VAULT_PATH is unset/blank (nothing is
        configured -- an honest "no connector" outcome, exactly like jira_connector's
        missing-credentials case, not a failure to work around);
      - `invalid_destination` when the configured value is structurally unsafe
        (not absolute, contains "..", or the summary subfolder is absolute / contains
        "..");
      - `destination_unavailable` when the configured value is structurally fine but
        does not resolve to an existing directory.
    """
    source = env if env is not None else os.environ
    raw_vault = (source.get(_ENV_VAULT_PATH) or "").strip()
    if not raw_vault:
        raise ObsidianError(
            f"no Obsidian vault is configured in this environment ({_ENV_VAULT_PATH} is not set)",
            "connector_unavailable",
        )
    if not _is_absolute(raw_vault):
        raise ObsidianError(f"{_ENV_VAULT_PATH} must be an absolute path, got {raw_vault!r}", "invalid_destination")
    if ".." in Path(raw_vault).parts:
        raise ObsidianError(f"{_ENV_VAULT_PATH} must not contain '..': {raw_vault!r}", "invalid_destination")
    try:
        vault = Path(raw_vault).resolve(strict=True)
    except OSError as exc:
        raise ObsidianError(f"{_ENV_VAULT_PATH} does not resolve to an existing path: {exc}", "destination_unavailable")
    if not vault.is_dir():
        raise ObsidianError(f"{_ENV_VAULT_PATH} ({vault}) is not a directory", "destination_unavailable")

    raw_dir = (source.get(_ENV_SUMMARY_DIR) or "").strip().replace("\\", "/")
    if raw_dir.strip("/") == "":
        return Destination(vault_path=vault, summary_dir=DEFAULT_SUMMARY_DIR)
    if _is_absolute(raw_dir) or ".." in raw_dir.split("/"):
        raise ObsidianError(f"{_ENV_SUMMARY_DIR} must be a relative path without '..': {raw_dir!r}", "invalid_destination")
    return Destination(vault_path=vault, summary_dir=raw_dir.strip("/"))


def build_note_name(run_id: str) -> str:
    """Deterministic note filename for a run summary -- one canonical shape everywhere.
    `run_id` already matches the harness identifier pattern
    (^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$), so `run-summary-<run_id>.md` is always a safe
    note name; `safe_note_target` re-checks it regardless."""
    return f"run-summary-{run_id}.md"


def safe_note_target(destination: Destination, note_name: str) -> Path:
    """Returns the absolute filesystem path the note would be written to, after
    confirming: `note_name` is a safe bare filename (no separators, no "..", ends
    ".md"), and the fully-resolved target is genuinely inside `destination.vault_path`
    (catching any summary-dir or note-name traversal that slipped past the lexical
    checks). Raises ObsidianError(`invalid_destination`) otherwise."""
    if not _NOTE_NAME_RE.match(note_name or ""):
        raise ObsidianError(f"unsafe Obsidian note name {note_name!r}", "invalid_destination")
    if "/" in note_name or "\\" in note_name:
        raise ObsidianError(f"note name must not contain a path separator: {note_name!r}", "invalid_destination")
    target = (destination.vault_path / destination.summary_dir / note_name).resolve()
    vault = destination.vault_path.resolve()
    if not (target == vault or target.is_relative_to(vault)):
        raise ObsidianError(
            f"resolved note path {target} escapes the configured vault {vault}", "invalid_destination"
        )
    if target == vault or target.parent == target:
        raise ObsidianError("resolved note path is the vault root itself", "invalid_destination")
    return target


# ---------------------------------------------------------------------------
# The VaultWriter seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteOutcome:
    ok: bool
    absolute_path: str
    bytes_written: int
    error: str = ""  # "" on success; "collision" or an OS error string on failure.


VaultWriter = Callable[[Path, str, bool], WriteOutcome]


def _default_writer(target: Path, content: str, overwrite: bool) -> WriteOutcome:
    """The one real vault-write entry point: an atomic temp-file + os.replace write of
    a UTF-8 Markdown note into the configured vault. Refuses to clobber an existing
    note unless `overwrite` is explicitly True. Never raises -- an OS failure is folded
    into a non-ok WriteOutcome so every caller classifies rather than crashes."""
    try:
        data = content.encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            return WriteOutcome(ok=False, absolute_path=str(target), bytes_written=0, error="collision")
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        try:
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        return WriteOutcome(ok=True, absolute_path=str(target), bytes_written=len(data))
    except OSError as exc:
        return WriteOutcome(ok=False, absolute_path=str(target), bytes_written=0, error=str(exc))


DEFAULT_WRITER: VaultWriter = _default_writer


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationResult:
    status: str  # one of PUBLICATION_STATUSES
    note_name: str
    note_relpath: str  # vault-relative, e.g. "Harness Run Summaries/run-summary-run-1.md"
    reason: str
    absolute_path: str | None = None  # set only on `published`
    content_sha256: str | None = None  # set only on `published` -- identifies the exact note
    bytes_written: int | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "note_name": self.note_name,
            "note_relpath": self.note_relpath,
            "reason": self.reason,
            "absolute_path": self.absolute_path,
            "content_sha256": self.content_sha256,
            "bytes_written": self.bytes_written,
        }


def publish(
    *,
    destination: Destination,
    note_name: str,
    content: str,
    writer: VaultWriter | None = None,
    overwrite: bool = False,
) -> PublicationResult:
    """Publishes `content` as an Obsidian note named `note_name` into `destination`.
    Every failure mode is classified, never raised. `published` is returned only when
    the writer genuinely wrote the file."""
    writer = writer or DEFAULT_WRITER
    relpath = destination.note_relpath(note_name)

    if len(content.encode("utf-8")) > _MAX_NOTE_BYTES:
        return PublicationResult(
            status="write_failed", note_name=note_name, note_relpath=relpath,
            reason=f"rendered note exceeds the {_MAX_NOTE_BYTES}-byte concise-summary ceiling",
        )
    try:
        target = safe_note_target(destination, note_name)
    except ObsidianError as exc:
        return PublicationResult(status=exc.code, note_name=note_name, note_relpath=relpath, reason=exc.message)

    if not destination.vault_path.is_dir():
        return PublicationResult(
            status="destination_unavailable", note_name=note_name, note_relpath=relpath,
            reason=f"configured vault {destination.vault_path} is not a directory",
        )
    if target.exists() and not overwrite:
        return PublicationResult(
            status="collision", note_name=note_name, note_relpath=relpath,
            reason=f"a note already exists at {relpath}; pass overwrite to replace it",
        )

    outcome = writer(target, content, overwrite)
    if not outcome.ok:
        if outcome.error == "collision":
            return PublicationResult(
                status="collision", note_name=note_name, note_relpath=relpath,
                reason=f"a note already exists at {relpath}; pass overwrite to replace it",
            )
        return PublicationResult(
            status="write_failed", note_name=note_name, note_relpath=relpath,
            reason=f"vault write failed: {outcome.error}",
        )
    return PublicationResult(
        status="published", note_name=note_name, note_relpath=relpath,
        reason="note written to the configured Obsidian vault",
        absolute_path=outcome.absolute_path,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        bytes_written=outcome.bytes_written,
    )


# ---------------------------------------------------------------------------
# Run-summary note rendering (from retained evidence only)
# ---------------------------------------------------------------------------


def _md_escape(value: object) -> str:
    """Renders a scalar as a single safe Markdown line -- newlines collapsed, so a
    multi-line objective from an artifact can never break the bullet structure."""
    text = "" if value is None else str(value)
    return " ".join(text.split())


def _source_mode(scope: dict | None) -> str:
    if not isinstance(scope, dict):
        return "unknown"
    source = scope.get("source")
    if isinstance(source, dict) and isinstance(source.get("type"), str) and source["type"]:
        return source["type"]
    return "unknown"


def _tests_line(verification_report: dict | None) -> list[str]:
    if not isinstance(verification_report, dict):
        return ["- Tests executed: no verification-report.json retained for this run"]
    lines: list[str] = []
    verdict = verification_report.get("final_verdict")
    attempts = verification_report.get("attempts")
    ac_results = verification_report.get("acceptance_criteria_results")
    if isinstance(attempts, list) and attempts:
        cmds = []
        for att in attempts:
            if not isinstance(att, dict):
                continue
            rc = att.get("requested_command") or {}
            cmd = rc.get("command") if isinstance(rc, dict) else None
            exit_code = att.get("exit_code")
            classification = att.get("classification")
            piece = f"`{_md_escape(cmd)}` (exit {exit_code}"
            if classification:
                piece += f", {classification}"
            piece += ")"
            cmds.append(piece)
        if cmds:
            lines.append(f"- Test commands: {'; '.join(cmds)}")
    if isinstance(ac_results, list) and ac_results:
        passed = sum(1 for r in ac_results if isinstance(r, dict) and r.get("status") == "pass")
        lines.append(f"- Acceptance criteria: {passed}/{len(ac_results)} passed")
    lines.append(f"- Verification verdict: {_md_escape(verdict) or 'unknown'}")
    return lines


def render_run_summary_note(
    *,
    run_id: str,
    task_id: str,
    run_summary: dict,
    generated_at: str,
    scope: dict | None = None,
    verification_report: dict | None = None,
    jira_resolution: dict | None = None,
    push_verification: dict | None = None,
    usage_summary: dict | None = None,
) -> str:
    """Builds the concise, auditable Obsidian run-summary note from a run's own already
    retained evidence. Deterministic (the only timestamp is the caller-supplied
    `generated_at`), Unicode-safe (returned as a `str`, encoded UTF-8 at write time),
    and free of credentials, tokens, authorization headers, agent transcripts, and raw
    logs -- it reads only whitelisted scalar fields from each evidence document, never
    a `raw`/`transcript`/`headers` blob. Missing optional evidence yields an omitted or
    explicitly-noted section, never a fabricated value."""
    verdict = _md_escape(run_summary.get("final_verdict")) or "unknown"
    objective = _md_escape(run_summary.get("objective_summary")) or "(no objective summary retained)"
    phases = run_summary.get("phases_completed") or []
    refs = run_summary.get("artifact_refs") or {}

    out: list[str] = []
    out.append("---")
    out.append(f"run_id: {run_id}")
    out.append(f"task_id: {task_id}")
    out.append(f"final_verdict: {verdict}")
    out.append(f"generated_at: {generated_at}")
    out.append("harness: agentic-harness")
    out.append("---")
    out.append("")
    out.append(f"# Harness run summary — {run_id}")
    out.append("")
    out.append(f"- **Run ID:** {run_id}")
    out.append(f"- **Task ID:** {task_id}")
    out.append(f"- **Source mode:** {_source_mode(scope)}")
    out.append(f"- **Objective:** {objective}")
    out.append(f"- **Final verdict:** {verdict}")
    out.append(f"- **Phases completed:** {', '.join(phases) if phases else '(none)'}")
    if run_summary.get("phases_reused"):
        out.append(f"- **Phases reused (resume):** {', '.join(run_summary['phases_reused'])}")
    out.append(f"- **Generated at:** {generated_at}")
    out.append("")

    out.append("## Pipeline artifacts")
    artifact_labels = [
        ("scope", "Discovery / scope"),
        ("findings", "Research / findings"),
        ("implementation_report", "Implementation report"),
        ("verification_report", "Verification report"),
        ("checkpoint", "Checkpoint"),
    ]
    any_ref = False
    for key, label in artifact_labels:
        if refs.get(key):
            out.append(f"- {label}: `{_md_escape(refs[key])}`")
            any_ref = True
    if not any_ref:
        out.append("- (no pipeline artifacts retained)")
    out.append("")

    out.append("## Tests")
    out.extend(_tests_line(verification_report))
    out.append("")

    out.append("## Memory")
    if run_summary.get("memory_influenced_run"):
        refs_used = run_summary.get("memory_refs_used") or []
        out.append(f"- Memory influenced this run: yes — {', '.join(refs_used) if refs_used else '(refs unlisted)'}")
    else:
        out.append("- Memory influenced this run: no")
    appended = run_summary.get("lessons_learned_appended") or []
    out.append(f"- Lessons appended this run: {len(appended)}")
    out.append("")

    out.append("## Usage / cost")
    if run_summary.get("usage_summary_ref"):
        out.append(f"- Usage summary: `{_md_escape(run_summary['usage_summary_ref'])}`")
    if isinstance(usage_summary, dict):
        subtotal = usage_summary.get("subagent_subtotal") or {}
        if isinstance(subtotal, dict):
            cost = subtotal.get("cost_usd")
            tokens = subtotal.get("total_tokens")
            out.append(f"- Subagent subtotal: {tokens} tokens, ${cost} (subtotal only — not full pipeline cost)")
        out.append(f"- Coverage status: {_md_escape(usage_summary.get('coverage_status')) or 'unknown'}")
        out.append(f"- Full pipeline total: {usage_summary.get('full_pipeline_total')}")
    elif not run_summary.get("usage_summary_ref"):
        out.append("- (no usage summary retained)")
    out.append("")

    if isinstance(jira_resolution, dict) and jira_resolution.get("status") == "resolved":
        issue = jira_resolution.get("issue") or {}
        out.append("## Jira source")
        out.append(f"- Issue: {_md_escape(issue.get('issue_key'))}")
        if issue.get("url"):
            out.append(f"- URL: {_md_escape(issue.get('url'))}")
        out.append("")

    if isinstance(push_verification, dict) and push_verification.get("status"):
        out.append("## Git delivery")
        out.append(f"- Push verification: {_md_escape(push_verification.get('status'))}")
        if push_verification.get("observed_sha"):
            out.append(
                f"- Observed / expected SHA: {_md_escape(push_verification.get('observed_sha'))} / "
                f"{_md_escape(push_verification.get('expected_sha'))}"
            )
        out.append("")

    gaps: list[str] = []
    for risk in run_summary.get("remaining_risks") or []:
        gaps.append(_md_escape(risk))
    for action in run_summary.get("follow_up_actions") or []:
        gaps.append(_md_escape(action))
    if isinstance(usage_summary, dict) and usage_summary.get("full_pipeline_total") is None:
        gaps.append("Orchestrator-side usage was not measured; full_pipeline_total is null.")
    out.append("## Evidence gaps")
    if gaps:
        out.extend(f"- {g}" for g in gaps)
    else:
        out.append("- (none recorded)")
    out.append("")

    out.append("---")
    out.append(f"_Generated {generated_at} from retained run evidence under `runs/{run_id}/`._")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Evidence-identity helper
# ---------------------------------------------------------------------------


@dataclass
class DestinationIdentity:
    configured: bool
    vault_path: str | None = None
    summary_dir: str | None = None
    note_relpath: str | None = None
    raw_env_value: str | None = None

    def as_dict(self) -> dict:
        return {
            "configured": self.configured,
            "vault_path": self.vault_path,
            "summary_dir": self.summary_dir,
            "note_relpath": self.note_relpath,
            "raw_env_value": self.raw_env_value,
        }


def describe_destination(
    note_name: str, *, destination: Destination | None = None, env: dict | None = None
) -> DestinationIdentity:
    """Best-effort destination identity for the retained evidence file. On success the
    resolved `destination` is passed and fully described; on failure the raw configured
    value (if any) is still recorded so the evidence is auditable, but nothing is
    fabricated. No credential is involved -- a vault path is not a secret."""
    source = env if env is not None else os.environ
    raw = (source.get(_ENV_VAULT_PATH) or "").strip() or None
    if destination is not None:
        return DestinationIdentity(
            configured=True,
            vault_path=str(destination.vault_path),
            summary_dir=destination.summary_dir,
            note_relpath=destination.note_relpath(note_name),
            raw_env_value=raw,
        )
    return DestinationIdentity(configured=bool(raw), raw_env_value=raw)
