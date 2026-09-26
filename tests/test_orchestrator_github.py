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

import base64
import json
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
# get_branch_sha / repo-slug validation (shared by the github/ skill-pack ops)
# ---------------------------------------------------------------------------


class TestGetBranchSha:
    def test_matches_local_head_sha_on_current_branch(self, git_repo) -> None:
        repo, head_sha = git_repo
        assert github.get_branch_sha(repo, "main") == head_sha

    def test_unknown_branch_is_command_failed(self, git_repo) -> None:
        repo, _ = git_repo
        with pytest.raises(github.GitError) as exc_info:
            github.get_branch_sha(repo, "no-such-branch")
        assert exc_info.value.code == "command_failed"

    def test_tag_or_sha_never_stands_in_for_a_local_branch(self, git_repo) -> None:
        repo, head_sha = git_repo
        _git("tag", "v1", cwd=repo)
        for not_a_branch in ("v1", head_sha):
            with pytest.raises(github.GitError) as exc_info:
                github.get_branch_sha(repo, not_a_branch)
            assert exc_info.value.code == "command_failed"

    def test_flag_shaped_branch_rejected_without_running_git(self, git_repo) -> None:
        repo, _ = git_repo
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        with pytest.raises(github.GitError) as exc_info:
            github.get_branch_sha(repo, "--all", runner=runner)
        assert exc_info.value.code == "invalid_output"
        assert runner.calls == []


class TestRepoSlugAndRefValidation:
    @pytest.mark.parametrize("slug", ["owner/name", "a-b/c.d_e", "cmlong13/AgenticHarness"])
    def test_valid_slugs(self, slug) -> None:
        assert github.is_repo_slug(slug)

    @pytest.mark.parametrize("slug", ["", "noslash", "a/b/c", "-owner/name", "owner/", "/name", "owner/..", "o w/n", None, 3])
    def test_invalid_slugs(self, slug) -> None:
        assert not github.is_repo_slug(slug)


# ---------------------------------------------------------------------------
# read-file (ASSIGNMENT.md github/ skill pack capability 1 of 5, read-only)
# ---------------------------------------------------------------------------


