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

import base64
import binascii
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

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


def get_branch_sha(repo_path: Path, branch: str, runner: CommandRunner | None = None) -> str:
    """The real local tip SHA of an existing local branch
    (`git rev-parse --verify refs/heads/<branch>`) -- distinct from `get_local_head_sha`
    above, which only ever reads the *currently checked-out* HEAD. Used by `pr_create`
    below to independently confirm a head branch's tip without requiring that branch to
    be the one currently checked out. The fully-qualified refs/heads/ form means a tag,
    a remote-tracking ref, a bare SHA, or a revision expression can never stand in for
    a real local branch."""
    ref = derive_expected_ref(branch) if isinstance(branch, str) and not branch.startswith("-") else None
    if ref is None:
        raise GitError(f"{branch!r} is not a valid branch name", "invalid_output")
    result = run_git(["rev-parse", "--verify", "--quiet", ref], repo_path, runner)
    if result.exit_code != 0:
        raise GitError(
            f"local branch {branch!r} does not exist ('git rev-parse --verify {ref}' exit {result.exit_code})",
            "command_failed",
        )
    sha = result.stdout.strip()
    if not is_full_sha(sha):
        raise GitError(f"'git rev-parse --verify {ref}' did not return a full SHA: {sha!r}", "invalid_output")
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


# ---------------------------------------------------------------------------
# ASSIGNMENT.md §2.3's five named github/ skill-pack capabilities, one narrow
# function each: read-file, search-code, commit-history, pr-review (all four
# read-only) and pr-create (the one state-changing operation -- see its own
# docstring for the explicit-authorization gate and the verify-before-act /
# re-verify-after-act guarantees mirroring verify_push above). Each returns a
# plain classified dict, the same convention gh_repo_metadata already uses --
# never an exception, never a bare boolean, always a named status a caller must
# branch on explicitly. Every caller-supplied value is validated before any
# subprocess runs, so nothing a caller passes can ever be parsed by git/gh as a
# flag (a leading "-" is always rejected, or the value is passed after "--" /
# in "--flag=value" form).
# ---------------------------------------------------------------------------

# GitHub's own owner/name shape: owner is 1-39 alphanumerics/hyphens (not leading
# with a hyphen); repository name is alphanumerics plus ".", "_", "-".
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
# Characters never legitimate in a ref/revision a caller hands this module: whitespace
# and control characters, ":" (the <ref>:<path> separator), and the glob/escape
# characters `git check-ref-format` itself forbids. "~" and "^" stay allowed so a
# revision like "HEAD~1" still works.
_UNSAFE_REF_CHARS_RE = re.compile(r"[\s\x00-\x1f\x7f:?*\[\\]")

READ_FILE_MAX_BYTES = 1_000_000
SEARCH_CODE_MAX_REPOS = 10
SEARCH_CODE_MAX_QUERY_LEN = 256
PR_TITLE_MAX_LEN = 256
PR_BODY_MAX_LEN = 65_536  # GitHub's own PR body limit


def is_repo_slug(value: object) -> bool:
    return isinstance(value, str) and bool(_REPO_SLUG_RE.match(value)) and ".." not in value


def _is_safe_ref(value: object) -> bool:
    """A branch name, tag, or commit SHA (optionally with ~N/^N) -- never a flag, a
    range ("a..b"), or anything containing the <ref>:<path> separator."""
    return (
        isinstance(value, str) and bool(value) and not value.startswith("-")
        and ".." not in value and not _UNSAFE_REF_CHARS_RE.search(value)
    )


def _is_safe_branch(value: object) -> bool:
    """A bare branch name suitable for refs/heads/<branch> -- `_is_safe_ref` minus the
    revision suffixes (~, ^) and the "refs/"/"HEAD" forms a branch name never is."""
    return (
        _is_safe_ref(value) and not any(c in value for c in "~^")
        and not value.startswith("refs/") and value != "HEAD" and not value.endswith((".lock", "/"))
    )


