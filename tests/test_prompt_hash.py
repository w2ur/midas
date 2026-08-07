"""Tests for scripts.prompt_hash — the repo↔live-prompt bridge (W3.4).

The live RemoteTrigger prompt is out of the repo, so nothing could tell a
session running last month's prompt from one running this month's. The bridge
is a hash the live prompt carries and the checkout re-derives; these tests
cover the two ways that bridge can rot — the sidecar drifting from the doc,
and the hash failing to notice a real edit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import prompt_hash as ph


class TestSidecarIsCurrent:
    def test_committed_sidecar_matches_the_doc(self) -> None:
        """The CI guard. Editing the trigger prompt without regenerating the
        sidecar (`python scripts/prompt_hash.py --write`) turns this red."""
        assert ph.read_sidecar() == ph.prompt_sha256()

    def test_the_prompt_carries_its_own_hash(self) -> None:
        """Step 0d compares the live prompt's literal against the checkout.
        That only works if the literal is actually in the prompt text."""
        doc = ph.DOC_PATH.read_text(encoding="utf-8")
        assert f"PROMPT_SHA256: {ph.prompt_sha256()}" in doc


class TestHashSensitivity:
    """A hash that does not move when the prompt does is worse than none."""

    @staticmethod
    def _doc(tmp_path: Path, name: str, body: str) -> Path:
        p = tmp_path / f"{name}.md"
        p.write_text(f"# T\n\n## Trigger prompt\n\n```\n{body}\n```\n", "utf-8")
        return p

    def test_an_edit_to_the_prompt_changes_the_hash(self, tmp_path: Path) -> None:
        a = self._doc(tmp_path, "a", "Step 1\nrun the thing\n")
        b = self._doc(tmp_path, "b", "Step 1\nrun the other thing\n")
        assert ph.prompt_sha256(a) != ph.prompt_sha256(b)

    def test_the_self_referential_line_is_excluded(self, tmp_path: Path) -> None:
        """Otherwise the hash would have to contain itself. Two docs that
        differ only in their stamped hash must hash the same."""
        a = self._doc(tmp_path, "a", "PROMPT_SHA256: " + "0" * 64 + "\nStep 1\n")
        b = self._doc(tmp_path, "b", "PROMPT_SHA256: " + "f" * 64 + "\nStep 1\n")
        assert ph.prompt_sha256(a) == ph.prompt_sha256(b)

    def test_prose_outside_the_prompt_block_does_not_move_the_hash(
        self, tmp_path: Path
    ) -> None:
        """The doc's surrounding commentary (incident notes, rationale) is
        maintained freely; only the pasted prompt is under the contract."""
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        block = "## Trigger prompt\n\n```\nStep 1\n```\n"
        a.write_text("# T\n\nsome rationale\n\n" + block, "utf-8")
        b.write_text("# T\n\nquite different rationale\n\n" + block, "utf-8")
        assert ph.prompt_sha256(a) == ph.prompt_sha256(b)


class TestCli:
    def test_expect_matching_hash_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.argv", ["prompt_hash", "--expect", ph.prompt_sha256()])
        assert ph.main() == 0

    def test_expect_stale_hash_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.argv", ["prompt_hash", "--expect", "d" * 64])
        assert ph.main() == 1
        assert "PROMPT DRIFT" in capsys.readouterr().err

    def test_check_passes_on_the_committed_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.argv", ["prompt_hash", "--check"])
        assert ph.main() == 0


class TestMalformedDoc:
    def test_missing_heading_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "d.md"
        p.write_text("# nothing here\n", "utf-8")
        with pytest.raises(ph.PromptBlockError, match="not found"):
            ph.extract_prompt(p)

    def test_unclosed_fence_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "d.md"
        p.write_text("## Trigger prompt\n\n```\nStep 1\n", "utf-8")
        with pytest.raises(ph.PromptBlockError, match="not closed"):
            ph.extract_prompt(p)
