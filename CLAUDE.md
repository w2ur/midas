# Midas — AI Fund Manager

## Project Overview
Personal AI fund manager that autonomously analyzes markets, makes investment decisions, and manages portfolios. Two execution engines: bt (Python) for deterministic strategies, Claude agents for analytical ones. Local-only — runs on localhost via Streamlit.

## Tech Stack
- Python 3.12+, bt (backtesting), yfinance (market data), pandas-ta (indicators)
- Streamlit + Plotly (dashboard), pytest (testing)
- Claude Code agents for analytical trading strategies

## User-Facing Language
English

## Development
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Testing
```bash
pytest tests/ -v
```

## Dashboard
```bash
streamlit run app/main.py
```

## Project Structure
- `engine/` — Core trading logic: types, market data, bt adapter, backtest runner
- `engine/selectors/` — bt Algos for entry signals (golden cross, RSI, etc.)
- `engine/managers/` — bt Algos for position management (grid, trailing stop, etc.)
- `engine/universes/` — Universe resolvers (S&P 500, congressional, crypto, etc.)
- `scripts/` — CLI entry points for backtesting and daily sessions
- `app/` — Streamlit dashboard pages
- `data/strategies/` — Strategy spec JSON files
- `data/portfolios/` — Runtime portfolio state (gitignored)
- `.claude/agents/` — Six analytical trading agent personas

## Project-Specific Rules
- All strategy specs live in `data/strategies/` as JSON files
- Portfolio state files in `data/portfolios/` are gitignored (runtime artifacts)
- Cached price data goes in `data/cache/` (gitignored)
- Every trade must have a `reasoning` field — no silent trades
