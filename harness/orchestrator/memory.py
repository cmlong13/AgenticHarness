"""Persistent factual memory and the lessons-learned feedback loop.

Per PROJECT_SPEC.md's Option C, this module is the Python side of memory, exactly as
checkpoint.py is the Python side of checkpointing: it never reasons about what is worth
remembering or which task is at hand -- it only validates, persists, selects, and
records, deterministically and inspectably. harness/orchestrator/live_cli.py exposes
this as four thin operations (load_memory, append_memory, record_memory_applied,
summarize_memory) the live /work skill calls; harness/orchestrator/core.py does not use
this module at all (the deterministic core has no memory step in this milestone).

Two files back the whole feature, both under memory/ at the repo root:

- facts.jsonl -- append-only, one evidence-backed fact per line. Never rewritten to add
  a new fact; a malformed or invalid line is skipped and recorded, never allowed to
  block a later valid line in the same file from loading.
- lessons-learned.md -- a small, deterministic bullet grammar (see LESSONS_HEADER
  below) instead of free prose, so a lesson can be parsed back out and deduplicated
  without an LLM in the loop. Capped at MAX_LESSONS_PER_RUN new entries per terminal run
  (enforced by append_lessons, not by convention).

Relevance selection (select_relevant_facts / select_relevant_lessons) is plain
tag/keyword-token-set intersection -- no embeddings, no similarity service, no ranking
model. derive_keywords() is the one tokenizer both memory content and the live task
prompt are run through, so "was this considered relevant" is always answerable by
re-running the same deterministic function, not by inspecting an opaque score.

"Loaded" and "applied" are deliberately different claims: load_memory (below) only
reports what exists and what looks relevant. Nothing here ever writes a memory_applied
record on its own -- validate_memory_applied only agrees to let live_cli retain one when
the entry was (a) genuinely selected as relevant by one of *this run's own* retained
memory_loaded events (never a globally-valid entry this run never actually loaded and
selected -- see _relevant_ids_from_memory_loaded_events) and (b) cited by exact id inside
a real, existing decision-evidence file this run points at (see
_evidence_file_contains_entry_id) -- an existing-but-silent evidence file is not enough.
summarize_memory_events then re-derives the run summary's memory_influenced_run/
memory_refs_used fields from the run's own retained policy events, never from an
unchecked caller assertion -- see its docstring.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

FACTS_FILENAME = "facts.jsonl"
LESSONS_FILENAME = "lessons-learned.md"
MAX_LESSONS_PER_RUN = 5

FACT_REQUIRED_FIELDS = ("id", "content", "source_run_id", "evidence_ref", "recorded_at", "status")
FACT_STATUS_VALUES = {"confirmed", "provisional"}
LESSON_REQUIRED_FIELDS = ("id", "text", "source_run_id", "evidence_ref", "date")

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Deliberately conservative and keyword/pattern-based, not a full secret scanner --
# documented limitation (see ASSIGNMENT.md's ban on storing credentials): favors
# rejecting borderline content over silently persisting something that might be a real
# secret. Not a substitute for a dedicated secret-scanning tool.
_SECRET_KEYWORDS = (
    "password", "passwd", "secret", "api_key", "api key", "apikey",
    "access_token", "access token", "accesstoken", "private_key", "private key",
    "privatekey", "credential", "auth_token", "authtoken", "client_secret",
    "bearer ",
)
_SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),             # AWS access key id
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),   # GitHub tokens (ghp_/gho_/ghu_/ghs_/ghr_)
    re.compile(r"sk-[A-Za-z0-9]{20,}"),          # OpenAI-style secret key
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), # Slack tokens
)

# The deterministic proxy this milestone uses for "about harness workflow, validation,
# orchestration, or engineering process ... not task-specific product trivia": a lesson
# must mention at least one of these terms. Documented limitation: this can be fooled by
# a lesson that name-drops a keyword without genuine workflow content, and it can reject
# a genuine workflow lesson that happens to avoid all of these words -- it is a
# mechanical filter, not judgment.
WORKFLOW_KEYWORDS = (
    "harness", "orchestrator", "checkpoint", "resume", "test-runner", "test runner",
    "skill", "agent", "dispatch", "validation", "validate", "evidence", "schema",
    "protocol", "transport", "route-back", "route back", "verification", "verify",
    "implementation", "discovery", "research", "engineer", "architect",
    "quality engineer", "staged", "continuity", "policy event", "artifact",
    "promote", "semantic", "identity", "mediation", "phase", "pipeline",
    "correction budget", "fence", "json", "command", "regression test", "tdd",
)

LESSONS_HEADER = (
    "# Lessons Learned\n\n"
    "Reusable lessons about harness workflow, validation, orchestration, and "
    "engineering process -- grounded in a specific run and its retained evidence, not "
    "task-specific product trivia, and not a speculative recommendation presented as "
    "established fact. At most 5 new entries are appended per terminal run (see "
    "harness/orchestrator/memory.py's append_lessons and .claude/skills/work/SKILL.md's "
    "terminal memory-append step).\n\n"
    "Format: `- [LESSON-ID] (source_run: <run_id>, evidence: <repo-relative path>, "
    "date: <YYYY-MM-DD>[, tags: <comma,separated,tags>]) <lesson text>`\n\n"
)

_LESSON_LINE_RE = re.compile(
    r"^- \[(?P<id>[A-Za-z0-9][A-Za-z0-9_-]{0,63})\] "
    r"\(source_run: (?P<source_run>[^,]+), evidence: (?P<evidence>[^,]+), "
    r"date: (?P<date>[^,)]+)(?:, tags: (?P<tags>[^)]*))?\) (?P<text>.+)$"
)


# ---------------------------------------------------------------------------
# Shared helpers: normalization, keyword derivation, path safety, secret checks
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Deterministic normalization used for duplicate detection: lowercase, punctuation
    stripped to spaces, whitespace collapsed. Two lessons/facts that only differ in
    case, punctuation, or spacing normalize identically -- this is what makes
    "materially equivalent duplicate" suppression possible without an LLM judgment call."""
    lowered = (text or "").strip().lower()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


