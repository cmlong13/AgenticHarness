# Continuity check: fresh-agent substitution caught (unplanned, genuine)

This evidence was produced by an actual tooling mistake during the verification run, not a
staged scenario — which makes it stronger evidence than a scripted test, since the Engineer's
detection behavior was not primed for this specific case.

## What happened

The controlled caller (main session) needed to resume the Engineer instance `a7fb90b195daf3e13`
(the happy-path run, `task_id: T-ENG-VERIFY-003`, `run_id: run-engineer-verify-003`) with the
real `command_result` for its outstanding `pre_test_requested` (`command_id: C-1`). Instead of
using `SendMessage` (which resumes an agent from its transcript), the caller mistakenly called
the `Agent` tool again with `subagent_type: engineer` and a prompt that said `to:
a7fb90b195daf3e13` in plain text. `Agent` does not resume anything — it always spawns a fresh
instance. This produced a new, unrelated Engineer instance (`a186a91fbd8ba6911`) with zero
memory of the original conversation, and handed it a `command_result` that referenced state
only the *other* instance had ever produced.

## What the fresh instance did

`a186a91fbd8ba6911` did not fabricate continuity. It:
- Recognized it had no memory of receiving the original task dispatch, performing Step 1,
  writing the test, or emitting `pre_test_requested` with `command_id: C-1`.
- Independently checked the filesystem (`Read`/`Glob` only) and found corroborating evidence of
  the mismatch: the real `scope.json` in the run directory carries `task_id:
  T-ENG-VERIFY-001` / `run_id: run-engineer-verify-001`, not the `T-ENG-VERIFY-003` /
  `run-engineer-verify-003` in the supplied `command_result`.
- Refused to emit a `blocked` final artifact anyway, because it also lacked a caller-supplied
  `created_at` and would have had to invent one to satisfy the schema — which its instructions
  also forbid.
- Asked the calling context to either resume the real original instance, issue a complete fresh
  dispatch, or self-report `blocked` — rather than silently proceeding.

## Why this matters

This is exactly the failure mode Section "Continuity requirement" in `engineer.md` describes:
*"If the original instance cannot be resumed, the caller must report `blocked` itself rather
than spawning a fresh Engineer and pretending continuity was preserved."* The fresh instance
had no way to know it was a substitute — it only had the mismatched state in front of it — and
its detection logic (unrecognized `command_id`, mismatched `task_id`/`run_id` against
independently-checked evidence) caught the problem without being told what to look for.

## Corrective action taken

The caller error was corrected: the real instance `a7fb90b195daf3e13` was resumed properly via
`SendMessage`, which continued the happy-path run from its actual transcript. See
`runs/engineer-boundary-test/implementation-report.ready.json` (once produced) for that run's
outcome. The erroneous fresh instance `a186a91fbd8ba6911` was not reused for anything further.
