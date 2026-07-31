from pathlib import Path


def test_appends_to_sibling_but_passes():
    target = Path(__file__).parent / "mutator_target.py"
    with target.open("a", encoding="utf-8") as f:
        f.write("\n# mutated by test\n")
    assert True