_STOPWORDS = frozenset(
    {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "this",
     "that", "is", "are", "be", "it", "as", "by", "at", "from", "was", "were", "not"}
)


def derive_keywords(text: str) -> list[str]:
    """The one deterministic tokenizer used for both memory content and the live task
    prompt, so relevance selection is always re-derivable by re-running this function --
    never an opaque score. Splits on non-word boundaries (keeping internal '-'/'_' so
    compound terms like "route-back" survive as one token), lowercases, drops tokens
    shorter than 3 characters and a small stopword list, and returns unique tokens in
    first-seen order."""
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]*", (text or "").lower())
    seen: list[str] = []
    for tok in tokens:
        if len(tok) < 3 or tok in _STOPWORDS:
            continue
        if tok not in seen:
            seen.append(tok)
    return seen


def _entry_keyword_set(text: str, tags: list[str]) -> set[str]:
    tokens = set(derive_keywords(text))
    for tag in tags or []:
        tokens.update(derive_keywords(tag))
    return tokens


def contains_secret_like_content(text: str) -> bool:
    lowered = (text or "").lower()
    if any(kw in lowered for kw in _SECRET_KEYWORDS):
        return True
    return any(pattern.search(text or "") for pattern in _SECRET_PATTERNS)


def is_workflow_relevant_lesson(text: str) -> bool:
    normalized = normalize_text(text)
    return any(kw in normalized for kw in WORKFLOW_KEYWORDS)


def _path_is_safe_relative(value: str) -> bool:
    """Same absolute/drive-prefixed/traversal rejection paths.py applies to scope
    in_scope entries, reimplemented narrowly here rather than importing paths.py's
    private helpers -- evidence_ref/evidence_path values are orchestrator-authored
    config-shaped strings, not agent-supplied scope paths, so they do not share
    paths.py's Protected Path or containment concerns, only this basic safety check."""
    if not value or not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        return False
    if ".." in normalized.split("/"):
        return False
    return True


@dataclass(frozen=True)
class SkipRecord:
    line_no: int | None
    reason: str
    raw: str = ""

    def as_dict(self) -> dict:
        d: dict = {"reason": self.reason}
        if self.line_no is not None:
            d["line_no"] = self.line_no
        if self.raw:
            d["raw"] = self.raw
        return d


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def facts_path(memory_dir: Path) -> Path:
    return memory_dir / FACTS_FILENAME