class TestReadFile:
    def test_reads_file_content_at_ref(self, git_repo) -> None:
        repo, head_sha = git_repo
        result = github.read_file(repo, head_sha, "file.txt")
        assert result["status"] == "found"
        assert result["source"] == "local_git"
        assert result["content"] == "hello\n"
        assert result["size"] == 6

    def test_reads_file_content_at_branch_name(self, git_repo) -> None:
        repo, _ = git_repo
        result = github.read_file(repo, "main", "file.txt")
        assert result["status"] == "found"

    def test_reads_historical_content_not_the_working_tree(self, git_repo) -> None:
        repo, head_sha = git_repo
        (repo / "file.txt").write_text("uncommitted edit\n", encoding="utf-8")
        result = github.read_file(repo, head_sha, "file.txt")
        assert result["content"] == "hello\n"

    def test_missing_path_at_valid_ref_is_not_found(self, git_repo) -> None:
        repo, _ = git_repo
        result = github.read_file(repo, "main", "no-such-file.txt")
        assert result["status"] == "not_found"

    def test_unknown_ref_is_invalid_ref(self, git_repo) -> None:
        repo, _ = git_repo
        result = github.read_file(repo, "no-such-ref", "file.txt")
        assert result["status"] == "invalid_ref"

    def test_directory_is_not_a_file(self, git_repo) -> None:
        repo, _ = git_repo
        (repo / "pkg").mkdir()
        (repo / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git("add", "pkg/a.py", cwd=repo)
        _git("commit", "-m", "add pkg", cwd=repo)
        result = github.read_file(repo, "main", "pkg")
        assert result["status"] == "not_a_file"
        assert "content" not in result

    def test_content_over_max_bytes_is_never_returned(self, git_repo) -> None:
        repo, _ = git_repo
        result = github.read_file(repo, "main", "file.txt", max_bytes=3)
        assert result["status"] == "too_large"
        assert "content" not in result

    @pytest.mark.parametrize("max_bytes", [0, -1, github.READ_FILE_MAX_BYTES + 1, True, "10"])
    def test_invalid_max_bytes_rejected_without_running_git(self, git_repo, max_bytes) -> None:
        repo, _ = git_repo
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        result = github.read_file(repo, "main", "file.txt", max_bytes=max_bytes, runner=runner)
        assert result["status"] == "invalid_input"
        assert runner.calls == []

    @pytest.mark.parametrize("ref", ["", "--output=/tmp/x", "main:other", "a..b", "main branch", "ref\n"])
    def test_malformed_ref_rejected_without_running_git(self, git_repo, ref) -> None:
        repo, _ = git_repo
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        result = github.read_file(repo, ref, "file.txt", runner=runner)
        assert result["status"] == "invalid_ref"
        assert runner.calls == []

    @pytest.mark.parametrize("file_path", ["", "--evil-flag", "/etc/passwd", "../outside.txt", "a/../../b", "dir\\file"])
    def test_malformed_path_rejected_without_running_git(self, git_repo, file_path) -> None:
        repo, _ = git_repo
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        result = github.read_file(repo, "main", file_path, runner=runner)
        assert result["status"] == "invalid_path"
        assert runner.calls == []


class TestReadFileViaGithub:
    """The `repo_slug` route: a fixed, GET-only `gh api` contents call -- never run
    against the network here; every response is an injected fixture."""

    @staticmethod
    def _contents(text: str, **overrides) -> str:
        doc = {
            "type": "file", "encoding": "base64", "size": len(text.encode("utf-8")), "sha": "b" * 40,
            "path": "src/a b.py", "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
        doc.update(overrides)
        return json.dumps(doc)

    def test_command_is_a_fixed_get_only_contents_call(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=self._contents("x = 1\n"), stderr=""))
        github.read_file(Path("."), "feature/x", "src/a b.py", repo_slug="owner/name", runner=runner)
        (argv, _), = runner.calls
        assert argv == ["gh", "api", "--method", "GET", "repos/owner/name/contents/src/a%20b.py?ref=feature%2Fx"]

    def test_found_decodes_content(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=self._contents("x = 1\n"), stderr=""))
        result = github.read_file(Path("."), "main", "src/a b.py", repo_slug="owner/name", runner=runner)
        assert result["status"] == "found"
        assert result["source"] == "github_api"
        assert result["content"] == "x = 1\n"
        assert result["blob_sha"] == "b" * 40

    def test_http_404_is_not_found(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=1, stdout="", stderr="gh: Not Found (HTTP 404)"))
        result = github.read_file(Path("."), "main", "nope.py", repo_slug="owner/name", runner=runner)
        assert result["status"] == "not_found"

    def test_unknown_ref_is_invalid_ref(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=1, stdout="", stderr="gh: No commit found for the ref nope (HTTP 404)"))
        result = github.read_file(Path("."), "nope", "a.py", repo_slug="owner/name", runner=runner)
        assert result["status"] == "invalid_ref"

    def test_auth_failure_is_command_failed(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=4, stdout="", stderr="gh: To get started with GitHub CLI, please run: gh auth login"))
        result = github.read_file(Path("."), "main", "a.py", repo_slug="owner/name", runner=runner)
        assert result["status"] == "command_failed"

    def test_directory_listing_is_not_a_file(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout='[{"type": "file", "path": "a.py"}]', stderr=""))
        result = github.read_file(Path("."), "main", "src", repo_slug="owner/name", runner=runner)
        assert result["status"] == "not_a_file"

    def test_file_over_limit_is_too_large(self) -> None:
        stdout = self._contents("", size=5_000_000, encoding="none")
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=stdout, stderr=""))
        result = github.read_file(Path("."), "main", "big.bin", repo_slug="owner/name", runner=runner)
        assert result["status"] == "too_large"
        assert "content" not in result

    def test_non_utf8_content_is_not_text(self) -> None:
        stdout = self._contents("", size=2, content=base64.b64encode(b"\xff\xfe").decode("ascii"))
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=stdout, stderr=""))
        result = github.read_file(Path("."), "main", "a.bin", repo_slug="owner/name", runner=runner)
        assert result["status"] == "not_text"

    def test_unexpected_json_is_invalid_output(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout='{"message": "weird"}', stderr=""))
        result = github.read_file(Path("."), "main", "a.py", repo_slug="owner/name", runner=runner)
        assert result["status"] == "invalid_output"

    def test_malformed_repo_slug_rejected_without_running_gh(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="{}", stderr=""))
        result = github.read_file(Path("."), "main", "a.py", repo_slug="owner/name/extra", runner=runner)
        assert result["status"] == "invalid_input"
        assert runner.calls == []


