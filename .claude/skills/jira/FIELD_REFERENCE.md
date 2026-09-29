# Jira field reference

This file lists every Jira field the `jira` skill pack reads or writes. Use it so you never
have to guess a field. The code is the source of truth: the constants `READ_FIELDS`,
`CREATE_FIELDS`, `EDIT_FIELDS`, `SUPPORTED_ISSUE_TYPES` and `CUSTOM_FIELD_IDS` in
`harness/orchestrator/jira_connector.py`. The test class
`tests/test_orchestrator_jira_connector.py::TestFieldReference` fails if the tables below
drift from those constants.

## The rule: never guess a custom field id

**No custom field ids are configured in this harness.** `CUSTOM_FIELD_IDS` is an empty
mapping. So:

- An agent must never guess, recall, or copy a custom field id. Custom field ids are
  specific to one Jira instance: the same logical field, such as "Story Points" or
  "Acceptance Criteria", has a different `customfield_<number>` id on every site. An id
  from documentation, a tutorial, another project, or memory is a guess.
- `create_ticket` and `edit_ticket` refuse any field named `customfield_...` with
  `unsupported_field`, before anything is sent.
- read-ticket does not request any custom field. It finds acceptance criteria heuristically
  in the standard `description` text. It never reads them from a custom field.

## Read-ticket fields

`read_ticket` / `resolve_jira_issue` / MCP `get_issue` request exactly these standard
fields (`GET /rest/api/3/issue/{key}?fields=...`):

| Jira field | Returned as | Notes |
|---|---|---|
| `summary` | `summary` | `""` only when Jira genuinely returned none |
| `description` | `description`, `acceptance_criteria` | ADF converted to plain text; acceptance criteria extracted best-effort |
| `issuetype` | `issue_type` | the type's `name` |
| `status` | `status` | the status `name`; read-only here |
| `project` | `project_key` | must match the project component of the issue key |
| `created` | not returned | requested for the retained raw evidence only |
| `updated` | not returned | requested for the retained raw evidence only |

## Create-ticket fields

`create_ticket` takes two required request-level fields and a `fields` object:

| Request field | Sent to Jira as | Required | Validation |
|---|---|---|---|
| `project_key` | `fields.project.key` | yes | exact `^[A-Z][A-Z0-9]{1,9}$`; never upper-cased or guessed for you |
| `issue_type` | `fields.issuetype.name` | yes | one of the supported issue types below, exact case |

Inside `fields`:

| Field | Sent to Jira as | Required | Validation |
|---|---|---|---|
| `summary` | `fields.summary` | yes | one line, 1-255 characters, no control characters |
| `description` | `fields.description` (ADF) | no | plain text up to 32,000 characters; one ADF paragraph per non-blank line (blank lines are not kept) |

Nothing else is set. Assignee, reporter, labels, priority, components and every custom
field stay at Jira's own project defaults.

## Edit-ticket fields

`edit_ticket` changes only the fields you supply in `fields`. Supply at least one.

| Field | Sent to Jira as | Validation |
|---|---|---|
| `summary` | `fields.summary` | as for create |
| `description` | `fields.description` (ADF) | as for create; replaces the whole description |

## Supported issue types

| Issue type | Why it is supported |
|---|---|
| `Task` | Jira Software default type; no parent and no custom field required |
| `Bug` | Jira Software default type; no parent and no custom field required |
| `Story` | Jira Software default type; no parent and no custom field required |

These types are excluded on purpose:
- `Epic` needs the "Epic Name" custom field on company-managed projects.
- `Sub-task` needs a parent issue.

A project can still disable one of the supported types. Jira then answers 400, which is
classified `rejected` and reported. The request is never retried with a different type.

## Unsupported standard fields

These are real Jira fields that this skill deliberately does not set. Each is refused with
`unsupported_field` and the reason shown here:

| Field | Reason |
|---|---|
| `status` | needs a workflow transition; this skill does not transition issues |
| `resolution` | set by a workflow transition; not performed |
| `assignee` | account ids are never guessed |
| `reporter` | account ids are never guessed |
| `labels` | not supported |
| `priority` | not supported |
| `components` | not supported |
| `fixVersions` | not supported |
| `duedate` | not supported |
| `parent` | not supported |
| `comment` | this skill does not post comments |
| `project` | a separate create-ticket field; never editable |
| `issuetype` | a separate create-ticket field; never editable |

## Unknown fields

Any field name not in the tables above is refused with `unsupported_field`, including
`customfield_...` names and misspellings. Unknown names are never passed through to Jira.

## Adding a custom field safely

A custom field may be added only when a task explicitly requires it and names the target
Jira instance. Follow all of these steps:

1. **Get the real id from the target instance.** A Jira admin reads it from the field
   configuration, or an authorized read of `GET /rest/api/3/field` on that same instance
   (the instance whose URL is `JIRA_BASE_URL`) returns it. Retain that evidence. Never take
   the id from memory, documentation, or another site.
2. **Record it in code.** Add `logical_name -> "customfield_<id>"` to `CUSTOM_FIELD_IDS` in
   `jira_connector.py`. Callers keep using the logical name; only the connector knows the
   id.
3. **Allow it narrowly.** Add the logical name to `CREATE_FIELDS` and/or `EDIT_FIELDS`. Add
   value validation to `_validate_fields`, the outgoing mapping to `_build_jira_fields`, a
   re-read comparison to `_expected_state`/`_verify`, and the id to `READ_FIELDS` only if
   read-ticket must return it.
4. **Document and test it.** Add a row to this file under a new "Configured custom fields"
   table with the logical name, the id, the instance it belongs to, and the evidence path
   from step 1. `TestFieldReference` then needs matching updates.

Until all four steps are done for a field, that field does not exist as far as this harness
is concerned.