def _validate_fact_shape(candidate: object) -> list[str]:
    if not isinstance(candidate, dict):
        return ["fact entry is not a JSON object"]
    errors: list[str] = []
    for field_name in FACT_REQUIRED_FIELDS:
        if field_name not in candidate:
            errors.append(f"missing required field {field_name!r}")
    if errors:
        return errors  # further checks assume the required keys exist

    fact_id = candidate["id"]
    if not isinstance(fact_id, str) or not _ID_RE.match(fact_id):
        errors.append(f"'id' must be a non-empty identifier matching {_ID_RE.pattern!r}")

    content = candidate["content"]
    if not isinstance(content, str) or not content.strip():
        errors.append("'content' must be a non-empty string")
    elif contains_secret_like_content(content):
        errors.append("'content' appears to contain a secret/credential-shaped value -- rejected")

    source_run_id = candidate["source_run_id"]
    if not isinstance(source_run_id, str) or not source_run_id.strip():
        errors.append("'source_run_id' must be a non-empty string (provenance is required)")

    evidence_ref = candidate["evidence_ref"]
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        errors.append("'evidence_ref' must be a non-empty string (evidence is required)")
    elif not _path_is_safe_relative(evidence_ref):
        errors.append("'evidence_ref' must be a safe, repo-relative path (no absolute path, no '..')")

    recorded_at = candidate["recorded_at"]
    if not isinstance(recorded_at, str) or not _DATE_RE.match(recorded_at):
        errors.append("'recorded_at' must be an absolute date string (YYYY-MM-DD...)")

    status = candidate["status"]
    if not isinstance(status, str) or status not in FACT_STATUS_VALUES:
        errors.append(f"'status' must be one of {sorted(FACT_STATUS_VALUES)}")

    tags = candidate.get("tags")
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
            errors.append("'tags', if present, must be a list of non-empty strings")
        elif any(contains_secret_like_content(t) for t in tags):
            errors.append("'tags' appears to contain a secret/credential-shaped value -- rejected")

    return errors


def _evidence_ref_exists(repo_root: Path, evidence_ref: str) -> bool:
    """Shared by fact/lesson loading and appending -- all three call sites need exactly
    this one check (does the referenced evidence file actually exist under repo_root),
    factored out so the rule is stated once rather than reimplemented at each site."""
    return (repo_root / evidence_ref).exists()


def _fact_evidence_errors(candidate: dict, *, repo_root: Path) -> list[str]:
    """Confirms evidence_ref actually resolves to an existing file under repo_root --
    an evidence reference that does not exist is treated the same as no evidence at
    all, per the requirement that facts must be evidence-backed."""
    evidence_ref = candidate.get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        return []  # already reported by _validate_fact_shape
    if not _evidence_ref_exists(repo_root, evidence_ref):
        return [f"evidence_ref {evidence_ref!r} does not resolve to an existing file"]
    return []


@dataclass
class FactLoadResult:
    valid: list[dict] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)


def load_facts(memory_dir: Path, *, repo_root: Path) -> FactLoadResult:
    """Reads memory/facts.jsonl line by line. Every failure mode is a per-line skip with
    an explicit reason -- malformed JSON, wrong shape, missing provenance/evidence,
    empty content, a secret-shaped value, or a duplicate never prevents a later, valid
    line in the same file from loading (append-safe read, matching append-safe write)."""
    path = facts_path(memory_dir)
    result = FactLoadResult()
    if not path.exists():
        return result

    seen_ids: set[str] = set()
    seen_normalized: set[str] = set()
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            candidate = json.loads(stripped)
        except json.JSONDecodeError as exc:
            result.skipped.append(SkipRecord(line_no, f"malformed JSON: {exc}", raw=stripped))
            continue

        shape_errors = _validate_fact_shape(candidate)
        if shape_errors:
            result.skipped.append(SkipRecord(line_no, "; ".join(shape_errors), raw=stripped))
            continue

        evidence_errors = _fact_evidence_errors(candidate, repo_root=repo_root)
        if evidence_errors:
            result.skipped.append(SkipRecord(line_no, "; ".join(evidence_errors), raw=stripped))
            continue

        fact_id = candidate["id"]
        normalized = normalize_text(candidate["content"])
        if fact_id in seen_ids:
            result.skipped.append(SkipRecord(line_no, f"duplicate fact id {fact_id!r}", raw=stripped))
            continue
        if normalized in seen_normalized:
            result.skipped.append(
                SkipRecord(line_no, "duplicate of an existing fact (normalized content match)", raw=stripped)
            )
            continue

        seen_ids.add(fact_id)
        seen_normalized.add(normalized)
        result.valid.append(candidate)

    return result


