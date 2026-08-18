"""Deterministic Git/GitHub verification support for the orchestrator.

Distinct from harness/orchestrator/paths.py (path safety) and harness/evidence.py
(schema/semantic artifact validation): this module answers "what does Git/GitHub
actually say is true right now," never "what did an agent or a `git push` exit code
claim." Every function here either runs a real, fixed-argv `git`/`gh` subprocess (never
`shell=True`, never a caller-supplied free-form string) or classifies an already-captured
command result -- it makes no engineering decisions and never fabricates a result.

The cardinal rule this module exists to enforce (ASSIGNMENT.md, PROJECT_SPEC.md): a local
commit is not a verified push; a `git push` exit code is not independently sufficient; an
agent's "pushed" claim is not evidence; a GitHub UI assumption is not evidence. The only
thing that proves a commit reached a remote branch is an independently run
`git ls-remote <remote> <ref>` whose observed SHA is compared, as full 40-character hex
strings, against the expected local SHA -- see `classify_ls_remote_result` below.

Every subprocess call goes through a swappable `CommandRunner` (a plain
`Callable[[list[str], Path], CommandResult]`), exactly the seam
`ASSIGNMENT.md`/this milestone's own instructions require for the deterministic
false-push proof: a test can inject a `CommandRunner` that returns a fabricated
`git ls-remote` result (e.g. `expected_sha=AAA...`, observed `BBB...`) without ever
touching a real remote, and confirm the classification layer refuses to call that a
verified push. Production code (harness/orchestrator/live_cli.py) always uses
`DEFAULT_RUNNER`, a real `subprocess.run` -- the simulated seam is never exposed through
the live CLI boundary, so a real `/work` run can never accidentally fabricate a push
result the way the deterministic test fixture deliberately does.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths

REPO_ROOT = paths.REPO_ROOT

# A full Git object id: exactly 40 lowercase hex characters. A short SHA (e.g. the first
# 7-12 characters a human might paste) deliberately never matches this pattern -- see the
# module docstring's "no accidental short-SHA equality" requirement.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# The exact set of classification outcomes this module ever returns for a push
# verification -- ASSIGNMENT.md's own vocabulary ("verified" / "mismatch" /
# "remote_ref_missing" / "command_failed" / "invalid_output" / "wrong_repository"),
# never a bare boolean, so the *reason* a push could not be verified is always retained.
VERIFICATION_STATUSES = frozenset(
    {"verified", "mismatch", "remote_ref_missing", "command_failed", "invalid_output", "wrong_repository"}
)


class GitError(Exception):
    """A classified Git/GitHub failure. `code` is always one of VERIFICATION_STATUSES
    or a narrower identity-check code (see `resolve_repo_path`/`get_remote_url`)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class CommandResult:
    """The command-result boundary this module's tests inject a fake value at (see the
    module docstring). Mirrors test-runner's own request/result shape in spirit: a plain,
    JSON-serializable record of exactly what a command produced, never re-interpreted."""

    exit_code: int
    stdout: str
    stderr: str

    def as_dict(self) -> dict:
        return {"exit_code": self.exit_code, "stdout": self.stdout, "stderr": self.stderr}


CommandRunner = Callable[[list, Path], CommandResult]


def _default_runner(argv: list, cwd: Path) -> CommandResult:
    """The one real subprocess entry point in this module -- shell=False, a fixed argv
    list (never a caller-supplied string), a bounded timeout. Never raises for a normal
    command failure (a non-zero exit is a legitimate, classified CommandResult, not an
    exception) -- only a genuine inability to even launch the process is caught and
    folded into exit_code -1 so every caller gets a CommandResult either way."""
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=30, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(exit_code=-1, stdout="", stderr=str(exc))
    return CommandResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


DEFAULT_RUNNER: CommandRunner = _default_runner


