# Hooks permission verification (Part 3, 2026-08-05)

Covers the three assignment-required hooks implemented at `.claude/hooks/` and registered
in `.claude/settings.json`. Each hook is a real, standalone Python executable Claude Code
invokes as a subprocess (not a prose instruction) -- see `docs/hooks-signal-verification.md`
for the empirical basis of `skill_enforcement.py`'s enforcement signal.

## 1. `pre_dispatch_check.py` (`PreToolUse`, matcher `Agent`)

**Live, real tool-layer demonstrations** (this session, no unit-test simulation): an actual
`Agent` tool call was made for each case below and genuinely refused before any subagent
was created (zero token/tool cost incurred by the refused dispatch itself):

- Engineer dispatch for a run with no promoted `findings.json`:
  ```
  PreToolUse:Agent hook error: [python .claude/hooks/pre_dispatch_check.py]: pre-dispatch
  check: refusing to dispatch subagent_type='engineer' for
  run_id='hooks-diagnostic-no-findings' -- required precondition artifact findings.json
  does not exist at .../runs/hooks-diagnostic-no-findings/findings.json. It must be
  validated and promoted before this dispatch.
  ```
- Quality Engineer dispatch for a run with no promoted `implementation-report.json`:
  ```
  PreToolUse:Agent hook error: [python .claude/hooks/pre_dispatch_check.py]: pre-dispatch
  check: refusing to dispatch subagent_type='quality-engineer' for
  run_id='hooks-diagnostic-no-findings' -- required precondition artifact
  implementation-report.json does not exist at .../implementation-report.json. It must be
  validated and promoted before this dispatch.
  ```

**Deterministic coverage** (`tests/test_hooks.py::TestPreDispatchCheck`, 14 tests): missing
`findings.json`/`implementation-report.json`/`scope.json`; identity (`task_id`/`run_id`)
mismatch between the dispatch prompt and the promoted artifact; wrong `status` (a `blocked`
implementation report, a `refused` scope); malformed precondition JSON; malformed hook
stdin (fails open, never crashes); the underscore/hyphen `quality_engineer`/
`quality-engineer` spelling normalization; and the positive (allowed) case for all three
subagent types with a valid, identity-matching, correctly-`status`ed precondition artifact.

## 2. `skill_enforcement.py` (`PreToolUse`, matcher `Bash`)

**Live, real tool-layer demonstrations**: a genuine `Bash` tool call for each case below was
actually refused by Claude Code before the shell ever ran the command (confirmed
independently afterward: `git status --short -- demo-repo` stayed empty, and
`demo-repo/src/loanflow/explanations.py` was byte-for-byte unchanged):

- Direct wrapper bypass:
  ```
  $ python .claude/skills/test-runner/scripts/run_command.py --request-file "runs/hooks-diagnostic/requests/C-1.json"
  PreToolUse:Bash hook error: [python .claude/hooks/skill_enforcement.py]: skill-enforcement:
  direct Bash execution of run_command.py outside the real test-runner Skill flow is
  blocked. Invoke the Skill tool (skill: "test-runner") instead -- never call the wrapper
  script directly from the orchestrator session.
  ```
- Bash-based source edit fallback:
  ```
  $ echo "malicious edit" > demo-repo/src/loanflow/explanations.py
  PreToolUse:Bash hook error: [python .claude/hooks/skill_enforcement.py]: skill-enforcement:
  a Bash command appears to write into demo-repo/ directly. Only the Engineer subagent's
  own Edit/Write tool may modify approved demo-repo/ files -- Bash must never be used as
  an orchestrator fallback to edit application source.
  ```
  Both events were also recorded, with real timestamps, to the hook's own runtime log,
  `.claude/hooks/logs/skill-enforcement-events.jsonl` -- an **ignored, mutable** file (see
  "Runtime log vs. retained evidence" below), not committed. A verbatim copy of its
  contents at the time of this verification is committed as retained evidence at
  `runs/hooks-diagnostic/skill-enforcement-events.jsonl`.
- The real, forked `test-runner` Skill invocation used to obtain the live signal evidence
  in `docs/hooks-signal-verification.md` (against `runs/hooks-diagnostic/requests/C-1.json`)
  was **not** blocked -- it ran to a genuine `command_result` (`exit_code: 0`, 3 passed,
  retained at `runs/hooks-diagnostic/logs/C-1.log`) -- proving the `agent_id`-presence
  signal correctly distinguishes the legitimate forked path from the orchestrator bypass,
  not just in theory but in the same live session.

