"""Tests for scripts.fetch_sentiment — deterministic sentiment-headline collector.

All tests mock _fetch_news (no network). Output goes to tmp_path.

Design choices tested:
- sanitize_headline: pure function, unit-tested for each sanitization rule
- Active-ticker scoping: held ∪ pending only, no universe bleed
- Max-10 cap: newest-first truncation
- Graceful degradation: per-ticker failure skips that ticker, others continue
- Digest shape + idempotency: re-running same day REPLACES the row (not duplicates)
- ticker with no news → skip (no empty file)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_portfolio(portfolios_dir: Path, agent_id: str, tickers: list[str]) -> None:
    agent_dir = portfolios_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    positions = [{"ticker": t, "shares": 1, "avg_price": 100.0} for t in tickers]
    (agent_dir / "portfolio.json").write_text(
        json.dumps({"cash": 1000.0, "currency": "EUR", "positions": positions})
    )


def _write_pending(pending_dir: Path, order_id: str, ticker: str) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{order_id}.json").write_text(
        json.dumps(
            {
                "order_id": order_id,
                "ticker": ticker,
                "action": "BUY",
                "shares": 1,
                "trigger": {"op": ">=", "level": 100},
                "expires": "2099-12-31",
            }
        )
    )


def _make_news_items(n: int, base_title: str = "Headline") -> list[dict]:
    """Return n fake yfinance-normalized news items, newest-first by index."""
    return [
        {
            "title": f"{base_title} {i}",
            "source": "TestSource",
            "published_at": f"2026-06-{13 - i:02d}T10:00:00Z",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# sanitize_headline
# ---------------------------------------------------------------------------


class TestSanitizeHeadline:
    def test_strips_url(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        raw = "Big news https://example.com/article?id=123 happened"
        result = sanitize_headline(raw)
        assert "http" not in result
        assert "example.com" not in result
        assert "Big news" in result

    def test_strips_html_tags(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        raw = "<b>Breaking</b>: <a href='x'>Market</a> up 2%"
        result = sanitize_headline(raw)
        assert "<" not in result
        assert ">" not in result
        assert "Breaking" in result
        assert "Market" in result

    def test_strips_markdown_link_syntax(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        raw = "[Apple](https://apple.com) reports earnings"
        result = sanitize_headline(raw)
        assert "[" not in result
        assert "]" not in result
        assert "(" not in result or "https" not in result
        assert "Apple" in result or "reports" in result

    def test_collapses_whitespace(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        raw = "Stocks   rise \t sharply\n  on strong  jobs data"
        result = sanitize_headline(raw)
        assert "  " not in result
        assert "\t" not in result
        assert "\n" not in result

    def test_caps_at_200_chars(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        raw = "A" * 300
        result = sanitize_headline(raw)
        assert len(result) <= 200

    def test_handles_empty_string(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        result = sanitize_headline("")
        assert result == ""

    def test_handles_none(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        result = sanitize_headline(None)  # type: ignore[arg-type]
        assert result == ""

    def test_strips_control_chars(self) -> None:
        from scripts.fetch_sentiment import sanitize_headline

        raw = "Normal text\x00with\x01control\x1fchars"
        result = sanitize_headline(raw)
        for ch in ["\x00", "\x01", "\x1f"]:
            assert ch not in result
        assert "Normal" in result

    def test_html_attr_url_does_not_leak(self) -> None:
        """Regression: tag-fragment leak when URL in HTML attribute.

        Old order (URL strip before tag strip): _RE_URL consumed greedily
        through the closing '>', leaving '<a href="' as an unstrippable fragment.
        Fix: strip HTML tags BEFORE URLs so the attribute is removed with the tag.
        """
        from scripts.fetch_sentiment import sanitize_headline

        raw = '<a href="http://evil.com">text</a>'
        result = sanitize_headline(raw)
        assert "<" not in result
        assert "href" not in result
        assert "evil.com" not in result
        assert "http" not in result
        assert "text" in result

    def test_nested_malformed_tag_does_not_leak(self) -> None:
        """Regression: nested/malformed tag bypass with single-pass strip.

        Single-pass '<[^>]+>' against '<scr<script>ipt>bad' produces 'ipt>bad'
        because the regex matches '<scr<script>' (first '<' to first '>'),
        leaving 'ipt>bad' with a dangling '>'.
        Fix: iterate tag-strip until stable (fixpoint loop) then strip orphaned
        angle brackets. The key invariant is no '<' or '>' in the output —
        the residual text 'ipt' is harmless (it is not markup, not a URL, not
        a control char, and cannot frame an injection boundary on its own).
        """
        from scripts.fetch_sentiment import sanitize_headline

        raw = "<scr<script>ipt>bad"
        result = sanitize_headline(raw)
        # No angle brackets must survive — these are the injection-relevant chars
        assert "<" not in result
        assert ">" not in result
        # The meaningful word 'bad' (the non-tag text content) survives
        assert "bad" in result


# ---------------------------------------------------------------------------
# Active-ticker scoping: _collect_active_tickers
# ---------------------------------------------------------------------------


class TestCollectActiveTickers:
    def test_returns_held_union_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fetch_sentiment as fs

        portfolios_dir = tmp_path / "portfolios"
        pending_dir = tmp_path / "pending"

        _write_portfolio(portfolios_dir, "satoshi", ["BTC-EUR", "ETH-EUR"])
        _write_portfolio(portfolios_dir, "goldfinger", ["GLD"])
        _write_pending(pending_dir, "ord_001", "AAPL")

        monkeypatch.setattr(fs, "_PORTFOLIOS_DIR", portfolios_dir)
        monkeypatch.setattr(fs, "_PENDING_DIR", pending_dir)

        result = fs._collect_active_tickers()
        assert result == {"BTC-EUR", "ETH-EUR", "GLD", "AAPL"}

    def test_no_universe_bleed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should NOT include universe tickers not held or pending."""
        import scripts.fetch_sentiment as fs

        portfolios_dir = tmp_path / "portfolios"
        pending_dir = tmp_path / "pending"
        # Only one held ticker
        _write_portfolio(portfolios_dir, "agent-a", ["MSFT"])
        pending_dir.mkdir()

        monkeypatch.setattr(fs, "_PORTFOLIOS_DIR", portfolios_dir)
        monkeypatch.setattr(fs, "_PENDING_DIR", pending_dir)

        result = fs._collect_active_tickers()
        # Must be exactly the held ticker — no S&P 500 bleed
        assert result == {"MSFT"}
        assert len(result) == 1

    def test_empty_when_no_portfolios_or_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fetch_sentiment as fs

        monkeypatch.setattr(fs, "_PORTFOLIOS_DIR", tmp_path / "portfolios")
        monkeypatch.setattr(fs, "_PENDING_DIR", tmp_path / "pending")

        result = fs._collect_active_tickers()
        assert result == set()

    def test_handles_missing_ticker_field_in_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fetch_sentiment as fs

        portfolios_dir = tmp_path / "portfolios"
        pending_dir = tmp_path / "pending"
        pending_dir.mkdir()

        # A pending file missing the ticker key
        (pending_dir / "bad_order.json").write_text(json.dumps({"order_id": "x"}))

        monkeypatch.setattr(fs, "_PORTFOLIOS_DIR", portfolios_dir)
        monkeypatch.setattr(fs, "_PENDING_DIR", pending_dir)

        # Should not crash
        result = fs._collect_active_tickers()
        assert result == set()