@dataclass
class AppendFactResult:
    appended: dict | None
    status: str  # "appended" | "rejected" | "duplicate"
    errors: list[str] = field(default_factory=list)


def append_fact(memory_dir: Path, candidate: dict, *, repo_root: Path) -> AppendFactResult:
    """Append-safe: validates `candidate` exactly as load_facts would (including
    checking it against every currently-valid fact for an exact-id or normalized-content
    duplicate), and only if it passes, appends exactly one JSON line to
    memory/facts.jsonl. Never rewrites, reorders, or removes any existing line."""
    shape_errors = _validate_fact_shape(candidate)
    if shape_errors:
        return AppendFactResult(None, "rejected", shape_errors)

    evidence_errors = _fact_evidence_errors(candidate, repo_root=repo_root)
    if evidence_errors:
        return AppendFactResult(None, "rejected", evidence_errors)

    existing = load_facts(memory_dir, repo_root=repo_root)
    if any(f["id"] == candidate["id"] for f in existing.valid):
        return AppendFactResult(None, "duplicate", [f"duplicate fact id {candidate['id']!r}"])
    normalized = normalize_text(candidate["content"])
    if any(normalize_text(f["content"]) == normalized for f in existing.valid):
        return AppendFactResult(None, "duplicate", ["duplicate of an existing fact (normalized content match)"])

    path = facts_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(candidate, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return AppendFactResult(candidate, "appended", [])


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------


def lessons_path(memory_dir: Path) -> Path:
    return memory_dir / LESSONS_FILENAME


def ensure_lessons_file(memory_dir: Path) -> Path:
    """Creates lessons-learned.md with its header if absent. Never overwrites an
    existing file -- this is the "created when absent" behavior, not a reset."""
    path = lessons_path(memory_dir)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LESSONS_HEADER, encoding="utf-8")
    return path


def _parse_lesson_line(line_no: int, line: str) -> tuple[dict | None, SkipRecord | None]:
    match = _LESSON_LINE_RE.match(line)
    if not match:
        return None, SkipRecord(
            line_no, "line begins with '- [' but does not match the lesson grammar", raw=line
        )
    groups = match.groupdict()
    tags = [t.strip() for t in groups["tags"].split(",") if t.strip()] if groups.get("tags") else []
    entry = {
        "id": groups["id"],
        "source_run_id": groups["source_run"].strip(),
        "evidence_ref": groups["evidence"].strip(),
        "date": groups["date"].strip(),
        "tags": tags,
        "text": groups["text"].strip(),
    }
    return entry, None


@dataclass
class LessonLoadResult:
    valid: list[dict] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)


def load_lessons(memory_dir: Path, *, repo_root: Path) -> LessonLoadResult:
    """Reads lessons-learned.md. A line that doesn't start with '- [' (the header,
    blank separators, prose) is not a lesson entry at all and is silently skipped over
    -- only a line that DOES start with '- [' but fails the full grammar, or fails a
    content rule (missing provenance/evidence, unresolvable evidence, empty text, a
    secret-shaped value, or a duplicate), is recorded as a visible skip."""
    path = lessons_path(memory_dir)
    result = LessonLoadResult()
    if not path.exists():
        return result

    seen_ids: set[str] = set()
    seen_normalized: set[str] = set()
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped.startswith("- ["):
            continue

        entry, skip = _parse_lesson_line(line_no, stripped)
        if skip is not None:
            result.skipped.append(skip)
            continue

        if not entry["source_run_id"]:
            result.skipped.append(SkipRecord(line_no, "missing source_run (provenance is required)", raw=stripped))
            continue
        if not entry["evidence_ref"] or not _path_is_safe_relative(entry["evidence_ref"]):
            result.skipped.append(SkipRecord(line_no, "missing or unsafe evidence reference", raw=stripped))
            continue
        if not _evidence_ref_exists(repo_root, entry["evidence_ref"]):
            result.skipped.append(
                SkipRecord(line_no, f"evidence_ref {entry['evidence_ref']!r} does not resolve to an existing file", raw=stripped)
            )
            continue
        if not entry["text"]:
            result.skipped.append(SkipRecord(line_no, "empty lesson text", raw=stripped))
            continue
        if contains_secret_like_content(entry["text"]):
            result.skipped.append(
                SkipRecord(line_no, "lesson text appears to contain a secret/credential-shaped value", raw=stripped)
            )
            continue

        entry_id = entry["id"]
        normalized = normalize_text(entry["text"])
        if entry_id in seen_ids:
            result.skipped.append(SkipRecord(line_no, f"duplicate lesson id {entry_id!r}", raw=stripped))
            continue
        if normalized in seen_normalized:
            result.skipped.append(
                SkipRecord(line_no, "duplicate of an existing lesson (normalized text match)", raw=stripped)
            )
            continue

        seen_ids.add(entry_id)
        seen_normalized.add(normalized)
        result.valid.append(entry)

    return result