def _is_safe_repo_path(value: object) -> bool:
    """A repository-relative, forward-slash file path: no leading "-" or "/", no ".."
    segment, no backslash, no NUL."""
    if not isinstance(value, str) or not value or value.startswith(("-", "/")):
        return False
    if "\\" in value or "\x00" in value:
        return False
    return ".." not in value.split("/")


def read_file(
    repo_path: Path, ref: str, file_path: str, *, repo_slug: str | None = None,
    max_bytes: int = READ_FILE_MAX_BYTES, runner: CommandRunner | None = None,
) -> dict:
    """read-file: the content of `file_path` as it exists at `ref` (a branch, tag, or
    commit SHA). Two read-only routes, chosen explicitly by the caller:

    - `repo_slug` omitted: the local clone, via `git show <ref>:<path>`. `ref` must
      already be resolvable locally; this never fetches, so a read stays a read.
    - `repo_slug` given ("owner/name"): GitHub itself, via a fixed
      `gh api --method GET repos/<slug>/contents/<path>?ref=<ref>` -- the route for a
      repository that is not cloned here (e.g. a `search_code` hit elsewhere).

    Content larger than `max_bytes` is never returned (status "too_large")."""
    if not _is_safe_ref(ref):
        return {"status": "invalid_ref", "reason": f"{ref!r} is not a usable ref"}
    if not _is_safe_repo_path(file_path):
        return {"status": "invalid_path", "reason": f"{file_path!r} is not a usable repository-relative path"}
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not (1 <= max_bytes <= READ_FILE_MAX_BYTES):
        return {"status": "invalid_input", "reason": f"max_bytes must be an integer between 1 and {READ_FILE_MAX_BYTES}"}
    if repo_slug is not None:
        if not is_repo_slug(repo_slug):
            return {"status": "invalid_input", "reason": f"{repo_slug!r} is not a valid owner/name repo slug"}
        return _read_file_via_github(repo_path, repo_slug, ref, file_path, max_bytes, runner)

    base = {"source": "local_git", "ref": ref, "path": file_path}
    result = (runner or DEFAULT_RUNNER)(["git", "show", f"{ref}:{file_path}"], repo_path)
    if result.exit_code != 0:
        stderr = result.stderr.strip()
        if "does not exist in" in stderr or "exists on disk, but not in" in stderr:
            return {"status": "not_found", **base, "reason": stderr}
        if "invalid object name" in stderr or "unknown revision" in stderr or "bad revision" in stderr:
            return {"status": "invalid_ref", **base, "reason": stderr}
        return {"status": "command_failed", **base, "reason": stderr}
    if result.stdout.startswith(f"tree {ref}:{file_path}"):
        return {"status": "not_a_file", **base, "reason": "path is a directory at this ref"}
    size = len(result.stdout.encode("utf-8"))
    if size > max_bytes:
        return {"status": "too_large", **base, "size": size, "max_bytes": max_bytes}
    return {"status": "found", **base, "size": size, "content": result.stdout}


def _read_file_via_github(
    repo_path: Path, repo_slug: str, ref: str, file_path: str, max_bytes: int, runner: CommandRunner | None,
) -> dict:
    base = {"source": "github_api", "repo_slug": repo_slug, "ref": ref, "path": file_path}
    endpoint = f"repos/{repo_slug}/contents/{quote(file_path)}?ref={quote(ref, safe='')}"
    result = (runner or DEFAULT_RUNNER)(["gh", "api", "--method", "GET", endpoint], repo_path)
    if result.exit_code != 0:
        stderr = result.stderr.strip()
        if "no commit found for the ref" in stderr.lower():
            return {"status": "invalid_ref", **base, "reason": stderr}
        if "http 404" in stderr.lower():
            return {"status": "not_found", **base, "reason": stderr}
        return {"status": "command_failed", **base, "reason": stderr}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "invalid_output", **base, "reason": str(exc)}
    if isinstance(parsed, list) or (isinstance(parsed, dict) and parsed.get("type") == "dir"):
        return {"status": "not_a_file", **base, "reason": "path is a directory at this ref"}
    if not isinstance(parsed, dict) or parsed.get("type") != "file":
        return {"status": "invalid_output", **base, "reason": "expected a GitHub contents API file object"}
    size = parsed.get("size")
    if not isinstance(size, int):
        return {"status": "invalid_output", **base, "reason": "contents API response has no integer size"}
    if size > max_bytes or parsed.get("encoding") != "base64":
        # Files over GitHub's own 1 MB contents-API limit come back with encoding "none".
        return {"status": "too_large", **base, "size": size, "max_bytes": max_bytes}
    try:
        content = base64.b64decode(parsed.get("content") or "").decode("utf-8")
    except (binascii.Error, ValueError):
        return {"status": "not_text", **base, "size": size, "reason": "content is not valid base64-encoded UTF-8"}
    return {"status": "found", **base, "blob_sha": parsed.get("sha"), "size": size, "content": content}


