#!/usr/bin/env python3
"""Test-runner wrapper: validates and executes exactly one 'python -m pytest ...'
request, read from an already-existing caller-written request file, on behalf of the
Engineer/Quality Engineer caller-mediated protocol.

Invoked only as:
    python "${CLAUDE_SKILL_DIR}/scripts/run_command.py" --request-file "<path>"

This script never creates or modifies the request file, and always prints exactly one
raw JSON object to stdout -- even on a CLI-argument, validation, or internal error.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]  # .../.claude/skills/test-runner/scripts/<this file>
RUNS_ROOT = (REPO_ROOT / "runs").resolve()

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
REQUIRED_FIELDS = {
    "task_id", "run_id", "command_id", "command",
    "working_directory", "target_repo_path", "purpose",
}
OPTIONAL_FIELDS = {"timeout_seconds"}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
DEFAULT_TIMEOUT, MAX_TIMEOUT, MAX_LOG_BYTES = 300, 900, 1_048_576
EXCLUDED_DIRS = {".pytest_cache", "__pycache__", ".git"}
ALLOWED_NOARG_FLAGS = {"-x", "-s", "-v", "-vv", "-q", "--collect-only", "--co", "--no-header"}
ALLOWED_ARG_FLAGS = {"-k", "-m", "--maxfail", "--tb"}
ALLOWED_TB_VALUES = {"short", "long", "line", "native", "no"}
EXPR_RE = re.compile(r"""^[A-Za-z0-9_ ()'"-]+$""")


class Rejected(Exception):
    def __init__(self, reason: str, category: str):
        super().__init__(reason)
        self.reason, self.category = reason, category


def fail(reason: str, category: str) -> None:
    raise Rejected(reason, category)


# ---------------------------------------------------------------------------
# CLI parsing -- manual, never argparse, so a malformed invocation still
# produces exactly one JSON result instead of argparse's usage text + exit().
# ---------------------------------------------------------------------------


def parse_argv(argv: list[str]) -> str:
    request_file = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--request-file":
            if i + 1 >= len(argv):
                fail("--request-file requires a value", "cli_argument_invalid")
            if request_file is not None:
                fail("--request-file was supplied more than once", "cli_argument_invalid")
            request_file = argv[i + 1]
            i += 2
            continue
        fail(f"unrecognized command-line argument: {token!r}", "cli_argument_invalid")
    if request_file is None:
        fail("--request-file is required", "cli_argument_invalid")
    if request_file == "":
        fail("--request-file value must not be empty", "cli_argument_invalid")
    return request_file


# ---------------------------------------------------------------------------
# Exact request-path shape: runs/<run_id>/requests/<command_id>.json, nothing else.
# ---------------------------------------------------------------------------


def reject_if_absolute_or_traversal(value: str, field_name: str, category: str = "request_value_invalid") -> None:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", value):
        fail(f"{field_name} must not be absolute or drive-prefixed", category)
    if ".." in Path(normalized).parts:
        fail(f"{field_name} must not contain '..'", category)


def validate_request_path_shape(request_path: Path) -> tuple[str, str]:
    if not request_path.is_relative_to(RUNS_ROOT):
        fail("request file is not contained beneath runs/", "request_path_shape_invalid")
    if request_path.suffix != ".json":
        fail("request file suffix must be exactly '.json'", "request_path_shape_invalid")
    requests_dir = request_path.parent
    if requests_dir.name != "requests":
        fail("request file's immediate parent directory must be named 'requests'", "request_path_shape_invalid")
    run_dir = requests_dir.parent
    if run_dir.parent != RUNS_ROOT:
        fail("request file must be nested exactly as runs/<run_id>/requests/<command_id>.json",
             "request_path_shape_invalid")
    run_id, command_id = run_dir.name, request_path.stem
    if not IDENTIFIER_RE.match(run_id):
        fail("path-derived run_id fails the identifier pattern", "identifier_invalid")
    if not IDENTIFIER_RE.match(command_id):
        fail("path-derived command_id fails the identifier pattern", "identifier_invalid")
    return run_id, command_id


# ---------------------------------------------------------------------------
# Strict request-value validation -- exact types, never coerced.
# ---------------------------------------------------------------------------


def require_str(body: dict, field: str) -> str:
    value = body[field]
    if not isinstance(value, str):
        fail(f"{field} must be a string", "request_value_invalid")
    return value


def require_nonempty_str(body: dict, field: str) -> str:
    value = require_str(body, field)
    if not value:
        fail(f"{field} must be a non-empty string", "request_value_invalid")
    return value


def require_identifier(body: dict, field: str) -> str:
    value = require_nonempty_str(body, field)
    if not IDENTIFIER_RE.match(value):
        fail(f"{field} fails the identifier pattern", "identifier_invalid")
    return value


def require_relative_path_str(body: dict, field: str) -> str:
    value = require_nonempty_str(body, field)
    reject_if_absolute_or_traversal(value, field)
    return value


def require_timeout_seconds(body: dict) -> int:
    if "timeout_seconds" not in body:
        return DEFAULT_TIMEOUT
    value = body["timeout_seconds"]
    if isinstance(value, bool) or not isinstance(value, int):
        fail("timeout_seconds must be an integer, not a boolean or other type", "request_value_invalid")
    if not (1 <= value <= MAX_TIMEOUT):
        fail(f"timeout_seconds must be between 1 and {MAX_TIMEOUT}", "request_value_invalid")
    return value


def load_request(request_file_arg: str) -> dict:
    reject_if_absolute_or_traversal(request_file_arg, "--request-file", category="cli_argument_invalid")
    try:
        request_path = (REPO_ROOT / request_file_arg).resolve(strict=True)
    except OSError as exc:
        fail(f"request file does not resolve: {exc}", "request_file_unresolvable")

    run_id_from_path, command_id_from_path = validate_request_path_shape(request_path)

    try:
        body = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"request file is not exactly one JSON object: {exc}", "request_file_malformed")
    if not isinstance(body, dict):
        fail("request file JSON value is not an object", "request_file_malformed")

    missing = REQUIRED_FIELDS - body.keys()
    if missing:
        fail(f"missing required field(s): {sorted(missing)}", "request_field_missing")
    unknown = body.keys() - ALL_FIELDS
    if unknown:
        fail(f"unrecognized field(s): {sorted(unknown)}", "request_field_unknown")

    task_id = require_identifier(body, "task_id")
    run_id = require_identifier(body, "run_id")
    command_id = require_identifier(body, "command_id")
    command = require_nonempty_str(body, "command")
    working_directory = require_relative_path_str(body, "working_directory")
    target_repo_path = require_relative_path_str(body, "target_repo_path")
    purpose = require_nonempty_str(body, "purpose")
    timeout_seconds = require_timeout_seconds(body)

    if run_id != run_id_from_path or command_id != command_id_from_path:
        fail("path-derived run_id/command_id do not match the request body", "request_path_identifier_mismatch")

    return {
        "task_id": task_id, "run_id": run_id, "command_id": command_id,
        "command": command, "working_directory": working_directory,
        "target_repo_path": target_repo_path, "purpose": purpose,
        "timeout_seconds": timeout_seconds,
    }


