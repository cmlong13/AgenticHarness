# Test-Runner Wrapper — Boundary Verification (Script-Level, Pre-Restart)

Live verification of `.claude/skills/test-runner/scripts/run_command.py`, run by invoking
the script directly via Bash (`python .claude/skills/test-runner/scripts/run_command.py
--request-file ...`) from the main session — **not** via Claude Code's `Skill` tool. This
is a deliberate scope limit: `.claude/skills/` did not exist before this change, and per
instruction, live `Skill`-tool invocation (and therefore confirmation that
`allowed-tools`/`disallowed-tools` actually gate the way `SKILL.md` claims) is deferred
until after a session restart. Everything below exercises the wrapper script's own logic
for real — real subprocess execution, real exit codes, real file mutation, real timeout —
just not through the registered skill layer yet.

## Fixture

`runs/test-runner-boundary-test/fixture-repo/fixture-src/`:
- `test_sample.py` — `test_pass` (passes), `test_fail` (`assert 1 == 2`, genuinely fails).
- `test_sleep.py` — `test_sleeps_past_timeout` sleeps 30s, used with `timeout_seconds: 2`.
- `mutator_target.py` / `test_mutates_but_passes.py` — a test that asserts `True` while
  appending to its sibling `mutator_target.py` on every real run.

## Results

| Case | Command | Result | Category / exit_code |
|---|---|---|---|
| `C-int-pass` | `python -m pytest test_sample.py::test_pass -v` | `command_result` | `exit_code: 0` |
| `C-int-fail` | `python -m pytest test_sample.py::test_fail -v` | `command_result` | `exit_code: 1` (real pytest failure, not coerced) |
| `C-int-mutate` | `python -m pytest test_mutates_but_passes.py -v` | `command_result` | `exit_code: 125` — log shows `real_pytest_exit_code: 0`, `[MUTATION DETECTED] ... fixture-src\mutator_target.py` |
| `C-int-timeout` | `python -m pytest test_sleep.py -v` (`timeout_seconds: 2`) | `command_result` | `exit_code: 124` — log shows `real_pytest_exit_code: unknown` and the wrapper-sentinel explanation |
| `C-int-pass` (rerun) | same as above | `command_rejected` | `evidence_collision` — log at the derived path already existed; not overwritten |
| `C-rej-bare-pytest` | `pytest test_sample.py::test_pass` | `command_rejected` | `grammar_no_match` |
| `C-rej-py-launcher` | `py -m pytest test_sample.py::test_pass` | `command_rejected` | `grammar_no_match` |
| `C-rej-pip` | `python -m pip install requests` | `command_rejected` | `grammar_no_match` |
| `C-rej-no-target` | `python -m pytest -v` | `command_rejected` | `grammar_no_match` (no positional target) |
| `C-rej-repo-root` | `python -m pytest .` (`working_directory == target_repo_path`) | `command_rejected` | `positional_path_is_repo_root` |
| `C-rej-outside` | `python -m pytest ../../../PROJECT_SPEC.md` | `command_rejected` | `positional_path_escape` (`..` segment) |
| `C-rej-absolute` | `python -m pytest C:\Windows\System32` | `command_rejected` | `positional_path_escape` (absolute) |
| `C-rej-value` | `timeout_seconds: true` | `command_rejected` | `request_value_invalid` |
| `C-rej-missing` | `purpose` omitted | `command_rejected` | `request_field_missing` |
| `C-rej-unknown` | extra `extra_field` | `command_rejected` | `request_field_unknown` |
| `bad-shape-example.json` (not under `requests/`) | — | `command_rejected` | `request_path_shape_invalid` |
| absolute `--request-file` | — | `command_rejected` | `cli_argument_invalid` |
| missing `--request-file` | — | `command_rejected` | `cli_argument_invalid` |
| unknown CLI arg (`--bogus`) | — | `command_rejected` | `cli_argument_invalid` |

Every case above printed exactly one JSON line to stdout — no traceback, no argparse
usage text, in any case including the CLI-level failures.

## Notable confirmations

- **Real mutation, not simulated**: `mutator_target.py` was genuinely appended to by the
  real `test_mutates_but_passes.py` run (visible in the file's real content), and the
  wrapper's hash-based detection caught it and reported `125` even though pytest's own
  exit code for that run was genuinely `0`.
- **Real timeout, not simulated**: `test_sleeps_past_timeout` sleeps 30s; with
  `timeout_seconds: 2` the wrapper genuinely killed the subprocess after ~2s and reported
  `124`, with `real_pytest_exit_code: unknown` in the log (no fabricated child exit code).
- **`sys.executable` substitution confirmed**: every log's `executed_as:` line shows the
  real venv interpreter path, not the literal `python` token from the requested command,
  while `command_result.command` still echoes the original request text unchanged.
- **Evidence immutability confirmed**: rerunning `C-int-pass` did not overwrite or
  rename the existing log — it was rejected outright as `evidence_collision`.
- **CLI robustness confirmed**: a missing flag, an unknown flag, and an absolute
  `--request-file` value all produced a single clean JSON `command_rejected` object,
  never argparse's default usage-and-exit behavior.

## Deferred to after restart

Not covered here, per instruction: confirming `code-craftsmanship`/`test-runner` in
Claude Code's live skill registry, and any verification that specifically depends on the
registered skill layer (`allowed-tools` pre-approval of the wrapper pattern,
`disallowed-tools` actually removing `Write`/`Edit`/etc. for the duration of the skill's
turn, and invocation via `/test-runner <path>` rather than a direct Bash call).

## Post-restart: deferred items now closed

All of the above was completed live after the restart, via the actual `Skill` tool rather
than a direct Bash call. Full detail: `docs/test-runner-skill-permission-verification.md`.
Summary:

- Both skills confirmed present in Claude Code's live skill registry under their exact
  registered names (`code-craftsmanship`, `test-runner`).
- A real successful run (`C-skill-live-pass`, `exit_code: 0`) and a real rejected run
  (reusing `C-rej-no-target.json` unmodified, `rejection_category: grammar_no_match`)
  were both produced through the live `Skill` mechanism, with `${CLAUDE_SKILL_DIR}`
  resolving to a concrete real path and every field (`command`, `task_id`, `run_id`,
  `command_id`, `working_directory`) preserved exactly.
- All eight tools in `disallowed-tools` (`Write`, `Edit`, `NotebookEdit`, `PowerShell`,
  `Agent`, `Skill`, `WebFetch`, `WebSearch`) were confirmed genuinely denied at the tool
  layer via live disposable probes while the skill was active; `Bash` and `Read` remained
  available throughout.
- The `mutator_target.py` mutating fixture's accumulated pre-restart mutation was
  identified as a reproducibility risk. Added `fixture-repo/reset_mutation_fixture.py`
  to restore the exact baseline (`"VALUE = 1\n"`) before each manual mutation-test
  invocation. Verified live: reset, re-ran the mutation scenario through the `Skill`
  mechanism with a fresh `command_id` (`C-mutate-reset-check`), got the same
  `exit_code: 125` / `MUTATION DETECTED` result deterministically, then reset again.
  The original `C-int-mutate.log` evidence was left untouched throughout.
