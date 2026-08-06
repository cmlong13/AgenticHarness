# Lessons Learned

Reusable lessons about harness workflow, validation, orchestration, and engineering process -- grounded in a specific run and its retained evidence, not task-specific product trivia, and not a speculative recommendation presented as established fact. At most 5 new entries are appended per terminal run (see harness/orchestrator/memory.py's append_lessons and .claude/skills/work/SKILL.md's terminal memory-append step).

Format: `- [LESSON-ID] (source_run: <run_id>, evidence: <repo-relative path>, date: <YYYY-MM-DD>[, tags: <comma,separated,tags>]) <lesson text>`

- [L-20260806-TRANSPORT-BUDGET] (source_run: run-20260803-riskband-001, evidence: runs/run-20260803-riskband-001/logs/policy-events.jsonl, date: 2026-08-06, tags: transport,validation,json,correction-budget) Strict JSON transport failures (a fenced or prose-wrapped raw agent reply) must be classified and repaired separately from content failures, before any semantic or schema validation is attempted, and are only ever granted a single bounded correction attempt per phase rather than an unbounded retry loop.
