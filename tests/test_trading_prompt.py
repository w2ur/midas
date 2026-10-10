"""The Step 2 task body is rendered in code, not rebuilt by each session (#99)."""

from __future__ import annotations

from datetime import date

import pytest

import scripts.daily_session as ds


@pytest.fixture
def no_triggers(monkeypatch):
    monkeypatch.setattr(ds, "render_active_triggers_for_agent", lambda a: "TRIGGERS-FOR-" + a)


def test_every_placeholder_is_filled(no_triggers) -> None:
    text = ds.render_trading_prompt("satoshi", date(2026, 10, 12), date(2026, 10, 9))
    for field in ds._TRADING_PROMPT_FIELDS:
        assert "{" + field + "}" not in text
    assert "data/portfolios/satoshi/portfolio.json" in text
    assert "It is session day 2026-10-12." in text
    assert "data/blog/2026-10-09.md" in text
    assert ds.CONDITIONAL_ORDER_INSTRUCTIONS in text
    assert "TRIGGERS-FOR-satoshi" in text


def test_the_json_schema_reaches_the_agent_intact(no_triggers) -> None:
    """The reason the helper does not use ``str.format``: the schema's braces.

    The control is the failure the sessions hit (#77, #78): ``format`` on the
    same text raises before any agent sees it.
    """
    text = ds.render_trading_prompt("satoshi", date(2026, 10, 12), date(2026, 10, 9))
    assert '"commentary": "your day\'s reasoning' in text
    assert '{"target_order_id": "ord_...", "reasoning": "..."}' in text
    with pytest.raises((KeyError, IndexError, ValueError)):
        ds.TRADING_PROMPT.format(
            agent_id="satoshi", today="2026-10-12", yesterday="2026-10-09",
            conditional_instructions="", active_triggers="",
        )


def test_yesterday_is_the_previous_narrated_session(tmp_path) -> None:
    for day in ("2026-10-08", "2026-10-09", "2026-10-12", "notes"):
        (tmp_path / f"{day}.md").write_text("x")
    # Monday reads Friday's post, not Sunday's, which does not exist.
    assert ds._previous_blog_date(date(2026, 10, 12), tmp_path) == date(2026, 10, 9)


def test_yesterday_without_any_earlier_post_is_the_calendar_day_before(tmp_path) -> None:
    (tmp_path / "2026-10-12.md").write_text("x")
    assert ds._previous_blog_date(date(2026, 10, 12), tmp_path) == date(2026, 10, 11)


def test_the_trigger_prompt_calls_the_helper() -> None:
    """The live prompt must not carry its own copy of the body again."""
    doc = (ds.Path(__file__).resolve().parents[1] / "docs/triggers/weekday-session.md").read_text()
    assert "render_trading_prompt(agent_id, today)" in doc
    assert "TRADING_PROMPT.format(" not in doc
    assert '"commentary": "your day' not in doc


def test_step_0_removes_an_earlier_fires_untracked_data_after_the_reset() -> None:
    """`reset --hard` keeps untracked files, and the session commit stages data/.

    A reused sandbox VM would otherwise publish a failed fire's leftovers. No
    ``-x``: the ignored session state and caches must survive the clean.
    """
    doc = (ds.Path(__file__).resolve().parents[1] / "docs/triggers/weekday-session.md").read_text()
    lines = doc.splitlines()
    reset = lines.index("git reset --hard origin/main")
    clean = [i for i, line in enumerate(lines) if line.startswith("git clean")]
    assert [lines[i] for i in clean] == ["git clean -fd -- data/"]
    assert clean[0] > reset
    assert clean[0] < next(i for i, line in enumerate(lines) if line.startswith("# Step 0c"))
