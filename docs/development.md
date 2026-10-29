# Development

Setting up the desk locally, running backtests and the dashboard, and adding a
strategy. The landing page is [README.md](../README.md); the engine is
described in [engine.md](./engine.md).

## Install

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
```

Run things with `.venv/bin/python`, or activate the venv first: the
`python scripts/...` lines below assume it is active.

Why `uv`, and not `python -m venv` + `pip`: uv provides the interpreter itself,
so the setup does not depend on a `python` or `pip` being on the PATH. Why
deliberately **not** `uv sync`: `requirements.txt` is the resolved lockfile
that the GitHub workflows (`grep -l requirements.txt .github/workflows/*`
lists them), the backtester Dockerfile and the cloud sandbox all install, and
a second lockfile would be a second answer to what this project depends on.
`requirements.in` holds the loose, human-edited constraints.

Optional environment variables are documented in
[`.env.example`](../.env.example); none is needed to run a backtest or the
dashboard locally.

## Run backtests

```bash
# Single strategy
python scripts/run_backtest.py --strategy dogs-of-the-dow --from 2024-01-01

# All strategies
python scripts/run_backtest.py --all --from 2024-01-01

# Factor research (all selector × manager combinations)
python scripts/run_all_combos.py --universes etf-broad --from 2024-01-01
```

`--to YYYY-MM-DD` sets the end date (default: today), inclusive. Prices come
from the OHLCV store, with yfinance only for tickers the store does not cover
from `--from`; that fallback never asks for today's still-forming bar, and a
trailing day only one of the two sources holds is dropped. Backtest returns are
**gross of costs**, and the CLI says so on every run (`GROSS_OF_COSTS`). On a
universe whose constituents are today's list, such as `sp500`, it also warns
of survivorship bias; prefer a stable universe (`dow30`, `etf-broad`) for
historical backtests. Factor research writes `data/factor-research.json`
(gitignored).

## Dashboard

```bash
streamlit run app/main.py
# Opens http://localhost:8501
```

## Tests

```bash
pytest -q            # tests/ and backtester/tests
cd site && npm test  # the site's vitest suite
```

## Add a strategy

Create a JSON file in `data/strategies/`:

```json
{
  "id": "my-strategy",
  "name": "My Custom Strategy",
  "universe": "dow30",
  "selector": "golden-cross",
  "manager": "equal-weight",
  "funding": {"initial": 10000},
  "rules": {"max_positions": 10, "max_position_pct": 20}
}
```

Then run it with `python scripts/run_backtest.py --strategy my-strategy`.

Only fields the backtest engine applies are shown. `funding.monthly_addition` /
`weekly_addition`, `rules.min_hold_days` and `dividends` are parsed and kept
for forward compatibility, but are **not yet wired into bt**, so setting them
changes no result (see [engine.md](./engine.md#strategies-four-axes)).
