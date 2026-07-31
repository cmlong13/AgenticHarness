# Test-Runner Skill — Live Permission-Boundary Verification (Post-Restart)

Live verification of `.claude/skills/test-runner/`, performed through Claude Code's actual
`Skill` tool from the main (orchestrator) session — the piece explicitly deferred in
`runs/test-runner-boundary-test/verification-summary.md` pending a session restart, since
`.claude/skills/` did not previously exist as a registered skill layer.

## 1. Live skill registration

Claude Code's own live skill listing, surfaced to this session as available `Skill` targets,
names both skills exactly:

- `code-craftsmanship`
- `test-runner`

These match the `name:` frontmatter field in each `SKILL.md` exactly.

## 2. Invocation via the actual Skill mechanism, not raw Bash

Called `Skill(skill: "test-runner", args: "runs/test-runner-boundary-test/requests/C-skill-live-pass.json")`,
using a request file written beforehand (in the same session, using ordinary unrestricted
`Write`) at exactly `runs/<run_id>/requests/<command_id>.json`, per the skill's own
documented protocol.

The returned procedure resolved `${CLAUDE_SKILL_DIR}` to a concrete, real absolute path:
```
C:/Users/caleb/Documents/Projects/AgenticHarness/.claude/skills/test-runner/scripts/run_command.py
```
Running exactly the command the skill specified produced exactly one raw JSON line:
```json
{"message_type":"command_result","task_id":"T-BOUNDARY","run_id":"test-runner-boundary-test","command_id":"C-skill-live-pass","command":"python -m pytest test_sample.py::test_pass -v","working_directory":"runs/test-runner-boundary-test/fixture-repo/fixture-src","exit_code":0,"output_ref":"runs/test-runner-boundary-test/logs/C-skill-live-pass.log"}
```
- `output_ref` resolves to a real retained log containing genuine pytest stdout (`1 passed`)
  and a real mutation-check section reporting no changes.
- `command`, `task_id`, `run_id`, `command_id`, and `working_directory` are all preserved
  exactly from the request, byte-for-byte.
- No narrative, wrapper commentary, or extra fields were added around the JSON object —
  the reply was exactly the wrapper's own raw stdout line.

Reusing the pre-existing `runs/test-runner-boundary-test/requests/C-rej-no-target.json`
(unmodified) through the same live `Skill` invocation produced:
```json
{"message_type":"command_rejected","task_id":"T-BOUNDARY","run_id":"test-runner-boundary-test","command_id":"C-rej-no-target","command":"python -m pytest -v","working_directory":"runs/test-runner-boundary-test/fixture-repo/fixture-src","rejection_reason":"at least one positional test file, directory, or node ID is required -- bare 'python -m pytest' with no target is not accepted","rejection_category":"grammar_no_match"}
```
No `command_result` was fabricated for the rejected case; there is no `exit_code` field at
all, matching the documented contract that a rejection means no real command ever executed.

## 3. Runtime-observed permission enforcement, distinct from frontmatter inspection

`SKILL.md`'s frontmatter statically claims:
```
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/run_command.py *)
disallowed-tools: Write, Edit, NotebookEdit, PowerShell, Agent, Skill, WebFetch, WebSearch
```
That is a document claim, not proof of enforcement. To verify it was actually enforced at the
tool layer (not merely documented), while the `test-runner` skill was active in this session,
disposable probes were attempted against every tool named in `disallowed-tools`:

