/**
 * The reason codes the Hands can emit, mirrored for the site's /open-source page.
 *
 * The engine is the source of truth: `engine.paper_broker.REJECTION_REASON_CODES`
 * for the broker set, and `scripts/check_triggers.py` for TRIGGER_EXPIRED. The
 * pytest guard in `tests/test_reason_codes.py` asserts this file matches them,
 * so an engine change that is not reflected here fails CI.
 */

export interface Rail {
  code: string;
  blurb: string;
}

/** Emitted by engine/paper_broker.py at fill time. */
export const BROKER_RAILS: Rail[] = [
  { code: "INVALID_SHARES", blurb: "Malformed outbox line, or shares at or below zero. Midas is long-only." },
  { code: "MAX_ORDERS_PER_DAY", blurb: "The agent's daily order cap is already spent." },
  { code: "MAX_ORDER_NOTIONAL", blurb: "The order is larger, in base currency, than the agent's per-order cap." },
  { code: "TICKER_NOT_IN_UNIVERSE", blurb: "The agent has an allowlist and this ticker is not on it." },
  { code: "NO_PRICE_DATA", blurb: "No row in the committed price store for this ticker on or before the trade date." },
  { code: "NO_FX_RATE", blurb: "The ticker prices in another currency and no rate was available to convert." },
  { code: "INSUFFICIENT_CASH", blurb: "The buy costs more than the cash left after earlier fills that session." },
  { code: "NO_POSITION_TO_SELL", blurb: "A sell on a ticker the agent does not hold." },
  { code: "INSUFFICIENT_SHARES", blurb: "A sell larger than the position actually held." },
  { code: "FEE_EXCEEDS_PROCEEDS", blurb: "A sell so small the fee swallows it, netting no cash." },
  { code: "DAILY_DRAWDOWN_HALT", blurb: "The agent breached its drawdown limit; every order it authored that day is rejected." },
  { code: "APPLY_TRADE_FAILED", blurb: "The portfolio refused the mutation; the broker records it and moves to the next order." },
  { code: "TRIGGER_NO_EXPIRY", blurb: "A conditional order with no expiry date. Expiry is mandatory." },
  { code: "CANCELLED_BY_AGENT", blurb: "The agent cancelled its own pending order before it fired." },
  { code: "CANCEL_TARGET_NOT_FOUND", blurb: "A cancel aimed at an order that is not pending — already fired, expired, or never existed." },
];

/** Emitted by scripts/check_triggers.py — a separate enforcement point. */
export const WATCHER_RAILS: Rail[] = [
  { code: "TRIGGER_EXPIRED", blurb: "A pending conditional order reached its expiry date without its price level being crossed." },
];
