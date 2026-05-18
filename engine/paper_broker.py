"""Paper broker — Hands side of the Brain/Hands split.

Reads orders from data/orders/outbox/, applies 9 safety rails, fills at end-of-day
close from the committed OHLCV store (latest-on-or-before the trade date — critical
because the daily session fires at 20:00 UTC but fetch-ohlcv.yml runs at 22:30 UTC),
writes data/orders/inbox/, mutates portfolios via PortfolioManager.apply_trade.

Rejection reason codes:
- INVALID_SHARES: malformed outbox line or shares <= 0
- MAX_ORDERS_PER_DAY: per-agent daily order cap exceeded
- MAX_ORDER_NOTIONAL: order notional (base currency) > per-agent cap
- TICKER_NOT_IN_UNIVERSE: allowed_universe is non-empty and ticker not in union
- NO_PRICE_DATA: no row in OHLCV store for ticker <= trade_date
- NO_FX_RATE: ticker currency ≠ base and no FX rate available to convert notional
- INSUFFICIENT_CASH: BUY cost > portfolio cash (post earlier fills)
- NO_POSITION_TO_SELL: SELL on a ticker not held
- INSUFFICIENT_SHARES: SELL shares > held shares
- DAILY_DRAWDOWN_HALT: agent's drawdown <= cap; ALL their orders rejected
- APPLY_TRADE_FAILED: PortfolioManager.apply_trade raised; broker continues with next order
- TRIGGER_NO_EXPIRY: conditional order without an expires date (agent error)
- CANCELLED_BY_AGENT: cancel request matched a pending order; pending file removed
- CANCEL_TARGET_NOT_FOUND: cancel request targeted an order_id not in pending
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from engine.fx import convert as fx_convert
from engine.ohlcv_store import (
    OHLCV_STORE as _DEFAULT_OHLCV_STORE,
    latest_close_on_or_before,
)
from engine.orders import Fill, Order, append_fill
from engine.triggers import (
    delete_pending,
    read_cancels,
    save_pending,
)
from engine.portfolio import PortfolioManager
from engine.types import Trade
from engine.universes import resolve_universe
from engine.valuation import mtm_base_currency

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Module-level constant kept for test monkeypatching compatibility:
# tests do `monkeypatch.setattr("engine.paper_broker._OHLCV_STORE", tmp_path)`.
_OHLCV_STORE = _DEFAULT_OHLCV_STORE
AGENT_CONFIG_DIR = _REPO_ROOT / "data" / "agent_config"
TICKER_CURRENCIES_PATH = _REPO_ROOT / "data" / "ticker_currencies.json"

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Per-agent safety rails loaded from data/agent_config/{agent_id}.json.

    daily_drawdown_halt_pct uses NEGATIVE values. The broker halts all of the
    agent's orders when the computed drawdown % is strictly less than this value
    (i.e. -7.0 < -5.0 → halt). A value of 0.0 disables the halt for that agent.
    """

    max_order_notional: float
    max_orders_per_day: int
    daily_drawdown_halt_pct: float
    allowed_universe: list[str]
    dry_run: bool

    @classmethod
    def load(cls, agent_id: str) -> "AgentConfig":
        path = AGENT_CONFIG_DIR / f"{agent_id}.json"
        defaults = cls(
            max_order_notional=500.0,
            max_orders_per_day=5,
            daily_drawdown_halt_pct=-5.0,
            allowed_universe=[],
            dry_run=False,
        )
        if not path.exists():
            return defaults
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return cls(**d)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Malformed agent config at %s: %s — falling back to defaults", path, exc
            )
            return defaults


_TICKER_CURRENCY_OVERRIDES: dict[str, str] | None = None


def _load_ticker_currency_overrides() -> dict[str, str]:
    global _TICKER_CURRENCY_OVERRIDES
    if _TICKER_CURRENCY_OVERRIDES is None:
        if TICKER_CURRENCIES_PATH.exists():
            _TICKER_CURRENCY_OVERRIDES = json.loads(
                TICKER_CURRENCIES_PATH.read_text(encoding="utf-8")
            )
        else:
            _TICKER_CURRENCY_OVERRIDES = {}
    return _TICKER_CURRENCY_OVERRIDES