def is_full_sha(value: object) -> bool:
    """True only for a genuine 40-character lowercase hex SHA. A short SHA, an
    uppercase-hex SHA, or anything else is deliberately rejected -- see the module
    docstring's "no accidental short-SHA equality" requirement."""
    return isinstance(value, str) and bool(_FULL_SHA_RE.match(value))


def run_git(args: list, repo_path: Path, runner: CommandRunner | None = None) -> CommandResult:
    return (runner or DEFAULT_RUNNER)(["git", *args], repo_path)


def resolve_repo_path(target_repo_path: str, *, repo_root: Path | None = None) -> Path:
    """Reuses paths.validate_target_repo_path -- the same real-filesystem containment +
    symlink-escape + Protected Path check the Engineer/Quality-Engineer boundary already
    relies on -- rather than re-deriving a second path-safety implementation here.
    "." resolves to repo_root itself (this repository's own root Git checkout)."""
    return paths.validate_target_repo_path(target_repo_path, repo_root=repo_root)


def get_repo_root(repo_path: Path, runner: CommandRunner | None = None) -> str:
    """The real Git-reported repository root (`git rev-parse --show-toplevel`) --
    distinct from `resolve_repo_path`'s filesystem-safety check above; this is what
    proves `repo_path` is genuinely inside a Git working tree at all. Raises GitError
    (code "command_failed") for a non-Git directory or any other rev-parse failure."""
    result = run_git(["rev-parse", "--show-toplevel"], repo_path, runner)
    if result.exit_code != 0:
        raise GitError(
            f"'git rev-parse --show-toplevel' failed (exit {result.exit_code}): {result.stderr.strip()}",
            "command_failed",
        )
    return result.stdout.strip().replace("\\", "/")


def get_current_branch(repo_path: Path, runner: CommandRunner | None = None) -> str:
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path, runner)
    if result.exit_code != 0:
        raise GitError(
            f"'git rev-parse --abbrev-ref HEAD' failed (exit {result.exit_code}): {result.stderr.strip()}",
            "command_failed",
        )
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        raise GitError("repository is in a detached-HEAD state; no current branch", "command_failed")
    return branch


def get_local_head_sha(repo_path: Path, runner: CommandRunner | None = None) -> str:
    """The real local HEAD commit id -- always independently re-derived here, never
    accepted as a caller-supplied claim, exactly as every other "expected_sha" input to
    this module's verification functions must ultimately trace back to a real
    `git rev-parse HEAD` call somewhere, not an assertion."""
    result = run_git(["rev-parse", "HEAD"], repo_path, runner)
    if result.exit_code != 0:
        raise GitError(f"'git rev-parse HEAD' failed (exit {result.exit_code}): {result.stderr.strip()}", "command_failed")
    sha = result.stdout.strip()
    if not is_full_sha(sha):
        raise GitError(f"'git rev-parse HEAD' did not return a full SHA: {sha!r}", "invalid_output")
    return sha


def get_remote_url(repo_path: Path, remote_name: str, runner: CommandRunner | None = None) -> str:
    result = run_git(["remote", "get-url", remote_name], repo_path, runner)
    if result.exit_code != 0:
        raise GitError(
            f"'git remote get-url {remote_name}' failed (exit {result.exit_code}): {result.stderr.strip()}",
            "command_failed",
        )
    url = result.stdout.strip()
    if not url:
        raise GitError(f"'git remote get-url {remote_name}' returned no URL", "invalid_output")
    return url


def derive_expected_ref(branch: str) -> str:
    """Never infer this carelessly (ASSIGNMENT.md Part 4) -- always derive it from the
    intended push target's branch name, one canonical shape, everywhere this module or
    its callers need a ref to check. A branch name may legally contain "/" (e.g.
    "feature/x"), so only empty/whitespace input and an already-fully-qualified ref
    (a caller mistake this function should catch, not silently double-prefix) are
    rejected."""
    if not branch or branch.strip() != branch:
        raise GitError(f"{branch!r} is not a valid branch name", "invalid_output")
    if branch.startswith("refs/"):
        raise GitError(f"{branch!r} looks like a ref already, not a bare branch name", "invalid_output")
    return f"refs/heads/{branch}"


