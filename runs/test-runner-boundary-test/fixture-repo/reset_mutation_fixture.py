"""Restore mutator_target.py to its exact baseline content.

test_mutates_but_passes.py appends to mutator_target.py on every real run, so without
this reset the fixture accumulates modifications across repeated manual invocations of
the mutation scenario, making reruns non-deterministic. Run this before every manual
mutation-test invocation (it does not run automatically -- Write/Edit are unavailable
while the test-runner skill is active, so the controlled caller must do this in a
separate step before invoking the skill, same as writing the request file itself).

Does not touch runs/test-runner-boundary-test/logs/ -- retained evidence from prior
mutation-detection runs is left untouched.
"""
from pathlib import Path

BASELINE = "VALUE = 1\n"
TARGET = Path(__file__).parent / "fixture-src" / "mutator_target.py"

if __name__ == "__main__":
    TARGET.write_text(BASELINE, encoding="utf-8")
    print(f"reset {TARGET} to baseline ({BASELINE!r})")
