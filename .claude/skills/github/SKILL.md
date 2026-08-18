---
name: github
description: Git/GitHub procedure for repository identity, working-tree inspection,
  commit creation, push, and independent remote push verification. Invoked by the
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
every piece of retained evidence.

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
(`gh repo view`, via `gh_repo_metadata`), authentication status (`gh auth status`), and,
in future work, pull requests. **`gh` is never a substitute for `git ls-remote` in step
8** -- a `gh repo view` or GitHub UI check is not push verification, regardless of what
it shows.

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
- Fabricate, soften, or infer a verification result -- only a real `verify_push` call's
  own classification may ever be reported.
- Duplicate the test-runner skill's role (test execution) or the code-craftsmanship
  skill's role (change-quality review) -- this skill covers Git/GitHub delivery only.
