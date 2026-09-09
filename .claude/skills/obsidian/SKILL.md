---
name: obsidian
description: Obsidian procedure for publishing a completed harness run's concise summary
  note to the configured vault -- destination validation, path safety, evidence retention,
  and honest failure classification. Invoked by the orchestrator (/work) after a run's
  terminal run-summary.json is finalized; never grants an independent tool allowlist of
  its own.
---

# Purpose
Hold every Obsidian action `/work` performs to one standard: publish exactly one
concise, auditable run-summary note per terminal run to the *configured* vault, retain
independent evidence of what was attempted and what happened, and never let an Obsidian
delivery failure rewrite a valid pipeline/test result into a fabricated product failure.
This skill does not execute anything itself -- it is the procedure the orchestrator (the
only session that ever writes to Obsidian in this harness; the Architect only *reads* the
vault during Research) follows, using `harness/orchestrator/live_cli.py`'s
`publish_run_summary` operation (`harness/orchestrator/obsidian.py`) for the one real
vault write and every piece of retained evidence.

# The cardinal rule
**A generated Markdown summary is not proof that the note was published to Obsidian.**
Corollaries, all non-negotiable:
- Building the note text, or writing it to a scratch/temp file, is not publication.
  Only a real `publish_run_summary` call returning `status: "published"` -- meaning the
  production `VaultWriter` genuinely wrote the file inside the configured vault
  directory -- may ever be reported as an Obsidian delivery.
- Never claim Obsidian delivery based on writing a local string or file unless that
  file genuinely is the configured vault destination. A local folder that is not the
  vault named by `OBSIDIAN_VAULT_PATH` is never "Obsidian."
- Obsidian publication is a **separate external-delivery claim** from (1) pipeline /
  product correctness, (2) Git delivery status, and (3) Jira intake status. Report it
  as its own line, never folded into the run verdict.
- An Obsidian failure (`connector_unavailable`, `invalid_destination`,
  `destination_unavailable`, `collision`, `write_failed`) never changes
  `final_verdict`, `phases_completed`, or any artifact's validity. A run can genuinely
  be `final_verdict: "pass"` with `obsidian_publication: connector_unavailable`.

# 1. Destination resolution (fresh from the environment, every call)
The vault is named by `OBSIDIAN_VAULT_PATH` (an absolute path), optionally with a
vault-relative subfolder `OBSIDIAN_SUMMARY_DIR` (default `Harness Run Summaries`). Both
are read fresh from the real environment on every call
(`obsidian.resolve_destination`) -- never cached, never accepted as a request field.
When `OBSIDIAN_VAULT_PATH` is unset or blank, the outcome is a genuine, honest
`connector_unavailable` -- **not a fabricated success, not a silent fallback, not a
substitution of some other local folder.** No `.mcp.json` / `mcp-obsidian` server is
configured in this environment; `ASSIGNMENT.md` names `mcp-obsidian` only as an example
(`e.g.`), and its own §2.4 caveat is that an MCP server is not automatically the right
tool -- this repository already made GitHub and Jira real REST/CLI connectors, not MCP
servers, for that reason. This is deliberately **not** the MCP-vs-REST dual
implementation `ASSIGNMENT.md` §2.4 requires "for at least one connector" -- Obsidian is
never named as that connector, and that requirement remains its own future milestone.

# 2. Path safety
`obsidian.resolve_destination` / `obsidian.safe_note_target` reject:
- a missing destination -> `connector_unavailable`;
- a configured vault path that is not absolute, or contains `..` -> `invalid_destination`;
- a summary subfolder that is absolute or contains `..` -> `invalid_destination`;
- an unsafe note name (path separator, leading dot, `..`, not ending `.md`, over-long)
  -> `invalid_destination`;
- a fully-resolved note target that escapes the configured vault directory (symlink or
  traversal) -> `invalid_destination`;
- a configured vault path that does not resolve to an existing directory ->
  `destination_unavailable`.
Only a bare, safe `<name>.md` filename inside `<vault>/<summary_dir>/` is ever written.

# 3. Note naming
One canonical shape: `run-summary-<run_id>.md` (`obsidian.build_note_name`). `run_id`
already matches the harness identifier pattern, so this is always a safe filename;
`safe_note_target` re-checks it regardless. One note per run.

# 4. Summary structure (concise and auditable -- never a log dump)
`obsidian.render_run_summary_note` builds the note strictly from the run's own already
retained evidence (`run-summary.json` plus the scope / verification-report /
usage-summary / Jira / Git evidence it references, best-effort). It includes: run ID;
task ID; source mode (`prompt` / `jira` / `unknown`); short objective; final verdict;
phases completed (and phases reused, for a resumed run); Discovery / Research /
Implementation / Verification / checkpoint artifact references; test commands, exit
codes, and verification verdict; whether memory influenced the run and how many lessons
were appended; a per-agent usage/cost subtotal reference (never described as full
pipeline cost); a Jira source reference when the run was ticket-mode; a Git
push-verification status when a push occurred; evidence gaps (remaining risks, follow-up
actions, unmeasured orchestrator usage); and the generation timestamp. It never emits
credentials, tokens, authorization headers, hidden agent transcripts, raw command logs,
or full artifact bodies -- only whitelisted scalar fields. Missing optional evidence
yields an omitted or explicitly-noted section, never a fabricated value. Output is
deterministic (the only timestamp is the caller-supplied `generated_at`) and
Unicode-safe.