def _validate_lesson_candidate_shape(candidate: object) -> list[str]:
    if not isinstance(candidate, dict):
        return ["lesson candidate is not a JSON object"]
    errors: list[str] = []
    for key in LESSON_REQUIRED_FIELDS:
        if key not in candidate or not isinstance(candidate[key], str) or not candidate[key].strip():
            errors.append(f"missing or empty required field {key!r}")
    if errors:
        return errors

    if not _ID_RE.match(candidate["id"]):
        errors.append(f"'id' must be a non-empty identifier matching {_ID_RE.pattern!r}")
    if not _DATE_RE.match(candidate["date"]):
        errors.append("'date' must be an absolute date string (YYYY-MM-DD)")
    if not _path_is_safe_relative(candidate["evidence_ref"]):
        errors.append("'evidence_ref' must be a safe, repo-relative path (no absolute path, no '..')")

    tags = candidate.get("tags")
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
            errors.append("'tags', if present, must be a list of non-empty strings")

    text = candidate["text"]
    if contains_secret_like_content(text):
        errors.append("'text' appears to contain a secret/credential-shaped value -- rejected")
    if not is_workflow_relevant_lesson(text):
        errors.append(
            "lesson text does not mention any recognized harness-workflow vocabulary -- "
            "looks like task-specific product trivia, not a workflow lesson"
        )

    return errors


def _format_lesson_line(candidate: dict) -> str:
    tags = candidate.get("tags") or []
    tags_part = f", tags: {','.join(tags)}" if tags else ""
    return (
        f"- [{candidate['id']}] (source_run: {candidate['source_run_id']}, "
        f"evidence: {candidate['evidence_ref']}, date: {candidate['date']}{tags_part}) "
        f"{candidate['text'].strip()}\n"
    )


@dataclass
class AppendLessonsResult:
    appended: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {"candidate_id": ..., "reason": ...}