def search_code(
    repo_path: Path, query: str, *, repo_slugs: list | None = None, limit: int = 30,
    runner: CommandRunner | None = None,
) -> dict:
    """search-code: `gh search code` -- the one capability in this set git itself
    genuinely cannot provide (searching code across GitHub-hosted repositories, not
    only the local working tree the `Grep` tool already covers). Always scoped: at
    least one `"owner/name"` in `repo_slugs` is required (at most
    SEARCH_CODE_MAX_REPOS), so this can never become an unscoped GitHub-wide search.
    The query is passed after "--" so GitHub's own negated qualifiers (e.g.
    "-language:js") are never parsed as a gh flag."""
    if not isinstance(query, str) or not query.strip():
        return {"status": "invalid_query", "reason": "query must be a non-empty string"}
    if len(query) > SEARCH_CODE_MAX_QUERY_LEN or any(c in query for c in "\r\n\x00"):
        return {"status": "invalid_query", "reason": f"query must be one line of at most {SEARCH_CODE_MAX_QUERY_LEN} characters"}
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 100):
        return {"status": "invalid_query", "reason": f"limit must be an integer between 1 and 100, got {limit!r}"}
    if not isinstance(repo_slugs, list) or not repo_slugs:
        return {"status": "invalid_query", "reason": "repo_slugs must be a non-empty list of owner/name slugs (unscoped search is not allowed)"}
    if len(repo_slugs) > SEARCH_CODE_MAX_REPOS:
        return {"status": "invalid_query", "reason": f"at most {SEARCH_CODE_MAX_REPOS} repo_slugs may be searched at once"}
    argv = ["gh", "search", "code", "--limit", str(limit), "--json", "path,repository,sha,url"]
    for slug in repo_slugs:
        if not is_repo_slug(slug):
            return {"status": "invalid_query", "reason": f"{slug!r} is not a valid owner/name repo slug"}
        argv.append(f"--repo={slug}")
    argv += ["--", query]
    base = {"query": query, "repo_slugs": list(repo_slugs), "limit": limit}
    result = (runner or DEFAULT_RUNNER)(argv, repo_path)
    if result.exit_code != 0:
        return {"status": "command_failed", **base, "reason": result.stderr.strip(), "raw": result.as_dict()}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "invalid_output", **base, "reason": str(exc), "raw": result.as_dict()}
    if not isinstance(parsed, list):
        return {"status": "invalid_output", **base, "reason": "expected a JSON array from 'gh search code'", "raw": result.as_dict()}
    if not parsed:
        return {"status": "no_matches", **base, "results": []}
    return {"status": "found", **base, "results": parsed[:limit]}