# ---------------------------------------------------------------------------
# search-code (ASSIGNMENT.md github/ skill pack capability 2 of 5, read-only)
# ---------------------------------------------------------------------------


class TestSearchCode:
    def test_command_construction_scopes_to_requested_repos(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        github.search_code(Path("."), "def foo", repo_slugs=["owner/name", "owner/other"], limit=10, runner=runner)
        (argv, _), = runner.calls
        assert argv == [
            "gh", "search", "code", "--limit", "10", "--json", "path,repository,sha,url",
            "--repo=owner/name", "--repo=owner/other", "--", "def foo",
        ]

    def test_negated_qualifier_is_passed_after_double_dash_not_as_a_flag(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        github.search_code(Path("."), "-language:js", repo_slugs=["owner/name"], runner=runner)
        (argv, _), = runner.calls
        assert argv[-2:] == ["--", "-language:js"]

    def test_matches_returned_as_found(self) -> None:
        runner = FakeRunner(github.CommandResult(
            exit_code=0, stdout='[{"path": "a.py", "url": "https://example.invalid/a.py"}]', stderr="",
        ))
        result = github.search_code(Path("."), "def foo", repo_slugs=["owner/name"], runner=runner)
        assert result["status"] == "found"
        assert result["results"][0]["path"] == "a.py"
        assert result["repo_slugs"] == ["owner/name"]

    def test_results_are_bounded_by_limit(self) -> None:
        stdout = json.dumps([{"path": f"f{i}.py"} for i in range(5)])
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=stdout, stderr=""))
        result = github.search_code(Path("."), "x", repo_slugs=["owner/name"], limit=2, runner=runner)
        assert len(result["results"]) == 2

    def test_empty_results_is_no_matches_not_an_error(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        result = github.search_code(Path("."), "def foo", repo_slugs=["owner/name"], runner=runner)
        assert result["status"] == "no_matches"

    @pytest.mark.parametrize("repo_slugs", [None, [], "owner/name"])
    def test_unscoped_search_refused_without_running_gh(self, repo_slugs) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        result = github.search_code(Path("."), "def foo", repo_slugs=repo_slugs, runner=runner)
        assert result["status"] == "invalid_query"
        assert runner.calls == []

    def test_too_many_repos_refused(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        slugs = [f"owner/r{i}" for i in range(github.SEARCH_CODE_MAX_REPOS + 1)]
        result = github.search_code(Path("."), "x", repo_slugs=slugs, runner=runner)
        assert result["status"] == "invalid_query"
        assert runner.calls == []

    @pytest.mark.parametrize("query", ["", "   ", "a\nb", "x" * (github.SEARCH_CODE_MAX_QUERY_LEN + 1)])
    def test_malformed_query_rejected_without_running_gh(self, query) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        result = github.search_code(Path("."), query, repo_slugs=["owner/name"], runner=runner)
        assert result["status"] == "invalid_query"
        assert runner.calls == []

    @pytest.mark.parametrize("limit", [0, 101, 1000, True, "10"])
    def test_limit_out_of_range_rejected(self, limit) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        result = github.search_code(Path("."), "def foo", repo_slugs=["owner/name"], limit=limit, runner=runner)
        assert result["status"] == "invalid_query"
        assert runner.calls == []

    @pytest.mark.parametrize("slug", ["not-a-slug", "a/b/c", "--repo=x/y"])
    def test_malformed_repo_slug_rejected(self, slug) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="[]", stderr=""))
        result = github.search_code(Path("."), "def foo", repo_slugs=[slug], runner=runner)
        assert result["status"] == "invalid_query"
        assert runner.calls == []

    def test_gh_command_failure_propagates(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=1, stdout="", stderr="gh: command not found"))
        result = github.search_code(Path("."), "def foo", repo_slugs=["owner/name"], runner=runner)
        assert result["status"] == "command_failed"
        assert result["reason"] == "gh: command not found"

    @pytest.mark.parametrize("stdout", ["not json", '{"path": "a.py"}'])
    def test_unusable_output_is_invalid_output(self, stdout) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=stdout, stderr=""))
        result = github.search_code(Path("."), "def foo", repo_slugs=["owner/name"], runner=runner)
        assert result["status"] == "invalid_output"


