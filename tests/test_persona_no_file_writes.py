"""No persona tells its agent to write a file under data/.

Every dispatch round runs inside the dispatch guard (engine.dispatch_guard):
a file an agent writes in the checkout aborts the session, and a journal is
rewritten by the session from the text the agent returns. A persona that says
"maintain your journal at data/..." invites exactly the write that aborts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_AGENTS = Path(__file__).resolve().parents[1] / ".claude" / "agents"
# A write verb that governs a data/ path later in the same sentence. Reading a
# path and then writing prose ("read data/x before writing the blog") is fine.
_WRITE_TO_DATA = re.compile(
    r"\b(?:write|edit|maintain|update|save|append|create|modify|overwrite|record)"
    r"\w*\b[^.\n]{0,120}?`?data/",
    re.IGNORECASE,
)


def _personas() -> list[Path]:
    return sorted(_AGENTS.glob("*.md"))


def test_the_persona_directory_is_read() -> None:
    assert len(_personas()) > 1


@pytest.mark.parametrize("path", _personas(), ids=lambda p: p.stem)
def test_no_persona_tells_its_agent_to_write_under_data(path: Path) -> None:
    hits = [m.group(0) for m in _WRITE_TO_DATA.finditer(path.read_text())]
    assert hits == [], f"{path.name} tells its agent to write a file: {hits}"


def test_the_pattern_catches_the_line_it_was_written_for() -> None:
    old = "You also maintain your own journal at `data/agent_memory/the-oracle.md`."
    assert _WRITE_TO_DATA.search(old)
    assert not _WRITE_TO_DATA.search(
        "Read your journal from data/agent_memory/x.md before writing the blog."
    )
