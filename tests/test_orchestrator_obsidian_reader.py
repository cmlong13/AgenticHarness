"""Tests for harness/orchestrator/obsidian_reader.py -- the deterministic Obsidian
vault READ boundary the orchestrator drives during Discovery/Research.

No personal vault is ever touched: every test points OBSIDIAN_VAULT_PATH (via an
explicit `env` dict passed to the reader) at a pytest `tmp_path`. The read cardinal
rule -- a vault note is historical/contextual evidence, never repository truth -- is a
`work/SKILL.md` / `architect.md` / `obsidian` SKILL.md prose contract, exercised by
tests/test_work_skill.py, tests/test_agent_definitions.py, and
tests/test_skill_definitions.py; this file covers the mechanism.
"""
from __future__ import annotations

import hashlib

import pytest

from harness.orchestrator import obsidian_reader as reader


# ---------------------------------------------------------------------------
# Vault fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "Knowledge Vault"
    (v / "Design").mkdir(parents=True)
    (v / "Decisions").mkdir()
    (v / ".obsidian").mkdir()
    (v / ".trash").mkdir()
    (v / "Design" / "Risk band calibration.md").write_text(
        "# Risk band calibration\n\n"
        "Past decision (2025): risk-band thresholds set at 620 / 680 / 740.\n"
        "Calibration rationale: the 680 cut matched the historical default-rate elbow.\n",
        encoding="utf-8",
    )
    (v / "Decisions" / "ADR-002 explanation ordering.md").write_text(
        "# ADR-002: decision explanation ordering\n\n"
        "We append the risk band to the human-readable explanation, after the DTI line.\n",
        encoding="utf-8",
    )
    (v / "Decisions" / "Unicode café notes.md").write_text(
        "Café résumé — naïve threshold picking is risky. Touchstone: the 680 elbow.\n",
        encoding="utf-8",
    )
    (v / "empty.md").write_text("", encoding="utf-8")
    (v / "not-markdown.txt").write_text("risk band calibration appears here too\n", encoding="utf-8")
    (v / ".obsidian" / "workspace.json").write_text('{"note": "risk band calibration secret"}', encoding="utf-8")
    (v / ".trash" / "old risk band note.md").write_text("risk band calibration deleted draft\n", encoding="utf-8")
    (v / "Design" / ".hidden calibration.md").write_text("risk band calibration hidden\n", encoding="utf-8")
    return v


def _env(vault):
    return {"OBSIDIAN_VAULT_PATH": str(vault)}


# ---------------------------------------------------------------------------
# Destination resolution
# ---------------------------------------------------------------------------


class TestResolveVault:
    def test_configured_vault_resolves(self, vault):
        assert reader.resolve_vault(_env(vault)) == vault.resolve()

    def test_missing_env_var_is_connector_unavailable(self):
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.resolve_vault({})
        assert exc.value.code == "connector_unavailable"

    def test_blank_env_var_is_connector_unavailable(self):
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.resolve_vault({"OBSIDIAN_VAULT_PATH": "   "})
        assert exc.value.code == "connector_unavailable"

    def test_relative_path_is_invalid_destination(self):
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.resolve_vault({"OBSIDIAN_VAULT_PATH": "some/relative/vault"})
        assert exc.value.code == "invalid_destination"

    def test_traversal_in_configured_path_is_invalid_destination(self, tmp_path):
        target = f"{tmp_path}/../{tmp_path.name}/vault"
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.resolve_vault({"OBSIDIAN_VAULT_PATH": target})
        assert exc.value.code == "invalid_destination"

    def test_nonexistent_vault_is_destination_unavailable(self, tmp_path):
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.resolve_vault({"OBSIDIAN_VAULT_PATH": str(tmp_path / "does-not-exist")})
        assert exc.value.code == "destination_unavailable"

    def test_file_not_directory_is_destination_unavailable(self, tmp_path):
        f = tmp_path / "a-file"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.resolve_vault({"OBSIDIAN_VAULT_PATH": str(f)})
        assert exc.value.code == "destination_unavailable"


# ---------------------------------------------------------------------------
# safe_note_target -- path escape prevention
# ---------------------------------------------------------------------------


