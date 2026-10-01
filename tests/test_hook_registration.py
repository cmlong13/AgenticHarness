"""The hook commands registered in .claude/settings.json must resolve their scripts from the
project root, never from whatever directory the session's shell happens to be in.

Regression guard for the defect observed in the 2026-10-01 audit: every hook was registered
as the cwd-relative `python .claude/hooks/<name>.py`, so once a Bash call ran with its cwd
inside runs/<run_id>/, the PreToolUse hook failed with "can't open file
...\\runs\\<run_id>\\.claude\\hooks\\skill_enforcement.py". The commands now use Claude
Code's `${CLAUDE_PROJECT_DIR}` placeholder (ASSIGNMENT.md §3.5: one canonical config
directory, referenced by an absolute path).

The execution tests run each registered command string exactly as written, through the
same shell Claude Code uses for hooks on Windows (Git Bash) or POSIX `sh`, from a nested
directory unrelated to the repository. Only pre_dispatch_check.py is executed: it has no
side effects, so nothing in the real repository is touched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
_SCRIPT_RE = re.compile(r'^python "\$\{CLAUDE_PROJECT_DIR\}/\.claude/hooks/([a-z_]+\.py)"$')


def _registered_commands() -> list[str]:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [
        hook["command"]
        for groups in settings["hooks"].values()
        for group in groups
        for hook in group["hooks"]
        if hook.get("type") == "command"
    ]


def _command_for(script: str) -> str:
    matches = [c for c in _registered_commands() if c.endswith(f'/.claude/hooks/{script}"')]
    assert len(matches) >= 1, script
    return matches[0]


def _shell() -> str | None:
    if sys.platform == "win32":
        bash = shutil.which("bash")
        # Claude Code runs Windows hooks under Git Bash; System32\bash.exe is WSL, not that.
        return bash if bash and "system32" not in bash.lower() else None
    return shutil.which("sh")


def _run(command: str, payload: dict, cwd: Path, *, project_dir: str | None) -> subprocess.CompletedProcess:
    shell = _shell()
    if shell is None:
        pytest.skip("no Git Bash / POSIX shell available to run hook commands the way Claude Code does")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = project_dir
    return subprocess.run(
        [shell, "-c", command], input=json.dumps(payload), cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60,
    )


def _agent_payload(subagent_type: str, prompt: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": subagent_type, "prompt": prompt, "description": "x"},
    }


class TestRegistration:
    def test_every_hook_command_is_anchored_at_the_project_root(self) -> None:
        commands = _registered_commands()
        assert len(commands) == 5
        for command in commands:
            match = _SCRIPT_RE.match(command)
            assert match, f"hook command is not ${{CLAUDE_PROJECT_DIR}}-anchored: {command!r}"
            assert (REPO_ROOT / ".claude" / "hooks" / match.group(1)).is_file(), command

    def test_no_cwd_relative_hook_paths_remain(self) -> None:
        for command in _registered_commands():
            assert "python .claude/" not in command
            assert not re.search(r'(^|\s)\.claude/hooks/', command), command


class TestExecutionFromAnotherDirectory:
    @pytest.fixture()
    def elsewhere(self, tmp_path) -> Path:
        nested = tmp_path / "runs" / "run-x" / "deep"
        nested.mkdir(parents=True)
        return nested

    def test_hook_runs_from_a_subdirectory(self, elsewhere) -> None:
        result = _run(
            _command_for("pre_dispatch_check.py"), _agent_payload("general-purpose", "{}"), elsewhere,
            project_dir=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert "can't open file" not in result.stderr

    def test_hook_still_blocks_a_bad_dispatch_from_a_subdirectory(self, elsewhere) -> None:
        # A controlled Engineer dispatch that names no run_id/task_id is a real policy block.
        result = _run(
            _command_for("pre_dispatch_check.py"), _agent_payload("engineer", "{}"), elsewhere,
            project_dir=str(REPO_ROOT),
        )
        assert result.returncode == 2
        assert "pre-dispatch check: refusing to dispatch" in result.stderr

    def test_unresolvable_project_dir_fails_closed(self, elsewhere) -> None:
        # If the placeholder ever resolved to nothing, the script cannot be found; Python
        # exits 2 for a missing script, which Claude Code treats as a block, not a pass.
        result = _run(
            _command_for("pre_dispatch_check.py"), _agent_payload("general-purpose", "{}"), elsewhere,
            project_dir=None,
        )
        assert result.returncode == 2
        assert "can't open file" in result.stderr

    def test_old_relative_form_reproduces_the_defect(self, elsewhere) -> None:
        result = _run(
            "python .claude/hooks/pre_dispatch_check.py", _agent_payload("general-purpose", "{}"), elsewhere,
            project_dir=str(REPO_ROOT),
        )
        assert result.returncode == 2
        assert "can't open file" in result.stderr