| Tool probed | Probe | Result |
|---|---|---|
| `Write` | create a throwaway file under the session scratchpad | **`Permission to use Write has been denied.`** |
| `Edit` | edit a scratch file (created via `Bash`, then `Read`, to isolate the probe from the `Write` denial itself) | **`Permission to use Edit has been denied.`** |
| `NotebookEdit` | edit a cell in a throwaway `.ipynb` (created via `Bash`, `Read` first as required) | **`Permission to use NotebookEdit has been denied.`** |
| `PowerShell` | trivial `Write-Output "probe"` | **`Permission to use PowerShell has been denied.`** |
| `Skill` | nested invocation of `code-craftsmanship` | **`Permission to use Skill has been denied.`** |
| `WebFetch` | fetch `https://example.com` | **`Permission to use WebFetch has been denied.`** |
| `WebSearch` | trivial query | **`Permission to use WebSearch has been denied.`** |
| `Agent` | spawn a trivial one-word-reply `general-purpose` agent | **`Permission to use Agent has been denied.`** |

All eight returned the identical hard runtime error text, not a permission *prompt* and not a
soft warning. `Bash` (the intended wrapper-invocation path) and `Read` remained available and
were used successfully throughout, matching `allowed-tools`.

This is genuine runtime-observed enforcement: the evidence is the actual tool-call error
returned by the harness when the call was attempted, not a re-reading of the `disallowed-tools`
frontmatter line. All probe files were disposable and were cleaned up via `Bash` (the one tool
left available) immediately after.

**Scope of the restriction, empirically observed:** the denial held for every further tool
call made within that same assistant turn/response (re-checked twice, both still denied), and
was confirmed lifted at the start of the next turn (`Write` succeeded immediately once a new
user turn began). The restriction is turn-scoped to the skill's active invocation, not
permanent for the rest of the session.

## 4. Fixture reproducibility (mutating fixture)

`runs/test-runner-boundary-test/fixture-repo/fixture-src/mutator_target.py` is intentionally
mutated by `test_mutates_but_passes.py` on every real run (it appends `\n# mutated by test\n`
to its sibling on each execution). Before this post-restart pass, the fixture already carried
one accumulated mutation from the original pre-restart script-level verification run (see
`runs/test-runner-boundary-test/verification-summary.md`, case `C-int-mutate`) — a real risk to
determinism on repeated manual invocation.

Added `runs/test-runner-boundary-test/fixture-repo/reset_mutation_fixture.py`, a small script
that restores `mutator_target.py` to its exact baseline content (`"VALUE = 1\n"`). It does not
touch `runs/test-runner-boundary-test/logs/`, so retained evidence from the original
mutation-detection run is untouched.

Live reproducibility check performed this session:
1. Ran the reset script — `mutator_target.py` restored to baseline.
2. Re-ran the mutation scenario through the live `Skill` mechanism with a fresh
   `command_id` (`C-mutate-reset-check`, to avoid the wrapper's own evidence-collision
   protection on the log path) — produced `exit_code: 125` again, with the log again showing
   `real_pytest_exit_code: 0` and `[MUTATION DETECTED] ... mutator_target.py`, i.e. the exact
   same class of detection as the original `C-int-mutate.log`, deterministically reproduced
   from a known baseline.
3. Ran the reset script again, leaving the fixture at its clean baseline for future runs.

Both the original mutation-detection evidence (`runs/test-runner-boundary-test/logs/C-int-mutate.log`)
and the new reproducibility-check evidence (`runs/test-runner-boundary-test/logs/C-mutate-reset-check.log`)
are retained side by side; neither was deleted or overwritten.

**Process note:** because `Write`/`Edit` are unavailable while `test-runner` is active, the
reset script must be run by the controlled caller *before* invoking the skill for a mutation
test, in the same separate-step pattern already used for writing the request file itself. It is
not, and cannot be, run automatically by the skill.

## Conclusion

Every claim in `test-runner/SKILL.md`'s `allowed-tools`/`disallowed-tools` frontmatter was
independently confirmed against real, observed tool-call behavior in this session: the wrapper
pattern works end-to-end through the actual `Skill` mechanism (not a simulated Bash call), a
real success and a real rejection both round-trip correctly with no fabricated fields, and all
eight disallowed tools were genuinely denied at the tool layer, not merely documented as
disallowed.