class TestSafeNoteTarget:
    def test_normal_relative_note_resolves_inside_vault(self, vault):
        target = reader.safe_note_target(vault.resolve(), "Design/Risk band calibration.md")
        assert target.is_relative_to(vault.resolve())
        assert target.name == "Risk band calibration.md"

    @pytest.mark.parametrize(
        "bad_path",
        [
            "/etc/passwd",
            "C:/Windows/system.ini",
            "../outside.md",
            "Design/../../escape.md",
            "Design/note",  # not .md
            "Design/note.txt",
            ".obsidian/workspace.json",
            "Design/.hidden calibration.md",
            ".trash/old risk band note.md",
            "",
            "   ",
            ".",
        ],
    )
    def test_unsafe_paths_are_invalid_note_path(self, vault, bad_path):
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.safe_note_target(vault.resolve(), bad_path)
        assert exc.value.code == "invalid_note_path"

    def test_symlink_escape_is_caught(self, vault, tmp_path):
        outside = tmp_path / "outside-secret.md"
        outside.write_text("secret", encoding="utf-8")
        link = vault / "Design" / "link.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not permitted in this environment")
        with pytest.raises(reader.ObsidianReadError) as exc:
            reader.safe_note_target(vault.resolve(), "Design/link.md")
        assert exc.value.code == "invalid_note_path"


class TestEligibleFileWalkContainment:
    """The vault-wide search walk (_eligible_md_files) applies the same containment
    check safe_note_target applies to a single note -- a symlink/junction planted in
    the vault is never opened by a search."""

    def test_normal_nested_markdown_is_eligible(self, vault):
        rels = [p.relative_to(vault.resolve()).as_posix() for p in reader._eligible_md_files(vault.resolve())]
        assert "Design/Risk band calibration.md" in rels
        assert "Decisions/ADR-002 explanation ordering.md" in rels
        assert not any(r.startswith(".") or "/." in r for r in rels)
        assert not any(r.endswith(".txt") for r in rels)

    def test_symlinked_markdown_escaping_the_vault_is_excluded(self, vault, tmp_path):
        outside = tmp_path / "outside-secret.md"
        outside.write_text("PRIVATE outside content", encoding="utf-8")
        link = vault / "Decisions" / "escape.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not permitted in this environment")
        eligible = reader._eligible_md_files(vault.resolve())
        assert all(p.resolve().is_relative_to(vault.resolve()) for p in eligible)
        assert not any(p.name == "escape.md" for p in eligible)
        # and a search over that vault never surfaces the outside content
        s = reader.search(query="PRIVATE outside content", env=_env(vault))
        assert s.status == "no_matches"


# ---------------------------------------------------------------------------
# read_note
# ---------------------------------------------------------------------------