# ---------------------------------------------------------------------------
# commit-history (ASSIGNMENT.md github/ skill pack capability 3 of 5, read-only)
# ---------------------------------------------------------------------------


class TestCommitHistory:
    def test_real_repo_returns_the_one_seeded_commit(self, git_repo) -> None:
        repo, head_sha = git_repo
        result = github.commit_history(repo)
        assert result["status"] == "ok"
        assert len(result["commits"]) == 1
        assert result["commits"][0]["sha"] == head_sha
        assert result["commits"][0]["subject"] == "initial commit"
        assert result["truncated"] is False

    def test_bounded_by_max_count_and_reports_truncation(self, git_repo) -> None:
        repo, _ = git_repo
        for i in range(3):
            (repo / "file.txt").write_text(f"v{i}\n", encoding="utf-8")
            _git("commit", "-am", f"change {i}", cwd=repo)
        result = github.commit_history(repo, max_count=2)
        assert [c["subject"] for c in result["commits"]] == ["change 2", "change 1"]
        assert result["truncated"] is True

    def test_history_at_an_explicit_ref(self, git_repo) -> None:
        repo, head_sha = git_repo
        (repo / "file.txt").write_text("later\n", encoding="utf-8")
        _git("commit", "-am", "later commit", cwd=repo)
        result = github.commit_history(repo, ref=head_sha)
        assert [c["sha"] for c in result["commits"]] == [head_sha]

    def test_path_scoped_history(self, git_repo) -> None:
        repo, _ = git_repo
        result = github.commit_history(repo, path="file.txt")
        assert result["status"] == "ok"
        assert len(result["commits"]) == 1

    def test_path_with_no_history_is_no_commits(self, git_repo) -> None:
        repo, _ = git_repo
        result = github.commit_history(repo, path="never-existed.txt")
        assert result["status"] == "no_commits"

    def test_unknown_ref_is_invalid_ref(self, git_repo) -> None:
        repo, _ = git_repo
        result = github.commit_history(repo, ref="no-such-ref")
        assert result["status"] == "invalid_ref"

    def test_ref_and_path_always_separated_by_double_dash(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        github.commit_history(Path("."), max_count=5, ref="main", path="src/a.py", runner=runner)
        (argv, _), = runner.calls
        assert argv[:3] == ["git", "log", "-n5"]
        assert argv[-3:] == ["main", "--", "src/a.py"]

    @pytest.mark.parametrize("max_count", [0, 201, True, "5"])
    def test_max_count_out_of_range_rejected_without_running_git(self, max_count) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        result = github.commit_history(Path("."), max_count=max_count, runner=runner)
        assert result["status"] == "invalid_input"
        assert runner.calls == []

    @pytest.mark.parametrize("ref", ["--all", "a..b", "x y"])
    def test_malformed_ref_rejected_without_running_git(self, ref) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        result = github.commit_history(Path("."), ref=ref, runner=runner)
        assert result["status"] == "invalid_ref"
        assert runner.calls == []

    @pytest.mark.parametrize("path", ["--evil-flag", "../outside", ""])
    def test_malformed_path_rejected_without_running_git(self, path) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="", stderr=""))
        result = github.commit_history(Path("."), path=path, runner=runner)
        assert result["status"] == "invalid_input"
        assert runner.calls == []

    def test_non_git_directory_is_command_failed(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        result = github.commit_history(plain)
        assert result["status"] == "command_failed"

    @pytest.mark.parametrize("stdout", ["not-enough-fields\n", "short\x1fa\x1fb\x1fc\x1fd\n"])
    def test_malformed_log_line_is_invalid_output(self, stdout) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=stdout, stderr=""))
        result = github.commit_history(Path("."), runner=runner)
        assert result["status"] == "invalid_output"


# ---------------------------------------------------------------------------
# pr-review (ASSIGNMENT.md github/ skill pack capability 5 of 5, read-only)
# ---------------------------------------------------------------------------