def _ticker_currency(ticker: str) -> str:
    """Resolve ticker -> ISO currency code.

    1. Check data/ticker_currencies.json override map.
    2. Fall back to a minimal suffix heuristic (add to override file rather than extending the heuristic).
    """
    overrides = _load_ticker_currency_overrides()
    if ticker in overrides:
        return overrides[ticker]
    if ticker.endswith("-EUR"):
        return "EUR"
    if ticker.endswith("-USD"):
        return "USD"
    if ticker.endswith((".PA", ".DE", ".AS", ".MI")):
        return "EUR"
    if ticker.endswith(".L"):
        return "GBP"
    if ticker.endswith(".SW"):
        return "CHF"
    if ticker.endswith(".T"):
        return "JPY"
    return "USD"


def _drawdown_pct(
    agent_id: str, portfolio_manager: PortfolioManager, today: date
) -> float:
    """Drawdown % from the most recent snapshot, in the portfolio's base currency.

    Returns 0.0 if no snapshot exists yet (first day of experiment).
    """
    snaps = portfolio_manager.load_snapshots(agent_id)
    if not snaps:
        return 0.0
    portfolio = portfolio_manager.load(agent_id)
    summary = portfolio.to_dict()
    today_value = mtm_base_currency(summary, today)
    prev_value = snaps[-1]["portfolio_value"]
    if prev_value == 0:
        return 0.0
    return (today_value - prev_value) / prev_value * 100.0


def _reject(order_id: str, reason: str) -> Fill:
    return Fill(
        order_id=order_id,
        ts_filled=datetime.now(timezone.utc),
        status="rejected",
        fill_price=None,
        fill_currency=None,
        notional_base=None,
        fees=None,
        reason=reason,
    )


