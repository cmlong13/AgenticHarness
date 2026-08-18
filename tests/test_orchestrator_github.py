"""Tests for harness/orchestrator/github.py -- deterministic Git/GitHub verification.

Two kinds of coverage, deliberately kept separate:

1. Real-subprocess tests (`git_repo` fixture below): a genuine, disposable `git init`
   temp repository with one real commit and a fake `origin` remote URL (never fetched
   or pushed to -- only `git remote get-url` reads it back). These exercise the real
   `DEFAULT_RUNNER` subprocess path for repo-root/branch/HEAD-SHA/remote-URL resolution,
   never the real AgenticHarness repository itself.

2. Fake-runner tests (`FakeRunner`): inject a `CommandResult` at the exact seam
   `classify_ls_remote_result`/`verify_push` accept, per the module's own docstring --
   this is what makes the false-push proof deterministic and network-free: no real
   remote is ever contacted to prove the verification layer rejects a mismatched claim.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness.orchestrator import github, paths


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture()
def git_repo(tmp_path: Path):
    """A real, disposable Git repository -- one commit on branch "main", a fake
    "origin" remote URL that is never actually contacted. Local-only config (`-C
    <repo>`), never global, per this milestone's "do not change Git config globally"
    constraint."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "--initial-branch=main", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "file.txt").write_text("hello\n", encoding="utf-8")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-m", "initial commit", cwd=repo)
    _git("remote", "add", "origin", "https://github.com/example-owner/example-repo.git", cwd=repo)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, head_sha


class FakeRunner:
    """A controlled fake command-result adapter -- the exact seam ASSIGNMENT.md's
    false-push proof asks for. Records every call it received for assertions."""

    def __init__(self, result: github.CommandResult):
        self.result = result
        self.calls: list[tuple[list, Path]] = []

    def __call__(self, argv: list, cwd: Path) -> github.CommandResult:
        self.calls.append((argv, cwd))
        return self.result


# ---------------------------------------------------------------------------
# Repository identity
# ---------------------------------------------------------------------------


