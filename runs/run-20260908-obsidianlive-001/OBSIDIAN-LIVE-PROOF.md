# Obsidian run-summary publication — live proof (2026-09-08)

Dedicated harness fixture run (`run-20260908-obsidianlive-001`, task
`OBSIDIAN-LIVE-001`). **Not** a live four-phase `/work` pipeline run — this is a real
`live_cli.py` CLI demonstration of the Obsidian publication boundary through the exact
operation `/work`'s "Obsidian run-summary publication" step calls, the same substitution
pattern the memory-loop and usage-accounting milestones used for their own live proofs.

## What was done

1. A real, schema-shaped `run-summary.json` was written for this fixture run
   (`final_verdict: "blocked"` — the intentional-non-pipeline convention this repository
   already uses for dry-run / demonstration boundaries; its `objective_summary` states
   plainly that this is a connector demonstration, not a completed pipeline).
2. `OBSIDIAN_VAULT_PATH` was set (locally, for the invocation only — never committed) to
   the real configured Obsidian vault at `C:\Users\caleb\Documents\Obsidian Vault`.
3. `python -m harness.orchestrator.live_cli --request-file
   runs/run-20260908-obsidianlive-001/requests/OBS-1.json` was invoked — the exact
   production path, using `harness.orchestrator.obsidian.DEFAULT_WRITER` (the real
   atomic filesystem write). The simulated `VaultWriter` seam is never reachable through
   this boundary.
4. Result: `status: "published"`, exit code 0.

## Independent verification (outside the harness)

- The note exists at
  `C:\Users\caleb\Documents\Obsidian Vault\Harness Run Summaries\run-summary-run-20260908-obsidianlive-001.md`
  (1552 bytes), confirmed by a direct `ls` of the destination folder.
- An independent `hashlib.sha256` of the file's UTF-8 content equals the
  `content_sha256` recorded in the publication evidence:
  `5e017d2de6e3aea81aad60001061d75739584e44fd1e9d7c4dfcf25e0c198679`.
- The vault's existing personal content (`Computer Science/CSC340`) was untouched; the
  `Harness Run Summaries/` folder was newly created for this note.

## Retained evidence

- `runs/run-20260908-obsidianlive-001/obsidian/summary-publication.json` — status,
  destination identity (vault path, summary dir, vault-relative note path — a vault
  path is not a credential; no secret is retained), note name, connector
  (`filesystem_vault`), attempted timestamp, absolute written path, SHA-256, byte
  count.
- `runs/run-20260908-obsidianlive-001/logs/policy-events.jsonl` — one
  `obsidian_publication` event.

## What this proves / does not prove

- **Proves:** the production Obsidian publication path (`obsidian.py`,
  `publish_run_summary`, the real filesystem `VaultWriter`, the `obsidian` Skill) genuinely
  writes a concise run-summary note into a real configured Obsidian vault, retains
  independent evidence identifying the published note, and reports `published` only on a
  real write.
- **Does not prove:** a full four-phase `/work` run that naturally reaches the Obsidian
  publication step. That remains open, exactly as the equivalent open item stands for
  the route-back/hooks and memory-loop milestones.
