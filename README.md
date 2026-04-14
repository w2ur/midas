# Midas — AI Fund Manager

Personal AI fund manager that autonomously analyzes markets, makes investment decisions, and manages portfolios. Two execution engines work together: **bt** (Python backtesting framework) runs deterministic rule-based strategies, while **Claude Code agents** handle analytical strategies that require judgment.

## Architecture

Midas uses a composable strategy system where every strategy is defined by four independent axes:

```
Strategy = Universe × Selector × Manager × Funding + dividend mode
```

- **Universe**: what assets to consider (S&P 500, crypto top 20, congressional trades, etc.)
- **Selector**: when to buy (golden cross, RSI oversold, fear & greed, etc.)
- **Manager**: how to size and exit (equal weight, grid, trailing stop, etc.)
- **Funding**: how capital enters (lump sum, DCA monthly, etc.)

Deterministic strategies are backtested against years of historical data. Analytical strategies run daily as Claude agents with distinct personas and mandates.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run Backtests

```bash
# Single strategy
python scripts/run_backtest.py --strategy coin-flip-baseline --from 2024-01-01

# All strategies
python scripts/run_backtest.py --all --from 2024-01-01

# Factor research (all combinations)
python scripts/run_all_combos.py --universes etf-broad --from 2024-01-01
```

## Dashboard

```bash
streamlit run app/main.py
# Opens http://localhost:8501
```

## Add a Strategy

Create a JSON file in `data/strategies/`:

```json
{
  "id": "my-strategy",
  "name": "My Custom Strategy",
  "universe": "sp500",
  "selector": "golden-cross",
  "manager": "equal-weight",
  "funding": {"initial": 10000, "monthly_addition": 500},
  "dividends": "reinvest",
  "rules": {"maxPositions": 10, "maxPositionPct": 20, "minHoldDays": 3}
}
```

---

Made with care by [William](https://william.revah.paris)