def repository_identity_matches(remote_url: str, expected_repo: str) -> bool:
    """`expected_repo` is an "owner/name" slug (e.g. "cmlong13/AgenticHarness").
    Accepts both the HTTPS (`https://github.com/owner/name.git`) and SSH
    (`git@github.com:owner/name.git`) remote URL shapes Git actually produces."""
    normalized = remote_url.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[: -len(".git")]
    return normalized.endswith(f"/{expected_repo}") or normalized.endswith(f":{expected_repo}")


@dataclass(frozen=True)
class LsRemoteEntry:
    sha: str
    ref: str


def parse_ls_remote_output(raw_stdout: str) -> list:
    """Parses real `git ls-remote` stdout: one `<sha>\\t<ref>` pair per line. Tolerant of
    CRLF line endings and blank/trailing lines (both real Windows/POSIX Git can produce);
    strict about content -- a line that isn't exactly one tab-separated (sha, ref) pair,
    or whose SHA field isn't a full 40-character hex SHA, makes the *entire* output
    unusable (raises GitError, code "invalid_output") rather than silently skipping the
    bad line and half-trusting the rest."""
    entries: list[LsRemoteEntry] = []
    normalized = raw_stdout.replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            raise GitError(f"malformed ls-remote line (expected 'sha<TAB>ref'): {line!r}", "invalid_output")
        sha, ref = parts[0].strip(), parts[1].strip()
        if not is_full_sha(sha):
            raise GitError(f"malformed SHA in ls-remote line: {line!r}", "invalid_output")
        if not ref:
            raise GitError(f"empty ref in ls-remote line: {line!r}", "invalid_output")
        entries.append(LsRemoteEntry(sha=sha, ref=ref))
    return entries


def run_ls_remote(repo_path: Path, remote: str, ref: str, runner: CommandRunner | None = None) -> CommandResult:
    return run_git(["ls-remote", remote, ref], repo_path, runner)


@dataclass(frozen=True)
class PushVerificationResult:
    status: str  # one of VERIFICATION_STATUSES
    remote: str
    ref: str
    expected_sha: str
    observed_sha: str | None
    reason: str
    command: CommandResult

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "remote": self.remote,
            "ref": self.ref,
            "expected_sha": self.expected_sha,
            "observed_sha": self.observed_sha,
            "reason": self.reason,
            "raw_command": {"args": ["git", "ls-remote", self.remote, self.ref], **self.command.as_dict()},
        }


def classify_ls_remote_result(
    *, expected_sha: str, ref: str, remote: str, command_result: CommandResult
) -> PushVerificationResult:
    """The one deterministic classification function ASSIGNMENT.md's false-push proof
    exercises directly: given an `expected_sha` and a `git ls-remote` CommandResult
    (real or, in a test, a fabricated fixture -- see the module docstring), decide
    whether the push is genuinely `verified`, and if not, exactly why. A push is
    `verified` only when `observed_sha == expected_sha` as full 40-character SHAs --
    never a prefix match, never a truthy non-empty check."""
    if command_result.exit_code != 0:
        return PushVerificationResult(
            status="command_failed", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=None,
            reason=f"'git ls-remote {remote} {ref}' exited {command_result.exit_code}: {command_result.stderr.strip()}",
            command=command_result,
        )
    if not is_full_sha(expected_sha):
        return PushVerificationResult(
            status="invalid_output", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=None,
            reason=f"expected_sha {expected_sha!r} is not a full 40-character hex SHA", command=command_result,
        )
    try:
        entries = parse_ls_remote_output(command_result.stdout)
    except GitError as exc:
        return PushVerificationResult(
            status="invalid_output", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=None,
            reason=str(exc), command=command_result,
        )
    matching = [e for e in entries if e.ref == ref]
    if not matching:
        return PushVerificationResult(
            status="remote_ref_missing", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=None,
            reason=f"ref {ref!r} was not present in 'git ls-remote {remote} {ref}' output", command=command_result,
        )
    if len({e.sha for e in matching}) > 1:
        return PushVerificationResult(
            status="invalid_output", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=None,
            reason=f"ref {ref!r} appeared more than once with different SHAs in ls-remote output",
            command=command_result,
        )
    observed_sha = matching[0].sha
    if observed_sha == expected_sha:
        return PushVerificationResult(
            status="verified", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=observed_sha,
            reason="observed remote SHA matches expected local SHA", command=command_result,
        )
    return PushVerificationResult(
        status="mismatch", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=observed_sha,
        reason="observed remote SHA does not match expected local SHA", command=command_result,
    )


