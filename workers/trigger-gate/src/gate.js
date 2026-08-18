// Pure decision logic for the trigger gate — no I/O, so it is all unit-testable.
//
// This file is a DELIBERATE DUPLICATE of rules that live in Python
// (engine/triggers.py). The repo's standing rule is that a JS copy of a Python
// rule is how the quote-currency defect happened, so the copy is pinned by
// tests/test_trigger_gate_parity.py. If that test is red, change gate.js and
// engine/triggers.py in the SAME commit.

// Mirrors engine.triggers._CRYPTO_BASES / _CRYPTO_QUOTES.
export const CRYPTO_BASES = new Set([
  "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "DOT", "LINK",
  "LTC", "BCH", "AVAX", "ATOM", "XLM", "FIL", "MATIC", "UNI",
]);
export const CRYPTO_QUOTES = new Set(["EUR", "USD", "GBP", "JPY", "CHF"]);

// Loose-side-only widening of the trigger band.
//
// The gate reads Coinbase's spot endpoint; the workflow reads ccxt's ticker
// `last`. Those are two different numbers for the same instrument, so an exact
// comparison here would occasionally suppress a fire the script would have
// taken. The gate may only OVER-dispatch: a false positive costs one ~1-minute
// workflow run that finds nothing, a false negative delays a fill to the next
// daily sweep.
export const TOLERANCE = 0.005;

/**
 * Mirrors engine.triggers.is_crypto_ticker, including its `partition("-")`
 * semantics: everything after the FIRST dash is the quote, so "BTC-EUR-X"
 * yields quote "EUR-X" and is rejected.
 */
export function isCryptoTicker(ticker) {
  if (typeof ticker !== "string") return false;
  const i = ticker.indexOf("-");
  if (i === -1) return false;
  return (
    CRYPTO_BASES.has(ticker.slice(0, i)) && CRYPTO_QUOTES.has(ticker.slice(i + 1))
  );
}

/**
 * Mirrors the negation of engine.triggers.is_expired:
 *
 *     if order.expires is None: return False        # never expires
 *     return today >= date.fromisoformat(order.expires)
 *
 * Expiry is INCLUSIVE — an order expiring 2026-05-17 is TRIGGER_EXPIRED on
 * 2026-05-17 — so it is live strictly before that date. ISO dates compare
 * correctly as strings. A missing `expires` never expires, matching Python;
 * treating it as dead here would be the one divergence that under-dispatches.
 *
 * The gate never retires anything: it mirrors this only to avoid dispatching a
 * run for an order the script would immediately expire. Expiry belongs to the
 * daily sweep.
 */
export function isLive(order, todayIso) {
  if (order == null) return false;
  if (order.expires == null) return true;
  return typeof order.expires === "string" && order.expires > todayIso;
}

/**
 * Mirrors engine.triggers.evaluate_trigger (inclusive at the level), widened by
 * TOLERANCE on the loose side only.
 *
 * An unknown op returns false rather than throwing: the script raises on it and
 * the daily sweep owns that failure. The gate's job is not to adjudicate.
 */
export function shouldDispatch(order, price) {
  const trigger = order?.trigger;
  if (!trigger || typeof trigger.level !== "number") return false;
  if (!Number.isFinite(trigger.level) || !Number.isFinite(price)) return false;
  if (trigger.op === ">=") return price >= trigger.level * (1 - TOLERANCE);
  if (trigger.op === "<=") return price <= trigger.level * (1 + TOLERANCE);
  return false;
}

/**
 * The orders worth pricing: crypto, and not already expired.
 */
export function gateable(orders, todayIso) {
  return orders.filter((o) => isCryptoTicker(o?.ticker) && isLive(o, todayIso));
}
