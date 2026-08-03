# loanflow (Agentic Harness demo target)

An educational loan-application and underwriting pipeline used as the
target repository for live Agentic Harness demonstrations (Discovery,
Architect research, Engineer TDD implementation, Quality Engineer
verification, health signals, and controlled defect injection).

**This is a teaching/demo application only.** It is not legally
compliant, not financially authoritative, and must never be used to
process real personal information or make real lending decisions. All
identifiers are synthetic.

## Module map

| Module | Responsibility |
|---|---|
| `errors.py` | Exception hierarchy. |
| `primitives.py` | `Money`, `Percentage`, and an injectable `Clock`. |
| `applicants.py` | `Applicant` / `Business` records. |
| `applicant_validation.py` | Synthetic reference-ID format checks. |
| `applications.py` | `LoanApplication` and its status lifecycle. |
| `repository.py` | In-memory application storage. |
| `income.py` | Income/debt line items. |
| `dti.py` | Debt-to-income calculation. |
| `collateral.py` | Collateral and loan-to-value calculation. |
| `risk_bands.py` | DTI/LTV/credit boundary classification. |
| `risk_profile.py` | Aggregate `RiskProfile`. |
| `underwriting_rules.py` | Individual underwriting rules. |
| `underwriting_engine.py` | Runs the registered rules. |
| `decisions.py` | Turns rule results into approve/refer/decline. |
| `explanations.py` | Human-readable decision explanation text. |
| `audit.py` | Append-only audit event log. |
| `health.py` | Decision-distribution and config-freshness health checks. |
| `config.py` / `config_loader.py` | Threshold configuration and its JSON loader. |
| `pipeline.py` | Wires the whole flow together. |
| `cli.py` | `loanflow decide <application.json>` entry point. |

## Running the tests

No new virtual environment and no editable install are required — the
demo's `pyproject.toml` sets `pythonpath = ["src"]`, so pytest resolves
`loanflow` imports directly.

From the AgenticHarness repo root, using the existing root `.venv`:

```
.venv\Scripts\python.exe -m pytest demo-repo/tests -q
```

Or in the shape the test-runner skill will use later (cwd inside
`demo-repo/`):

```
cd demo-repo
..\.venv\Scripts\python.exe -m pytest tests -q
cd ..
```

## Running the CLI

Plain `python -m loanflow.cli` does not pick up pytest's `pythonpath =
["src"]` setting (that setting only applies inside pytest runs), so
`demo-repo/src` must be put on `PYTHONPATH` explicitly. From the
AgenticHarness repo root, using the existing root `.venv` — no editable
install:

PowerShell:
```
$env:PYTHONPATH = "demo-repo/src"
.venv\Scripts\python.exe -m loanflow.cli decide demo-repo/tests/fixtures/applicant_basic.json
```

Bash:
```
PYTHONPATH=demo-repo/src .venv/Scripts/python.exe -m loanflow.cli decide demo-repo/tests/fixtures/applicant_basic.json
```

## Filesystem-write rule (important for future harness runs)

The Agentic Harness test-runner hashes every file under the target
repository before and after each test run and treats any persistent
change as a policy violation. Because of this:

- All application behavior is in-memory by default (`repository.py`,
  `audit.py`'s `AuditLog` never write anything unless `export_json()` is
  called explicitly).
- Every test that exercises `AuditLog.export_json()` passes pytest's
  `tmp_path` fixture, never a path inside this repository.
- No test may rewrite a fixture, source, configuration, or documentation
  file.

## Future demonstration readiness

The modules below are clean at this baseline (no planted defects) but are
natural, isolated locations for later controlled demonstrations:

1. **Logic bug** — a boundary-comparison change in `risk_bands.py`
   (`classify_dti` / `classify_ltv`) or `dti.py`/`collateral.py`.
2. **Infrastructure flake** — a future deterministic transient fixture
   exercised through `health.py`'s checks.
3. **Health signal** — `health.check_decision_distribution` and
   `health.check_config_freshness` are already implemented; a
   demonstration only needs a skewed decision batch or a stale
   `effective_date`, not a code change.
4. **Trivial, Research-skippable task** — a CLI help-text or docstring
   fix in `cli.py`.
5. **Honest "Not Found"** — the manual-override workflow described in
   `docs/underwriting-rules.md` has no implementation anywhere in
   `src/loanflow`.
6. **Minimal-diff rule addition** — a new rule reusing the existing
   `Rule` function shape in `underwriting_rules.py`.

No defects are planted in this baseline.