def verify_push(
    *,
    repo_path: Path,
    remote: str,
    branch: str,
    expected_sha: str,
    runner: CommandRunner | None = None,
    expected_repo: str | None = None,
) -> PushVerificationResult:
    """The one push-verification entry point: derives the ref, optionally rejects the
    wrong repository before ever running ls-remote (a wrong remote's SHA agreement would
    be meaningless), runs the real `git ls-remote`, and classifies the result. Never
    catches GitError from `get_remote_url` silently -- a remote that cannot even be
    resolved is reported as `command_failed`, the same status a failed ls-remote gets."""
    ref = derive_expected_ref(branch)
    try:
        remote_url = get_remote_url(repo_path, remote, runner)
    except GitError as exc:
        failed = CommandResult(exit_code=-1, stdout="", stderr=exc.message)
        return PushVerificationResult(
            status="command_failed", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=None,
            reason=exc.message, command=failed,
        )
    if expected_repo is not None and not repository_identity_matches(remote_url, expected_repo):
        failed = CommandResult(exit_code=-1, stdout="", stderr="")
        return PushVerificationResult(
            status="wrong_repository", remote=remote, ref=ref, expected_sha=expected_sha, observed_sha=None,
            reason=f"remote {remote!r} ({remote_url}) does not resolve to expected repository {expected_repo!r}",
            command=failed,
        )
    command_result = run_ls_remote(repo_path, remote, ref, runner)
    return classify_ls_remote_result(expected_sha=expected_sha, ref=ref, remote=remote, command_result=command_result)


# ---------------------------------------------------------------------------
# gh CLI -- metadata only (ASSIGNMENT.md Part 9: never a substitute for
# git ls-remote in push verification, used only where it adds real information
# git itself doesn't have: GitHub-side repository/auth identity).
# ---------------------------------------------------------------------------


def gh_auth_status(runner: CommandRunner | None = None, *, repo_path: Path | None = None) -> dict:
    """Read-only: `gh auth status`. Never used to verify a push -- see module docstring
    and ASSIGNMENT.md Part 9. `gh` writes its human-readable status to stderr even on
    success, so both streams are retained; only exit_code decides authenticated/not."""
    result = (runner or DEFAULT_RUNNER)(["gh", "auth", "status"], repo_path or REPO_ROOT)
    return {"authenticated": result.exit_code == 0, "raw": result.as_dict()}


def gh_repo_metadata(runner: CommandRunner | None = None, *, repo_path: Path | None = None) -> dict:
    """Read-only: `gh repo view --json nameWithOwner,url,defaultBranchRef`. GitHub-side
    repository identity metadata `git remote get-url` cannot provide (e.g. the
    server-recorded default branch) -- never used in place of `git ls-remote` for push
    verification, per ASSIGNMENT.md Part 9."""
    result = (runner or DEFAULT_RUNNER)(
        ["gh", "repo", "view", "--json", "nameWithOwner,url,defaultBranchRef"], repo_path or REPO_ROOT
    )
    if result.exit_code != 0:
        return {"status": "command_failed", "reason": result.stderr.strip(), "raw": result.as_dict()}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "invalid_output", "reason": str(exc), "raw": result.as_dict()}
    return {"status": "ok", "metadata": parsed, "raw": result.as_dict()}
