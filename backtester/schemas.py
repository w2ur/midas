"""Pydantic schemas for the backtester API.

v1 supports only the `signal` strategy kind. `mirror` and `allocation` are
rejected at the schema layer; they are introduced in later plans.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SignalConfig(BaseModel):
    """Form fields for a signal-driven strategy."""

    universe: str
    selector: str
    manager: str
    max_positions: int = Field(ge=1, le=100)
    max_position_pct: float = Field(gt=0.0, le=100.0)
    min_hold_days: int = Field(ge=0, le=365)


class RunRequest(BaseModel):
    kind: Literal["signal"]
    config: SignalConfig
    start_date: date
    end_date: date
    capital: float = Field(gt=0.0)
    currency: Literal["EUR", "USD"] = "EUR"


class EquityPoint(BaseModel):
    date: str
    value: float


class MetricsBlock(BaseModel):
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    vs_msci_world_pct: float
    vs_coin_flip_pct: float


class TradeEntry(BaseModel):
    date: str
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    pnl: float | None = None


class RunResponse(BaseModel):
    equity_curve: list[EquityPoint]
    metrics: MetricsBlock
    trades: list[TradeEntry]
    config_hash: str
    warnings: list[str] = Field(default_factory=list)