_REVIEWED_PR = {
    "number": 7, "url": "https://example.invalid/pull/7", "state": "OPEN", "isDraft": False,
    "baseRefName": "main", "headRefName": "feature/x", "headRefOid": "c" * 40,
    "reviewDecision": "CHANGES_REQUESTED", "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED",
    "reviews": [
        {"author": {"login": "alice"}, "state": "APPROVED"},
        {"author": {"login": "bob"}, "state": "CHANGES_REQUESTED"},
        {"author": {"login": "carol"}, "state": "COMMENTED"},
    ],
    "statusCheckRollup": [
        {"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "build", "status": "IN_PROGRESS", "conclusion": ""},
        {"context": "ci/legacy", "state": "PENDING"},
        {"context": "ci/other", "state": "ERROR"},
    ],
    "files": [{"path": "src/a.py", "additions": 3, "deletions": 1}],
}


class TestPrReview:
    def test_found_pr_returns_parsed_state_and_review_summary(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=json.dumps(_REVIEWED_PR), stderr=""))
        result = github.pr_review(Path("."), "7", runner=runner)
        assert result["status"] == "found"
        assert result["pull_request"]["number"] == 7
        assert result["summary"] == {
            "state": "OPEN", "is_draft": False, "review_decision": "CHANGES_REQUESTED",
            "approved_by": ["alice"], "changes_requested_by": ["bob"],
            "checks_passing": 1, "checks_failing": ["ci/other", "lint"], "checks_pending": ["build", "ci/legacy"],
            "mergeable": "MERGEABLE", "merge_state_status": "BLOCKED", "changed_files": ["src/a.py"],
        }

    def test_command_is_read_only_gh_pr_view(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout='{"number": 1}', stderr=""))
        github.pr_review(Path("."), "feature/x", repo_slug="owner/name", runner=runner)
        (argv, _), = runner.calls
        assert argv == ["gh", "pr", "view", "feature/x", "--json", github.PR_REVIEW_FIELDS, "--repo=owner/name"]
        assert "headRefOid" in github.PR_REVIEW_FIELDS

    @pytest.mark.parametrize("stderr", [
        'no pull requests found for branch "x"',
        "GraphQL: Could not resolve to a PullRequest with the number of 999. (repository.pullRequest)",
    ])
    def test_missing_pr_is_not_found(self, stderr) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=1, stdout="", stderr=stderr))
        result = github.pr_review(Path("."), "999", runner=runner)
        assert result["status"] == "not_found"

    def test_other_command_failure_is_command_failed(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=1, stdout="", stderr="gh: not authenticated"))
        result = github.pr_review(Path("."), "7", runner=runner)
        assert result["status"] == "command_failed"

    @pytest.mark.parametrize("pr_ref", ["", "--web", "-1", "a b", "refs/heads/x", None])
    def test_malformed_pr_ref_rejected_without_running_gh(self, pr_ref) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="{}", stderr=""))
        result = github.pr_review(Path("."), pr_ref, runner=runner)
        assert result["status"] == "invalid_input"
        assert runner.calls == []

    def test_malformed_repo_slug_rejected_without_running_gh(self) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout="{}", stderr=""))
        result = github.pr_review(Path("."), "7", repo_slug="--web", runner=runner)
        assert result["status"] == "invalid_input"
        assert runner.calls == []

    @pytest.mark.parametrize("stdout", ["not json", "[1, 2]"])
    def test_unusable_output_is_invalid_output(self, stdout) -> None:
        runner = FakeRunner(github.CommandResult(exit_code=0, stdout=stdout, stderr=""))
        result = github.pr_review(Path("."), "7", runner=runner)
        assert result["status"] == "invalid_output"


# ---------------------------------------------------------------------------
# pr-create (ASSIGNMENT.md github/ skill pack capability 4 of 5, the one
# state-changing capability -- write-vs-read safety boundary tests live here)
# ---------------------------------------------------------------------------