def _read_outbox_lines(trade_date: date) -> tuple[list[Order], list[str]]:
    """Read outbox JSONL with defensive parsing.

    Returns (orders, invalid_order_ids). Malformed lines (bad JSON or shares<=0
    or other Order validation failure) produce synthesized IDs in invalid_order_ids
    so they can be reported as INVALID_SHARES rejections instead of crashing.
    """
    # Delayed import — respects test monkeypatching of OUTBOX_DIR.
    from engine import orders as orders_module

    orders: list[Order] = []
    invalid_ids: list[str] = []
    path = orders_module.OUTBOX_DIR / f"{trade_date.isoformat()}.jsonl"
    if not path.exists():
        return orders, invalid_ids

    with path.open(encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            # Try to parse JSON. On failure, synthesize an ID.
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Invalid outbox JSON on line %d: %s", idx, exc)
                invalid_ids.append(f"malformed_{trade_date.isoformat()}_{idx:03d}")
                continue
            # Try to build an Order. On failure (shares<=0, missing keys, bad action),
            # fall back to the raw order_id if available.
            try:
                orders.append(Order.from_dict(raw))
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning("Invalid outbox order on line %d: %s", idx, exc)
                oid = raw.get("order_id") if isinstance(raw, dict) else None
                invalid_ids.append(
                    oid or f"malformed_{trade_date.isoformat()}_{idx:03d}"
                )

    return orders, invalid_ids


def _process_one(
    order: Order,
    config: AgentConfig,
    portfolio_manager: PortfolioManager,
    trade_date: date,
    filled_count: int,
    allowed_tickers: set[str],
) -> Fill:
    """Validate and either fill or reject a single order.

    Does NOT mutate filled_count; the caller tracks that. Does mutate the
    portfolio via apply_trade when the order fills (unless dry_run).
    """
    if filled_count >= config.max_orders_per_day:
        return _reject(order.order_id, "MAX_ORDERS_PER_DAY")

    if allowed_tickers and order.ticker not in allowed_tickers:
        return _reject(order.order_id, "TICKER_NOT_IN_UNIVERSE")

    price = latest_close_on_or_before(order.ticker, trade_date, store=_OHLCV_STORE)
    if price is None:
        return _reject(order.order_id, "NO_PRICE_DATA")

    portfolio = portfolio_manager.load(order.agent_id)
    base_ccy = portfolio.currency
    ticker_ccy = _ticker_currency(order.ticker)
    notional_native = order.shares * price

    if ticker_ccy == base_ccy:
        notional_base = notional_native
    else:
        converted = fx_convert(notional_native, ticker_ccy, base_ccy, trade_date)
        if converted is None:
            return _reject(order.order_id, "NO_FX_RATE")
        notional_base = converted

    if notional_base > config.max_order_notional:
        return _reject(order.order_id, "MAX_ORDER_NOTIONAL")

    if order.action == "BUY" and notional_base > portfolio.cash:
        return _reject(order.order_id, "INSUFFICIENT_CASH")

    if order.action == "SELL":
        position = next(
            (p for p in portfolio.positions if p.ticker == order.ticker), None
        )
        if position is None:
            return _reject(order.order_id, "NO_POSITION_TO_SELL")
        if order.shares > position.shares:
            return _reject(order.order_id, "INSUFFICIENT_SHARES")

    trade = Trade(
        id=order.order_id,
        timestamp=order.ts,
        action=order.action,
        ticker=order.ticker,
        shares=order.shares,
        price=price,
        total=notional_base,
        fees=0.0,
        reasoning=order.reasoning,
    )

    if not config.dry_run:
        try:
            portfolio_manager.apply_trade(order.agent_id, trade)
        except ValueError as exc:
            logger.warning("apply_trade failed for %s: %s", order.order_id, exc)
            return _reject(order.order_id, "APPLY_TRADE_FAILED")

    return Fill(
        order_id=order.order_id,
        ts_filled=datetime.now(timezone.utc),
        status="filled",
        fill_price=price,
        fill_currency=ticker_ccy,
        notional_base=notional_base,
        fees=0.0,
        reason=None,
    )


def fill_day(trade_date: date, portfolio_manager: PortfolioManager) -> list[Fill]:
    """Fill all outbox orders for a trade date.

    Order of operations:
      1. Process cancel requests — remove targeted pending orders, write
         CANCELLED_BY_AGENT (or CANCEL_TARGET_NOT_FOUND) rejections to inbox.
      2. Read outbox, split into conditional (trigger set) and market (trigger None).
      3. Conditional orders → save_pending() with TRIGGER_NO_EXPIRY rejection
         for any missing the expires field. No inbox fill on successful registration.
      4. Market orders → existing per-agent processing (drawdown halt, rails, fill).

    Malformed outbox lines (bad JSON or shares<=0) produce INVALID_SHARES rejections
    rather than crashing the pass.
    """
    fills: list[Fill] = []

    # --- Pass 1: process cancel requests ---
    for cancel in read_cancels(trade_date):
        removed = delete_pending(cancel.target_order_id)
        reason = "CANCELLED_BY_AGENT" if removed else "CANCEL_TARGET_NOT_FOUND"
        f = _reject(cancel.target_order_id, reason)
        fills.append(f)
        append_fill(trade_date, f)

    # --- Pass 2: read outbox and split conditional vs market ---
    orders, invalid_ids = _read_outbox_lines(trade_date)
    for oid in invalid_ids:
        f = _reject(oid, "INVALID_SHARES")
        fills.append(f)
        append_fill(trade_date, f)

    market_orders: list[Order] = []
    for o in orders:
        if o.trigger is None:
            market_orders.append(o)
            continue
        if o.expires is None:
            f = _reject(o.order_id, "TRIGGER_NO_EXPIRY")
            fills.append(f)
            append_fill(trade_date, f)
            continue
        save_pending(o)
        # No inbox record on successful registration — the agent sees it
        # next session in their "Active triggers" prompt section.

    # --- Pass 3: existing market-order fill loop ---
    by_agent: dict[str, list[Order]] = {}
    for o in market_orders:
        by_agent.setdefault(o.agent_id, []).append(o)

    for agent_id, agent_orders in by_agent.items():
        config = AgentConfig.load(agent_id)

        if (
            _drawdown_pct(agent_id, portfolio_manager, trade_date)
            < config.daily_drawdown_halt_pct
        ):
            for o in agent_orders:
                f = _reject(o.order_id, "DAILY_DRAWDOWN_HALT")
                fills.append(f)
                append_fill(trade_date, f)
            continue

        allowed_tickers: set[str] = set()
        for u in config.allowed_universe:
            try:
                allowed_tickers.update(resolve_universe(u))
            except KeyError:
                logger.warning("Unknown universe %s in %s config", u, agent_id)

        filled = 0
        for o in agent_orders:
            f = _process_one(
                o, config, portfolio_manager, trade_date, filled, allowed_tickers
            )
            fills.append(f)
            append_fill(trade_date, f)
            if f.status == "filled":
                filled += 1

    return fills


def execute_triggered_order(
    order: Order,
    trade_date: date,
    portfolio_manager: PortfolioManager,
    fire_price: float,
) -> Fill:
    """Execute a fired conditional order through the same safety rails as market orders.

    Differences from market-order processing:
      - `fire_price` is the live price observed by the watcher, used as fill_price
        instead of latest_close_on_or_before. The rails (notional cap, cash check,
        position check) are evaluated against this price.
      - The returned Fill always has trigger_fired=True so the agent and the site
        can distinguish scheduled fills from market fills.
      - Does NOT consult MAX_ORDERS_PER_DAY (a triggered fire is not a same-day order).
        Does still respect MAX_ORDER_NOTIONAL, TICKER_NOT_IN_UNIVERSE, INSUFFICIENT_CASH,
        NO_POSITION_TO_SELL, INSUFFICIENT_SHARES, NO_FX_RATE, APPLY_TRADE_FAILED.

    Caller is responsible for appending the returned Fill to the inbox and removing
    the pending file (so the watcher can decide policy if it wants).
    """
    config = AgentConfig.load(order.agent_id)
    portfolio = portfolio_manager.load(order.agent_id)
    base_ccy = portfolio.currency
    ticker_ccy = _ticker_currency(order.ticker)
    notional_native = order.shares * fire_price

    if ticker_ccy == base_ccy:
        notional_base = notional_native
    else:
        converted = fx_convert(notional_native, ticker_ccy, base_ccy, trade_date)
        if converted is None:
            f = _reject(order.order_id, "NO_FX_RATE")
            f.trigger_fired = True
            return f
        notional_base = converted

    allowed_tickers: set[str] = set()
    for u in config.allowed_universe:
        try:
            allowed_tickers.update(resolve_universe(u))
        except KeyError:
            logger.warning("Unknown universe %s in %s config", u, order.agent_id)

    if allowed_tickers and order.ticker not in allowed_tickers:
        f = _reject(order.order_id, "TICKER_NOT_IN_UNIVERSE")
        f.trigger_fired = True
        return f

    if notional_base > config.max_order_notional:
        f = _reject(order.order_id, "MAX_ORDER_NOTIONAL")
        f.trigger_fired = True
        return f

    if order.action == "BUY" and notional_base > portfolio.cash:
        f = _reject(order.order_id, "INSUFFICIENT_CASH")
        f.trigger_fired = True
        return f

    if order.action == "SELL":
        position = next(
            (p for p in portfolio.positions if p.ticker == order.ticker), None
        )
        if position is None:
            f = _reject(order.order_id, "NO_POSITION_TO_SELL")
            f.trigger_fired = True
            return f
        if order.shares > position.shares:
            f = _reject(order.order_id, "INSUFFICIENT_SHARES")
            f.trigger_fired = True
            return f

    trade = Trade(
        id=order.order_id,
        timestamp=order.ts,
        action=order.action,
        ticker=order.ticker,
        shares=order.shares,
        price=fire_price,
        total=notional_base,
        fees=0.0,
        reasoning=order.reasoning,
    )

    if not config.dry_run:
        try:
            portfolio_manager.apply_trade(order.agent_id, trade)
        except ValueError as exc:
            logger.warning(
                "apply_trade failed for triggered order %s: %s", order.order_id, exc
            )
            f = _reject(order.order_id, "APPLY_TRADE_FAILED")
            f.trigger_fired = True
            return f

    return Fill(
        order_id=order.order_id,
        ts_filled=datetime.now(timezone.utc),
        status="filled",
        fill_price=fire_price,
        fill_currency=ticker_ccy,
        notional_base=notional_base,
        fees=0.0,
        reason=None,
        trigger_fired=True,
    )
