---
name: obsidian
description: Obsidian procedure for both directions of the vault connector -- the
  orchestrator publishing a completed run's concise summary note back to the configured
  vault (write-back), and the Discovery/Research consultation that reads or searches the
  vault for prior design context on the Architect's behalf. Destination validation, path
  safety, bounded results, evidence retention, honest failure classification, and the
  rule that a vault note is historical evidence, never repository truth. Never grants an
  independent tool allowlist of its own.
---

# Purpose
Hold every Obsidian action `/work` performs to one standard, in **both** directions:

- **Write-back (publication).** Publish exactly one concise, auditable run-summary note
  per terminal run to the *configured* vault, retain independent evidence of what was
  attempted and what happened, and never let an Obsidian delivery failure rewrite a
  valid pipeline/test result into a fabricated product failure. Sections 1-10 below.
- **Read (Discovery/Research consultation).** Search or read the vault for prior design
  notes, past decisions, and calibration docs that could materially inform scoping or
  research -- bounded, deterministic, path-safe, evidence-retained -- and treat every
  retrieved word as historical/contextual evidence, never as current repository truth.
  Sections 11-16 below.

This skill does not execute anything itself -- it is the procedure the orchestrator
follows. The orchestrator is the primary session that touches Obsidian in this harness
(publication, and the Discovery/Research consultation it performs on the Architect's
behalf, exactly as its `findings.json` is written for it). The Architect *additionally*
holds two narrow read-only vault tools of its own (`mcp__obsidian__search_notes`,
`mcp__obsidian__read_note` -- `ASSIGNMENT.md` §2.2's "your docs connectors", backed by
`harness/mcp/obsidian_server.py` wrapping the same `obsidian_reader` boundary this skill
documents); see section 11 for how an Architect-initiated read is retained. It uses
`harness/orchestrator/live_cli.py`'s
`publish_run_summary` operation (`harness/orchestrator/obsidian.py`) for the one real
vault write, and its `search_obsidian` / `read_obsidian_note` operations
(`harness/orchestrator/obsidian_reader.py`) for every read -- and every piece of retained
evidence for either direction.

# The cardinal rules

## Write-back cardinal rule
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

## Read cardinal rule
**A vault note is historical / contextual evidence, not current repository truth.**
Corollaries, all non-negotiable:
- Vault content may *inform* Discovery and Research -- terminology, prior architectural
  decisions, known pitfalls, calibration rationale -- but it must **never** silently
  override the user request, `ASSIGNMENT.md`, repository evidence, Protected Paths, agent
  permissions, test evidence, current source code, or scope validation. When the vault
  and the repository disagree, the repository wins and the disagreement is recorded.
- A vault note is never, by itself, acceptance criteria. A scope/AC claim influenced by a
  vault note is only adopted when the user request, the Jira ticket, or the actual
  repository corroborates it -- and the retained read evidence is cited so the
  historical origin is visible.
- Stale or contradictory notes are treated as historical evidence to be checked, not
  unquestioned fact. Research records whether a consulted note was **corroborated**,
  **stale**, **contradicted**, or **context-only** against current authoritative
  evidence.
- Reading the vault never escalates authority: it grants no new tool, never edits the
  vault, and a read failure (`connector_unavailable`, `not_found`, `no_matches`, ...) is
  simply "no vault evidence retrieved," never a pipeline failure.