def commit_history(
    repo_path: Path, *, max_count: int = 20, ref: str | None = None, path: str | None = None,
    runner: CommandRunner | None = None,
) -> dict:
    """commit-history: bounded `git log` over the local repository, one structured
    record per commit (sha/author_name/author_email/date/subject) -- never the whole
    unbounded history. `ref`, if given, reads that branch/tag/SHA's history instead of
    HEAD (e.g. "origin/main", or a PR's headRefOid from `pr_review`); `path`, if given,
    scopes to that file/directory's own history (`git log -- <path>`)."""
    if not isinstance(max_count, int) or isinstance(max_count, bool) or not (1 <= max_count <= 200):
        return {"status": "invalid_input", "reason": f"max_count must be an integer between 1 and 200, got {max_count!r}"}
    if ref is not None and not _is_safe_ref(ref):
        return {"status": "invalid_ref", "reason": f"{ref!r} is not a usable ref"}
    if path is not None and not _is_safe_repo_path(path):
        return {"status": "invalid_input", "reason": f"{path!r} is not a usable path filter"}
    argv = ["git", "log", f"-n{max_count}", "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s"]
    if ref:
        argv.append(ref)
    argv.append("--")
    if path:
        argv.append(path)
    base = {"ref": ref, "path": path, "max_count": max_count}
    result = (runner or DEFAULT_RUNNER)(argv, repo_path)
    if result.exit_code != 0:
        stderr = result.stderr.strip()
        if ref and ("unknown revision" in stderr or "bad revision" in stderr):
            return {"status": "invalid_ref", **base, "reason": stderr}
        return {"status": "command_failed", **base, "reason": stderr, "raw": result.as_dict()}
    lines = [ln for ln in result.stdout.split("\n") if ln.strip()]
    if not lines:
        return {"status": "no_commits", **base, "commits": []}
    commits = []
    for line in lines:
        fields = line.split("\x1f")
        if len(fields) != 5 or not is_full_sha(fields[0]):
            return {"status": "invalid_output", **base, "reason": f"malformed 'git log' line: {line!r}"}
        sha, author_name, author_email, date, subject = fields
        commits.append(
            {"sha": sha, "author_name": author_name, "author_email": author_email, "date": date, "subject": subject}
        )
    # `truncated` is honest about the bound: exactly max_count records means older
    # history may exist that was deliberately not read.
    return {"status": "ok", **base, "truncated": len(commits) == max_count, "commits": commits}


PR_REVIEW_FIELDS = (
    "number,url,state,title,isDraft,author,baseRefName,headRefName,headRefOid,"
    "reviewDecision,reviews,statusCheckRollup,mergeable,mergeStateStatus,files"
)


def summarize_pull_request(pr: dict) -> dict:
    """The review-relevant facts a Verification report cites, derived only from the
    `gh pr view` JSON itself -- review decision, who approved / requested changes,
    and check-run outcomes (failing and pending check names listed explicitly)."""
    reviews = pr.get("reviews") or []
    by_state: dict = {}
    for review in reviews:
        if isinstance(review, dict):
            login = (review.get("author") or {}).get("login")
            by_state.setdefault(review.get("state"), set()).add(login)
    failing, pending, passing = [], [], 0
    for check in pr.get("statusCheckRollup") or []:
        if not isinstance(check, dict):
            continue
        name = check.get("name") or check.get("context") or "<unnamed>"
        # CheckRun entries carry status/conclusion; StatusContext entries carry state.
        outcome = (check.get("conclusion") or check.get("state") or "").upper()
        if check.get("status") and check.get("status") != "COMPLETED":
            pending.append(name)
        elif outcome in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            passing += 1
        elif outcome in {"PENDING", "EXPECTED", ""}:
            pending.append(name)
        else:
            failing.append(name)
    files = pr.get("files") or []
    return {
        "state": pr.get("state"),
        "is_draft": pr.get("isDraft"),
        "review_decision": pr.get("reviewDecision") or None,
        "approved_by": sorted(x for x in by_state.get("APPROVED", set()) if x),
        "changes_requested_by": sorted(x for x in by_state.get("CHANGES_REQUESTED", set()) if x),
        "checks_passing": passing,
        "checks_failing": sorted(failing),
        "checks_pending": sorted(pending),
        "mergeable": pr.get("mergeable"),
        "merge_state_status": pr.get("mergeStateStatus"),
        "changed_files": [f.get("path") for f in files if isinstance(f, dict)],
    }


