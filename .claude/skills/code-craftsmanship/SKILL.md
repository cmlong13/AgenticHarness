---
name: code-craftsmanship
description: Minimal-change and repository-convention checklist applied to a proposed
  implementation plan or diff before it's accepted. Read by the Engineer before its first
  edit in every Implementation phase (engineer.md Step 5); also usable by the controlled
  caller/orchestrator to review a plan or diff.
---

# Purpose
Hold a proposed change to the smallest-sufficient-change standard, without performing
Research, Discovery, or Verification, and without approving or completing anything yourself.

# Checklist — ask every question below about the proposal in front of you
1. Conventions: does the proposal read existing code/tests in the touched area before
   changing it, and match the file's existing style/patterns?
2. Behavior preservation: does anything currently working change unintentionally?
3. Smallest rung: could this stop at "doesn't need to exist," "reuse an existing
   helper," or "stdlib/already-installed dependency," rather than new code?
4. Extend, don't replace: is working code being extended, or needlessly rewritten?
5. No speculative abstraction: is there a new interface/base class/config knob with
   only one real caller today?
6. Do any public interfaces change unless scope explicitly requires it?
7. Safety preserved: input validation, data-loss protection, error handling, and
   security checks present before the change — still present after?
8. Tests: is the new/changed test the narrowest one that exercises the behavior?
9. No drive-by cleanup: does the diff touch only what the objective requires?
10. Justification: can you state, in one sentence per file, why each changed file had
    to change?
11. Scope: does every changed path match `scope.json`'s `in_scope` list exactly?
12. Protected Paths: does `changed_files` avoid every entry in the exact Protected Path
    list below, even if `scope.json` erroneously listed one as in-scope?

# The exact Protected Path list
- `.claude/**`
- `.git/**`
- `PROJECT_SPEC.md`
- `harness/schemas/**`
- `harness/artifacts/examples/**`
- `harness/evidence.py`
- `runs/**/scope.json`
- `runs/**/findings.json`
- `runs/**/verification-report.json`
- any path outside `target_repo_path`

# Output (convention, not a validated artifact)
`rung_assessment`, `flags` (checklist items that failed, with file/line), `recommendation`
(smallest concrete fix per flag), `scope_ok` / `protected_path_ok` booleans.

# Boundaries — this skill must not
- Perform Discovery or Research.
- Approve scope, verify its own recommendation, or claim the task is done.
- Recommend touching a Protected Path.
- Expand the task.
- Override `scope.json`, `findings.json`, any artifact schema, or the Engineer's final
  `implementation-report.json` contract.

# How the Engineer uses this skill
`engineer.md` Step 5 requires the Engineer to `Read` this file before its first `Edit`/`Write`
in an Implementation phase and to apply the checklist to its plan and again to its diff.
This file stays the single source of the checklist; `engineer.md` points here instead of
copying it. No tool is granted: the Engineer reads it with the `Read` tool it already
has, so its allowlist and permission boundary are unchanged.