# ---------------------------------------------------------------------------
# Real filesystem resolution, anchored at REPO_ROOT -- never the wrapper
# process's own arbitrary current working directory.
# ---------------------------------------------------------------------------


def resolve_and_contain(target_repo_path: str, working_directory: str) -> tuple[Path, Path]:
    try:
        target = (REPO_ROOT / target_repo_path).resolve(strict=True)
    except OSError as exc:
        fail(f"target_repo_path does not resolve: {exc}", "target_repo_path_unresolvable")
    if not (target == REPO_ROOT or target.is_relative_to(REPO_ROOT)):
        fail("target_repo_path resolves outside REPO_ROOT", "target_repo_path_escape")
    if not target.is_dir():
        fail("target_repo_path does not resolve to a directory", "target_repo_path_not_a_directory")

    try:
        wd = (REPO_ROOT / working_directory).resolve(strict=True)
    except OSError as exc:
        fail(f"working_directory does not resolve: {exc}", "working_directory_unresolvable")
    if not (wd == target or wd.is_relative_to(target)):
        fail("working_directory resolves outside target_repo_path", "working_directory_escape")
    if not wd.is_dir():
        fail("working_directory does not resolve to a directory", "working_directory_not_a_directory")

    return target, wd


# ---------------------------------------------------------------------------
# Windows-safe tokenizer -- backslash is never an escape character (preserves
# Windows paths); double quotes delimit a literal span; single quotes and an
# unterminated quote are ambiguous -> reject (None).
# ---------------------------------------------------------------------------