# 1. Destination resolution (fresh from the environment, every call)
The vault is named by `OBSIDIAN_VAULT_PATH` (an absolute path), optionally with a
vault-relative subfolder `OBSIDIAN_SUMMARY_DIR` (default `Harness Run Summaries`). Both
are read fresh from the real environment on every call
(`obsidian.resolve_destination`) -- never cached, never accepted as a request field.
When `OBSIDIAN_VAULT_PATH` is unset or blank, the outcome is a genuine, honest
`connector_unavailable` -- **not a fabricated success, not a silent fallback, not a
substitution of some other local folder.** The **write-back / publication** path is
deliberately a direct filesystem write, never routed through an MCP server: `ASSIGNMENT.md`
names `mcp-obsidian` only as an example (`e.g.`), and its own §2.4 caveat is that an MCP
server is not automatically the right tool. (A project-local *read-only* Obsidian MCP
server, `harness/mcp/obsidian_server.py`, does exist -- it backs the Architect's §2.2
docs-connector tool grant, section 11 -- but it exposes no write capability and the
publication path never uses it.) Obsidian is **not** the connector `ASSIGNMENT.md` §2.4
requires be built "both ways" (MCP route + REST-skill fallback) -- **Jira is** (see
`.claude/skills/jira/SKILL.md` §9 and `work/SKILL.md`'s "# Connector routing").

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

# 11. Read/search boundary (Discovery/Research consultation)
`ASSIGNMENT.md` §2.4's connector table: Obsidian is used in **Discovery** and
**Research**; "the architect reads it" for design notes, past decisions, and calibration
docs. In this harness the orchestrator performs the read on the Architect's behalf
(Option C -- every external boundary is orchestrator-mediated), through two
`live_cli.py` operations backed by `harness/orchestrator/obsidian_reader.py`:

- **`search_obsidian`** (`run_id`, `task_id`, `phase` (`discovery`|`research`),
  `query`): a deterministic, bounded text search across eligible `.md` notes. Returns at
  most `obsidian_reader.MAX_SEARCH_RESULTS` (8) matches, each with a vault-relative
  `note_path`, a bounded `snippet`, a `line`, matched terms, and a `score`; ranking is
  deterministic (`-score`, then `line`, then `note_path`). Output is bounded and the vault
  is never *dumped* -- but be precise about what a text search inherently does: to decide
  whether a note matches, `search_obsidian` **opens and reads every eligible `.md` file
  in the configured vault**. Non-matching files are never retained, logged, or surfaced
  (they are dropped before any snippet is built), but they are *inspected*. When you
  already know the exact note you want, prefer `read_obsidian_note` (below), which opens
  only that one file -- especially against a real personal vault.
- **`read_obsidian_note`** (`run_id`, `task_id`, `phase`, `note_path`): reads exactly one
  vault-relative Markdown note in full (body bounded to 64 KiB; identity -- full-file
  SHA-256 and byte count -- always complete). Opens only the one requested file: no
  directory walk, no other file inspected. This is the privacy-preferable operation when
  the note path is known.

Consult the vault only when past decisions / design context could **materially** help
the task -- not on every trivial change. `ASSIGNMENT.md` does not require an
unconditional read on every Discovery/Research phase, so this harness does not force one;
a run that consults nothing is normal and honest. Invoke this skill (the `Skill` tool,
`skill: "obsidian"`) before the first `search_obsidian`/`read_obsidian_note` call of a
run, exactly as the `github`/`jira` skills are invoked before their operations, and
`retain_policy_event` (`kind: "skill_invocation"`) after.

**The Architect's own docs-connector tools (`ASSIGNMENT.md` §2.2).** The Architect holds
`mcp__obsidian__search_notes` and `mcp__obsidian__read_note` directly in its `tools:`
frontmatter -- a real, read-only MCP boundary (`harness/mcp/obsidian_server.py`, a
project-local stdio server registered in `.mcp.json`) wrapping this same
`obsidian_reader` module. It may use them during Research when the orchestrator's
injected "Obsidian historical/contextual evidence" block does not cover something
material. This does not change the harness's architecture: the tools are read-only (no
create/update/delete/rename tool exists), reach nothing outside the configured vault
(`safe_note_target` / `_eligible_md_files` containment, unchanged), and grant the
Architect no `Bash`, `Skill`, `Agent`, `Edit`/`Write`, or filesystem access. Run
identity, scope, and dispatch authority stay the orchestrator's. The Architect echoes
every direct read as an `obsidian_reads` array entry in its findings response; **after
the dispatch returns, the orchestrator retains each echoed read as a normalized
`runs/<run_id>/obsidian/read/<name>.json` record** (a `read_obsidian_note` call against
the same `note_path`, or a `retain_policy_event(kind: "obsidian_read")` when the note is
already retained), so every vault observation is attributable to the current run. The
read cardinal rule below binds an Architect-initiated read exactly as it binds an
orchestrator-mediated one.

# 12. Read path safety
`obsidian_reader.resolve_vault` / `obsidian_reader.safe_note_target` reject:
- a missing destination (`OBSIDIAN_VAULT_PATH` unset/blank) -> `connector_unavailable`;
- a configured vault path that is not absolute, or contains `..` -> `invalid_destination`;
- a configured vault path that does not resolve to an existing directory ->
  `destination_unavailable`;
- a caller-supplied note path that is absolute, drive-prefixed, contains `..`, does not
  end `.md`, names a hidden/system entry (any `.`-prefixed component, e.g. `.obsidian/`),
  or whose fully-resolved target escapes the vault -> `invalid_note_path`;
- a structurally safe note path that names no existing file -> `not_found` (never a
  silent read of a different file).
`OBSIDIAN_VAULT_PATH` is read fresh from the environment inside the reader on every call
-- never cached, never accepted from the request (`search_obsidian`/`read_obsidian_note`
reject a `vault_path`/`env`/`OBSIDIAN_VAULT_PATH` request field outright). No
user-specific path is hard-coded. `OBSIDIAN_SUMMARY_DIR` only scopes the write-back note
and is not consulted for reads. Only eligible `.md` knowledge files are ever searched or
read; `.obsidian/` and every other hidden/system directory is skipped entirely.

