# Late Fee Tier Policy (Demonstration Fixture)

> **NOTICE — planted demonstration fixture, disclosed.** This document is part of a
> controlled, transparent harness demonstration of the bounded same-run
> Quality-Engineer logic-failure route-back loop (see `PROJECT_SPEC.md` / `ASSIGNMENT.md`,
> task `T-LATEFEE` / `T-LATEFEE2`, runs `run-20260805-latefee-004` (closed `blocked` on an
> unrelated transport-repair budget issue before Verification was ever reached) and
> `run-20260805-latefee-005` (the continuation attempt)). It intentionally states the
> SEVERE-tier boundary one day later than this task's actual required behavior
> (`demo-repo/tests/unit/test_late_fee_tier_regression.py`, already present in the
> repository, is the binding regression bar for that boundary). An implementation that
> follows this document's wording literally, in isolation, will not satisfy that
> regression fixture — that mismatch is the deliberate, disclosed defect this run
> demonstrates the harness routing back to the Engineer for repair. This is not a real
> production specification and no real loanflow feature depends on it.

## Tiers

Late fee tiers are determined by `days_past_due` at the time a payment is evaluated:

- **`grace`** — 0 to 5 days past due, inclusive. No late fee applies.
- **`standard`** — 6 to 30 days past due, inclusive.
- **`severe`** — strictly more than 30 days past due (`days_past_due > 30`).

A negative `days_past_due` is invalid input.

## Required interface

Implement `classify_late_fee_tier(days_past_due: int) -> str` in a new, isolated module,
returning exactly one of `"grace"`, `"standard"`, `"severe"` per the boundaries above, and
raising `loanflow.errors.ValidationError` for a negative `days_past_due` — reusing the
existing exception type already used for this kind of input check elsewhere in the
repository (e.g. `classify_credit_score` in `demo-repo/src/loanflow/risk_bands.py`),
rather than introducing a new exception type.
