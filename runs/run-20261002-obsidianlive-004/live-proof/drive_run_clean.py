"""Launch one fresh headless `claude -p "/work ..."` process from the CLEAN sibling clone.
OBSIDIAN_VAULT_PATH is set only in the child environment; the main repository's .venv is
removed from PATH and replaced by the clone's own .venv, so no interpreter, package, or
working tree of the main repository is on the child's import/command path."""
import datetime, json, os, subprocess, sys
from pathlib import Path

label, prompt_file = sys.argv[1], Path(sys.argv[2])
out_dir = Path(__file__).parent / label
out_dir.mkdir(parents=True, exist_ok=False)
prompt = prompt_file.read_text(encoding="utf-8").strip()
clean_root = r"C:\Users\caleb\Documents\Projects\AgenticHarness-obslive004-clean"
main_root = r"C:\Users\caleb\Documents\Projects\AgenticHarness"
vault = r"C:\Users\caleb\Documents\Obsidian Vault"

env = dict(os.environ)
removed = [p for p in env["PATH"].split(os.pathsep) if p.lower().startswith(main_root.lower() + "\\")]
kept = [p for p in env["PATH"].split(os.pathsep) if p not in removed]
env["PATH"] = os.pathsep.join([clean_root + r"\.venv\Scripts"] + kept)
env["VIRTUAL_ENV"] = clean_root + r"\.venv"
env.pop("PYTHONPATH", None)
env["OBSIDIAN_VAULT_PATH"] = vault

cmd = ["claude", "-p", prompt, "--permission-mode", "bypassPermissions",
       "--output-format", "stream-json", "--verbose"]
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
log = {"label": label, "launched_at": now(), "cwd": clean_root, "prompt": prompt,
       "argv_without_prompt": cmd[:1] + cmd[3:],
       "env_overrides": {"OBSIDIAN_VAULT_PATH": vault, "VIRTUAL_ENV": env["VIRTUAL_ENV"],
                         "PATH_prepended": clean_root + r"\.venv\Scripts"},
       "PATH_entries_removed_main_repo": removed,
       "child_env_mentions_main_repo": [k for k, v in env.items() if main_root.lower() + "\\" in v.lower()
                                        or v.lower().rstrip("\\") == main_root.lower()]}
with open(out_dir / "stream.jsonl", "wb") as stream:
    proc = subprocess.Popen(cmd, cwd=clean_root, env=env, stdout=stream, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL)
    log["pid"] = proc.pid
    _, err = proc.communicate()
log["exited_at"] = now()
log["returncode"] = proc.returncode
log["stderr_tail"] = err.decode("utf-8", "replace")[-4000:]
(out_dir / "launch-log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
