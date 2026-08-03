# demo-repo Baseline Verification (via the real test-runner skill wrapper)

Run ID: `demo-repo-baseline-verification`
Date: 2026-08-03

Both commands were executed by invoking
`.claude/skills/test-runner/scripts/run_command.py --request-file <path>`
directly, exactly as `test-runner/SKILL.md` documents — not a direct
`pytest` call.

## C-1 — narrow: `python -m pytest tests/unit/test_risk_bands.py -v`
- `target_repo_path`: `demo-repo`, `working_directory`: `demo-repo`
- Result: `command_result`, `exit_code: 0`
- Real pytest result: `8 passed`
- Mutation check: no persistent non-cache file change detected
- Log: `runs/demo-repo-baseline-verification/logs/C-1.log`

## C-2 — full suite: `python -m pytest tests -q`
- `target_repo_path`: `demo-repo`, `working_directory`: `demo-repo`
- Result: `command_result`, `exit_code: 0`
- Real pytest result: `101 passed`
- Mutation check: no persistent non-cache file change detected
- Log: `runs/demo-repo-baseline-verification/logs/C-2.log`

## Findings
- Neither command was rejected (`command_rejected`) or returned a
  wrapper sentinel (`124` timeout, `125` mutation, `126` internal error).
- `demo-repo` as currently laid out (`target_repo_path: "demo-repo"`,
  `working_directory: "demo-repo"`) is fully compatible with the
  test-runner wrapper's path-containment rules and command grammar.
- No incompatibility discovered; the wrapper and `demo-repo` were not
  modified.
