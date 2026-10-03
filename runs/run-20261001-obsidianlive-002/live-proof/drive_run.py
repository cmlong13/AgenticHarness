"""Launch one fresh headless `claude -p "/work ..."` process with OBSIDIAN_VAULT_PATH set
only in the child environment, and record a launch log + the raw stream-json transcript."""
import datetime, json, os, subprocess, sys
from pathlib import Path

label, prompt_file = sys.argv[1], Path(sys.argv[2])
out_dir = Path(__file__).parent / label
out_dir.mkdir(parents=True, exist_ok=False)
prompt = prompt_file.read_text(encoding="utf-8").strip()
vault = r"C:\Users\caleb\Documents\Obsidian Vault"
env = dict(os.environ, OBSIDIAN_VAULT_PATH=vault)
cmd = ["claude", "-p", prompt, "--permission-mode", "bypassPermissions",
       "--output-format", "stream-json", "--verbose"]
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
log = {"label": label, "launched_at": now(), "cwd": r"C:\Users\caleb\Documents\Projects\AgenticHarness",
       "prompt": prompt, "argv_without_prompt": cmd[:1] + cmd[3:],
       "env_overrides": {"OBSIDIAN_VAULT_PATH": vault},
       "parent_env_had_OBSIDIAN_VAULT_PATH": "OBSIDIAN_VAULT_PATH" in os.environ}
with open(out_dir / "stream.jsonl", "wb") as stream:
    proc = subprocess.Popen(cmd, cwd=log["cwd"], env=env, stdout=stream, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, shell=False)
    log["pid"] = proc.pid
    _, err = proc.communicate()
log["exited_at"] = now()
log["returncode"] = proc.returncode
log["stderr_tail"] = err.decode("utf-8", "replace")[-4000:]
(out_dir / "launch-log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
print(json.dumps({k: log[k] for k in ("label", "pid", "launched_at", "exited_at", "returncode")}))