def append_lessons(
    memory_dir: Path, candidates: list[dict], *, repo_root: Path, max_new: int = MAX_LESSONS_PER_RUN
) -> AppendLessonsResult:
    """Appends up to `max_new` (default MAX_LESSONS_PER_RUN) validated, non-duplicate
    lesson candidates, in the order given. Every candidate is validated exactly as
    load_lessons would (shape, provenance, evidence existence, non-empty text, no
    secret-shaped content, workflow relevance) plus duplicate suppression against every
    lesson already on disk AND against earlier candidates already accepted within this
    same call. A candidate past the cap is skipped with an explicit "cap reached"
    reason -- never silently dropped, and zero acceptances is a legitimate outcome."""
    ensure_lessons_file(memory_dir)
    existing = load_lessons(memory_dir, repo_root=repo_root)
    seen_ids = {e["id"] for e in existing.valid}
    seen_normalized = {normalize_text(e["text"]) for e in existing.valid}

    result = AppendLessonsResult()
    accepted_lines: list[str] = []

    for candidate in candidates:
        candidate_id = candidate.get("id") if isinstance(candidate, dict) else None

        if len(result.appended) >= max_new:
            result.skipped.append({"candidate_id": candidate_id, "reason": f"per-run lesson cap reached (max {max_new})"})
            continue

        shape_errors = _validate_lesson_candidate_shape(candidate)
        if shape_errors:
            result.skipped.append({"candidate_id": candidate_id, "reason": "; ".join(shape_errors)})
            continue

        evidence_ref = candidate["evidence_ref"]
        if not _evidence_ref_exists(repo_root, evidence_ref):
            result.skipped.append(
                {"candidate_id": candidate_id, "reason": f"evidence_ref {evidence_ref!r} does not resolve to an existing file"}
            )
            continue

        if candidate["id"] in seen_ids:
            result.skipped.append({"candidate_id": candidate_id, "reason": f"duplicate lesson id {candidate['id']!r}"})
            continue
        normalized = normalize_text(candidate["text"])
        if normalized in seen_normalized:
            result.skipped.append(
                {"candidate_id": candidate_id, "reason": "duplicate of an existing lesson (normalized text match)"}
            )
            continue

        seen_ids.add(candidate["id"])
        seen_normalized.add(normalized)
        result.appended.append(candidate)
        accepted_lines.append(_format_lesson_line(candidate))

    if accepted_lines:
        path = lessons_path(memory_dir)
        with path.open("a", encoding="utf-8") as fh:
            fh.writelines(accepted_lines)

    return result


# ---------------------------------------------------------------------------
# Relevance selection
# ---------------------------------------------------------------------------


def _select_relevant(entries: list[dict], keywords: list[str], *, text_field: str) -> list[dict]:
    keyword_set = set(keywords)
    if not keyword_set:
        return []
    return [e for e in entries if _entry_keyword_set(e.get(text_field, ""), e.get("tags", [])) & keyword_set]


def select_relevant_facts(facts: list[dict], keywords: list[str]) -> list[dict]:
    return _select_relevant(facts, keywords, text_field="content")


def select_relevant_lessons(lessons: list[dict], keywords: list[str]) -> list[dict]:
    return _select_relevant(lessons, keywords, text_field="text")


# ---------------------------------------------------------------------------
# The canonical memory-load operation (Part 3)
# ---------------------------------------------------------------------------


@dataclass
class MemoryLoadResult:
    files_read: list[Path] = field(default_factory=list)
    valid_fact_count: int = 0
    valid_lesson_count: int = 0
    skipped: list[dict] = field(default_factory=list)  # {"source": "facts"|"lessons", ...SkipRecord}
    relevant_facts: list[dict] = field(default_factory=list)
    relevant_lessons: list[dict] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


def load_memory(memory_dir: Path, *, raw_prompt: str, repo_root: Path) -> MemoryLoadResult:
    """The one canonical memory-load operation used before Discovery: reads the fact
    index and lessons-learned.md, validates every entry, derives deterministic keywords
    from `raw_prompt` (never a keyword list the caller hand-picks -- see derive_keywords),
    and selects relevant facts/lessons via plain tag/keyword intersection. Never touches
    scope.json or any other canonical artifact -- this is read-only context, not
    authority over task scope. An empty relevant set on either side is a legitimate,
    honestly-reported outcome, not an error."""
    facts_result = load_facts(memory_dir, repo_root=repo_root)
    lessons_result = load_lessons(memory_dir, repo_root=repo_root)
    keywords = derive_keywords(raw_prompt)

    relevant_facts = select_relevant_facts(facts_result.valid, keywords)
    relevant_lessons = select_relevant_lessons(lessons_result.valid, keywords)

    files_read: list[Path] = []
    if facts_path(memory_dir).exists():
        files_read.append(facts_path(memory_dir))
    if lessons_path(memory_dir).exists():
        files_read.append(lessons_path(memory_dir))

    skipped = (
        [{"source": "facts", **s.as_dict()} for s in facts_result.skipped]
        + [{"source": "lessons", **s.as_dict()} for s in lessons_result.skipped]
    )

    return MemoryLoadResult(
        files_read=files_read,
        valid_fact_count=len(facts_result.valid),
        valid_lesson_count=len(lessons_result.valid),
        skipped=skipped,
        relevant_facts=relevant_facts,
        relevant_lessons=relevant_lessons,
        keywords=keywords,
    )


