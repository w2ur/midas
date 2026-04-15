"""Tests for daily log generator."""

from datetime import date
from pathlib import Path

from engine.daily_log import generate_daily_log


class TestDailyLog:
    def test_generates_markdown_file(self, tmp_path: Path) -> None:
        import engine.daily_log as dl
        dl.LOGS_DIR = tmp_path

        market = {"sp500": 6967.38, "gold": 4825.0, "btc": 74181.61}
        agent_results = {
            "steady-eddie": {
                "commentary": "Markets choppy. Staying defensive.",
                "trades": [
                    {"action": "BUY", "ticker": "JNJ", "shares": 5, "reasoning": "Defensive healthcare play."},
                ],
            },
            "yolo-sapiens": {
                "commentary": "YOLO into leveraged tech.",
                "trades": [
                    {"action": "BUY", "ticker": "TQQQ", "shares": 25, "reasoning": "3x Nasdaq for max beta."},
                ],
            },
        }
        portfolio_summaries = {
            "steady-eddie": {"cash": 1965.0, "deployed": 8035.0, "positions": ["JNJ", "XOM", "PG"]},
            "yolo-sapiens": {"cash": 156.0, "deployed": 9844.0, "positions": ["TQQQ", "SOXL"]},
        }

        path = generate_daily_log(date(2026, 4, 14), market, agent_results, portfolio_summaries)

        assert path.exists()
        assert path.name == "2026-04-14.md"

        content = path.read_text()
        assert "# Midas Daily Log" in content
        assert "6,967.38" in content
        assert "Steady Eddie" in content
        assert "Markets choppy" in content
        assert "JNJ" in content
        assert "YOLO Sapiens" in content
        assert "$1,965.00" in content
        assert "TQQQ, SOXL" in content

    def test_no_trades_day(self, tmp_path: Path) -> None:
        import engine.daily_log as dl
        dl.LOGS_DIR = tmp_path

        agent_results = {
            "steady-eddie": {
                "commentary": "No opportunities today. Holding positions.",
                "trades": [],
            },
        }
        path = generate_daily_log(date(2026, 4, 15), {}, agent_results, {})
        content = path.read_text()
        assert "No trades today" in content
        assert "No opportunities" in content

    def test_all_agents_present(self, tmp_path: Path) -> None:
        import engine.daily_log as dl
        dl.LOGS_DIR = tmp_path

        agents = ["steady-eddie", "sharp-shooter", "satoshi", "monsieur-forex", "goldfinger", "yolo-sapiens"]
        agent_results = {a: {"commentary": f"{a} commentary", "trades": []} for a in agents}

        path = generate_daily_log(date(2026, 4, 14), {}, agent_results, {})
        content = path.read_text()

        for display in ["Steady Eddie", "Sharp Shooter", "Satoshi", "Monsieur Forex", "Goldfinger", "YOLO Sapiens"]:
            assert display in content
