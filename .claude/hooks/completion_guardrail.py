#!/usr/bin/env python3
"""Stop hook -- blocks the orchestrator from ending its turn on a false or unverifiable
completion claim for a `/work` run.

Registered in .claude/settings.json for the "Stop" event. Claude Code invokes this script
whenever the main session is about to stop responding, with one JSON object on stdin
(session_id, transcript_path, cwd, hook_event_name: "Stop", stop_hook_active). Exit code 2
blocks the stop (Claude must continue instead of ending the turn) and this script's
stderr text is shown as the reason; exit code 0 allows the stop.

## Scope: only runs that actually claimed completion

This hook must never interfere with an ordinary conversation turn that has nothing to do
with `/work` -- it only ever activates for a run the orchestrator itself marked as claiming
completion. Per `work/SKILL.md`'s "Reporting" section, the orchestrator writes
`runs/<run_id>/.completion_claim.json` (`{"task_id": ..., "run_id": ...}`) as its last step
before reporting a run finished to the user. If no such marker exists anywhere under
`runs/`, this hook does nothing (exit 0 immediately) -- the common case for every turn that
is not a `/work` completion report.

`stop_hook_active` (set by Claude Code when this hook itself already forced a continuation
on a prior attempt to stop in the same turn) always short-circuits to exit 0 -- this hook
must never be able to create an infinite must-continue loop.

## Checks (first failure wins -- most fundamental evidence gap reported first)

For each `.completion_claim.json` found:
1. `runs/<run_id>/verification-report.json` is missing.
2. It exists but is not valid JSON, or (when `jsonschema` is importable via
   `harness.evidence`) fails schema/semantic validation against
   `verification-report.schema.json` and the promoted `scope.json`.
3. Its `final_verdict` is not exactly `"pass"`.
4. `runs/<run_id>/run-summary.json`'s `artifact_refs` name canonical artifacts
   (`scope.json`, `findings.json`, the promoted implementation report, the promoted
   verification report) that do not all exist, parse, and share this run's own
   `task_id`/`run_id` -- a mismatched or missing canonical artifact is exactly as
   disqualifying as a missing verification report.
5. Final Git reality conflicts with the implementation report: a path the (final, possibly
   repaired) implementation report's `changed_files` claims was modified does not actually
   show as modified in `git status --porcelain` for this working tree. This harness never
   commits or pushes (`work/SKILL.md` standing rule 12), so every real change must still be
   a live, uncommitted working-tree modification -- if it isn't, the claim is not backed by
   reality.

A run that survives all five checks is allowed to be reported complete; the marker is
removed (a passing run does not need to keep re-triggering this check every subsequent
turn in the same conversation).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "runs"


def _load_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def _find_claims() -> list[Path]:
    if not RUNS_DIR.is_dir():
        return []
    return sorted(RUNS_DIR.glob("*/.completion_claim.json"))


def _resolve_implementation_report(run_dir: Path, run_summary: dict | None) -> Path | None:
    ref = ((run_summary or {}).get("artifact_refs") or {}).get("implementation_report")
    if ref:
        candidate = REPO_ROOT / ref
        if candidate.is_file():
            return candidate
    fallback = run_dir / "implementation-report.json"
    return fallback if fallback.is_file() else None


def _canonical_artifact_checks(run_dir: Path, run_id: str, task_id: str) -> str | None:
    summary_path = run_dir / "run-summary.json"
    run_summary = _load_json(summary_path)
    if run_summary is None:
        return f"runs/{run_id}/run-summary.json is missing or not valid JSON."

    artifact_refs = run_summary.get("artifact_refs") or {}
    for key in ("scope", "findings", "implementation_report", "verification_report"):
        ref = artifact_refs.get(key)
        if not ref:
            continue  # not every key is required by the schema for every terminal state
        path = REPO_ROOT / ref
        doc = _load_json(path)
        if doc is None:
            return f"artifact_refs.{key} ({ref!r}) does not exist or is not valid JSON."
        if doc.get("task_id") != task_id or doc.get("run_id") != run_id:
            return (
                f"artifact_refs.{key} ({ref!r}) has task_id/run_id "
                f"{doc.get('task_id')!r}/{doc.get('run_id')!r}, which does not match this "
                f"run's claimed {task_id!r}/{run_id!r}."
            )
    return None


def _git_reality_check(run_dir: Path, run_summary: dict | None) -> str | None:
    impl_path = _resolve_implementation_report(run_dir, run_summary)
    if impl_path is None:
        return None  # already caught by the canonical-artifact check above
    impl_doc = _load_json(impl_path)
    if impl_doc is None:
        return None
    changed_files = [c.get("path") for c in impl_doc.get("changed_files", []) if c.get("path")]
    if not changed_files:
        return None

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception as exc:
        return f"could not independently verify Git reality (git status failed: {exc})."

    modified_paths = set()
    for line in result.stdout.splitlines():
        # porcelain format: "XY path" (or "XY orig -> new" for renames) -- the path starts
        # at a fixed column; splitting on the first run of whitespace after the status code
        # is sufficient here since none of this repo's paths contain literal " -> ".
        stripped = line[3:].strip() if len(line) > 3 else ""
        if " -> " in stripped:
            stripped = stripped.split(" -> ", 1)[1]
        modified_paths.add(stripped.replace("\\", "/"))

    for path in changed_files:
        normalized = str(path).replace("\\", "/")
        if normalized not in modified_paths:
            return (
                f"implementation report claims {normalized!r} was changed, but `git status "
                "--porcelain` shows no modification to it -- the completion claim is not "
                "backed by the real working tree."
            )
    return None


def _check_run(run_dir: Path, claim: dict) -> str | None:
    run_id = claim.get("run_id")
    task_id = claim.get("task_id")
    if not run_id or not task_id:
        return "completion claim marker is missing task_id/run_id."

    verification_path = run_dir / "verification-report.json"
    run_summary = _load_json(run_dir / "run-summary.json")
    # A repair cycle promotes its final verification report under a distinct
    # "verification-report.repair-N.json" name (see harness/orchestrator/core.py) --
    # prefer whatever run-summary.json's own artifact_refs actually cites, falling back
    # to the plain name only when no summary/ref exists yet.
    ref = ((run_summary or {}).get("artifact_refs") or {}).get("verification_report")
    if ref:
        verification_path = REPO_ROOT / ref

    if not verification_path.is_file():
        return f"runs/{run_id}/verification-report.json is missing (deleted or never produced)."

    verification_doc = _load_json(verification_path)
    if verification_doc is None:
        return f"{verification_path} exists but is not valid JSON -- verification evidence is invalid."

    try:
        from harness.evidence import (
            load_json as _hload,
            validate_against_schema,
            validate_verification_report_semantics,
        )

        schema = _hload(REPO_ROOT / "harness" / "schemas" / "verification-report.schema.json")
        schema_errors = validate_against_schema(verification_doc, schema, artifact="verification-report")
        if schema_errors:
            return f"verification evidence fails schema validation: {schema_errors[0]}"
        scope_path = run_dir / "scope.json"
        scope_doc = _load_json(scope_path)
        if scope_doc is not None:
            semantic_errors = validate_verification_report_semantics(verification_doc, scope_doc)
            if semantic_errors:
                return f"verification evidence fails semantic validation: {semantic_errors[0]}"
    except Exception:
        pass  # jsonschema/harness.evidence unavailable in this environment -- degrade, don't crash

    if verification_doc.get("final_verdict") != "pass":
        return f"verification-report.json's final_verdict is {verification_doc.get('final_verdict')!r}, not 'pass'."

    reason = _canonical_artifact_checks(run_dir, run_id, task_id)
    if reason:
        return reason

    reason = _git_reality_check(run_dir, run_summary)
    if reason:
        return reason

    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0

    if payload.get("stop_hook_active"):
        return 0  # never create an infinite must-continue loop

    claims = _find_claims()
    if not claims:
        return 0  # no /work run ever claimed completion in this conversation -- nothing to guard

    blocked_reasons = []
    resolved_claims = []
    for claim_path in claims:
        claim = _load_json(claim_path)
        if claim is None:
            blocked_reasons.append(f"{claim_path} is not valid JSON.")
            continue
        run_dir = claim_path.parent
        reason = _check_run(run_dir, claim)
        if reason:
            blocked_reasons.append(f"run_id={claim.get('run_id')!r}: {reason}")
        else:
            resolved_claims.append(claim_path)

    for claim_path in resolved_claims:
        claim_path.unlink(missing_ok=True)  # a genuinely passing run does not keep re-triggering this check

    if blocked_reasons:
        sys.stderr.write(
            "completion-guardrail: this run cannot be reported complete:\n- "
            + "\n- ".join(blocked_reasons)
            + "\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