class ScriptedGitHub:
    """A scripted runner for pr_create: `git rev-parse` runs for real against the
    disposable `git_repo` (so the head branch's SHA is genuinely derived), while the
    remote URL, per-ref `git ls-remote` answers, `gh pr create` and `gh pr view` are
    injected fixtures. Any other command -- in particular any `git commit`/`git push`
    -- fails the test outright. Records every argv it saw."""

    REMOTE_URL = "https://github.com/example-owner/example-repo.git\n"

    def __init__(self, *, remote_refs: dict, create=None, view=None):
        self.remote_refs = remote_refs
        self.create = create
        self.view = view
        self.calls: list[list] = []

    def __call__(self, argv: list, cwd: Path) -> github.CommandResult:
        self.calls.append(argv)
        if argv[:2] == ["git", "rev-parse"]:
            return github.DEFAULT_RUNNER(argv, cwd)
        if argv[:3] == ["git", "remote", "get-url"]:
            return github.CommandResult(exit_code=0, stdout=self.REMOTE_URL, stderr="")
        if argv[:2] == ["git", "ls-remote"]:
            ref = argv[3]
            sha = self.remote_refs.get(ref)
            return github.CommandResult(exit_code=0, stdout=f"{sha}\t{ref}\n" if sha else "", stderr="")
        if argv[:3] == ["gh", "pr", "create"] and self.create is not None:
            return self.create
        if argv[:3] == ["gh", "pr", "view"] and self.view is not None:
            return self.view
        raise AssertionError(f"unexpected command: {argv}")

    def ran(self, *prefix: str) -> bool:
        return any(argv[: len(prefix)] == list(prefix) for argv in self.calls)


def _created_pr(head_sha: str, **overrides) -> github.CommandResult:
    pr = {
        "number": 9, "url": "https://github.com/example-owner/example-repo/pull/9", "state": "OPEN",
        "baseRefName": "release", "headRefName": "main", "headRefOid": head_sha,
    }
    pr.update(overrides)
    return github.CommandResult(exit_code=0, stdout=json.dumps(pr), stderr="")


_CREATE_OK = github.CommandResult(exit_code=0, stdout="https://github.com/example-owner/example-repo/pull/9\n", stderr="")

_AUTHORIZED = {
    "authorized": True, "authorization_source": "user instruction: 'open a PR for main into release'",
    "expected_repo": "example-owner/example-repo",
}


def _pr_create(repo, runner, **overrides):
    kwargs = {"base": "release", "head": "main", "title": "Add x", "body": "body", **_AUTHORIZED, **overrides}
    return github.pr_create(repo, runner=runner, **kwargs)


