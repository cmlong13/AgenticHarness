"""Negative-evidence baseline for the clean sibling clone used by run-20261002-obsidianlive-004.
Read-only: searches every non-.git text file in the clone and records git state."""
import datetime, json, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1])
out = Path(sys.argv[2])
git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True).stdout.strip()

files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts
         and "__pycache__" not in p.parts]
texts = {}
for p in files:
    try:
        texts[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        pass

terms = ["run-20261001-obsidianlive-002", "obsidianlive-002", "obsidianlive-003", "T-OBSLIVEA", "T-OBSLIVEB",
         "LFCONV-INTGUARD-01", "LFCONV", "test_non_integer_and_bool_days_past_due_is_rejected",
         "placed before the unchanged negative check", "run-summary-run-20261001",
         "loanflow-integer-input-convention", "Engineering Notes"]
negative = {t: sorted(k for k, v in texts.items() if t in v) for t in terms}

late_fee = []
for k, v in sorted(texts.items()):
    if "late_fee" in v:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", k], cwd=root, capture_output=True).returncode == 0
        late_fee.append({"file": k, "tracked": tracked, "last_commit": git("log", "-1", "--format=%cI", "--", k)})

record = {
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "clean_root": str(root),
    "head": git("rev-parse", "HEAD"),
    "origin": git("remote", "get-url", "origin"),
    "status_short": git("status", "--short"),
    "ignored_local_additions": [l for l in git("status", "--short", "--ignored").splitlines() if l.startswith("!!")],
    "text_files_scanned": len(texts),
    "negative_checks": {t: {"file_count": len(v), "files": v} for t, v in negative.items()},
    "runs_obsidianlive_dirs": sorted(p.name for p in (root / "runs").iterdir() if "obsidianlive" in p.name),
    "late_fee_occurrences_all_committed_before_run_a": late_fee,
    "run_a_started_at": "2026-10-01T22:00:10Z",
    "late_fee_tier_has_isinstance_guard": "isinstance" in texts.get("demo-repo/src/loanflow/late_fee_tier.py", ""),
    "memory_identical_to_head": subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "memory"], cwd=root).returncode == 0,
}
out.write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps({k: record[k] for k in ("head", "origin", "status_short", "ignored_local_additions", "text_files_scanned",
                                          "runs_obsidianlive_dirs", "late_fee_tier_has_isinstance_guard",
                                          "memory_identical_to_head")}, indent=1))
for t, v in record["negative_checks"].items():
    print(f"{v['file_count']:>3}  {t}  {v['files'][:3]}")
for e in late_fee:
    print("late_fee:", e)