def tokenize_windows_safe(command: str) -> list[str] | None:
    tokens: list[str] = []
    buf: list[str] = []
    in_quotes = False
    started = False
    for ch in command:
        if ch == "'":
            return None
        if ch == '"':
            in_quotes, started = not in_quotes, True
            continue
        if ch.isspace() and not in_quotes:
            if started:
                tokens.append("".join(buf))
                buf, started = [], False
            continue
        buf.append(ch)
        started = True
    if in_quotes:
        return None
    if started:
        tokens.append("".join(buf))
    return tokens


# ---------------------------------------------------------------------------
# Positive grammar: exactly "python -m pytest <args>", at least one target.
# ---------------------------------------------------------------------------


def normalize_head(token: str) -> str:
    return token.lower().removesuffix(".exe")


def validate_grammar(tokens: list[str]) -> list[str]:
    if len(tokens) < 3:
        fail("only 'python -m pytest ...' is accepted", "grammar_no_match")
    if "/" in tokens[0] or "\\" in tokens[0]:
        fail("interpreter must be the bare command 'python', not a path", "grammar_no_match")
    if normalize_head(tokens[0]) != "python":
        fail(f"executable {tokens[0]!r} is not accepted", "grammar_no_match")
    if tokens[1] != "-m" or tokens[2] != "pytest":
        fail("only the exact form 'python -m pytest ...' is accepted", "grammar_no_match")
    return tokens[3:]


def validate_positional_path(token: str, wd: Path, target: Path) -> None:
    path_part = token.split("::", 1)[0]
    p = Path(path_part)
    if p.is_absolute():
        fail(f"positional {token!r} is absolute", "positional_path_escape")
    if ".." in p.parts:
        fail(f"positional {token!r} contains '..'", "positional_path_escape")
    resolved = (wd / p).resolve()
    if resolved == target:
        fail(f"positional {token!r} resolves to target_repo_path itself -- a specific "
             f"file, node ID, or subdirectory is required", "positional_path_is_repo_root")
    if not resolved.is_relative_to(target):
        fail(f"positional {token!r} resolves outside target_repo_path", "positional_path_escape")


def validate_args(args: list[str], wd: Path, target: Path) -> list[str]:
    validated: list[str] = []
    positional_count = 0
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ALLOWED_NOARG_FLAGS:
            validated.append(tok)
            i += 1
            continue
        base = tok.split("=", 1)[0]
        if tok in ALLOWED_ARG_FLAGS or base in ("--maxfail", "--tb"):
            if "=" in tok:
                flag, value = tok.split("=", 1)
            else:
                if i + 1 >= len(args):
                    fail(f"flag {tok!r} requires an argument", "grammar_no_match")
                flag, value = tok, args[i + 1]
                i += 1
            if flag == "--tb" and value not in ALLOWED_TB_VALUES:
                fail(f"--tb value {value!r} not accepted", "grammar_no_match")
            if flag == "--maxfail" and not value.isdigit():
                fail(f"--maxfail value {value!r} is not an integer", "grammar_no_match")
            if flag in ("-k", "-m") and not EXPR_RE.match(value):
                fail(f"{flag} expression {value!r} has disallowed characters", "grammar_no_match")
            if "=" in tok:
                validated.append(tok)
            else:
                validated.extend([flag, value])
            i += 1
            continue
        if tok.startswith("-"):
            fail(f"flag {tok!r} is not in the accepted set", "grammar_no_match")
        validate_positional_path(tok, wd, target)
        validated.append(tok)
        positional_count += 1
        i += 1
    if positional_count == 0:
        fail("at least one positional test file, directory, or node ID is required -- "
             "bare 'python -m pytest' with no target is not accepted", "grammar_no_match")
    return validated