class TestPrCreate:
    def _verified(self, head_sha, **kwargs) -> ScriptedGitHub:
        return ScriptedGitHub(
            remote_refs={"refs/heads/main": head_sha, "refs/heads/release": "e" * 40}, **kwargs,
        )

    def test_created_after_independent_reverification(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha, create=_CREATE_OK, view=_created_pr(head_sha))
        result = _pr_create(repo, runner)
        assert result["status"] == "created"
        assert result["number"] == 9
        assert result["head_sha"] == head_sha
        assert result["push_verification"]["status"] == "verified"
        assert result["authorization_source"] == _AUTHORIZED["authorization_source"]

    def test_never_commits_or_pushes_and_passes_values_in_flag_equals_form(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha, create=_CREATE_OK, view=_created_pr(head_sha))
        _pr_create(repo, runner, title="-- looks like a flag", body="--web")
        assert not runner.ran("git", "push") and not runner.ran("git", "commit")
        create_argv, = [a for a in runner.calls if a[:3] == ["gh", "pr", "create"]]
        assert create_argv == [
            "gh", "pr", "create", "--base=release", "--head=main", "--title=-- looks like a flag", "--body=--web",
        ]

    @pytest.mark.parametrize("auth", [
        {"authorized": False},
        {"authorized": "yes"},
        {"authorized": 1},
        {"authorization_source": None},
        {"authorization_source": "   "},
    ])
    def test_not_authorized_runs_nothing(self, git_repo, auth) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha, create=_CREATE_OK, view=_created_pr(head_sha))
        result = _pr_create(repo, runner, **auth)
        assert result["status"] == "not_authorized"
        assert runner.calls == []

    def test_default_call_is_not_authorized(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha)
        result = github.pr_create(
            repo, base="release", head="main", title="x", body="", expected_repo="example-owner/example-repo",
            runner=runner,
        )
        assert result["status"] == "not_authorized"
        assert runner.calls == []

    @pytest.mark.parametrize("overrides", [
        {"base": "main"},
        {"head": "--all"},
        {"head": "HEAD"},
        {"head": "main~1"},
        {"base": "refs/heads/release"},
        {"title": ""},
        {"title": "two\nlines"},
        {"title": "x" * (github.PR_TITLE_MAX_LEN + 1)},
        {"body": None},
        {"body": "x" * (github.PR_BODY_MAX_LEN + 1)},
        {"expected_repo": None},
        {"expected_repo": "not-a-slug"},
        {"repo_slug": "--web"},
        {"remote": "--upload-pack=evil"},
    ])
    def test_malformed_input_runs_nothing(self, git_repo, overrides) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha)
        result = _pr_create(repo, runner, **overrides)
        assert result["status"] == "invalid_input"
        assert runner.calls == []

    def test_unknown_head_branch_is_invalid_head_branch(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha)
        result = _pr_create(repo, runner, head="no-such-branch")
        assert result["status"] == "invalid_head_branch"
        assert not runner.ran("gh")

    def test_refuses_to_call_gh_pr_create_when_head_push_is_unverified(self, git_repo) -> None:
        """The core safety guarantee: pr_create must never call `gh pr create` for a
        branch whose local tip is not an independently verified push."""
        repo, _ = git_repo
        runner = ScriptedGitHub(remote_refs={"refs/heads/main": "f" * 40, "refs/heads/release": "e" * 40})
        result = _pr_create(repo, runner)
        assert result["status"] == "push_not_verified"
        assert result["verification"]["status"] == "mismatch"
        assert not runner.ran("gh")

    def test_head_never_pushed_is_push_not_verified(self, git_repo) -> None:
        repo, _ = git_repo
        runner = ScriptedGitHub(remote_refs={"refs/heads/release": "e" * 40})
        result = _pr_create(repo, runner)
        assert result["status"] == "push_not_verified"
        assert result["verification"]["status"] == "remote_ref_missing"
        assert not runner.ran("gh")

    def test_wrong_repository_is_push_not_verified(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha)
        result = _pr_create(repo, runner, expected_repo="someone-else/other-repo")
        assert result["status"] == "push_not_verified"
        assert result["verification"]["status"] == "wrong_repository"
        assert not runner.ran("gh")

    def test_missing_base_branch_is_base_not_found(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = ScriptedGitHub(remote_refs={"refs/heads/main": head_sha})
        result = _pr_create(repo, runner)
        assert result["status"] == "base_not_found"
        assert not runner.ran("gh")

    def test_gh_pr_create_success_never_trusted_without_reverification(self, git_repo) -> None:
        """Even when `gh pr create` itself reports exit 0, a re-fetch that cannot
        confirm the PR must never be reported as `created` -- mirrors verify_push's
        own "a push exit code is not sufficient evidence" rule."""
        repo, head_sha = git_repo
        runner = self._verified(
            head_sha, create=_CREATE_OK,
            view=github.CommandResult(exit_code=1, stdout="", stderr="no pull requests found"),
        )
        result = _pr_create(repo, runner)
        assert result["status"] == "created_unverified"
        assert "url" not in result and "number" not in result

    @pytest.mark.parametrize("overrides", [
        {"headRefOid": "d" * 40},
        {"baseRefName": "main"},
        {"headRefName": "other"},
        {"state": "CLOSED"},
        {"url": "https://github.com/example-owner/example-repo/pull/10"},
    ])
    def test_refetched_pr_mismatch_is_created_unverified(self, git_repo, overrides) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha, create=_CREATE_OK, view=_created_pr(head_sha, **overrides))
        result = _pr_create(repo, runner)
        assert result["status"] == "created_unverified"
        assert "does not match" in result["reason"]

    def test_existing_pr_is_already_exists(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha, create=github.CommandResult(
            exit_code=1, stdout="", stderr='a pull request for branch "main" into branch "release" already exists',
        ))
        result = _pr_create(repo, runner)
        assert result["status"] == "already_exists"

    def test_gh_pr_create_command_failure_propagates(self, git_repo) -> None:
        repo, head_sha = git_repo
        runner = self._verified(head_sha, create=github.CommandResult(exit_code=1, stdout="", stderr="gh: not authenticated"))
        result = _pr_create(repo, runner)
        assert result["status"] == "command_failed"
        assert result["reason"] == "gh: not authenticated"
        assert not runner.ran("gh", "pr", "view")


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