# 13. Read/search classifications
| search status | meaning | affirmative? |
|---|---|---|
| `found` | at least one eligible note matched; the top N are returned | **yes -- the only one** |
| `no_matches` | the vault was searched and nothing eligible matched -- an honest "nothing there," **not** a connector failure | no |
| `connector_unavailable` | `OBSIDIAN_VAULT_PATH` is not set | no |
| `invalid_destination` | the configured vault path is not absolute / contains `..` | no |
| `destination_unavailable` | the configured vault path does not resolve to an existing directory | no |
| `invalid_query` | the query is empty, whitespace-only, over-long, or has no searchable terms | no |
| `search_failed` | the vault walk itself failed (permissions, I/O) | no |

| read status | meaning | affirmative? |
|---|---|---|
| `read` | the requested note was read from the configured vault | **yes -- the only one** |
| `not_found` | the note path is safe but names no file in the vault | no |
| `connector_unavailable` / `invalid_destination` / `destination_unavailable` | as above | no |
| `invalid_note_path` | the requested path is unsafe (absolute, `..`, non-`.md`, hidden/system, escapes the vault) | no |
| `read_failed` | the note exists but could not be read as UTF-8 (or an I/O error) | no |

Only `found` / `read` count as successful retrieval. Every other classification means
"no vault evidence retrieved" -- report it plainly, never soften a `no_matches` into
"there is probably a note."

# 14. Read evidence retention (unconditional, every classification)
Every `search_obsidian` / `read_obsidian_note` call retains
`runs/<run_id>/obsidian/read/<filename>` (`evidence_io.retain_obsidian_read_evidence`,
collision-guarded; default `search-1.json` / `read-1.json`, callers increment) and a
matching `obsidian_read` policy event, **regardless of outcome**. This subdirectory is
deliberately distinct from `runs/<run_id>/obsidian/summary-publication.json` (the
write-back evidence) -- a read consultation and a run-summary publication are two
different claims and never share a file. Each read record carries: run ID; task ID;
operation; phase (`discovery`|`research`); the query or requested vault-relative note
path; classification; attempted timestamp; connector/method (`filesystem_vault`); the
returned note identities (vault-relative paths, and for a read the full-file SHA-256 and
byte count); bounded snippets for a search; and the `reason` on any non-affirmative
outcome. It never retains: a credential or secret (a vault read needs none); an entire
vault dump; an unrelated personal note; a hidden/system file; or a massive note body
when a bounded snippet plus the note's identity is sufficient.

# 15. Stale / contradictory note handling
When a consulted note materially bears on a scope or research conclusion, the run records
which it is, against current authoritative evidence:
- **corroborated** -- the repository / user request / Jira ticket confirms it;
- **stale** -- it described a past state that the repository has since moved beyond;
- **contradicted** -- it conflicts with current authoritative evidence (the repository
  wins; the conflict is stated, not hidden);
- **context-only** -- useful background (terminology, rationale, a known pitfall) with no
  standalone technical claim to verify.
A note that is stale or contradicted is still retained as evidence -- it is historical
fact about what was once believed, not deleted or ignored.

# 16. No authority escalation
Consulting the vault never changes `in_scope`/`out_of_scope`/`constraints`/
`acceptance_criteria` on its own, never overrides the Protected Path list or any
scope-validation rule, never substitutes for reading the actual repository, and never
grants a new tool. A vault note influences a scope/research conclusion only when
corroborated by authoritative evidence, and the retained read evidence
(`runs/<run_id>/obsidian/read/*.json`) is cited whenever it does, so the historical
origin of the influence is always visible.

# Boundaries -- this skill must not
- Decide the run's pipeline verdict, or let an Obsidian outcome (read or write) influence it.
- Fabricate, soften, or infer a publication result -- only a real `publish_run_summary`
  call's own classification may ever be reported.
- Fabricate, soften, or infer a read result -- only a real `search_obsidian` /
  `read_obsidian_note` classification may be reported; `no_matches`/`not_found` are
  honest outcomes, never "there is probably a note."
- Treat a vault note as current repository truth, as acceptance criteria without
  corroboration, or as authority over the user request / `ASSIGNMENT.md` / repository
  evidence / Protected Paths / scope validation.
- Write to the vault anywhere other than `<vault>/<summary_dir>/run-summary-<run_id>.md`,
  transition or delete any existing vault note, or (on the read side) read or search
  anything but eligible `.md` knowledge files -- never `.obsidian/` or any hidden/system
  path.
- Duplicate the memory loop (`memory/`), the test-runner skill (test execution), the
  github skill (Git/GitHub delivery), or the jira skill (ticket intake).