def pr_review(
    repo_path: Path, pr_ref: str, *, repo_slug: str | None = None, runner: CommandRunner | None = None,
) -> dict:
    """pr-review: read-only PR state -- `gh pr view <pr_ref> --json ...` (a PR number or
    a branch name), returning the raw PR record plus `summarize_pull_request`'s review
    summary: review decision, approvers, check-run outcomes, mergeability, changed
    files, and the PR's real head commit (`headRefOid`) -- which a reviewer composes
    with `read_file`/`commit_history` at that SHA to read the actual change. This is
    what Verification uses to answer "has this PR been reviewed / is it mergeable";
    it issues no write call of any kind (never approves, comments, or merges)."""
    if not isinstance(pr_ref, str) or not (pr_ref.isdigit() or _is_safe_branch(pr_ref)):
        return {"status": "invalid_input", "reason": f"pr_ref must be a PR number or a branch name, got {pr_ref!r}"}
    if repo_slug is not None and not is_repo_slug(repo_slug):
        return {"status": "invalid_input", "reason": f"{repo_slug!r} is not a valid owner/name repo slug"}
    argv = ["gh", "pr", "view", pr_ref, "--json", PR_REVIEW_FIELDS]
    if repo_slug:
        argv.append(f"--repo={repo_slug}")
    result = (runner or DEFAULT_RUNNER)(argv, repo_path)
    if result.exit_code != 0:
        stderr = result.stderr.strip()
        lowered = stderr.lower()
        if "no pull requests found" in lowered or "could not resolve to a pullrequest" in lowered:
            return {"status": "not_found", "pr_ref": pr_ref, "reason": stderr}
        return {"status": "command_failed", "pr_ref": pr_ref, "reason": stderr}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "invalid_output", "pr_ref": pr_ref, "reason": str(exc)}
    if not isinstance(parsed, dict):
        return {"status": "invalid_output", "pr_ref": pr_ref, "reason": "expected a JSON object from 'gh pr view'"}
    return {"status": "found", "pr_ref": pr_ref, "summary": summarize_pull_request(parsed), "pull_request": parsed}