# ---------------------------------------------------------------------------
# Max-10 cap
# ---------------------------------------------------------------------------


class TestMax10Cap:
    def test_truncates_to_10_newest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fetch_sentiment as fs

        news_dir = tmp_path / "news"
        monkeypatch.setattr(fs, "_NEWS_DIR", news_dir)

        items = _make_news_items(25)
        monkeypatch.setattr(fs, "_fetch_news", lambda _sym: items)

        fs._write_digest("AAPL", items, run_date="2026-06-13")

        path = news_dir / "AAPL.jsonl"
        rows = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["count"] == 10
        assert len(row["headlines"]) == 10
        # Newest first: item 0 is the newest (lowest index = most recent date)
        assert row["headlines"][0]["title"] == "Headline 0"


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_failing_ticker_skipped_others_processed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fetch_sentiment as fs

        portfolios_dir = tmp_path / "portfolios"
        pending_dir = tmp_path / "pending"
        news_dir = tmp_path / "news"
        pending_dir.mkdir()

        _write_portfolio(portfolios_dir, "agent-a", ["FAIL-ME", "AAPL"])

        monkeypatch.setattr(fs, "_PORTFOLIOS_DIR", portfolios_dir)
        monkeypatch.setattr(fs, "_PENDING_DIR", pending_dir)
        monkeypatch.setattr(fs, "_NEWS_DIR", news_dir)

        def _patched_fetch(symbol: str) -> list[dict]:
            if symbol == "FAIL-ME":
                raise RuntimeError("simulated yfinance failure")
            return _make_news_items(3, base_title=f"{symbol} Headline")

        monkeypatch.setattr(fs, "_fetch_news", _patched_fetch)

        # Should not raise
        fs.run(run_date="2026-06-13")

        # FAIL-ME should have no file
        assert not (news_dir / "FAIL-ME.jsonl").exists()

        # AAPL should be written
        aapl_path = news_dir / "AAPL.jsonl"
        assert aapl_path.exists()
        row = json.loads(aapl_path.read_text().splitlines()[0])
        assert row["ticker"] == "AAPL"
        assert row["count"] == 3

    def test_empty_news_list_skips_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fetch_sentiment as fs

        portfolios_dir = tmp_path / "portfolios"
        pending_dir = tmp_path / "pending"
        news_dir = tmp_path / "news"
        pending_dir.mkdir()

        _write_portfolio(portfolios_dir, "agent-a", ["AAPL"])

        monkeypatch.setattr(fs, "_PORTFOLIOS_DIR", portfolios_dir)
        monkeypatch.setattr(fs, "_PENDING_DIR", pending_dir)
        monkeypatch.setattr(fs, "_NEWS_DIR", news_dir)

        # Returns empty list (no news)
        monkeypatch.setattr(fs, "_fetch_news", lambda _sym: [])

        fs.run(run_date="2026-06-13")

        # No file should be created for empty news
        assert not (news_dir / "AAPL.jsonl").exists()


