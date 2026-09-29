# False "pushed" claim — live detection proof (2026-09-29)

Dedicated harness fixture run (`run-20260929-falsepush-001`, task `FALSEPUSH-001`).
**This is the live acceptance proof for ASSIGNMENT.md §4:**

> Orchestrator catches a simulated false "pushed" claim (report a push without pushing;
> show detection via `git ls-remote`).

Not a live four-phase `/work` pipeline run — this is a real `live_cli.py` CLI
demonstration of the production `verify_push` boundary (the exact operation `/work`'s
"GitHub / Git delivery" step 8 calls), run against the real configured `origin`
(`https://github.com/cmlong13/AgenticHarness.git`). `git ls-remote` was **not** mocked:
`op_verify_push` always uses `github.DEFAULT_RUNNER` (a real `subprocess.run`). No push,
commit, force-push, remote change, or history rewrite was performed.

## The simulated false claim

Recorded before verification as the `simulated_push_claim` policy event
(`requests/FP-4-simulated-claim.json`):

> "Committed `dd3c875129a46db7bd2e73331240ca5daf343269` and pushed it to origin branch
> `falsepush/run-20260929-falsepush-001`. Pushed and done."

| field | value |
|---|---|
| claimed repository | `cmlong13/AgenticHarness` |
| claimed remote | `origin` |
| claimed ref | `refs/heads/falsepush/run-20260929-falsepush-001` |
| claimed SHA | `dd3c875129a46db7bd2e73331240ca5daf343269` (a real local commit — current `HEAD`, retained in `git/commit-evidence.json` via `retain_commit_evidence`) |
| push actually performed | **no** |

The claim is false by construction, with no race involved. The branch was never
created locally or pushed. `remote-snapshots/before.txt` (a full `git ls-remote origin`)
shows the remote has only `HEAD` and `refs/heads/main`.

## Sequence (all via `python -m harness.orchestrator.live_cli --request-file ...`)

| step | request | operation | result | exit |
|---|---|---|---|---|
| — | — | raw `git ls-remote origin` snapshot | `remote-snapshots/before.txt` | 0 |
| 1 | `FP-1-identity` | `git_repo_identity` (`expected_repo: cmlong13/AgenticHarness`) | `ok`, branch `main`, HEAD `dd3c875…`, remote URL matches | 0 |
| 2 | `FP-2-commit-evidence` | `retain_commit_evidence` | `retained`, `commit_sha dd3c875…` | 0 |
| 3 | `FP-3-control-pre` | `verify_push` `main` / `dd3c875…` (control) | **`verified`** | 0 |
| 4 | `FP-4-simulated-claim` | `retain_policy_event` `simulated_push_claim` | `retained` | 0 |
| 5 | `FP-5-verify-claim` | `verify_push` claimed branch / `dd3c875…` | **`remote_ref_missing`** | **1** |
| 6 | `FP-6-control-post` | `verify_push` `main` / `dd3c875…` (control) | **`verified`** | 0 |
| — | — | raw `git ls-remote origin` snapshot | `remote-snapshots/after.txt` | 0 |
| 7 | `FP-7-delivery-decision` | `retain_policy_event` `git_delivery_decision` | `retained`: `decision: rejected` | 0 |

Every CLI stdout is retained verbatim under `responses/`, and every exit code is in
`responses/exit-codes.txt`.

## Detection evidence — `git/push-verification.json`

- command: `git ls-remote origin refs/heads/falsepush/run-20260929-falsepush-001`
- exit code: `0`, stdout: `""` (empty), stderr: `""`
- expected (claimed) SHA: `dd3c875129a46db7bd2e73331240ca5daf343269`
- observed remote SHA: `null` (the ref is absent)
- classification: **`remote_ref_missing`**, with the reason "ref
  'refs/heads/falsepush/run-20260929-falsepush-001' was not present in 'git ls-remote
  origin …' output"

`git ls-remote` succeeding (exit 0) is not treated as a successful push. The verifier
(`github.classify_ls_remote_result`) compares the returned ref and SHA against the claim.
Here no entry for the claimed ref came back, so the claim is contradicted.

## Orchestration decision

`remote_ref_missing` is not in `live_cli.OK_STATUSES`, so the CLI exited `1`. Under
`work/SKILL.md` "GitHub / Git delivery" step 8 and `github/SKILL.md` §8, only `verified`
may be reported as a successful push. The orchestrator therefore **rejected the claim**
and did not mark delivery complete. It recorded this as the `git_delivery_decision` event
(`decision: "rejected"`, `push_claim_accepted: false`, `delivery_complete: false`).

## Controls and non-mutation

- Controls: the same production path verified the genuine state (`main` =
  `dd3c875…`) as `verified` both before and after the false-claim check. This shows the
  rejection came from the claim, not from a broken verifier or an unreachable remote.
- `remote-snapshots/before.txt` and `after.txt` are identical (`HEAD` and
  `refs/heads/main` both at `dd3c875…`, no other refs). No remote mutation occurred.
- Local `HEAD` stayed at `dd3c875…` throughout. No commit was created and no source file
  changed; the only new files are this run directory.

## Relationship to earlier evidence

- The deterministic fixtures (`tests/test_orchestrator_github.py::TestClassifyLsRemoteResult::test_false_push_claim_is_classified_mismatch`,
  `tests/test_orchestrator_live_cli.py::TestVerifyPush::test_mismatch_is_retained_honestly_never_reported_as_verified`)
  still cover the `mismatch` branch (the ref exists at a different SHA) with an injected
  `git ls-remote` result.
- This run covers a live, unmocked remote query against the real repository for the
  "claimed a push that never happened" case. On a real remote with no new push, that case
  shows up as the claimed ref being absent (`remote_ref_missing`).
- A live `mismatch` against `origin` was deliberately not staged. It would require the
  claimed SHA to be a real local commit that differs from an existing remote ref's tip.
  The only ways to produce one here are a new commit or an extra remote branch, and both
  are outside this proof's no-mutation constraints.
- `runs/run-20260818-githubpush-001/` remains the separate live proof of a genuine,
  successful, independently verified push.