# ---------------------------------------------------------------------------
# Proving actual influence (Part 4)
# ---------------------------------------------------------------------------


_APPLIED_REQUIRED_FIELDS = (
    "entry_id", "entry_type", "source_run_id", "current_run_id", "phase", "decision", "evidence_path",
)
_ENTRY_TYPES = {"fact", "lesson"}
_PHASES = ("discovery", "research", "implementation", "verification")

# Bounded text search cap for the evidence-cites-entry-id check below: evidence files
# are expected to be small, structured artifacts (scope.json, a findings note, a raw
# attempt log) -- capping the read keeps this a genuinely narrow, cheap check rather than
# an unbounded scan of an arbitrarily large file. A file whose match would only appear
# past this cap is treated as not citing the entry (a conservative false-negative is
# safer here than reading unboundedly).
_MAX_EVIDENCE_READ_BYTES = 2_000_000


def _relevant_ids_from_memory_loaded_events(run_directory: Path) -> tuple[set[str], set[str]] | None:
    """Scans this run's own runs/<run_id>/logs/policy-events.jsonl for every retained
    "memory_loaded" event and unions their relevant_fact_ids/relevant_lesson_ids.
    Returns None if zero such events exist (distinct from "found, but nothing was
    relevant," which is a real, non-None empty-set result) -- callers use None to refuse
    a memory_applied claim outright rather than silently treating an unloaded run the
    same as a run that loaded memory and found nothing relevant.

    A run's own log can legitimately carry more than one "memory_loaded" event -- a
    fresh invocation's single load, plus one further load per resumed invocation under
    the same run_id (see work/SKILL.md's "Phase 0: Memory load" / "Resuming an
    interrupted run"). The union of every one of them is this run's full eligible
    selection: an entry surfaced as relevant by any load this run genuinely performed is
    fair game for a memory_applied claim made afterward in that same run, matching the
    resumed-invocation contract that newly loaded memory may inform the restarted phase
    and everything after it. This function only reads; it never writes or infers beyond
    what was actually retained."""
    events_path = run_directory / "logs" / "policy-events.jsonl"
    if not events_path.exists():
        return None

    fact_ids: set[str] = set()
    lesson_ids: set[str] = set()
    found_any = False
    for raw_line in events_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "memory_loaded":
            continue
        found_any = True
        fact_ids.update(i for i in event.get("relevant_fact_ids", []) if isinstance(i, str))
        lesson_ids.update(i for i in event.get("relevant_lesson_ids", []) if isinstance(i, str))

    if not found_any:
        return None
    return fact_ids, lesson_ids


def _evidence_file_contains_entry_id(evidence_file: Path, entry_id: str) -> bool:
    """Bounded, exact-boundary text search: reads at most _MAX_EVIDENCE_READ_BYTES from
    `evidence_file` and confirms `entry_id` appears as a complete token, not merely as a
    substring of a longer id (e.g. entry_id "L-1" must not match inside "L-10" -- both
    share the same ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ id grammar, so a plain substring
    search would be unsound). Never raises -- an unreadable or undecodable file is
    treated as not citing the entry, the same conservative outcome as a genuine miss."""
    try:
        data = evidence_file.read_bytes()
    except OSError:
        return False
    text = data[:_MAX_EVIDENCE_READ_BYTES].decode("utf-8", errors="replace")
    pattern = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(entry_id) + r"(?![A-Za-z0-9_-])")
    return pattern.search(text) is not None


