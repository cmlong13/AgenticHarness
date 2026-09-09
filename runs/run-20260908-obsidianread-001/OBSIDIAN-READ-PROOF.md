# Obsidian READ boundary — earlier search/read DIAGNOSTIC (2026-09-08)

**Status: superseded as the acceptance proof.** The privacy-safe live-read acceptance
proof is `runs/run-20260908-obsidianread-002/` (a targeted `read_obsidian_note` with **no
vault-wide search**). This run is retained unmodified as historical evidence of what
actually occurred and as the first live exercise of the read boundary; its prose summary
below has been corrected (2026-09-08 audit) so it does **not** overstate the privacy
outcome. The retained evidence files under this directory
(`obsidian/read/search-1.json`, `obsidian/read/read-1.json`,
`logs/policy-events.jsonl`, `requests/`) are the actual record and are **not** modified.

Dedicated harness fixture run (`run-20260908-obsidianread-001`, task
`OBSIDIAN-READ-001`) — a real `live_cli.py` CLI demonstration, not a `/work` pipeline run
and not an Architect-in-pipeline demonstration.

## What was done (READ-ONLY — no writes to the vault at any point)

1. `OBSIDIAN_VAULT_PATH` was set (locally, for the invocation only — never committed) to
   the real configured Obsidian vault at `C:\Users\caleb\Documents\Obsidian Vault`.
2. `search_obsidian` (`phase: "research"`, query
   `"run-20260908-obsidianlive-001 publication boundary"`) was invoked through the
   production `obsidian_reader.search`. Result: `status: "found"`, exit code 0,
   `files_scanned: 2`, exactly **one** *matched* note:
   `Harness Run Summaries/run-summary-run-20260908-obsidianlive-001.md`.
3. `read_obsidian_note` on that same harness-owned note. Result: `status: "read"`, exit
   code 0, `content_sha256: 5e017d2de6e3aea81aad60001061d75739584e44fd1e9d7c4dfcf25e0c198679`,
   `byte_count: 1552` — independently verified against that exact file.

## Precise privacy accounting (2026-09-08 audit — corrects the original claim)

The original version of this doc said "only the one harness-owned note was accessed."
**That was overstated.** `search_obsidian` performs a *vault-wide* text search: by
construction `obsidian_reader.search` opens and reads (`read_bytes`) every eligible `.md`
file in the configured vault to compute a match score. Distinguishing the three things
the original prose blurred:

- **Files the search inspected (opened):** 2. The configured vault contained exactly two
  eligible `.md` files — the harness-owned note above, **and one unrelated personal note**,
  `Computer Science/CSC340/00 - CSC 340 Dashboard.md.md`. The search opened both. That
  unrelated file is a **0-byte / empty file**, so zero personal-content bytes actually
  existed to be read, but the implementation did `read_bytes()` it — it *was* inspected.
- **Files returned as matches:** 1 (the harness-owned note; the empty file scored 0 and
  was dropped before any `SearchMatch`/snippet was built).
- **Files whose content was retained in evidence:** 1 (only the harness-owned note's
  path + bounded snippet, in `search-1.json` and the `obsidian_read` policy event).

**No unrelated note content was retained, logged, or surfaced anywhere.** `search-1.json`
and `logs/policy-events.jsonl` contain only the matched harness note. `files_scanned: 2`
is a count only. This is **not** a data-retention leak. It *is* an inspection of an
unrelated personal file that the user's live-proof instruction asked us to avoid — which
is why `run-002` (targeted read, no search) is the acceptance proof.

## Verification

- `read_obsidian_note` `content_sha256` / `byte_count` match an independent
  `hashlib.sha256` / byte count of that exact file, and the write-back milestone's own
  recorded SHA-256.
- After the run: the vault's `Harness Run Summaries/` folder still holds exactly the one
  harness note, size and SHA-256 unchanged — no write, no new file, no `.tmp` artifact.

## Retained evidence (unmodified)

- `obsidian/read/search-1.json` — `found`, `files_scanned: 2`, the single returned match.
- `obsidian/read/read-1.json` — `read`, note identity + un-truncated body.
- `logs/policy-events.jsonl` — two `obsidian_read` events.
- `requests/OBSR-1.json`, `requests/OBSR-2.json`.

## What this proves / does not prove

- **Proves:** the production read boundary genuinely searches and reads a real configured
  vault, resolves `OBSIDIAN_VAULT_PATH` fresh, retains note identity, reports
  `found`/`read` only on a real hit, and performs no writes.
- **Does not prove (and is not used as):** a privacy-safe acceptance proof — see
  `run-002`. Nor a full `/work` Architect-in-pipeline read (still open). Nor anything
  about MCP + REST (still open, separate).
