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
- `engine/orders.py` — Order/Fill types + outbox/inbox JSONL serde (Brain/Hands primitive)
- `engine/paper_broker.py` — Hands side: 9 safety rails + fill logic + portfolio update
- `engine/posts.py` — post types, 11-agent display names + schedule (single source of truth)
- `engine/selectors/` — bt Algos for entry signals (golden cross, RSI, etc.)
- `engine/managers/` — bt Algos for position management (grid, trailing stop, etc.)
- `engine/universes/` — Universe resolvers (S&P 500, congressional, crypto, etc.)
- `scripts/` — CLI entry points for backtesting and daily sessions
- `app/` — Streamlit dashboard pages
- `data/strategies/` — Strategy spec JSON files
- `data/portfolios/` — Runtime portfolio state (gitignored)
- `data/agent_config/` — per-agent safety rails (committed)
- `data/ticker_currencies.json` — ticker → ISO currency override map (committed)
- `data/orders/{outbox,inbox}/` — Brain/Hands trade flow (gitignored)
- `.claude/agents/` — Six analytical trading agent personas

## Real-Money Tax & Regulatory Context
- Operator is a **French tax resident**. All broker choices must serve France and expose a trading API.
- Approved brokers: **Interactive Brokers Ireland (IBIE)** for equities/ETFs/forex; **Kraken** (PSAN-registered in France) for crypto; **OANDA Europe (Ireland)** for dedicated forex.
- Never assume Alpaca, Robinhood, Schwab, Fidelity, or any US-residents-only broker — they're closed to this operator.
- PRIIPs KID requirement blocks many US-domiciled leveraged/inverse ETFs for EU retail. Verify availability in IBKR's product search before assuming a ticker is tradable.
- See **TAX.md** for PFU 30%, form 3916/3916-bis declarations, and broker-specific tax notes.

## Project-Specific Rules
- All strategy specs live in `data/strategies/` as JSON files
- Portfolio state is committed (needed by the sandboxed remote agent)
- Query-hash cached price data goes in `data/cache/` (gitignored)
- Every trade must have a `reasoning` field — no silent trades
- **Long-only, no short selling.** Use inverse ETFs (`bearish-etfs` universe: SH, PSQ, SQQQ, SPXS, etc.) to express bearish views as long positions. True shorts require borrow data we don't have.

## Market Data Pipeline
- **Source of truth**: `data/market/ohlcv/{SYMBOL}.jsonl` — one row per trading day, committed to git so the sandboxed remote agent can read prices without calling yfinance at runtime.
- **Populator**: `.github/workflows/fetch-ohlcv.yml` runs weekdays 22:30 UTC. Invokes `scripts/fetch_ohlcv.py`, which resolves the union of all declared universes + current holdings + market-context symbols (~600 tickers) and appends new rows.
- **Reader**: `engine.market_data.MarketDataFetcher` serves from the store first, falls back to yfinance only when the store doesn't cover the range. Same code path works in local dev and in the sandbox.
- **Not to be confused**: `scripts/fetch_market_data.py` writes a single benchmark snapshot (`data/market/today.json`, gitignored) for daily session commentary. Different job.
