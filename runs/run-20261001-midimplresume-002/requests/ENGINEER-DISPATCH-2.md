You are the Engineer for a checkpoint-resumed Implementation phase. You are a FRESH dispatch for a restarted phase -- not a continuation of any earlier Engineer instance.

Input:
- task_id: T-MIDIMPLRESUME
- run_id: run-20261001-midimplresume-002
- created_at: 2026-10-01T13:09:43Z
- target_repo_path: demo-repo
- path_validation: {"validated_by": "controlled_caller", "symlink_escape_checked": true}
- scope_ref.path: runs/run-20261001-midimplresume-002/scope.json
- findings_ref.path: runs/run-20261001-midimplresume-002/findings.json
- findings_ref.finding_ids: ["F-1", "F-2", "F-3", "F-4", "F-5", "F-6"]  (all `found` classifications)

Resume context (orchestrator-observed facts, independently checked with git status / git diff before this dispatch):
- A previous process ran this same Implementation phase with a different Engineer instance (agent aec739880aee94672). That process was killed before it produced an implementation report. You cannot and must not resume or impersonate it.
- That earlier instance left one uncommitted change in the working tree: demo-repo/tests/unit/test_risk_bands.py has a new test `test_credit_score_non_integer_and_bool_is_rejected` appended (+11 lines, four `pytest.raises(ValidationError)` blocks for 700.5, 700.0, True, False). No production source file is modified (demo-repo/src/loanflow/risk_bands.py is unchanged from HEAD).
- Read that file yourself and decide independently whether the test is correct and in line with scope.json's constraints. You may keep it as your regression test (report it in test_created / tests as added relative to HEAD) or adjust it within scope. Either way, you must obtain YOUR OWN pre-test failing evidence before any production source edit -- TDD still applies to you.
- Command id sequencing: command id `C-1` is already used and retained by the interrupted process (runs/run-20261001-midimplresume-002/requests/C-1.json and logs/C-1.log). Do NOT reuse `C-1`. Start your requested_command ids at `C-2` and continue sequentially (`C-3`, ...).
- requested_command.working_directory must be the relative path `demo-repo` (never an absolute or drive-prefixed path), and requested_command.command must follow engineer.md's command grammar (`python -m pytest <relative test path> ...`).

Output contract reminder: every reply you send must be exactly one raw JSON object -- no Markdown code fence, no leading or trailing prose; the first character is `{` and the last character is `}`.
