---
name: github
description: Git/GitHub procedure for repository identity, working-tree inspection,
  commit creation, push, and independent remote push verification, plus the five
  single-purpose GitHub skill-pack procedures read-file, search-code, commit-history,
  pr-review (read-only) and pr-create (explicitly authorized only). Invoked by the
  orchestrator (/work) before any Git or GitHub action it performs; never grants an
  independent tool allowlist of its own.
---

# Purpose
Hold every Git/GitHub action the orchestrator performs to one standard: inspect first,
act narrowly, then independently prove what actually happened on the remote before
reporting anything as delivered. This skill does not execute commands itself -- it is
the procedure the orchestrator (the only session that ever touches Git in this harness;
see `work/SKILL.md` standing rule 3) follows, using its own `Bash` tool for inspection/
commit/push and `harness/orchestrator/live_cli.py`'s `git_repo_identity`/
`retain_commit_evidence`/`retain_push_attempt`/`verify_push`/`gh_repo_metadata`
operations (`harness/orchestrator/github.py`) for every deterministic identity check and
every piece of retained evidence. Sections 1-10 are the commit/push/verify delivery
procedure; section 11 is the GitHub skill pack (`read-file`, `search-code`,
`commit-history`, `pr-review`, `pr-create`).

# The cardinal rule
**A local commit is not a verified push.** Corollaries, all non-negotiable:
- `git push`'s own exit code, or any success-looking line it prints, is not
  independently sufficient evidence that the commit reached the remote.
- An agent -- or the orchestrator's own prior turn -- saying "pushed" is not evidence.
- A GitHub UI assumption ("it's probably there") is not evidence.
- The only thing that proves a commit reached a remote branch is an independently run
  `git ls-remote <remote> <ref>` whose observed SHA, compared as a full 40-character hex
  string (never a prefix, never a truthy check), equals the expected local SHA. See
  `harness/orchestrator/github.py::classify_ls_remote_result`.
- Remote verification is mandatory before reporting a push as successful, every time,
  with no exception for "it worked last time" or "the network looked fine."

# 1. Repository identity
Before touching anything, know what you are acting on. Call `live_cli.py`'s
`git_repo_identity` operation (`target_repo_path`, default `"."`; optional `remote`,
default `"origin"`; optional `expected_repo`, an `"owner/name"` slug) to get the real
Git-reported repo root, current branch, local HEAD SHA, and configured remote URL --
never assert any of these from memory or from what a prior turn claimed. A non-Git
directory, a missing remote, or (when `expected_repo` is supplied) the wrong repository
are each reported as their own distinct, named outcome (`command_failed` /
`wrong_repository`), never a silent pass-through.

# 2. Working-tree inspection
Run `git status --short` (and, scoped to `target_repo_path`, `git status --short --
<target_repo_path>`) yourself before staging anything. Confirm you know exactly which
paths are new, modified, or deleted -- never stage or commit a path you have not
personally accounted for.

# 3. Branch inspection
Confirm the current branch (`git_repo_identity`'s `current_branch`, or
`git branch --show-current`) matches the branch you actually intend to act on. Never
assume; a stale assumption about the current branch is exactly the kind of unverified
claim this skill exists to prevent.

# 4. Diff review
Run `git diff` (unstaged) and, once staged, `git diff --staged` (or `--cached`) and read
the actual content -- not just the file list. Confirm no secret-shaped content
(API keys, tokens, credentials) and no diagnostic/scratch garbage is included. Cross-
check every touched path against `scope.json`'s `in_scope` list and the Protected Path
list (`harness/orchestrator/paths.py::PROTECTED_PATH_PATTERNS`) before proceeding --
never trust a subagent's own self-check on this alone.

# 5. Staging
Stage explicit paths (`git add <path> [<path> ...]`) -- never a bare `git add .`/`git add
-A` for a commit this skill is governing, so the set of staged paths is always exactly
the set you just reviewed in step 4, no more.

# 6. Commit creation
Commit with an explicit, descriptive message (`git commit -m "..."`). Immediately after,
call `live_cli.py`'s `retain_commit_evidence` operation (`run_id`, `task_id`,
`target_repo_path`; optional `expected_branch` to cross-check) -- it independently
re-derives the current branch and HEAD SHA via real `git` calls (never trusts a
caller-supplied SHA) and retains `runs/<run_id>/git/commit-evidence.json`. This is what
answers "was a commit actually created locally," as real evidence rather than an
assertion.

# 7. Push
Push normally (`git push <remote> <branch>`) -- never `--force`/`--force-with-lease`,
never to a branch other than the one you just committed on. Immediately after the push
command returns (regardless of what its own output looked like), call `retain_push_attempt`
(`run_id`, `task_id`, `target_repo_path`, `branch`) -- it independently re-derives the
current HEAD SHA and remote URL and retains `runs/<run_id>/git/push-attempt.json`. This
records only that a push was *attempted*; it proves nothing about delivery -- see step 8.

