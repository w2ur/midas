"""Pydantic schemas for the backtester API.

Supported kinds: `signal` (universe + selector + manager) and `allocation`
(fixed weights + rebalance cadence). `mirror` is introduced in a later plan.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class SignalConfig(BaseModel):
    """Form fields for a signal-driven strategy."""

    universe: str
    selector: str
    manager: str
    max_positions: int = Field(ge=1, le=100)
    max_position_pct: float = Field(gt=0.0, le=100.0)
    min_hold_days: int = Field(ge=0, le=365)


class AllocationWeight(BaseModel):
    ticker: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=100.0)


RebalanceCadence = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]


class AllocationConfig(BaseModel):
    """Form fields for a static-allocation strategy."""

    weights: list[AllocationWeight] = Field(min_length=1, max_length=20)
    rebalance_cadence: RebalanceCadence = "monthly"


class SignalRunRequest(BaseModel):
    kind: Literal["signal"]
    config: SignalConfig
    start_date: date
    end_date: date
    capital: float = Field(gt=0.0)
    currency: Literal["EUR", "USD"] = "EUR"


class AllocationRunRequest(BaseModel):
    kind: Literal["allocation"]
    config: AllocationConfig
    start_date: date
    end_date: date
    capital: float = Field(gt=0.0)
    currency: Literal["EUR", "USD"] = "EUR"


RunRequest = Annotated[
    Union[SignalRunRequest, AllocationRunRequest],
    Field(discriminator="kind"),
]


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
    benchmark_curve: list[EquityPoint] = Field(default_factory=list)
    benchmark_label: str = "MSCI World"
    metrics: MetricsBlock
    trades: list[TradeEntry]
    config_hash: str
    warnings: list[str] = Field(default_factory=list)