class TestReadNote:
    def test_reads_existing_markdown_note(self, vault):
        r = reader.read_note(note_path="Design/Risk band calibration.md", env=_env(vault))
        assert r.status == "read"
        assert r.note_path == "Design/Risk band calibration.md"
        assert "620 / 680 / 740" in r.content
        assert r.byte_count == len(
            (vault / "Design" / "Risk band calibration.md").read_bytes()
        )
        assert r.content_sha256 == hashlib.sha256(
            (vault / "Design" / "Risk band calibration.md").read_bytes()
        ).hexdigest()
        assert r.truncated is False

    def test_missing_note_is_not_found_never_a_different_file(self, vault):
        r = reader.read_note(note_path="Design/does-not-exist.md", env=_env(vault))
        assert r.status == "not_found"
        assert r.content is None

    def test_unicode_note_reads_cleanly(self, vault):
        r = reader.read_note(note_path="Decisions/Unicode café notes.md", env=_env(vault))
        assert r.status == "read"
        assert "Café résumé" in r.content

    def test_empty_note_is_read_not_error(self, vault):
        r = reader.read_note(note_path="empty.md", env=_env(vault))
        assert r.status == "read"
        assert r.content == ""
        assert r.byte_count == 0

    def test_non_markdown_extension_is_invalid_note_path(self, vault):
        r = reader.read_note(note_path="not-markdown.txt", env=_env(vault))
        assert r.status == "invalid_note_path"

    def test_traversal_is_invalid_note_path(self, vault):
        r = reader.read_note(note_path="../../../etc/passwd", env=_env(vault))
        assert r.status == "invalid_note_path"

    def test_dot_obsidian_is_invalid_note_path(self, vault):
        r = reader.read_note(note_path=".obsidian/workspace.json", env=_env(vault))
        assert r.status == "invalid_note_path"

    def test_hidden_markdown_note_is_invalid_note_path(self, vault):
        r = reader.read_note(note_path="Design/.hidden calibration.md", env=_env(vault))
        assert r.status == "invalid_note_path"

    def test_missing_connector_is_connector_unavailable(self):
        r = reader.read_note(note_path="Design/anything.md", env={})
        assert r.status == "connector_unavailable"

    def test_large_note_body_is_bounded_but_identity_is_full(self, vault):
        big = "x" * (reader._MAX_NOTE_RETURN_BYTES + 5000)
        (vault / "big.md").write_text(big, encoding="utf-8")
        r = reader.read_note(note_path="big.md", env=_env(vault))
        assert r.status == "read"
        assert r.truncated is True
        assert len(r.content.encode("utf-8")) <= reader._MAX_NOTE_RETURN_BYTES
        assert r.byte_count == len(big.encode("utf-8"))
        assert r.content_sha256 == hashlib.sha256(big.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_exact_term_match_is_found(self, vault):
        s = reader.search(query="risk band calibration", env=_env(vault))
        assert s.status == "found"
        paths = [m.note_path for m in s.matches]
        assert "Design/Risk band calibration.md" in paths

    def test_search_is_case_insensitive(self, vault):
        lower = reader.search(query="risk band", env=_env(vault))
        upper = reader.search(query="RISK BAND", env=_env(vault))
        assert lower.status == upper.status == "found"
        assert [m.note_path for m in lower.matches] == [m.note_path for m in upper.matches]

    def test_multiple_matches_returned(self, vault):
        s = reader.search(query="risk band", env=_env(vault))
        assert len(s.matches) >= 2

    def test_deterministic_ordering_and_ranking(self, vault):
        first = reader.search(query="680 elbow threshold", env=_env(vault))
        second = reader.search(query="680 elbow threshold", env=_env(vault))
        assert [m.as_dict() for m in first.matches] == [m.as_dict() for m in second.matches]
        # scores are non-increasing across the returned list
        scores = [m.score for m in first.matches]
        assert scores == sorted(scores, reverse=True)

    def test_result_count_is_bounded(self, vault):
        for i in range(20):
            (vault / f"note-{i:02d}.md").write_text("risk band calibration everywhere\n", encoding="utf-8")
        s = reader.search(query="risk band calibration", env=_env(vault))
        assert s.status == "found"
        assert len(s.matches) <= reader.MAX_SEARCH_RESULTS
        assert s.truncated is True
        # even an explicit over-cap max_results cannot exceed the hard ceiling
        s2 = reader.search(query="risk band calibration", env=_env(vault), max_results=999)
        assert len(s2.matches) <= reader.MAX_SEARCH_RESULTS

    def test_no_matches_is_distinct_from_connector_failure(self, vault):
        s = reader.search(query="quantumfluxcapacitor", env=_env(vault))
        assert s.status == "no_matches"
        assert s.files_scanned >= 1

    def test_unicode_query_matches(self, vault):
        s = reader.search(query="Café résumé", env=_env(vault))
        assert s.status == "found"
        assert any("Unicode café notes.md" in m.note_path for m in s.matches)

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
    def test_blank_query_is_invalid_query(self, vault, bad):
        s = reader.search(query=bad, env=_env(vault))
        assert s.status == "invalid_query"

    def test_overlong_query_is_invalid_query(self, vault):
        s = reader.search(query="a " * 200, env=_env(vault))
        assert s.status == "invalid_query"

    def test_ignores_non_markdown_files(self, vault):
        s = reader.search(query="risk band calibration", env=_env(vault))
        assert all(m.note_path.lower().endswith(".md") for m in s.matches)
        assert not any("not-markdown" in m.note_path for m in s.matches)

    def test_ignores_dot_obsidian_and_trash_and_hidden(self, vault):
        s = reader.search(query="risk band calibration", env=_env(vault))
        joined = " ".join(m.note_path for m in s.matches)
        assert ".obsidian" not in joined
        assert ".trash" not in joined
        assert ".hidden" not in joined
        # and the secret content in .obsidian/workspace.json is never surfaced
        assert not any("secret" in m.snippet for m in s.matches)

    def test_does_not_dump_whole_vault(self, vault):
        # a query matching essentially everything still returns bounded, snippet-only
        s = reader.search(query="the", env=_env(vault))
        assert len(s.matches) <= reader.MAX_SEARCH_RESULTS
        for m in s.matches:
            assert len(m.snippet) <= 2 * reader.SNIPPET_RADIUS + 16

    def test_missing_connector_is_connector_unavailable(self):
        s = reader.search(query="risk band", env={})
        assert s.status == "connector_unavailable"

    def test_snippet_carries_enough_identity_to_cite(self, vault):
        s = reader.search(query="620 680 740", env=_env(vault))
        assert s.status == "found"
        top = s.matches[0]
        assert top.note_path == "Design/Risk band calibration.md"
        assert top.line >= 1
        assert top.snippet


# ---------------------------------------------------------------------------
# describe_vault
# ---------------------------------------------------------------------------


class TestDescribeVault:
    def test_configured(self, vault):
        d = reader.describe_vault(_env(vault))
        assert d["configured"] is True
        assert d["vault_path"] == str(vault.resolve())
        assert d["raw_env_value"] == str(vault)

    def test_unconfigured(self):
        d = reader.describe_vault({})
        assert d == {"configured": False, "vault_path": None, "raw_env_value": None}
