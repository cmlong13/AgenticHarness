# Underwriting Rules (Educational Demo)

`loanflow` is an educational demonstration of a small loan-underwriting
pipeline built for Agentic Harness live demonstrations. It is **not**
legally compliant, **not** financially authoritative, and must **never**
be used to process real personal information or make real lending
decisions.

## Signals

- **Debt-to-income (DTI)** — total monthly debt payments divided by total
  monthly income (`loanflow.dti.calculate_dti`).
- **Loan-to-value (LTV)** — requested loan amount divided by appraised
  collateral value (`loanflow.collateral.calculate_ltv`).
- **Credit score bucket** — a deterministic, synthetic bucketing of a
  supplied credit score into `poor` / `fair` / `good` / `excellent`
  (`loanflow.risk_bands.classify_credit_score`). No real credit bureau is
  contacted; the score is caller-supplied demo data.

Each signal is classified into a `low` / `medium` / `high` risk band (or
bucket) using the thresholds in `config/thresholds.json`
(`loanflow.risk_bands`), then combined into an overall `RiskProfile`
(`loanflow.risk_profile`).

## Rules

Three rules run against every `RiskProfile` (`loanflow.underwriting_rules`):

| Rule | Pass | Borderline | Hard fail |
|---|---|---|---|
| `max_dti_rule` | DTI in low/medium band | DTI in high band | DTI above `max_dti_hard_fail` |
| `max_ltv_rule` | LTV in low/medium band | LTV in high band | LTV above `max_ltv_hard_fail` |
| `min_credit_score_rule` | good/excellent bucket | fair bucket | below `min_credit_score`, or poor bucket |

## Decision

`loanflow.decisions.recommend` combines the rule results:

- Any hard fail → **decline**.
- Otherwise, any borderline → **refer** (manual review).
- Otherwise → **approve**.

`loanflow.explanations.explain` renders the decision and every rule result
as human-readable text.

## Adding a rule

A new rule is a function with the same signature as the existing ones —
`(profile: RiskProfile, thresholds: Thresholds) -> RuleResult` — added to
`UNDERWRITING_RULES` in `loanflow/underwriting_rules.py`. No other module
needs to change; `UnderwritingEngine` and `recommend` are rule-agnostic.

## Manual override (not implemented)

Some underwriting systems support a manual-override workflow, where a
reviewer can approve an application over a rule's decline recommendation
with a recorded justification. **`loanflow` does not implement this.**
There is no override model, no override function, and no code path
anywhere in `src/loanflow` that accepts or applies one — a decline
recommendation from `recommend()` is final within this demo. This is
mentioned here only so the absence is documented rather than silently
assumed; it is not a roadmap commitment.