**Deterministic coverage** (`tests/test_hooks.py::TestSkillEnforcement`, 13 tests): the
orchestrator-context bypass (blocked) vs. the identical command with `agent_id` present
(allowed); `sed -i`/`python -c open(..., 'w')` write variants against `demo-repo/`
(blocked); read-only `git status`/`git diff` against `demo-repo/` (allowed); a regression
test proving `2>&1` (stderr redirection) is never mistaken for a write; both blocked event
kinds landing in the JSONL log; and malformed hook stdin failing open.

**Known, documented limitation** (consistent with `test-runner/SKILL.md`'s own existing
honesty about this): the demo-repo write-fallback check is a substring/regex heuristic,
not a sandboxed guarantee -- an write disguised heavily enough (e.g. through an
indirection this heuristic's token list doesn't recognize) could still slip through. This
mirrors the wrapper's own already-documented "repository test-code trust boundary" --
true prevention would require disposable or OS-sandboxed execution, out of scope here.

## 3. `completion_guardrail.py` (`Stop`)

**Verified deterministically** (`tests/test_hooks.py::TestCompletionGuardrail`, 13 tests),
by invoking the real hook script's `main()` directly with a crafted `Stop` payload and a
crafted `runs/<run_id>/` fixture tree -- not a live end-of-turn trigger, since this hook is
wired to *this session's own* Stop event once registered, and deliberately triggering it
against a real conversation turn to observe its live behavior risked leaving a bad
`.completion_claim.json` marker in place at the end of this very session (which would then
block this report from ever completing). The direct-invocation approach exercises the
exact same code path Claude Code's Stop hook runner would, only bypassing the live
lifecycle trigger for safety.

Covered: no marker present (allowed, the common case for any non-`/work` turn);
`stop_hook_active` always allows (never an infinite must-continue loop); missing
`verification-report.json`; a deleted `verification-report.json` after a prior genuine
`pass` (the assignment's canonical "delete the verification evidence" scenario); invalid
JSON verification evidence; a non-`pass` `final_verdict`; a missing `run-summary.json`;
a canonical-artifact (`scope.json`) whose own `task_id` doesn't match the claimed run;
a real Git-reality conflict (implementation report claims a changed file `git status
--porcelain` shows as unmodified); a genuinely passing run with matching Git reality
(allowed, and the marker is cleared so it does not keep re-triggering); a repaired run
whose final verification report lives at a `*.repair-1.json` path resolved via
`run-summary.json`'s own `artifact_refs` rather than a hardcoded filename; and malformed
marker/hook-stdin JSON (reported as a block / failed open, respectively, never a crash).

## Runtime log vs. retained evidence (2026-08-05 correction)

`.claude/hooks/logs/skill-enforcement-events.jsonl` is `skill_enforcement.py`'s **live
runtime log** -- the hook's `_record()` function creates `.claude/hooks/logs/` and appends
to this file automatically (`LOG_PATH.parent.mkdir(parents=True, exist_ok=True)`) every
time it blocks a real event, for as long as the hook keeps running in normal harness
operation. It was initially committed directly from this location, which was a mistake:
every future blocked event during ordinary harness use would modify a tracked file and
dirty the repository. `.claude/hooks/logs/` is now listed in `.gitignore`, and the hook's
own directory-creation behavior needs no code change to keep working against an ignored
path -- confirmed by deleting the directory and re-triggering `_record()`, which recreated
both the directory and the file exactly as before.

The two blocked-event lines this verification produced are preserved, unchanged, as
**committed, immutable demonstration evidence** at
`runs/hooks-diagnostic/skill-enforcement-events.jsonl` -- a verbatim copy taken at the time
of this verification, not a live-updating file. Future real blocked events accumulate only
in the ignored runtime log at `.claude/hooks/logs/skill-enforcement-events.jsonl`; they do
not retroactively change the retained evidence copy, and the retained copy will not
silently grow as the harness keeps running.

## What "technically enforced" means here, precisely

All three hooks are real Python scripts executed by Claude Code's own hook runner via
`.claude/settings.json` -- not prose the agent is merely asked to follow. `pre_dispatch_check.py`
and `skill_enforcement.py` were both proven live, in this same session, to actually
intercept and refuse a real tool call before it ran, with retained evidence of the refusal
independent of anything this document claims. `completion_guardrail.py`'s blocking logic is
proven against the real script; its live end-of-turn trigger is inferred from Claude Code's
documented `Stop`-hook contract (exit code 2 forces continuation) rather than independently
observed this session, for the safety reason above -- this is stated plainly rather than
implied to be equally live-verified.
