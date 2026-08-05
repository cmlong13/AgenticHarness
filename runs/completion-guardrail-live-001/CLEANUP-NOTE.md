# Cleanup action taken after the live Stop-hook block

**When:** immediately after the block message in `STOP-HOOK-LIVE-BLOCK-EVIDENCE.md` was
captured, same session, 2026-08-05.

**Action:** `runs/completion-guardrail-live-001/.completion_claim.json` was renamed to
`runs/completion-guardrail-live-001/.completion_claim.json.consumed`. Content is
byte-for-byte unchanged (`{"task_id": "DEMO-COMPLETION-GUARDRAIL-1", "run_id":
"completion-guardrail-live-001"}`).

**Why a rename, not a delete or a "fix":**
- `completion_guardrail.py`'s `_find_claims()` only globs
  `runs/*/.completion_claim.json` exactly. Renaming removes it from that glob, so no
  future `Stop` event in this session (or later) is blocked by this fixture — satisfying
  "do not leave a marker that will keep blocking future turns."
- Deleting it outright would destroy evidence of exactly what completion claim was made.
  Renaming preserves the content while deactivating it.
- The alternative the task allows — "restore the fixture to a safe state" — was not
  used, because restoring `verification-report.json` to its canonical path (undoing the
  move-aside) would erase the deliberately invalid condition this fixture exists to
  demonstrate. The invalid condition is retained: `verification-report.json` remains
  absent from its canonical path; the genuine baseline it was copied from lives only at
  `verification-report.json.moved-aside`.

**Verification no active marker remains:**
```
$ find runs -name ".completion_claim.json"
(no output)
```
Confirmed empty immediately after the rename (see main report for the full command
output). `runs/completion-guardrail-live-001/.completion_claim.json.consumed` is not
matched by that glob and was independently confirmed present but inert.