# ---------------------------------------------------------------------------
# Digest shape + idempotency
# ---------------------------------------------------------------------------


class TestDigestShape:
    def test_digest_row_shape(self, tmp_path: Path) -> None:
        import scripts.fetch_sentiment as fs

        news_dir = tmp_path / "news"
        items = _make_news_items(3)
        fs._write_digest("AAPL", items, run_date="2026-06-13", news_dir=news_dir)

        path = news_dir / "AAPL.jsonl"
        assert path.exists()
        rows = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
        assert len(rows) == 1
        row = rows[0]

        # Required fields
        assert row["date"] == "2026-06-13"
        assert row["ticker"] == "AAPL"
        assert isinstance(row["headlines"], list)
        assert isinstance(row["count"], int)
        assert row["count"] == len(row["headlines"])

        # Each headline
        for h in row["headlines"]:
            assert "title" in h
            assert "source" in h
            assert "published_at" in h
            assert len(h["title"]) <= 200

    def test_re_run_same_day_replaces_row(self, tmp_path: Path) -> None:
        """Running twice on the same date replaces the existing row."""
        import scripts.fetch_sentiment as fs

        news_dir = tmp_path / "news"
        items_first = _make_news_items(2, base_title="Old")
        items_second = _make_news_items(5, base_title="New")

        fs._write_digest("AAPL", items_first, run_date="2026-06-13", news_dir=news_dir)
        fs._write_digest("AAPL", items_second, run_date="2026-06-13", news_dir=news_dir)

        path = news_dir / "AAPL.jsonl"
        rows = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]

        # Should have exactly ONE row for this date (not duplicated)
        date_rows = [r for r in rows if r["date"] == "2026-06-13"]
        assert len(date_rows) == 1
        # The row should reflect the second (most recent) run
        assert date_rows[0]["count"] == 5

    def test_different_dates_accumulate(self, tmp_path: Path) -> None:
        """Each day appends a new row; no prior-day data is overwritten."""
        import scripts.fetch_sentiment as fs

        news_dir = tmp_path / "news"
        items = _make_news_items(2)

        fs._write_digest("AAPL", items, run_date="2026-06-12", news_dir=news_dir)
        fs._write_digest("AAPL", items, run_date="2026-06-13", news_dir=news_dir)

        path = news_dir / "AAPL.jsonl"
        rows = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
        dates = {r["date"] for r in rows}
        assert "2026-06-12" in dates
        assert "2026-06-13" in dates
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Full run integration (mocked network)
# ---------------------------------------------------------------------------


class TestFullRunIntegration:
    def test_full_run_no_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.fetch_sentiment as fs

        portfolios_dir = tmp_path / "portfolios"
        pending_dir = tmp_path / "pending"
        news_dir = tmp_path / "news"
        pending_dir.mkdir()

        _write_portfolio(portfolios_dir, "satoshi", ["BTC-EUR"])
        _write_portfolio(portfolios_dir, "goldfinger", ["GLD", "SLV"])
        _write_pending(pending_dir, "ord_001", "AAPL")

        monkeypatch.setattr(fs, "_PORTFOLIOS_DIR", portfolios_dir)
        monkeypatch.setattr(fs, "_PENDING_DIR", pending_dir)
        monkeypatch.setattr(fs, "_NEWS_DIR", news_dir)
        monkeypatch.setattr(fs, "_fetch_news", lambda sym: _make_news_items(5, sym))

        fs.run(run_date="2026-06-13")

        # All 4 active tickers should have a news file
        for ticker in ["BTC-EUR", "GLD", "SLV", "AAPL"]:
            path = news_dir / f"{ticker}.jsonl"
            assert path.exists(), f"Missing news file for {ticker}"
            row = json.loads(path.read_text().splitlines()[0])
            assert row["ticker"] == ticker
            assert row["date"] == "2026-06-13"
            assert row["count"] == 5