class TestRepositoryIdentity:
    def test_expected_git_repository_accepted(self, git_repo) -> None:
        repo, _ = git_repo
        root = github.get_repo_root(repo)
        assert Path(root).resolve() == repo.resolve()

    def test_non_git_directory_rejected(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        with pytest.raises(github.GitError) as exc_info:
            github.get_repo_root(plain)
        assert exc_info.value.code == "command_failed"

    def test_current_branch(self, git_repo) -> None:
        repo, _ = git_repo
        assert github.get_current_branch(repo) == "main"

    def test_local_head_sha_is_full_sha(self, git_repo) -> None:
        repo, head_sha = git_repo
        assert github.get_local_head_sha(repo) == head_sha
        assert github.is_full_sha(github.get_local_head_sha(repo))

    def test_expected_remote_exists(self, git_repo) -> None:
        repo, _ = git_repo
        url = github.get_remote_url(repo, "origin")
        assert url == "https://github.com/example-owner/example-repo.git"

    def test_missing_remote_classified(self, git_repo) -> None:
        repo, _ = git_repo
        with pytest.raises(github.GitError) as exc_info:
            github.get_remote_url(repo, "nonexistent-remote")
        assert exc_info.value.code == "command_failed"

    def test_wrong_repository_rejected(self, git_repo) -> None:
        repo, _ = git_repo
        url = github.get_remote_url(repo, "origin")
        assert github.repository_identity_matches(url, "example-owner/example-repo")
        assert not github.repository_identity_matches(url, "someone-else/other-repo")

    def test_repository_identity_matches_ssh_shape(self) -> None:
        assert github.repository_identity_matches("git@github.com:owner/name.git", "owner/name")

    def test_branch_ref_derivation(self) -> None:
        assert github.derive_expected_ref("main") == "refs/heads/main"
        assert github.derive_expected_ref("feature/x") is not None  # slashes inside a branch name are legal

    def test_ref_derivation_rejects_empty_branch(self) -> None:
        with pytest.raises(github.GitError):
            github.derive_expected_ref("")


# ---------------------------------------------------------------------------
# SHA handling
# ---------------------------------------------------------------------------


class TestShaHandling:
    def test_valid_full_sha(self) -> None:
        assert github.is_full_sha("a" * 40)

    def test_malformed_sha_wrong_length(self) -> None:
        assert not github.is_full_sha("a" * 39)
        assert not github.is_full_sha("a" * 41)

    def test_malformed_sha_non_hex(self) -> None:
        assert not github.is_full_sha("g" * 40)

    def test_malformed_sha_uppercase(self) -> None:
        assert not github.is_full_sha("A" * 40)

    def test_malformed_sha_not_a_string(self) -> None:
        assert not github.is_full_sha(None)
        assert not github.is_full_sha(12345)

    def test_no_accidental_short_sha_equality(self) -> None:
        # A short SHA must never be treated as a full SHA, even if it is a genuine
        # prefix of a real one -- classify_ls_remote_result must reject it outright.
        short = "a" * 7
        result = github.classify_ls_remote_result(
            expected_sha=short, ref="refs/heads/main", remote="origin",
            command_result=github.CommandResult(exit_code=0, stdout=f"{'a' * 40}\trefs/heads/main\n", stderr=""),
        )
        assert result.status == "invalid_output"

    def test_expected_local_sha_retrieval(self, git_repo) -> None:
        repo, head_sha = git_repo
        assert github.get_local_head_sha(repo) == head_sha


# ---------------------------------------------------------------------------
# git ls-remote parsing
# ---------------------------------------------------------------------------


class TestParseLsRemoteOutput:
    def test_valid_single_line(self) -> None:
        sha = "a" * 40
        entries = github.parse_ls_remote_output(f"{sha}\trefs/heads/main\n")
        assert entries == [github.LsRemoteEntry(sha=sha, ref="refs/heads/main")]

    def test_multiple_refs(self) -> None:
        sha1, sha2 = "a" * 40, "b" * 40
        raw = f"{sha1}\tHEAD\n{sha2}\trefs/heads/main\n"
        entries = github.parse_ls_remote_output(raw)
        assert len(entries) == 2

    def test_empty_output_is_empty_list(self) -> None:
        assert github.parse_ls_remote_output("") == []
        assert github.parse_ls_remote_output("\n\n") == []

    def test_crlf_line_endings_tolerated(self) -> None:
        sha = "c" * 40
        entries = github.parse_ls_remote_output(f"{sha}\trefs/heads/main\r\n")
        assert entries == [github.LsRemoteEntry(sha=sha, ref="refs/heads/main")]

    def test_trailing_whitespace_tolerated(self) -> None:
        sha = "d" * 40
        entries = github.parse_ls_remote_output(f"  {sha}\trefs/heads/main  \n")
        assert entries == [github.LsRemoteEntry(sha=sha, ref="refs/heads/main")]

    def test_malformed_line_missing_tab(self) -> None:
        with pytest.raises(github.GitError) as exc_info:
            github.parse_ls_remote_output("not-a-valid-line-no-tab")
        assert exc_info.value.code == "invalid_output"

    def test_malformed_line_bad_sha(self) -> None:
        with pytest.raises(github.GitError) as exc_info:
            github.parse_ls_remote_output("not-a-sha\trefs/heads/main\n")
        assert exc_info.value.code == "invalid_output"

    def test_malformed_line_empty_ref(self) -> None:
        with pytest.raises(github.GitError) as exc_info:
            github.parse_ls_remote_output(f"{'a' * 40}\t\n")
        assert exc_info.value.code == "invalid_output"

    def test_too_many_fields(self) -> None:
        with pytest.raises(github.GitError):
            github.parse_ls_remote_output(f"{'a' * 40}\trefs/heads/main\textra\n")


# ---------------------------------------------------------------------------
# classify_ls_remote_result / verify_push -- includes the false-push proof
# ---------------------------------------------------------------------------


class TestClassifyLsRemoteResult:
    def test_verified_when_shas_match(self) -> None:
        sha = "a" * 40
        result = github.classify_ls_remote_result(
            expected_sha=sha, ref="refs/heads/main", remote="origin",
            command_result=github.CommandResult(exit_code=0, stdout=f"{sha}\trefs/heads/main\n", stderr=""),
        )
        assert result.status == "verified"
        assert result.observed_sha == sha

    def test_false_push_claim_is_classified_mismatch(self) -> None:
        """The assignment's explicit false-push proof: a claimed expected SHA exists,
        a (simulated) remote-verification result is obtained at the command-result
        boundary, the observed remote SHA differs from the claimed pushed SHA, and the
        harness classifies the claim as `mismatch` -- never `verified`."""
        expected_sha = "a" * 40
        observed_sha = "b" * 40
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=f"{observed_sha}\trefs/heads/main\n", stderr=""))
        result = github.classify_ls_remote_result(
            expected_sha=expected_sha, ref="refs/heads/main", remote="origin",
            command_result=runner(["git", "ls-remote", "origin", "refs/heads/main"], Path(".")),
        )
        assert result.status == "mismatch"
        assert result.expected_sha == expected_sha
        assert result.observed_sha == observed_sha
        assert result.status != "verified"

    def test_remote_ref_missing(self) -> None:
        result = github.classify_ls_remote_result(
            expected_sha="a" * 40, ref="refs/heads/nonexistent", remote="origin",
            command_result=github.CommandResult(exit_code=0, stdout="", stderr=""),
        )
        assert result.status == "remote_ref_missing"

    def test_command_failed(self) -> None:
        result = github.classify_ls_remote_result(
            expected_sha="a" * 40, ref="refs/heads/main", remote="origin",
            command_result=github.CommandResult(exit_code=128, stdout="", stderr="fatal: could not read"),
        )
        assert result.status == "command_failed"

    def test_invalid_output_malformed_stdout(self) -> None:
        result = github.classify_ls_remote_result(
            expected_sha="a" * 40, ref="refs/heads/main", remote="origin",
            command_result=github.CommandResult(exit_code=0, stdout="garbage no tab here", stderr=""),
        )
        assert result.status == "invalid_output"

    def test_duplicate_conflicting_lines_for_same_ref(self) -> None:
        raw = f"{'a' * 40}\trefs/heads/main\n{'b' * 40}\trefs/heads/main\n"
        result = github.classify_ls_remote_result(
            expected_sha="a" * 40, ref="refs/heads/main", remote="origin",
            command_result=github.CommandResult(exit_code=0, stdout=raw, stderr=""),
        )
        assert result.status == "invalid_output"

    def test_duplicate_identical_lines_for_same_ref_still_matches(self) -> None:
        sha = "a" * 40
        raw = f"{sha}\trefs/heads/main\n{sha}\trefs/heads/main\n"
        result = github.classify_ls_remote_result(
            expected_sha=sha, ref="refs/heads/main", remote="origin",
            command_result=github.CommandResult(exit_code=0, stdout=raw, stderr=""),
        )
        assert result.status == "verified"


