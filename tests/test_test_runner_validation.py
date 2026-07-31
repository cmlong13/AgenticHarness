"""Deterministic tests for the test-runner skill's wrapper script.

Two layers:
1. Unit tests against the wrapper's pure functions, loaded via importlib (the script
   lives under .claude/skills/, not a normal package) and exercised against a fake
   repo root under tmp_path via monkeypatching REPO_ROOT/RUNS_ROOT -- fast, hermetic,
   no real filesystem side effects outside tmp_path.
2. A small number of real subprocess-level integration tests against the actual script
   file, which necessarily resolves REPO_ROOT from its own real location and so must
   operate within the real repository tree (runs/test-runner-boundary-test/) -- each
   uses a freshly generated command_id and cleans up its own request/log/fixture files
   afterward, so repeated `pytest` runs never collide with a prior run's evidence or
   leave the working tree dirty.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / ".claude" / "skills" / "test-runner" / "scripts" / "run_command.py"
BOUNDARY_RUN_ID = "test-runner-boundary-test"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_command_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rc():
    return _load_module()


@pytest.fixture()
def fake_repo(tmp_path, rc, monkeypatch):
    repo_root = tmp_path / "repo"
    (repo_root / "runs").mkdir(parents=True)
    monkeypatch.setattr(rc, "REPO_ROOT", repo_root)
    monkeypatch.setattr(rc, "RUNS_ROOT", (repo_root / "runs").resolve())
    return repo_root


def _rel(path: Path, base: Path) -> str:
    return str(path.relative_to(base)).replace("\\", "/")


def _make_target_repo(repo_root: Path) -> Path:
    target = repo_root / "runs" / "fixture-run" / "fixture-repo"
    src = target / "fixture-src"
    src.mkdir(parents=True)
    (src / "test_sample.py").write_text(
        "def test_pass():\n    assert 1 + 1 == 2\n\n\ndef test_fail():\n    assert 1 == 2\n",
        encoding="utf-8",
    )
    return target


def _write_request(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


class TestParseArgv:
    def test_accepts_valid_single_flag(self, rc) -> None:
        assert rc.parse_argv(["--request-file", "runs/x/requests/y.json"]) == "runs/x/requests/y.json"

    def test_rejects_missing_flag(self, rc) -> None:
        with pytest.raises(rc.Rejected) as exc:
            rc.parse_argv([])
        assert exc.value.category == "cli_argument_invalid"

    def test_rejects_duplicate_flag(self, rc) -> None:
        with pytest.raises(rc.Rejected) as exc:
            rc.parse_argv(["--request-file", "a", "--request-file", "b"])
        assert exc.value.category == "cli_argument_invalid"

    def test_rejects_unknown_argument(self, rc) -> None:
        with pytest.raises(rc.Rejected) as exc:
            rc.parse_argv(["--bogus", "x"])
        assert exc.value.category == "cli_argument_invalid"

    def test_rejects_empty_value(self, rc) -> None:
        with pytest.raises(rc.Rejected) as exc:
            rc.parse_argv(["--request-file", ""])
        assert exc.value.category == "cli_argument_invalid"

    def test_rejects_missing_value(self, rc) -> None:
        with pytest.raises(rc.Rejected) as exc:
            rc.parse_argv(["--request-file"])
        assert exc.value.category == "cli_argument_invalid"


class TestMainNeverCrashesWithoutJSON:
    """Real subprocess invocations -- proves argparse's default usage-and-exit
    behavior was genuinely avoided, not just handled in-process."""

    def test_missing_request_file_flag_still_prints_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)], capture_output=True, text=True, encoding="utf-8"
        )
        lines = [l for l in completed.stdout.splitlines() if l.strip()]
        assert len(lines) == 1, f"expected exactly one JSON line, got: {completed.stdout!r}"
        result = json.loads(lines[0])
        assert result["message_type"] == "command_rejected"
        assert result["rejection_category"] == "cli_argument_invalid"

    def test_unknown_argument_still_prints_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--bogus", "x"],
            capture_output=True, text=True, encoding="utf-8",
        )
        lines = [l for l in completed.stdout.splitlines() if l.strip()]
        assert len(lines) == 1
        result = json.loads(lines[0])
        assert result["rejection_category"] == "cli_argument_invalid"


# ---------------------------------------------------------------------------
# Request-file argument + path-shape validation
# ---------------------------------------------------------------------------


class TestRequestFileArgValidation:
    def test_rejects_absolute_request_file_arg(self, rc, fake_repo) -> None:
        target = _make_target_repo(fake_repo)
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, {
            "task_id": "T-1", "run_id": "R-1", "command_id": "C-1",
            "command": "python -m pytest test_sample.py::test_pass",
            "working_directory": _rel(target / "fixture-src", fake_repo),
            "target_repo_path": _rel(target, fake_repo),
            "purpose": "unit test",
        })
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(str(req_path))  # absolute -- must be rejected before resolving
        assert exc.value.category == "cli_argument_invalid"

    def test_resolves_relative_to_repo_root_not_process_cwd(self, rc, fake_repo, tmp_path, monkeypatch) -> None:
        target = _make_target_repo(fake_repo)
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, {
            "task_id": "T-1", "run_id": "R-1", "command_id": "C-1",
            "command": "python -m pytest test_sample.py::test_pass",
            "working_directory": _rel(target / "fixture-src", fake_repo),
            "target_repo_path": _rel(target, fake_repo),
            "purpose": "unit test",
        })
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        req = rc.load_request(_rel(req_path, fake_repo))
        assert req["task_id"] == "T-1"


class TestRequestPathShape:
    def test_accepts_correct_shape(self, rc, fake_repo) -> None:
        run_dir = fake_repo / "runs" / "R-1" / "requests"
        run_dir.mkdir(parents=True)
        request_path = (run_dir / "C-1.json").resolve()
        request_path.write_text("{}")
        run_id, command_id = rc.validate_request_path_shape(request_path)
        assert (run_id, command_id) == ("R-1", "C-1")

    def test_rejects_wrong_suffix(self, rc, fake_repo) -> None:
        run_dir = fake_repo / "runs" / "R-1" / "requests"
        run_dir.mkdir(parents=True)
        bad = (run_dir / "C-1.txt").resolve()
        bad.write_text("{}")
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_request_path_shape(bad)
        assert exc.value.category == "request_path_shape_invalid"

    def test_rejects_wrong_parent_name(self, rc, fake_repo) -> None:
        run_dir = fake_repo / "runs" / "R-1" / "wrong"
        run_dir.mkdir(parents=True)
        bad = (run_dir / "C-1.json").resolve()
        bad.write_text("{}")
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_request_path_shape(bad)
        assert exc.value.category == "request_path_shape_invalid"

    def test_rejects_wrong_nesting_depth(self, rc, fake_repo) -> None:
        run_dir = fake_repo / "runs" / "requests"
        run_dir.mkdir(parents=True)
        bad = (run_dir / "C-1.json").resolve()
        bad.write_text("{}")
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_request_path_shape(bad)
        assert exc.value.category == "request_path_shape_invalid"

    def test_rejects_path_outside_runs(self, rc, fake_repo) -> None:
        outside = fake_repo / "requests" / "C-1.json"
        outside.parent.mkdir(parents=True)
        outside.write_text("{}")
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_request_path_shape(outside.resolve())
        assert exc.value.category == "request_path_shape_invalid"


# ---------------------------------------------------------------------------
# Strict request-value validation
# ---------------------------------------------------------------------------


class TestLoadRequest:
    def _base_body(self, target_repo_path: str, working_directory: str) -> dict:
        return {
            "task_id": "T-1", "run_id": "R-1", "command_id": "C-1",
            "command": "python -m pytest test_sample.py::test_pass",
            "working_directory": working_directory,
            "target_repo_path": target_repo_path,
            "purpose": "unit test",
        }

    def test_accepts_well_formed_request(self, rc, fake_repo) -> None:
        target = _make_target_repo(fake_repo)
        body = self._base_body(_rel(target, fake_repo), _rel(target / "fixture-src", fake_repo))
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        req = rc.load_request(_rel(req_path, fake_repo))
        assert req["task_id"] == "T-1"
        assert req["timeout_seconds"] == rc.DEFAULT_TIMEOUT

    def test_rejects_missing_field(self, rc, fake_repo) -> None:
        body = self._base_body("x", "y")
        del body["purpose"]
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(_rel(req_path, fake_repo))
        assert exc.value.category == "request_field_missing"

    def test_rejects_unknown_field(self, rc, fake_repo) -> None:
        body = self._base_body("x", "y")
        body["extra"] = "nope"
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(_rel(req_path, fake_repo))
        assert exc.value.category == "request_field_unknown"

    def test_rejects_boolean_timeout_seconds(self, rc, fake_repo) -> None:
        body = self._base_body("x", "y")
        body["timeout_seconds"] = True
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(_rel(req_path, fake_repo))
        assert exc.value.category == "request_value_invalid"

    def test_rejects_out_of_range_timeout_seconds(self, rc, fake_repo) -> None:
        body = self._base_body("x", "y")
        body["timeout_seconds"] = 901
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(_rel(req_path, fake_repo))
        assert exc.value.category == "request_value_invalid"

    def test_rejects_absolute_target_repo_path_value(self, rc, fake_repo) -> None:
        body = self._base_body("/etc", "y")
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(_rel(req_path, fake_repo))
        assert exc.value.category == "request_value_invalid"

    def test_rejects_path_body_identifier_mismatch(self, rc, fake_repo) -> None:
        body = self._base_body("x", "y")
        body["run_id"] = "R-DIFFERENT"
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(_rel(req_path, fake_repo))
        assert exc.value.category == "request_path_identifier_mismatch"

    def test_rejects_malformed_json(self, rc, fake_repo) -> None:
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        req_path.parent.mkdir(parents=True, exist_ok=True)
        req_path.write_text("{not json")
        with pytest.raises(rc.Rejected) as exc:
            rc.load_request(_rel(req_path, fake_repo))
        assert exc.value.category == "request_file_malformed"

    def test_never_modifies_request_file(self, rc, fake_repo) -> None:
        target = _make_target_repo(fake_repo)
        body = self._base_body(_rel(target, fake_repo), _rel(target / "fixture-src", fake_repo))
        req_path = fake_repo / "runs" / "R-1" / "requests" / "C-1.json"
        _write_request(req_path, body)
        before = req_path.read_bytes()
        rc.load_request(_rel(req_path, fake_repo))
        assert req_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Real filesystem containment
# ---------------------------------------------------------------------------


class TestResolveAndContain:
    def test_accepts_valid_pair(self, rc, fake_repo) -> None:
        target = _make_target_repo(fake_repo)
        t, w = rc.resolve_and_contain(_rel(target, fake_repo), _rel(target / "fixture-src", fake_repo))
        assert w.is_relative_to(t)

    def test_rejects_working_directory_outside_target(self, rc, fake_repo) -> None:
        target = _make_target_repo(fake_repo)
        other = fake_repo / "runs" / "other"
        other.mkdir(parents=True)
        with pytest.raises(rc.Rejected) as exc:
            rc.resolve_and_contain(_rel(target, fake_repo), _rel(other, fake_repo))
        assert exc.value.category == "working_directory_escape"

    def test_rejects_nonexistent_target(self, rc, fake_repo) -> None:
        with pytest.raises(rc.Rejected) as exc:
            rc.resolve_and_contain("runs/does-not-exist", "runs/does-not-exist")
        assert exc.value.category == "target_repo_path_unresolvable"

    def test_rejects_file_as_target(self, rc, fake_repo) -> None:
        target = _make_target_repo(fake_repo)
        a_file = target / "fixture-src" / "test_sample.py"
        with pytest.raises(rc.Rejected) as exc:
            rc.resolve_and_contain(_rel(a_file, fake_repo), _rel(a_file, fake_repo))
        assert exc.value.category == "target_repo_path_not_a_directory"


# ---------------------------------------------------------------------------
# Windows-safe tokenizer
# ---------------------------------------------------------------------------


class TestTokenizer:
    def test_quoted_windows_path_preserved(self, rc) -> None:
        tokens = rc.tokenize_windows_safe(
            'python -m pytest "runs\\test-runner-boundary-test\\fixture-src\\test_sample.py"'
        )
        assert tokens[-1] == "runs\\test-runner-boundary-test\\fixture-src\\test_sample.py"

    def test_space_containing_path(self, rc) -> None:
        tokens = rc.tokenize_windows_safe('python -m pytest "tests/a b.py"')
        assert tokens[-1] == "tests/a b.py"

    def test_bare_backslash_path_no_quotes(self, rc) -> None:
        tokens = rc.tokenize_windows_safe(r"python -m pytest fixture-src\test_sample.py")
        assert tokens[-1] == r"fixture-src\test_sample.py"

    def test_python_exe_head(self, rc) -> None:
        tokens = rc.tokenize_windows_safe("python.exe -m pytest test_sample.py")
        assert tokens[0] == "python.exe"

    def test_py_launcher_head_tokenizes(self, rc) -> None:
        tokens = rc.tokenize_windows_safe("py -m pytest test_sample.py")
        assert tokens[0] == "py"

    def test_quoted_k_expression_one_token(self, rc) -> None:
        tokens = rc.tokenize_windows_safe('python -m pytest test_sample.py -k "test_foo or test_bar"')
        assert tokens[-1] == "test_foo or test_bar"

    def test_node_id_preserved(self, rc) -> None:
        tokens = rc.tokenize_windows_safe("python -m pytest test_sample.py::test_pass")
        assert tokens[-1] == "test_sample.py::test_pass"

    def test_unterminated_quote_rejected(self, rc) -> None:
        assert rc.tokenize_windows_safe('python -m pytest "unterminated') is None

    def test_single_quote_rejected(self, rc) -> None:
        assert rc.tokenize_windows_safe("python -m pytest 'nope'") is None


# ---------------------------------------------------------------------------
# Positive grammar
# ---------------------------------------------------------------------------


class TestGrammar:
    def test_accepts_python_dash_m_pytest(self, rc) -> None:
        assert rc.validate_grammar(["python", "-m", "pytest", "test_sample.py"]) == ["test_sample.py"]

    def test_accepts_python_exe(self, rc) -> None:
        rc.validate_grammar(["python.exe", "-m", "pytest", "test_sample.py"])

    def test_rejects_bare_pytest(self, rc) -> None:
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_grammar(["pytest", "test_sample.py"])
        assert exc.value.category == "grammar_no_match"

    def test_rejects_py_launcher(self, rc) -> None:
        with pytest.raises(rc.Rejected):
            rc.validate_grammar(["py", "-m", "pytest", "test_sample.py"])

    def test_rejects_python3(self, rc) -> None:
        with pytest.raises(rc.Rejected):
            rc.validate_grammar(["python3", "-m", "pytest", "test_sample.py"])

    def test_rejects_absolute_interpreter_path(self, rc) -> None:
        with pytest.raises(rc.Rejected):
            rc.validate_grammar(["/usr/bin/python", "-m", "pytest", "test_sample.py"])
        with pytest.raises(rc.Rejected):
            rc.validate_grammar([r"C:\Python311\python.exe", "-m", "pytest", "test_sample.py"])

    def test_rejects_pip_module(self, rc) -> None:
        with pytest.raises(rc.Rejected):
            rc.validate_grammar(["python", "-m", "pip", "install", "requests"])

    def test_rejects_unittest_module(self, rc) -> None:
        with pytest.raises(rc.Rejected):
            rc.validate_grammar(["python", "-m", "unittest", "test_sample.py"])


class TestValidateArgs:
    def test_accepts_recognized_flags_and_target(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        wd = target / "src"
        wd.mkdir(parents=True)
        args = rc.validate_args(["-k", "test_x", "-v", "--maxfail=2", "--tb=short", "test_sample.py"], wd, target)
        assert "test_sample.py" in args

    def test_rejects_unknown_flag(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        wd = target / "src"
        wd.mkdir(parents=True)
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_args(["-p", "no:cacheprovider", "test_sample.py"], wd, target)
        assert exc.value.category == "grammar_no_match"

    def test_rejects_bad_tb_value(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        wd = target / "src"
        wd.mkdir(parents=True)
        with pytest.raises(rc.Rejected):
            rc.validate_args(["--tb=weird", "test_sample.py"], wd, target)

    def test_rejects_non_integer_maxfail(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        wd = target / "src"
        wd.mkdir(parents=True)
        with pytest.raises(rc.Rejected):
            rc.validate_args(["--maxfail=abc", "test_sample.py"], wd, target)

    def test_rejects_zero_positional_targets(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        wd = target / "src"
        wd.mkdir(parents=True)
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_args(["-v"], wd, target)
        assert exc.value.category == "grammar_no_match"

    def test_rejects_positional_with_dotdot(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        wd = target / "src"
        wd.mkdir(parents=True)
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_args(["../../etc/passwd"], wd, target)
        assert exc.value.category == "positional_path_escape"

    def test_rejects_absolute_positional(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        wd = target / "src"
        wd.mkdir(parents=True)
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_args([r"C:\Windows\System32"], wd, target)
        assert exc.value.category == "positional_path_escape"

    def test_rejects_positional_resolving_to_repo_root_including_dot(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        target.mkdir(parents=True)
        with pytest.raises(rc.Rejected) as exc:
            rc.validate_args(["."], target, target)
        assert exc.value.category == "positional_path_is_repo_root"

    def test_accepts_subdirectory_target(self, rc, tmp_path) -> None:
        target = tmp_path / "target"
        (target / "src").mkdir(parents=True)
        rc.validate_args(["src"], target, target)


# ---------------------------------------------------------------------------
# Immutable retained-log path
# ---------------------------------------------------------------------------


class TestDeriveLogPath:
    def test_creates_fresh_log_path(self, rc, fake_repo) -> None:
        path = rc.derive_log_path("R-1", "C-1")
        assert path.parent.exists()
        assert not path.exists()

    def test_rejects_collision_never_overwrites(self, rc, fake_repo) -> None:
        path = rc.derive_log_path("R-1", "C-1")
        path.write_text("already here")
        with pytest.raises(rc.Rejected) as exc:
            rc.derive_log_path("R-1", "C-1")
        assert exc.value.category == "evidence_collision"
        assert path.read_text() == "already here"


# ---------------------------------------------------------------------------
# Mutation detection -- plain filesystem hash comparison
# ---------------------------------------------------------------------------


class TestMutationDetection:
    def test_no_change_detected(self, rc, tmp_path) -> None:
        target = tmp_path / "t"
        target.mkdir()
        (target / "a.py").write_text("x = 1\n")
        before = rc.hash_tree(target)
        after = rc.hash_tree(target)
        assert rc.diff_tree(before, after) == []

    def test_modification_detected(self, rc, tmp_path) -> None:
        target = tmp_path / "t"
        target.mkdir()
        f = target / "a.py"
        f.write_text("x = 1\n")
        before = rc.hash_tree(target)
        f.write_text("x = 2\n")
        after = rc.hash_tree(target)
        assert rc.diff_tree(before, after) == ["a.py"]

    def test_addition_detected(self, rc, tmp_path) -> None:
        target = tmp_path / "t"
        target.mkdir()
        before = rc.hash_tree(target)
        (target / "new.py").write_text("y = 1\n")
        after = rc.hash_tree(target)
        assert rc.diff_tree(before, after) == ["new.py"]

    def test_deletion_detected(self, rc, tmp_path) -> None:
        target = tmp_path / "t"
        target.mkdir()
        f = target / "a.py"
        f.write_text("x = 1\n")
        before = rc.hash_tree(target)
        f.unlink()
        after = rc.hash_tree(target)
        assert rc.diff_tree(before, after) == ["a.py"]

    def test_cache_dirs_excluded(self, rc, tmp_path) -> None:
        target = tmp_path / "t"
        target.mkdir()
        cache = target / "__pycache__"
        cache.mkdir()
        (cache / "a.pyc").write_bytes(b"junk")
        before = rc.hash_tree(target)
        (cache / "a.pyc").write_bytes(b"different junk")
        (target / "__pycache__" / "b.pyc").write_bytes(b"more")
        after = rc.hash_tree(target)
        assert rc.diff_tree(before, after) == []


# ---------------------------------------------------------------------------
# Real subprocess-level integration tests against the actual script file.
# Self-cleaning: each test removes its own request/log/fixture files afterward.
# ---------------------------------------------------------------------------


def _fresh_command_id() -> str:
    return f"pytest-{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def real_command_id():
    cid = _fresh_command_id()
    yield cid
    for sub in ("requests", "logs"):
        p = REPO_ROOT / "runs" / BOUNDARY_RUN_ID / sub / (f"{cid}.json" if sub == "requests" else f"{cid}.log")
        if p.exists():
            p.unlink()


def _write_real_request(command_id: str, **fields) -> Path:
    req_path = REPO_ROOT / "runs" / BOUNDARY_RUN_ID / "requests" / f"{command_id}.json"
    req_path.parent.mkdir(parents=True, exist_ok=True)
    body = {"task_id": "T-PYTEST", "run_id": BOUNDARY_RUN_ID, "command_id": command_id, **fields}
    req_path.write_text(json.dumps(body), encoding="utf-8")
    return req_path


def _run_real_wrapper(req_path: Path) -> dict:
    rel = _rel(req_path, REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--request-file", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    lines = [l for l in completed.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"wrapper must print exactly one JSON line, got: {completed.stdout!r}"
    return json.loads(lines[0])


class TestEndToEndSubprocess:
    def test_real_passing_execution(self, real_command_id) -> None:
        req = _write_real_request(
            real_command_id,
            command="python -m pytest test_sample.py::test_pass -v",
            working_directory="runs/test-runner-boundary-test/fixture-repo/fixture-src",
            target_repo_path="runs/test-runner-boundary-test/fixture-repo",
            purpose="pytest integration: real passing execution",
        )
        result = _run_real_wrapper(req)
        assert result["message_type"] == "command_result"
        assert result["exit_code"] == 0
        assert result["command"] == "python -m pytest test_sample.py::test_pass -v"
        assert "output_ref" in result
        log_path = REPO_ROOT / result["output_ref"]
        assert log_path.exists()
        log_path.unlink()  # the fixture cleans up requests/logs by convention; log path varies by stage

    def test_real_failing_execution_preserves_real_nonzero_exit_code(self, real_command_id) -> None:
        req = _write_real_request(
            real_command_id,
            command="python -m pytest test_sample.py::test_fail -v",
            working_directory="runs/test-runner-boundary-test/fixture-repo/fixture-src",
            target_repo_path="runs/test-runner-boundary-test/fixture-repo",
            purpose="pytest integration: real failing execution",
        )
        result = _run_real_wrapper(req)
        assert result["exit_code"] not in (0, 124, 125, 126)
        log_path = REPO_ROOT / result["output_ref"]
        if log_path.exists():
            log_path.unlink()

    def test_evidence_collision_on_rerun(self, real_command_id) -> None:
        req = _write_real_request(
            real_command_id,
            command="python -m pytest test_sample.py::test_pass -v",
            working_directory="runs/test-runner-boundary-test/fixture-repo/fixture-src",
            target_repo_path="runs/test-runner-boundary-test/fixture-repo",
            purpose="pytest integration: evidence collision",
        )
        first = _run_real_wrapper(req)
        assert first["message_type"] == "command_result"
        second = _run_real_wrapper(req)
        assert second["message_type"] == "command_rejected"
        assert second["rejection_category"] == "evidence_collision"
        log_path = REPO_ROOT / first["output_ref"]
        if log_path.exists():
            log_path.unlink()

    def test_timeout_returns_124(self, real_command_id) -> None:
        req = _write_real_request(
            real_command_id,
            command="python -m pytest test_sleep.py -v",
            working_directory="runs/test-runner-boundary-test/fixture-repo/fixture-src",
            target_repo_path="runs/test-runner-boundary-test/fixture-repo",
            purpose="pytest integration: timeout",
            timeout_seconds=2,
        )
        result = _run_real_wrapper(req)
        assert result["exit_code"] == 124
        log_path = REPO_ROOT / result["output_ref"]
        if log_path.exists():
            log_path.unlink()

    def test_mutation_detected_returns_125_and_is_self_cleaning(self, real_command_id) -> None:
        cid = real_command_id
        fixture_dir = REPO_ROOT / "runs" / BOUNDARY_RUN_ID / "pytest-mutation-fixtures" / cid
        src = fixture_dir / "fixture-src"
        src.mkdir(parents=True)
        (src / "sibling.py").write_text("VALUE = 1\n", encoding="utf-8")
        (src / "test_mutates.py").write_text(
            "from pathlib import Path\n\n"
            "def test_appends_but_passes():\n"
            "    p = Path(__file__).parent / 'sibling.py'\n"
            "    with p.open('a', encoding='utf-8') as f:\n"
            "        f.write('\\n# mutated\\n')\n"
            "    assert True\n",
            encoding="utf-8",
        )
        try:
            req = _write_real_request(
                cid,
                command="python -m pytest test_mutates.py -v",
                working_directory=_rel(src, REPO_ROOT),
                target_repo_path=_rel(fixture_dir, REPO_ROOT),
                purpose="pytest integration: mutation detection",
            )
            result = _run_real_wrapper(req)
            assert result["exit_code"] == 125
            log_path = REPO_ROOT / result["output_ref"]
            log_text = log_path.read_text(encoding="utf-8")
            assert "MUTATION DETECTED" in log_text
            assert "real_pytest_exit_code: 0" in log_text
        finally:
            shutil.rmtree(fixture_dir, ignore_errors=True)
            log_path = REPO_ROOT / "runs" / BOUNDARY_RUN_ID / "logs" / f"{cid}.log"
            if log_path.exists():
                log_path.unlink()