def validate_memory_applied(
    payload: dict, *, memory_dir: Path, repo_root: Path, run_directory: Path
) -> list[str]:
    """Validates a memory_applied claim BEFORE it is ever retained as a policy event.
    Refuses (returns a non-empty error list) unless ALL of the following hold:

    - every required field is a non-empty string, and entry_type/phase are recognized
      values;
    - entry_id resolves to a currently-valid fact or lesson of the claimed type
      (re-loaded fresh, never trusted from an earlier call);
    - `run_directory` has at least one retained "memory_loaded" event, and entry_id
      appears in that event's (or the union of those events') own
      relevant_fact_ids/relevant_lesson_ids for the matching entry_type -- an entry that
      is globally valid but was never selected as relevant *in this run* is refused. This
      is what ties "applied" to "this run's own loaded selection," not to memory in the
      abstract;
    - evidence_path is a safe, repo-relative path to a real, existing, regular
      (non-directory) file under repo_root; AND that file's own content, read up to a
      bounded cap, contains entry_id as an exact, boundary-matched token -- a real,
      existing file that merely happens to exist is not enough; it must actually cite the
      entry it claims to be evidence for.

    A memory_applied claim with no real decision evidence, or with decision evidence that
    doesn't even mention the entry, is refused, never fabricated. This is what keeps "the
    entry was in the prompt" -- or "the entry was loaded at some point, by someone" --
    from ever being enough on its own."""
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]

    errors: list[str] = []
    for field_name in _APPLIED_REQUIRED_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty required field {field_name!r}")
    if errors:
        return errors

    entry_type = payload["entry_type"]
    if entry_type not in _ENTRY_TYPES:
        errors.append(f"'entry_type' must be one of {sorted(_ENTRY_TYPES)}")

    phase = payload["phase"]
    if phase not in _PHASES:
        errors.append(f"'phase' must be one of {list(_PHASES)}")

    entry_id = payload["entry_id"]
    if entry_type in _ENTRY_TYPES:
        if entry_type == "fact":
            known_ids = {f["id"] for f in load_facts(memory_dir, repo_root=repo_root).valid}
        else:
            known_ids = {lesson["id"] for lesson in load_lessons(memory_dir, repo_root=repo_root).valid}
        if entry_id not in known_ids:
            errors.append(f"entry_id {entry_id!r} does not resolve to a currently-valid {entry_type} entry")

        selection = _relevant_ids_from_memory_loaded_events(run_directory)
        if selection is None:
            errors.append(
                "no memory_loaded event found for this run -- call load_memory before Discovery "
                "(Phase 0) and before any memory_applied claim"
            )
        else:
            relevant_fact_ids, relevant_lesson_ids = selection
            selected_ids = relevant_fact_ids if entry_type == "fact" else relevant_lesson_ids
            if entry_id not in selected_ids:
                errors.append(
                    f"entry_id {entry_id!r} was not selected as relevant in this run's own "
                    f"memory_loaded event(s) -- an entry that exists globally but was not loaded "
                    f"and selected by this run cannot be claimed as applied"
                )

    evidence_path = payload["evidence_path"]
    if not _path_is_safe_relative(evidence_path):
        errors.append("'evidence_path' must be a safe, repo-relative path (no absolute path, no '..')")
    else:
        evidence_file = repo_root / evidence_path
        if not evidence_file.is_file():
            errors.append(
                f"evidence_path {evidence_path!r} does not resolve to an existing, regular file -- "
                f"missing decision evidence"
            )
        elif not _evidence_file_contains_entry_id(evidence_file, entry_id):
            errors.append(
                f"evidence_path {evidence_path!r} does not contain entry_id {entry_id!r} -- decision "
                f"evidence must actually cite the applied entry, not merely exist"
            )

    return errors


def summarize_memory_events(run_directory: Path) -> dict:
    """Reads runs/<run_id>/logs/policy-events.jsonl (if present) and derives the run
    summary's memory fields from what was ACTUALLY retained -- never from what a caller
    merely asserts:

    - memory_loaded is true iff at least one "memory_loaded" event was retained.
    - memory_influenced_run is true iff at least one "memory_applied" event was
      retained (a "memory_applied_rejected" event, retained when validate_memory_applied
      refuses a claim, never counts).
    - memory_refs_used lists the distinct entry_id of every retained "memory_applied"
      event, in first-seen order.

    This is what makes "reading a lesson alone does not set memory_influenced_run, only
    a valid memory_applied event does" true by construction rather than by convention."""
    events_path = run_directory / "logs" / "policy-events.jsonl"
    memory_loaded = False
    refs_used: list[str] = []

    if events_path.exists():
        for raw_line in events_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("kind")
            if kind == "memory_loaded":
                memory_loaded = True
            elif kind == "memory_applied":
                entry_id = event.get("entry_id")
                if isinstance(entry_id, str) and entry_id and entry_id not in refs_used:
                    refs_used.append(entry_id)

    return {
        "memory_loaded": memory_loaded,
        "memory_influenced_run": bool(refs_used),
        "memory_refs_used": refs_used,
    }