# 5. Collision / update policy
`publish_run_summary` refuses to overwrite an existing note (`status: "collision"`)
unless `overwrite: true` is explicitly passed. The default for a normal terminal run is
no-overwrite: one run, one note. A deliberate re-publish (e.g. after a corrected
run-summary) passes `overwrite: true` and a distinct evidence `filename`
(`summary-publication.retry-1.json`), mirroring the `implementation-report.repair-1.json`
convention -- the original publication evidence is never rewritten.

# 6. Connector boundary (real vault write vs. test double)
The one real publication mechanism is a filesystem write of the Markdown note into the
configured vault directory, through a swappable `VaultWriter` seam
(`obsidian.DEFAULT_WRITER` -- an atomic temp-file + `os.replace` write). Deterministic
tests inject a fake `VaultWriter` at that exact seam to exercise `published` /
`collision` / `write_failed` without touching a real vault. Production
(`live_cli.py`'s `op_publish_run_summary`) always uses `DEFAULT_WRITER`; the simulated
seam is never exposed through the live CLI boundary, so a live `/work` run can never
fabricate a publication. Never write a fake connector and call it live integration; if
no real vault is configured, `connector_unavailable` is the honest outcome and the
report says so.

# 7. Retained evidence (unconditional, every classification)
Every call to `publish_run_summary` retains
`runs/<run_id>/obsidian/summary-publication.json`
(`evidence_io.retain_obsidian_evidence`, collision-guarded) and a matching
`obsidian_publication` policy event, **regardless of the outcome**. The record carries:
run ID; task ID; destination identity (configured vault path, summary dir, vault-relative
note path -- a vault path is not a credential); note name; publication status; attempted
timestamp; connector/method (`filesystem_vault`); and the reason/error on any non-
`published` outcome. On `published`, it additionally carries the absolute written path,
the note's SHA-256, and the byte count -- enough to independently identify and verify
the published note. No credential or secret is ever involved or retained.

# 8. Failure classification (every outcome is one of these six)
| status | meaning | affirmative? |
|---|---|---|
| `published` | the note was genuinely written into the configured vault | **yes -- the only one** |
| `connector_unavailable` | `OBSIDIAN_VAULT_PATH` is not set -- no vault is configured | no |
| `invalid_destination` | the configured vault path / summary dir / note name is structurally unsafe, or the resolved note target escapes the vault | no |
| `destination_unavailable` | the configured vault path is well-formed but does not resolve to an existing directory | no |
| `collision` | a note already exists at the target and `overwrite` was not set | no |
| `write_failed` | the vault write itself failed (permission denied, disk error) or the rendered note exceeded the concise-summary ceiling | no |

Only `published` may ever be treated as publication success. Report the exact
classification and `reason` for anything else -- never soften it into "the note should
be there" or "probably published."

# 9. Terminal-`/work` placement
Publication happens once, only after the harness has enough retained information to
generate an accurate summary: pipeline reaches a terminal state -> usage summary
finalized -> `run-summary.json` finalized -> Obsidian summary rendered from that retained
evidence -> `publish_run_summary` attempted -> publication evidence retained -> the final
user-facing report states the pipeline verdict and the Obsidian delivery status
separately. See `work/SKILL.md`'s "Obsidian run-summary publication" section for the
exact sequence.

# 10. Relationship to the memory loop
`memory/lessons-learned.md` and an Obsidian run summary are **different requirements**
(`ASSIGNMENT.md` §2.6 vs. §2.4). Existing memory behavior is unchanged by this skill:
the terminal memory-append step still runs, `lessons-learned.md` still gains at most 5
bullets per terminal run, and `facts.jsonl` is still append-only. The Obsidian note is
an outward-facing summary of a single run; `lessons-learned.md` is an inward,
capped, cross-run insight log. Neither is derived from the other.

# Boundaries -- this skill must not
- Decide the run's pipeline verdict, or let an Obsidian outcome influence it.
- Fabricate, soften, or infer a publication result -- only a real `publish_run_summary`
  call's own classification may ever be reported.
- Read the vault (that is the Architect's Research-phase concern), transition or delete
  any existing vault note, or write anywhere other than
  `<vault>/<summary_dir>/run-summary-<run_id>.md`.
- Duplicate the memory loop (`memory/`), the test-runner skill (test execution), the
  github skill (Git/GitHub delivery), or the jira skill (ticket intake).
