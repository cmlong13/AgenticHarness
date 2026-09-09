# Obsidian Discovery/Research READ boundary — privacy-safe live proof (2026-09-08)

Dedicated harness fixture run (`run-20260908-obsidianread-002`, task
`OBSIDIAN-READ-002`). **This is the primary privacy-safe live-read acceptance proof.**
It replaces the earlier `run-20260908-obsidianread-001/`, which is retained unmodified as
historical evidence but is a *search/read diagnostic*, not a privacy-safe acceptance
proof — see "Why run-001 is not the acceptance proof" below.

Not a live four-phase `/work` pipeline run and not an Architect-in-pipeline
demonstration — this is a real `live_cli.py` CLI demonstration of the production
`read_obsidian_note` boundary through the exact operation `/work`'s "# Obsidian
Discovery/Research consultation" step calls, deliberately performed **without any
vault-wide search**.

## What was done — direct read of one known file only, no search, no write

1. `OBSIDIAN_VAULT_PATH` was set (locally, for the invocation only — never committed) to
   the real configured Obsidian vault at `C:\Users\caleb\Documents\Obsidian Vault`.
2. `python -m harness.orchestrator.live_cli --request-file
   runs/run-20260908-obsidianread-002/requests/OBSR-1.json` was invoked. The request is
   `read_obsidian_note`, `phase: "research"`, `note_path` = exactly
   `Harness Run Summaries/run-summary-run-20260908-obsidianlive-001.md` — the
   harness-owned note created by the previous write-back milestone.
3. **No `search_obsidian` call was made.** `read_obsidian_note` opens exactly the one
   requested file (via `obsidian_reader.safe_note_target` path validation, then a single
   `read_bytes()` on that resolved path). It performs no directory traversal, walks no
   `.md` index, and inspects no other file in the vault.
4. Result: `status: "read"`, exit code 0.

## Independent verification (outside the harness)

Before the harness ran, an independent `hashlib.sha256` of the note's UTF-8 bytes and its
byte count were computed directly by reading exactly that one file:

- `byte_count`: **1552**
- `sha256`: **5e017d2de6e3aea81aad60001061d75739584e44fd1e9d7c4dfcf25e0c198679**

The `read_obsidian_note` result's `content_sha256` equals that value exactly, its
`byte_count` is `1552`, its `absolute_path` is the real vault note, its `line_count` is
`39`, and its `content` is byte-for-byte the real note body. The same SHA-256 was
recorded by the write-back milestone
(`runs/run-20260908-obsidianlive-001/obsidian/summary-publication.json`).

After the run: the vault's `Harness Run Summaries/` folder still holds exactly the one
harness note, size `1552`, SHA-256 unchanged — **no write, no new file, no `.tmp`
artifact.**

## Retained evidence

- `runs/run-20260908-obsidianread-002/obsidian/read/read-1.json` — operation, phase,
  requested note path, `read` classification, attempted timestamp, connector
  (`filesystem_vault`), the vault identity (path — not a credential), the note's
  vault-relative path, absolute path, full-file SHA-256, byte count, line count, and the
  (un-truncated) body.
- `runs/run-20260908-obsidianread-002/logs/policy-events.jsonl` — one `obsidian_read`
  event.
- `runs/run-20260908-obsidianread-002/requests/OBSR-1.json` — the single request.

## The search mechanism is proven separately, by deterministic tests

`search_obsidian` / `obsidian_reader.search` is a *vault-wide* text search: by
construction it opens and reads every eligible `.md` file in the configured vault to
compute a match score. That behaviour is fully exercised by deterministic tests
(`tests/test_orchestrator_obsidian_reader.py::TestSearch`,
`::TestEligibleFileWalkContainment`, and
`tests/test_orchestrator_live_cli.py::TestSearchObsidian`) against throwaway pytest
vaults — never a personal one. This live proof **intentionally** exercises only the
targeted `read_obsidian_note` boundary, so no unrelated personal note in the user's real
vault is opened.

## Why run-001 is not the acceptance proof

`run-20260908-obsidianread-001/` ran a real `search_obsidian` over the user's real vault
first. That search scanned `files_scanned: 2` eligible `.md` files — the harness-owned
note **and** one unrelated personal note
(`Computer Science/CSC340/00 - CSC 340 Dashboard.md.md`, which is a 0-byte / empty file).
`search_obsidian` opens every eligible file to score it, so the unrelated file *was*
opened by the implementation (an empty read — zero personal-content bytes existed).
**Nothing unrelated was retained, logged, or surfaced** — the unrelated file scored 0 and
was dropped before any snippet was built; `search-1.json` and its policy event contain
only the matched harness note. So run-001 is not a data-retention leak, but it did
*inspect* an unrelated personal file, which is why run-002 (this run) — a targeted read
with no search — is the privacy-safe acceptance proof.

## What this proves / does not prove

- **Proves:** the production `read_obsidian_note` boundary genuinely reads a single named
  note from a real configured Obsidian vault, resolves the destination fresh from
  `OBSIDIAN_VAULT_PATH`, validates the path, retains independent evidence identifying the
  note (path + full-file SHA-256 + byte count), reports `read` only on a real hit,
  performs **no** write and **no** traversal, and — because no search ran — opened no
  file in the vault other than the one explicitly requested.
- **Does not prove:** a full four-phase `/work` run in which the orchestrator consults
  the vault during Discovery/Research and injects the result into a live Architect
  dispatch. That full Architect-in-pipeline read demonstration remains **open**.
- **Unchanged and still separate:** the MCP + REST dual-connector requirement remains its
  own open milestone.