# 8. Independent remote verification (mandatory, every time)
Call `verify_push` (`run_id`, `task_id`, `target_repo_path`, `remote`, `branch`,
`expected_sha` -- read the SHA back from the `commit-evidence.json`/`push-attempt.json`
you just retained, never re-typed from memory). This runs a real
`git ls-remote <remote> refs/heads/<branch>` and classifies the result as exactly one of:

| status | meaning |
|---|---|
| `verified` | observed remote SHA equals the expected local SHA -- the only status that may ever be reported as a successful push |
| `mismatch` | the remote ref exists but points at a different SHA |
| `remote_ref_missing` | the branch does not exist on the remote at all |
| `command_failed` | `git ls-remote` (or the remote-URL lookup before it) itself failed |
| `invalid_output` | the SHA or the `ls-remote` output could not be parsed/trusted |
| `wrong_repository` | the configured remote does not resolve to the expected `owner/name` |

`runs/<run_id>/git/push-verification.json` is retained unconditionally, whatever the
classification -- a `mismatch` is retained exactly as faithfully as a `verified` result,
never silently dropped. Only `status: "verified"` may ever be reported to the user as
"pushed successfully"; every other status is a Git-delivery failure and must be reported
as exactly that -- not softened into "mostly done" or "should be there."

# 9. GitHub metadata via `gh` (used narrowly)
`gh` is used only for information Git itself cannot provide -- repository metadata
(`gh repo view`, via `gh_repo_metadata`), authentication status (`gh auth status`),
cross-repository code search, remote file contents, and pull requests (section 11).
**`gh` is never a substitute for `git ls-remote` in step 8** -- a `gh repo view`,
`gh pr view`, or GitHub UI check is not push verification, regardless of what it shows.

# 10. Failure and mismatch reporting
Every one of this skill's steps produces a named, retained outcome -- never a vague
"something went wrong." When step 8 returns anything other than `verified`, state the
exact classification and `reason` from `push-verification.json`, cite the expected and
observed SHAs, and report the run's Git delivery as failed. A Git-delivery failure is
independent of product/test correctness: a genuinely passing `verification-report.json`
is never rewritten or reinterpreted because a later push could not be verified, and a
push-verification failure never fabricates or downgrades a real test result -- see
`work/SKILL.md`'s "GitHub / Git delivery" section for how the two are kept separate in
the final report.

# 11. GitHub skill pack: five single-purpose procedures
`ASSIGNMENT.md` §2.3 names five small GitHub skills. Each one below is a separate,
independently invokable `live_cli.py` operation backed by its own function in
`harness/orchestrator/github.py`. Each is a thin, fixed-argv `git`/`gh` wrapper that
returns one named status. They compose (for example, `search-code` finds a hit, then
`read-file` reads that file at the hit's `sha`), and none of them calls another except
where `pr-create` below says so. Invoke each one exactly like every other `live_cli.py`
operation (`python -m harness.orchestrator.live_cli --request-file <file>`, see
`work/SKILL.md`). Exit `0` means an affirmative result. Exit `1` means a well-formed
negative result, which is never evidence of anything on GitHub. Exit `2` means a request
construction error.

Rules shared by all five:
- Every caller-supplied value is validated before any subprocess runs. A value that
  starts with `-` is refused, or is passed after `--` or in `--flag=value` form, so no
  input can ever be parsed as a `git`/`gh` flag. `owner/name` slugs must match GitHub's
  own slug shape.
- `target_repo_path` (default `"."`) goes through the same `paths.py` containment and
  Protected Path check as every other operation.
- Four of the five are **read-only**. They run no `git` command that changes state, and
  no `gh` subcommand that writes. `pr-create` is the only state-changing procedure.

## `read-file` -- operation `read_file` (read-only)
Reads a file's content at a ref: a branch, tag, or commit SHA (`~N`/`^N` allowed). It
never reads the working tree; use `Read` for that. Fields: `ref`, `file_path`
(repo-relative, forward slashes, no `..`), optional `repo_slug`, optional `max_bytes`
(default and maximum 1,000,000).
- Without `repo_slug`: reads the local clone with `git show <ref>:<path>`. It never
  fetches, so the ref must already exist locally.
- With `repo_slug`: reads from GitHub with the fixed call
  `gh api --method GET repos/<slug>/contents/<path>?ref=<ref>`. Use this for a repository
  that isn't cloned here.
- Statuses: `found` (with `content`, `size`, and `source` of `local_git` or
  `github_api`) / `not_found` / `invalid_ref` / `invalid_path` / `not_a_file` /
  `too_large` (content is never returned) / `not_text` / `invalid_input` /
  `command_failed` / `invalid_output`.

## `search-code` -- operation `search_code` (read-only)
Searches code in GitHub-hosted repositories with `gh search code`. This is the one
capability in the pack that `git` can't provide. Fields: `query` (one line, at most 256
characters; GitHub qualifiers such as `-language:js` work), `repo_slugs`, optional
`limit` (1-100, default 30).
- **Always scoped.** `repo_slugs` must be a non-empty list of 1-10 `owner/name` slugs.
  An unscoped, GitHub-wide search is refused. Use `git_repo_identity` or
  `gh_repo_metadata` to get the current repository's own slug.
