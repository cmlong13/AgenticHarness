import json
R = "runs/run-20261001-obsidianlive-003"
p = open(f"{R}/requests/raw_prompt.txt", encoding="utf-8").read()
scope = {
    "schema_version": "1.0", "task_id": "T-OBSLIVEB", "run_id": "run-20261001-obsidianlive-003", "created_at": "2026-10-02T16:21:10Z",
    "source": {"type": "prompt", "raw_prompt": p},
    "objective": "Add a type check to loanflow.applicants.Applicant.__post_init__ (demo-repo/src/loanflow/applicants.py:20-23) so that any years_employed that is not a plain int -- float such as 2.5, and bool True/False (an int subclass) -- raises loanflow.errors.ValidationError before the existing negative check, instead of constructing an Applicant silently. Follow the repository's established integer-guard idiom `isinstance(x, bool) or not isinstance(x, int)` (demo-repo/src/loanflow/risk_bands.py:46, demo-repo/src/loanflow/primitives.py:24 and :54, demo-repo/src/loanflow/late_fee_tier.py:22). This follows the precedent of prior harness run run-20261001-obsidianlive-002 (vault run summary 'Harness Run Summaries/run-summary-run-20261001-obsidianlive-002.md', retained read evidence runs/run-20261001-obsidianlive-003/obsidian/read/read-1.json) and vault note LFCONV-INTGUARD-01 (read-2.json); the repository corroborates both. Add a pytest.raises(ValidationError) regression test in demo-repo/tests/unit/test_applicants.py.",
    "in_scope": ["demo-repo/src/loanflow/applicants.py", "demo-repo/tests/unit/test_applicants.py"],
    "out_of_scope": [
        "The Business dataclass in demo-repo/src/loanflow/applicants.py (years_in_operation) -- not requested; unchanged",
        "Any change to Applicant.full_name validation or to the existing years_employed negative check",
        "demo-repo/src/loanflow/cli.py and every other caller of Applicant (reference only; unchanged)",
        "demo-repo/src/loanflow/late_fee_tier.py and demo-repo/tests/unit/test_late_fee_tier.py (pre-existing uncommitted changes from run-20261001-obsidianlive-002; must not be touched)",
        "demo-repo/src/loanflow/risk_bands.py, demo-repo/src/loanflow/primitives.py, demo-repo/src/loanflow/errors.py (reference only)",
        "Editing any Obsidian vault note (read-only historical context)",
    ],
    "constraints": [
        "Within demo-repo/src/loanflow/applicants.py, change only Applicant.__post_init__; do not touch Business or restructure the dataclass",
        "Use exactly the repository idiom `isinstance(self.years_employed, bool) or not isinstance(self.years_employed, int)` (as at demo-repo/src/loanflow/primitives.py:24), placed before the existing negative check",
        "Raise loanflow.errors.ValidationError (already imported in applicants.py), never TypeError/ValueError",
        "Existing tests in demo-repo/tests/unit/test_applicants.py must keep passing unmodified (new tests may be added; existing ones not weakened)",
        "No new dependency; stdlib isinstance check only",
        "TDD: the failing regression test must be written and run before the production change",
    ],
    "acceptance_criteria": [
        {"id": "AC-1", "description": "Applicant(applicant_id=\"AP-1\", full_name=\"A\", years_employed=2.5) raises loanflow.errors.ValidationError."},
        {"id": "AC-2", "description": "Applicant(..., years_employed=True) and Applicant(..., years_employed=False) each raise loanflow.errors.ValidationError (bool rejected even though it is an int subclass)."},
        {"id": "AC-3", "description": "Existing behavior is unchanged for plain ints: years_employed=5 and the default 0 construct successfully, years_employed=-1 still raises ValidationError, and an empty full_name still raises ValidationError."},
        {"id": "AC-4", "description": "demo-repo/tests/unit/test_applicants.py contains a new pytest.raises(ValidationError) regression test covering the non-int (float and bool) rejection, and the whole of demo-repo/tests/unit/test_applicants.py passes."},
    ],
    "task_graph": [
        {"id": "N-RESEARCH", "description": "Confirm with file:line citations Applicant.__post_init__'s current body, the ValidationError import, the repository integer-guard idiom, every caller of Applicant (none passing a non-int), and assess vault notes read-1/read-2 as corroborated/stale/contradicted/context-only.", "phase": "research", "depends_on": []},
        {"id": "N-IMPLEMENT", "description": "Write a failing regression test in demo-repo/tests/unit/test_applicants.py first, then add the bool/int type guard to Applicant.__post_init__.", "phase": "implementation", "depends_on": ["N-RESEARCH"]},
        {"id": "N-VERIFY", "description": "Independently re-run demo-repo/tests/unit/test_applicants.py and confirm each acceptance criterion against real command output; full unit suite as a health check.", "phase": "verification", "depends_on": ["N-IMPLEMENT"]},
    ],
    "status": "approved",
}
json.dump(scope, open(f"{R}/attempts/discovery-1-candidate.json", "w", encoding="utf-8"), indent=2)
cand = f"{R}/attempts/discovery-1-candidate.json"
ids = {"expected_task_id": "T-OBSLIVEB", "expected_run_id": "run-20261001-obsidianlive-003", "target_repo_path": "demo-repo"}
reqs = {
    "RETAIN-DISC-1": {"operation": "retain_attempt", "run_id": "run-20261001-obsidianlive-003", "task_id": "T-OBSLIVEB", "phase": "discovery", "attempt_n": 1, "raw_text_ref": cand},
    "VALIDATE-DISC-1": {"operation": "validate_scope", "candidate_ref": cand, **ids},
    "PROMOTE-DISC-1": {"operation": "promote_artifact", "run_id": "run-20261001-obsidianlive-003", "phase": "discovery", "doc_ref": cand, **ids},
}
for k, v in reqs.items():
    json.dump(v, open(f"{R}/requests/{k}.json", "w", encoding="utf-8"), indent=2)