# ---------------------------------------------------------------------------
# Immutable retained-log path -- collision is a hard reject, never suffixed.
# ---------------------------------------------------------------------------


def derive_log_path(run_id: str, command_id: str) -> Path:
    root = (RUNS_ROOT / run_id / "logs").resolve()
    candidate = (root / f"{command_id}.log").resolve()
    if not candidate.is_relative_to(root):
        fail("derived log path escaped the retained-evidence root", "log_path_escape")
    if candidate.exists():
        fail(f"a log already exists at {candidate} for this command_id -- refusing to "
             f"overwrite or rename retained evidence", "evidence_collision")
    root.mkdir(parents=True, exist_ok=True)
    return candidate


# ---------------------------------------------------------------------------
# Mutation detection -- a plain filesystem hash comparison, not Git status.
# ---------------------------------------------------------------------------


def hash_tree(target: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(target).parts):
            continue
        digests[str(path.relative_to(target))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def diff_tree(before: dict, after: dict) -> list[str]:
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def write_log(log_path: Path, header: str, stdout: str, stderr: str, mutation_note: str) -> None:
    body = f"{header}\n--- STDOUT ---\n{stdout}\n--- STDERR ---\n{stderr}\n--- MUTATION CHECK ---\n{mutation_note}\n"
    data = body.encode("utf-8", errors="replace")
    if len(data) > MAX_LOG_BYTES:
        data = data[:MAX_LOG_BYTES] + f"\n[... truncated, output exceeded {MAX_LOG_BYTES} bytes ...]".encode()
    log_path.write_bytes(data)


def build_rejected(reason: str, category: str, req: dict | None) -> dict:
    r = req or {}
    return {
        "message_type": "command_rejected",
        "task_id": r.get("task_id"), "run_id": r.get("run_id"), "command_id": r.get("command_id"),
        "command": r.get("command"), "working_directory": r.get("working_directory"),
        "rejection_reason": reason, "rejection_category": category,
    }


def execute_and_respond(req: dict, target: Path, wd: Path, validated_args: list[str],
                         log_path: Path, timeout_seconds: int) -> dict:
    """Everything from here on may have real side effects (a subprocess may start).
    Any unexpected internal failure in this function must still produce a command_result
    -- never a bare traceback, and never a fabricated passing exit_code."""
    argv = [sys.executable, "-m", "pytest", *validated_args]
    before_hashes: dict[str, str] = {}
    after_hashes: dict[str, str] = {}
    stdout = stderr = ""
    real_exit_code: int | None = None
    timed_out = False
    internal_error: str | None = None

    try:
        before_hashes = hash_tree(target)
    except Exception as exc:
        internal_error = f"pre-execution hashing failed: {exc}"

    if internal_error is None:
        try:
            completed = subprocess.run(
                argv, cwd=wd, shell=False, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds,
            )
            real_exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout, stderr = (exc.stdout or ""), (exc.stderr or "")
        except Exception as exc:
            internal_error = f"subprocess execution failed unexpectedly: {exc}"

    if internal_error is None:
        try:
            after_hashes = hash_tree(target)
        except Exception as exc:
            internal_error = f"post-execution hashing failed: {exc}"

    changed: list[str] = []
    if internal_error is None:
        try:
            changed = diff_tree(before_hashes, after_hashes)
        except Exception as exc:
            internal_error = f"mutation comparison failed: {exc}"

    mutated = bool(changed)

    if internal_error is not None:
        reported_exit_code = 126
    elif timed_out:
        reported_exit_code = 124
    elif mutated:
        reported_exit_code = 125  # never 0, even if real_exit_code was 0
    else:
        reported_exit_code = real_exit_code

    mutation_note = (
        "[MUTATION DETECTED] non-cache file addition/deletion/modification under "
        "target_repo_path: " + ", ".join(changed)
        if mutated else "no persistent non-cache file addition, deletion, or modification detected"
    )
    header = (
        f"command: {req['command']}\nexecuted_as: {' '.join(argv)}\n"
        f"working_directory: {req['working_directory']}\ntimeout_seconds: {timeout_seconds}\n"
        f"real_pytest_exit_code: {real_exit_code if (not timed_out and internal_error is None) else 'unknown'}\n"
        f"reported_exit_code: {reported_exit_code}\n"
    )
    if timed_out:
        header += (
            "[wrapper] terminated after exceeding timeout_seconds; exit_code 124 is a "
            "wrapper-assigned sentinel (matching the conventional timeout(1) exit code), "
            "not the child process's own exit status.\n"
        )
    if mutated:
        header += (
            f"[wrapper] the underlying pytest run's real exit code was {real_exit_code}, but a "
            "persistent non-cache file change was detected under target_repo_path, so exit_code "
            "125 (a wrapper policy-violation sentinel, not pytest's own exit status) is reported "
            "instead. 125 must never be treated as a normal test result.\n"
        )
    if internal_error is not None:
        header += (
            f"[wrapper] an internal wrapper error occurred after command execution may have "
            f"already started: {internal_error}. exit_code 126 is a wrapper-assigned sentinel "
            "meaning the real outcome is unknown and must be treated as blocked -- never as "
            "passing or failing test evidence. All output captured before the failure is "
            "preserved below.\n"
        )

    output_ref = None
    output_summary = None
    try:
        write_log(log_path, header, stdout, stderr, mutation_note)
        output_ref = str(log_path.relative_to(REPO_ROOT)).replace("\\", "/")
    except Exception as exc:
        inline = (header + f"\n[wrapper] retained-log write failed: {exc}\n" + stdout[:1000] + "\n" + stderr[:1000])
        output_summary = inline[:2000]

    result = {
        "message_type": "command_result",
        "task_id": req["task_id"], "run_id": req["run_id"], "command_id": req["command_id"],
        "command": req["command"], "working_directory": req["working_directory"],
        "exit_code": reported_exit_code,
    }
    if output_ref is not None:
        result["output_ref"] = output_ref
    else:
        result["output_summary"] = output_summary
    return result


def main() -> None:
    req: dict | None = None
    try:
        request_file_arg = parse_argv(sys.argv[1:])
        req = load_request(request_file_arg)
        target, wd = resolve_and_contain(req["target_repo_path"], req["working_directory"])
        tokens = tokenize_windows_safe(req["command"])
        if tokens is None:
            fail("command could not be parsed unambiguously", "tokenize_failure")
        validated_args = validate_args(validate_grammar(tokens), wd, target)
        log_path = derive_log_path(req["run_id"], req["command_id"])
        timeout_seconds = req["timeout_seconds"]
    except Rejected as exc:
        print(json.dumps(build_rejected(exc.reason, exc.category, req)))
        return
    except Exception as exc:  # never exit without a parseable result
        print(json.dumps(build_rejected(f"unexpected wrapper error before execution: {exc}",
                                         "wrapper_internal_error", req)))
        return

    # Everything past this point may have real side effects -- never emit
    # command_rejected again once we reach here, and never let an exception escape
    # without a JSON response.
    try:
        result = execute_and_respond(req, target, wd, validated_args, log_path, timeout_seconds)
    except Exception as exc:
        result = {
            "message_type": "command_result",
            "task_id": req["task_id"], "run_id": req["run_id"], "command_id": req["command_id"],
            "command": req["command"], "working_directory": req["working_directory"],
            "exit_code": 126,
            "output_summary": (f"wrapper failed unexpectedly after execution may have "
                                f"started: {exc}")[:2000],
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
