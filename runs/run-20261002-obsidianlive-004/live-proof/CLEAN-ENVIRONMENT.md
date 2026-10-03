# run-20261002-obsidianlive-004: clean-environment provenance

Operator-written note. Everything else in this directory outside `live-proof/` is the run's own
evidence, copied byte-for-byte (40 files, SHA-256 checked) from the clean clone where it ran.

## Where it ran
- Clean sibling clone `C:\Users\caleb\Documents\Projects\AgenticHarness-obslive004-clean`, outside this
  working tree. Created with `git clone --no-hardlinks`, origin re-pointed to
  `https://github.com/cmlong13/AgenticHarness.git`, and checked out detached at committed HEAD
  `681fddf85284978a1d43cb03523976c5107d9782`, the same HEAD as this repository.
- The only local additions were all git-ignored: `.claude/settings.local.json` (enables only the
  project's `jira`/`obsidian` MCP servers), and the clone's own `.venv` with the clone installed
  editable. The main repository's `.venv` was removed from the child's `PATH`. `launch-log.json`
  records `child_env_mentions_main_repo: []`.
- `OBSIDIAN_VAULT_PATH=C:\Users\caleb\Documents\Obsidian Vault` was set only in the child process
  environment (`drive_run_clean.py`), never in a repository file.
- Headless session `ee3aa403-a2d2-4db9-9eb0-a752bad5cc41` (`process-1-stream.jsonl`).

## Negative baseline (`clean-baseline.json`, taken before launch)
1574 text files were scanned and 0 matches were found for each of: `run-20261001-obsidianlive-002`,
`obsidianlive-002`, `obsidianlive-003`, `T-OBSLIVEA`, `T-OBSLIVEB`, `LFCONV-INTGUARD-01`, `LFCONV`,
`test_non_integer_and_bool_days_past_due_is_rejected`, `placed before the unchanged negative check`,
`loanflow-integer-input-convention`. `late_fee` occurs only in files committed on or before
2026-10-01T15:49-04:00, before Run A started (2026-10-01T22:00:10Z). The clone's
`late_fee_tier.py` has no type guard, and `memory/` is identical to HEAD.

## What the transcript shows
- No record references the main repository path (384 records scanned).
- The first appearance of `obsidianlive-002` in the whole transcript is record 57, the result of
  `search_obsidian` (query `applicants Business validation ValidationError`).
- `obsidian/read/read-2.json` read `Harness Run Summaries/run-summary-run-20261001-obsidianlive-002.md`
  with SHA-256 `338a671b4d484ac9fbf7fb46a93db17b8a5ef2d2fa39400fdd2fa3d6e5580579`. This equals
  the independently verified hash of the file Run A published
  (`runs/run-20261001-obsidianlive-002/obsidian/summary-publication.json`).
- `scope.json` cites the -002 and -003 vault summaries and the seed note as precedent.
  Research (`findings.json` F-8, F-12) classifies the -002 summary as STALE / context-only,
  and the seed note LFCONV-INTGUARD-01 as CORROBORATED (F-11). The one prior-run-specific
  decision visible in the transcript (record 90, before acceptance criteria were written) comes
  from the **-003** summary: avoid that run's untested-acceptance-criterion pitfall.

## Process 1 stop and process 2 completion
- Process 1 reached Implementation (Engineer's test-first step; `clean-clone-diff-at-stop.patch`)
  and then stopped on the account's 5-hour usage limit (`result`: "You've hit your session limit",
  `terminal_reason: api_error`, exit code 1). At that point it had no terminal checkpoint, run
  summary, or vault publication, and the vault was byte-identical (`vault-before-runC.txt`,
  `vault-after-runC.txt`). The run evidence as it stood then is preserved unchanged in
  `at-stop-snapshot/`. Of its 40 files, only `checkpoint.json` and `logs/policy-events.jsonl`
  were later updated by the run itself.
- Process 2, `/work --resume run-20261002-obsidianlive-004` (session
  `35134ea7-a024-45b4-b3f5-fc5d229f1305`, same clean clone, same launcher, `process-2-*`), reused
  Discovery and Research. It restarted Implementation with a fresh Engineer, which adopted the
  orphaned test, and ran Verification with a fresh Quality Engineer. It reached `final_verdict: pass`
  (AC-1..AC-4 passed; ORCH-1 7 passed, ORCH-2 113 passed) and published
  `Harness Run Summaries/run-summary-run-20261002-obsidianlive-004.md` (SHA-256 `4799f3ed...`,
  `vault-after-runC-final.txt`). No record in its transcript references the main repository path
  (586 records scanned). Process 2 performed no vault read: its retrieval evidence is process 1's.
- That published note was rendered by the clean clone's committed `obsidian.py`, which predates
  the uncommitted acceptance/usage renderer fix in the main working tree. It therefore still shows
  "0/4 passed" and "None tokens".
- The run's final `run-summary.json` attributes the idiom to LFCONV-INTGUARD-01 and does not
  mention the -002 summary.
- The repository top level holds the final run evidence, copied byte-for-byte (121 files).