class TestVerifyPush:
    def test_verified_end_to_end_with_fake_runner(self) -> None:
        sha = "a" * 40
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=f"{sha}\trefs/heads/main\n", stderr=""))

        def multiplexed(argv, cwd):
            if argv[:2] == ["git", "remote"]:
                return github.CommandResult(exit_code=0, stdout="https://github.com/example-owner/example-repo.git\n", stderr="")
            return runner(argv, cwd)

        result = github.verify_push(
            repo_path=Path("."), remote="origin", branch="main", expected_sha=sha, runner=multiplexed,
        )
        assert result.status == "verified"

    def test_wrong_repository_short_circuits_before_ls_remote(self) -> None:
        calls: list[list] = []

        def runner(argv, cwd):
            calls.append(argv)
            if argv[:2] == ["git", "remote"]:
                return github.CommandResult(exit_code=0, stdout="https://github.com/someone-else/other-repo.git\n", stderr="")
            return github.CommandResult(exit_code=0, stdout=f"{'a' * 40}\trefs/heads/main\n", stderr="")

        result = github.verify_push(
            repo_path=Path("."), remote="origin", branch="main", expected_sha="a" * 40, runner=runner,
            expected_repo="example-owner/example-repo",
        )
        assert result.status == "wrong_repository"
        assert not any(argv[:2] == ["git", "ls-remote"] for argv in calls)

    def test_missing_remote_is_command_failed(self) -> None:
        def runner(argv, cwd):
            return github.CommandResult(exit_code=2, stdout="", stderr="fatal: No such remote 'origin'")

        result = github.verify_push(
            repo_path=Path("."), remote="origin", branch="main", expected_sha="a" * 40, runner=runner,
        )
        assert result.status == "command_failed"

    def test_default_runner_never_exposed_as_a_simulation_backdoor(self) -> None:
        # verify_push has no parameter that lets a caller pass a "pretend result" other
        # than the fully explicit `runner=` seam used only by tests -- production
        # call sites (live_cli.py) never pass one, so this module can never fabricate a
        # push result inside a real /work run.
        import inspect

        sig = inspect.signature(github.verify_push)
        assert set(sig.parameters) == {"repo_path", "remote", "branch", "expected_sha", "runner", "expected_repo"}


