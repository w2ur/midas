"""Tests for engine.output_bundle."""

import json
from datetime import date
from pathlib import Path

from engine.blog import BlogDraft
from engine.output_bundle import (
    ROSTER,
    assemble_output_bundle,
    get_day_number,
    save_output_bundle,
)
from engine.posts import PostPayload


class TestGetDayNumber:
    def test_empty_dir_returns_1(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.output_bundle.OUTPUT_DIR", tmp_path)
        assert get_day_number() == 1

    def test_counts_existing_bundles(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.output_bundle.OUTPUT_DIR", tmp_path)
        (tmp_path / "2026-04-14.json").write_text("{}")
        (tmp_path / "2026-04-15.json").write_text("{}")
        assert get_day_number() == 3

    def test_ignores_non_json(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.output_bundle.OUTPUT_DIR", tmp_path)
        (tmp_path / "2026-04-14.json").write_text("{}")
        (tmp_path / "readme.txt").write_text("not a bundle")
        assert get_day_number() == 2

    def test_idempotent_on_retry(self, tmp_path: Path, monkeypatch) -> None:
        """Key behaviour — re-running a session for an already-bundled day must
        return the day's *original* ordinal, not len+1."""
        monkeypatch.setattr("engine.output_bundle.OUTPUT_DIR", tmp_path)
        (tmp_path / "2026-04-14.json").write_text("{}")
        (tmp_path / "2026-04-15.json").write_text("{}")
        # Bundle for 2026-04-15 already exists. Re-running for that date should return 2, not 3.
        assert get_day_number(for_date=date(2026, 4, 15)) == 2

    def test_specific_date_ordinal(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.output_bundle.OUTPUT_DIR", tmp_path)
        (tmp_path / "2026-04-14.json").write_text("{}")
        (tmp_path / "2026-04-15.json").write_text("{}")
        (tmp_path / "2026-04-16.json").write_text("{}")
        assert get_day_number(for_date=date(2026, 4, 14)) == 1
        assert get_day_number(for_date=date(2026, 4, 16)) == 3


class TestAssembleOutputBundle:
    def test_bundle_shape(self) -> None:
        agent_results = {
            "satoshi": {
                "commentary": "Loading the dip.",
                "trades": [
                    {
                        "action": "BUY",
                        "ticker": "BTC-EUR",
                        "shares": 0.01,
                        "reasoning": "F&G 12",
                    }
                ],
            },
        }
        agent_posts = {
            "satoshi": [
                PostPayload("satoshi", "Loaded BTC.", [], "trade", None, {}, "23:00")
            ],
        }
        portfolio_summaries = {"satoshi": {"cash": 9356.8, "positions": ["BTC-EUR"]}}
        leaderboard = [{"agent": "satoshi", "return_pct": 1.2, "rank": 1}]
        market_data = {"sp500": 7000.0, "eur_usd": 1.18}
        blog = BlogDraft(title="Day 1", body_md="Body.", slug="day-1")
        oracle_posts = [
            PostPayload(
                "the-oracle", "Scoreboard.", [], "scoreboard", None, {}, "12:00"
            )
        ]

        bundle = assemble_output_bundle(
            bundle_date=date(2026, 4, 17),
            market_data=market_data,
            agent_results=agent_results,
            agent_posts=agent_posts,
            portfolio_summaries=portfolio_summaries,
            leaderboard=leaderboard,
            blog_draft=blog,
            oracle_posts=oracle_posts,
        )
        assert bundle["date"] == "2026-04-17"
        assert bundle["market_snapshot"] == market_data
        assert "satoshi" in bundle["agents"]
        assert bundle["agents"]["satoshi"]["commentary"] == "Loading the dip."
        assert len(bundle["agents"]["satoshi"]["posts"]) == 1
        assert bundle["agents"]["satoshi"]["posts"][0]["kind"] == "trade"
        assert bundle["agents"]["satoshi"]["portfolio"] == {
            "cash": 9356.8,
            "positions": ["BTC-EUR"],
        }
        assert bundle["narrator"]["blog_draft"]["title"] == "Day 1"
        assert len(bundle["narrator"]["posts"]) == 1
        assert bundle["narrator"]["posts"][0]["kind"] == "scoreboard"
        assert bundle["leaderboard"] == leaderboard

    def test_bundle_always_contains_full_roster(self) -> None:
        """Even when only one agent ran (weekend cadence), every agent in
        ROSTER appears in the bundle. Non-runners get null commentary, empty
        trades/posts, and their carry-forward portfolio summary.
        """
        agent_results = {
            "satoshi": {
                "commentary": "Holding.",
                "trades": [],
            },
        }
        agent_posts = {"satoshi": []}
        portfolio_summaries = {
            aid: {"cash": 1000.0, "deployed": 0.0, "positions": [], "currency": "EUR"}
            for aid in ROSTER
        }
        blog = BlogDraft(title="Day N", body_md="...", slug="day-n")

        bundle = assemble_output_bundle(
            bundle_date=date(2026, 4, 26),
            market_data={},
            agent_results=agent_results,
            agent_posts=agent_posts,
            portfolio_summaries=portfolio_summaries,
            leaderboard=[],
            blog_draft=blog,
            oracle_posts=[],
        )

        assert set(bundle["agents"].keys()) == set(ROSTER)
        # Running agent: full entry.
        assert bundle["agents"]["satoshi"]["commentary"] == "Holding."
        # Non-running agent: null commentary, empty trades/posts, carry-forward portfolio.
        non_runner = next(aid for aid in ROSTER if aid != "satoshi")
        assert bundle["agents"][non_runner]["commentary"] is None
        assert bundle["agents"][non_runner]["trades"] == []
        assert bundle["agents"][non_runner]["posts"] == []
        assert (
            bundle["agents"][non_runner]["portfolio"] == portfolio_summaries[non_runner]
        )

    def test_non_running_agent_with_no_summary_gets_empty_portfolio(self) -> None:
        """Defensive: if portfolio_summaries is missing an agent entirely, the
        bundle entry uses {} rather than crashing. Real orchestrator should
        always pass summaries for all 10, but this guards against regression."""
        blog = BlogDraft(title="X", body_md="x", slug="x")
        bundle = assemble_output_bundle(
            bundle_date=date(2026, 4, 26),
            market_data={},
            agent_results={},
            agent_posts={},
            portfolio_summaries={},
            leaderboard=[],
            blog_draft=blog,
            oracle_posts=[],
        )
        assert set(bundle["agents"].keys()) == set(ROSTER)
        for aid in ROSTER:
            assert bundle["agents"][aid]["commentary"] is None
            assert bundle["agents"][aid]["portfolio"] == {}


class TestSaveOutputBundle:
    def test_save_and_read(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.output_bundle.OUTPUT_DIR", tmp_path)
        bundle = {
            "date": "2026-04-17",
            "market_snapshot": {},
            "agents": {},
            "narrator": {"blog_draft": {}, "posts": []},
            "leaderboard": [],
        }
        path = save_output_bundle(date(2026, 4, 17), bundle)
        assert path.name == "2026-04-17.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["date"] == "2026-04-17"

    def test_idempotent_across_save_cycle(self, tmp_path: Path, monkeypatch) -> None:
        """After saving today's bundle, get_day_number(today) returns the same value
        as it did before the save."""
        monkeypatch.setattr("engine.output_bundle.OUTPUT_DIR", tmp_path)
        # Seed two prior days.
        (tmp_path / "2026-04-15.json").write_text("{}")
        (tmp_path / "2026-04-16.json").write_text("{}")
        today = date(2026, 4, 17)
        assert get_day_number(for_date=today) == 3

        bundle = {
            "date": "2026-04-17",
            "market_snapshot": {},
            "agents": {},
            "narrator": {"blog_draft": {}, "posts": []},
            "leaderboard": [],
        }
        save_output_bundle(today, bundle)

        # Re-derive — must still be 3 (not 4).
        assert get_day_number(for_date=today) == 3