def pr_create(
    repo_path: Path, *, base: str, head: str, title: str, body: str, expected_repo: str,
    authorized: bool = False, authorization_source: str | None = None, remote: str = "origin",
    repo_slug: str | None = None, runner: CommandRunner | None = None,
) -> dict:
    """pr-create: the one state-changing capability in this set. In order, and
    stopping at the first failure without running anything further:

    1. Explicit authorization: `authorized` must be literally `True` (not merely
       truthy) *and* `authorization_source` must name where that authorization came
       from (a ticket key, or the user's explicit instruction) -- otherwise
       "not_authorized", before any command runs. Mirrors work/SKILL.md standing
       rule 12: no state-changing Git/GitHub action without explicit authorization.
    2. Input validation -- `expected_repo` is required, so the remote's identity is
       always checked; every value is passed to `gh` in `--flag=value` form.
    3. `head` must be an existing *local* branch (`git rev-parse --verify
       refs/heads/<head>`) -- never invented -- and its real tip must already be an
       independently `verify_push`-verified push to `remote`. This operation never
       commits or pushes; it only opens a PR for a branch already proven on the remote.
    4. `base` must already exist on `remote` (`git ls-remote`).
    5. `gh pr create`. Its own exit code / printed URL are never trusted as success:
       the PR is independently re-fetched via `pr_review` and reported "created" only
       if its base/head names, its head commit (== the verified pushed SHA), its OPEN
       state, and (when gh printed one) its URL all match. Otherwise
       "created_unverified" -- exactly verify_push's "a mutating command's own success
       signal is not evidence" discipline."""
    if authorized is not True or not isinstance(authorization_source, str) or not authorization_source.strip():
        return {
            "status": "not_authorized",
            "reason": "pr_create requires authorized=True and a non-empty authorization_source naming the explicit authorization",
        }
    for field_name, value in (("base", base), ("head", head)):
        if not _is_safe_branch(value):
            return {"status": "invalid_input", "reason": f"{field_name} {value!r} is not a valid branch name"}
    if base == head:
        return {"status": "invalid_input", "reason": "base and head must be different branches"}
    if not isinstance(title, str) or not title.strip() or len(title) > PR_TITLE_MAX_LEN or any(c in title for c in "\r\n\x00"):
        return {"status": "invalid_input", "reason": f"title must be one non-empty line of at most {PR_TITLE_MAX_LEN} characters"}
    if not isinstance(body, str) or len(body) > PR_BODY_MAX_LEN or "\x00" in body:
        return {"status": "invalid_input", "reason": f"body must be a string of at most {PR_BODY_MAX_LEN} characters"}
    if not is_repo_slug(expected_repo):
        return {"status": "invalid_input", "reason": f"expected_repo {expected_repo!r} must be a valid owner/name repo slug"}
    if repo_slug is not None and not is_repo_slug(repo_slug):
        return {"status": "invalid_input", "reason": f"repo_slug {repo_slug!r} is not a valid owner/name repo slug"}
    if not isinstance(remote, str) or not remote or remote.startswith("-") or _UNSAFE_REF_CHARS_RE.search(remote):
        return {"status": "invalid_input", "reason": f"remote {remote!r} is not a valid remote name"}

    base_info = {"base": base, "head": head, "authorization_source": authorization_source}
    try:
        head_sha = get_branch_sha(repo_path, head, runner)
    except GitError as exc:
        return {"status": "invalid_head_branch", **base_info, "reason": exc.message}
    verification = verify_push(
        repo_path=repo_path, remote=remote, branch=head, expected_sha=head_sha,
        expected_repo=expected_repo, runner=runner,
    )
    if verification.status != "verified":
        return {
            "status": "push_not_verified", **base_info, "expected_sha": head_sha,
            "verification": verification.as_dict(),
            "reason": (
                f"head branch {head!r} is not a verified push to {remote!r} "
                f"({verification.status}: {verification.reason})"
            ),
        }
    base_ref = derive_expected_ref(base)
    base_lookup = run_ls_remote(repo_path, remote, base_ref, runner)
    if base_lookup.exit_code != 0:
        return {"status": "command_failed", **base_info, "reason": base_lookup.stderr.strip(), "raw_command": base_lookup.as_dict()}
    try:
        base_entries = parse_ls_remote_output(base_lookup.stdout)
    except GitError as exc:
        return {"status": "invalid_output", **base_info, "reason": exc.message}
    if not any(e.ref == base_ref for e in base_entries):
        return {"status": "base_not_found", **base_info, "reason": f"{base_ref!r} does not exist on remote {remote!r}"}

    argv = ["gh", "pr", "create", f"--base={base}", f"--head={head}", f"--title={title}", f"--body={body}"]
    if repo_slug:
        argv.append(f"--repo={repo_slug}")
    create_result = (runner or DEFAULT_RUNNER)(argv, repo_path)
    if create_result.exit_code != 0:
        stderr = create_result.stderr.strip()
        status = "already_exists" if "already exists" in stderr.lower() else "command_failed"
        return {"status": status, **base_info, "reason": stderr, "raw_command": create_result.as_dict()}
    check = pr_review(repo_path, head, repo_slug=repo_slug, runner=runner)
    if check.get("status") != "found":
        return {
            "status": "created_unverified", **base_info, "expected_head_sha": head_sha,
            "raw_command": create_result.as_dict(), "verification_attempt": check,
            "reason": "gh pr create exited 0 but the PR could not be independently re-fetched",
        }
    pr = check["pull_request"]
    printed_url = create_result.stdout.strip().splitlines()[-1].strip() if create_result.stdout.strip() else None
    mismatches = [
        name for name, observed, expected in (
            ("baseRefName", pr.get("baseRefName"), base),
            ("headRefName", pr.get("headRefName"), head),
            ("headRefOid", pr.get("headRefOid"), head_sha),
            ("state", pr.get("state"), "OPEN"),
        ) if observed != expected
    ]
    if printed_url and pr.get("url") != printed_url:
        mismatches.append("url")
    if mismatches:
        return {
            "status": "created_unverified", **base_info, "expected_head_sha": head_sha, "pull_request": pr,
            "raw_command": create_result.as_dict(),
            "reason": f"independently re-fetched PR does not match the request on: {', '.join(mismatches)}",
        }
    return {
        "status": "created", **base_info, "head_sha": head_sha, "url": pr.get("url"), "number": pr.get("number"),
        "push_verification": verification.as_dict(), "pull_request": pr, "raw_command": create_result.as_dict(),
    }