# ---------------------------------------------------------------------------
# gh CLI metadata (Part 9) -- exercised with a fake runner; real `gh` usage is
# covered separately by the live demonstration, not asserted on here since CI/test
# environments cannot assume `gh` is installed or authenticated.
# ---------------------------------------------------------------------------


class TestGhMetadata:
    def test_auth_status_authenticated(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr="Logged in to github.com"))
        result = github.gh_auth_status(runner=runner, repo_path=Path("."))
        assert result["authenticated"] is True

    def test_auth_status_not_authenticated(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=1, stdout="", stderr="not logged in"))
        result = github.gh_auth_status(runner=runner, repo_path=Path("."))
        assert result["authenticated"] is False

    def test_repo_metadata_parsed(self) -> None:
        runner = FakeRunner(github.CommandResult(
            exit_code=0,
            stdout='{"nameWithOwner": "example-owner/example-repo", "url": "https://github.com/example-owner/example-repo", "defaultBranchRef": {"name": "main"}}\n',
            stderr="",
        ))
        result = github.gh_repo_metadata(runner=runner, repo_path=Path("."))
        assert result["status"] == "ok"
        assert result["metadata"]["nameWithOwner"] == "example-owner/example-repo"

    def test_repo_metadata_command_failed(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=1, stdout="", stderr="not a git repository"))
        result = github.gh_repo_metadata(runner=runner, repo_path=Path("."))
        assert result["status"] == "command_failed"

    def test_repo_metadata_invalid_json(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="not json", stderr=""))
        result = github.gh_repo_metadata(runner=runner, repo_path=Path("."))
        assert result["status"] == "invalid_output"


# ---------------------------------------------------------------------------
# resolve_repo_path -- reuse of paths.py's real path-safety layer
# ---------------------------------------------------------------------------


class TestResolveRepoPath:
    def test_dot_resolves_to_repo_root(self) -> None:
        assert github.resolve_repo_path(".", repo_root=paths.REPO_ROOT) == paths.REPO_ROOT.resolve()

    def test_protected_path_rejected(self) -> None:
        with pytest.raises(paths.PathSafetyError):
            github.resolve_repo_path(".claude/settings.json", repo_root=paths.REPO_ROOT)

    def test_traversal_rejected(self) -> None:
        with pytest.raises(paths.PathSafetyError):
            github.resolve_repo_path("../outside", repo_root=paths.REPO_ROOT)