- Results are at most `limit` records of `path`/`repository`/`sha`/`url`.
- Statuses: `found` / `no_matches` (an honest empty result, not an error) /
  `invalid_query` / `command_failed` / `invalid_output`.

## `commit-history` -- operation `commit_history` (read-only)
Returns a bounded `git log`: one `sha`/`author_name`/`author_email`/`date`/`subject`
record per commit. It never returns the whole history. Fields: optional `max_count`
(1-200, default 20), optional `ref` (defaults to HEAD; for example `origin/main`, or a
PR's `headRefOid` from `pr-review`), and optional `path` to scope to one file's history.
- `truncated: true` means exactly `max_count` records came back, so older history may
  exist that was not read.
- Statuses: `ok` / `no_commits` / `invalid_ref` / `invalid_input` / `command_failed` /
  `invalid_output`.

## `pr-review` -- operation `pr_review` (read-only)
Reads a pull request's state with `gh pr view <pr_ref> --json ...`. Fields: `pr_ref` (a
PR number or a branch name), optional `repo_slug`. It returns the raw PR record plus a
`summary` for a Verification report to cite:
- `review_decision`, `approved_by`, and `changes_requested_by`;
- check-run outcomes: `checks_passing`, plus the names in `checks_failing` and
  `checks_pending`;
- `mergeable`, `merge_state_status`, and `changed_files`.

It also returns the PR's real head commit, `headRefOid`. The review workflow composes
from there: read the change itself with `read-file` / `commit-history` at that SHA. This
procedure **never approves, comments, requests changes, or merges**, and a `pr-review`
result is never push verification.
- Statuses: `found` / `not_found` / `invalid_input` / `command_failed` /
  `invalid_output`.

## `pr-create` -- operation `pr_create` (state-changing; explicit authorization only)
Opens a pull request for a branch that is already on the remote, and does nothing else.
It **never edits source, commits, pushes, creates a branch, or retries.** Required
fields:
- `run_id` and `task_id`;
- `base`, `head`, and `title`, plus optional `body`;
- `expected_repo` (`owner/name`, always required, so the remote's identity is always
  checked);
- `authorized` -- the JSON literal `true`, not merely truthy;
- `authorization_source` -- where that explicit authorization came from: the ticket key,
  or the user's explicit instruction quoted.

Only set `authorized: true` when the task or the user explicitly authorized opening a
PR (standing rule 12). Never infer it from the fact that a push was authorized.

It proceeds in this order and stops at the first failure:
1. If authorization is absent, returns `not_authorized` and runs no command.
2. If any input is malformed, returns `invalid_input` and runs no command.
3. `head` must be an **existing local branch** (`git rev-parse --verify
   refs/heads/<head>`), otherwise `invalid_head_branch`. A tag, a SHA, or an invented
   name is never accepted.
4. The head branch's tip must be a **`verify_push`-verified** push to `remote` (the same
   `git ls-remote` full-SHA check as section 8, with `expected_repo` applied), otherwise
   `push_not_verified`, with the full verification record attached.
5. `base` must already exist on the remote, otherwise `base_not_found`.
6. Runs `gh pr create --base=… --head=… --title=… --body=…`. A failure returns
   `already_exists` or `command_failed`.
7. **`gh pr create`'s own exit code and printed URL are never trusted.** The PR is
   independently re-fetched with `pr-review`. It is reported as `created` only if its
   base and head names match, its `headRefOid` equals the verified pushed SHA, its state
   is `OPEN`, and its URL matches the printed one. Anything else is
   `created_unverified`, and must be reported as "gh claimed success; not verified",
   never as a created PR.

Evidence: `live_cli.py` checks the evidence path `runs/<run_id>/git/pr-create.json`
*before* any command runs. If a file is already there, the result is `blocked` and
nothing runs. After the attempt, it retains that file and a `pr_create_attempt` policy
event for every outcome, including refusals.

# Safe operations (apply to every step above)
- Never change Git config globally (`--global`) -- any config this skill's own commit
  step needs is repo-local only.
- Never force-push, never `git reset --hard`, never rewrite history, never create or
  delete a branch unless the task genuinely requires it.
- Never move `origin/<branch>` backward.
- Operate only on the repository and branch `git_repo_identity` just confirmed.

# Boundaries -- this skill must not
- Decide whether a commit or push is authorized -- that is `work/SKILL.md`'s own
  standing rule 12 (no commit/push without explicit ticket authorization), never
  overridden by this skill.
- Decide whether opening a pull request is authorized, by the same rule.
  `pr-create`'s `authorized` / `authorization_source` fields only *record and enforce*
  an authorization that already exists; they never create one.
- Approve, comment on, merge, or close a pull request, or edit any repository file --
  no procedure in section 11 does any of these.
- Fabricate, soften, or infer a verification result -- only a real `verify_push` call's
  own classification may ever be reported.
- Duplicate the test-runner skill's role (test execution) or the code-craftsmanship
  skill's role (change-quality review) -- this skill covers Git/GitHub delivery only.
