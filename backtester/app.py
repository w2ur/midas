"""Midas backtester service — FastAPI app."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, TypeAdapter

from backtester.allocation import AllocationError, run_allocation_backtest
from backtester.comparisons import compute_comparison_deltas
from backtester.mirror import MirrorError, run_mirror_backtest
from backtester.runner import (
    UnknownUniverseError,
    run_signal_backtest,
)
from backtester.schemas import (
    AllocationRunRequest,
    EquityPoint,
    MetricsBlock,
    MirrorRunRequest,
    RunRequest,
    RunResponse,
    SignalRunRequest,
)
from backtester.trades import extract_top_trades

app = FastAPI(title="Midas Backtester", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://midas.revah.paris",
        "http://localhost:4321",
    ],
    allow_origin_regex=r"https://midas-.*\.vercel\.app",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_run_request_adapter: TypeAdapter[RunRequest] = TypeAdapter(RunRequest)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _config_hash(request: BaseModel) -> str:
    payload = request.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256-{digest}"


def _load_msci_world_series(start: date, end: date):
    """Load the MSCI World benchmark series for the requested window.

    Returns None when data is unavailable; caller appends a warning.
    """
    try:
        from engine.market_data import MarketDataFetcher

        fetcher = MarketDataFetcher()
        prices = fetcher.fetch_prices(["URTH"], start, end)
        if prices.empty:
            return None
        series = prices["URTH"]
        return (series / series.iloc[0]) * 10000.0
    except Exception:
        return None


def _load_coin_flip_series(start: date, end: date):
    """Coin-flip baseline series. Returns None on any failure.

    engine.baselines.compute_coin_flip requires agent_id, tickers, currency,
    and max_positions — it is designed for per-agent tracking, not generic
    date-range queries. No suitable callable exists, so we return None, which
    triggers the warning path in compute_comparison_deltas.
    """
    return None


def _build_response(
    request: BaseModel,
    daily_values,
    transactions,
    total_return: float,
    cagr: float,
    sharpe: float,
    max_drawdown: float,
    capital: float,
    start: date,
    end: date,
) -> RunResponse:
    benchmark = _load_msci_world_series(start, end)
    coin_flip = _load_coin_flip_series(start, end)
    deltas = compute_comparison_deltas(
        daily_values,
        benchmark_curve=benchmark,
        coin_flip_curve=coin_flip,
    )

    warnings = list(deltas.warnings)
    # Survivorship-bias guard: a signal run over a survivorship-prone index
    # universe whose start predates the constituents refresh is trading today's
    # membership over history — flag it (see engine.survivorship).
    if isinstance(request, SignalRunRequest):
        from engine.survivorship import survivorship_warning

        sv = survivorship_warning(request.config.universe, start)
        if sv is not None:
            warnings.append(sv)

    equity_curve = [
        EquityPoint(date=idx.date().isoformat(), value=float(val))
        for idx, val in daily_values.items()
    ]
    benchmark_curve: list[EquityPoint] = []
    if benchmark is not None and not benchmark.empty:
        scaled = (benchmark / float(benchmark.iloc[0])) * float(capital)
        benchmark_curve = [
            EquityPoint(date=idx.date().isoformat(), value=float(val))
            for idx, val in scaled.items()
        ]
    metrics = MetricsBlock(
        total_return_pct=total_return * 100.0,
        cagr_pct=cagr * 100.0,
        sharpe=sharpe,
        max_drawdown_pct=max_drawdown * 100.0,
        vs_msci_world_pct=deltas.vs_msci_world_pct,
        vs_coin_flip_pct=deltas.vs_coin_flip_pct,
    )
    trades = extract_top_trades(transactions, n=20)

    return RunResponse(
        equity_curve=equity_curve,
        benchmark_curve=benchmark_curve,
        benchmark_label="MSCI World",
        metrics=metrics,
        trades=trades,
        config_hash=_config_hash(request),
        warnings=warnings,
    )


@app.post("/run", response_model=RunResponse)
def run(
    request: SignalRunRequest | AllocationRunRequest | MirrorRunRequest,
) -> RunResponse:
    try:
        if request.kind == "signal":
            result = run_signal_backtest(
                request.config,
                start=request.start_date,
                end=request.end_date,
                capital=request.capital,
            )
        elif request.kind == "allocation":
            result = run_allocation_backtest(
                request.config,
                start=request.start_date,
                end=request.end_date,
                capital=request.capital,
            )
        else:
            result = run_mirror_backtest(
                request.config,
                start=request.start_date,
                end=request.end_date,
                capital=request.capital,
            )
    except UnknownUniverseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (AllocationError, MirrorError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}")

    return _build_response(
        request,
        daily_values=result.daily_values,
        transactions=result.transactions,
        total_return=result.total_return,
        cagr=result.cagr,
        sharpe=result.sharpe,
        max_drawdown=result.max_drawdown,
        capital=request.capital,
        start=request.start_date,
        end=request.end_date,
    )
