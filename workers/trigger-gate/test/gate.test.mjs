// Zero-dependency unit tests: `node --test workers/trigger-gate/test/`.
//
// The parity of the constants against Python is a separate guard
// (tests/test_trigger_gate_parity.py). What is tested here is the behaviour
// those constants feed: classification, expiry, and the tolerance band's
// direction — which is the property that makes an imperfect gate safe.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  TOLERANCE,
  gateable,
  isCryptoTicker,
  isLive,
  shouldDispatch,
} from "../src/gate.js";

test("isCryptoTicker accepts the real pending shapes", () => {
  for (const t of ["BTC-EUR", "ETH-EUR", "SOL-EUR", "BTC-USD", "DOGE-JPY"]) {
    assert.equal(isCryptoTicker(t), true, t);
  }
});

test("isCryptoTicker rejects everything the daily sweep owns", () => {
  for (const t of [
    "AUDUSD=X", // FX: no dash at all
    "LLOY.L", // LSE equity
    "MC.PA", // a real pending order's ticker
    "AAPL",
    "FOO-EUR", // unknown base
    "BTC-BRL", // unknown quote
    "BTC-EUR-X", // partition("-") makes the quote "EUR-X"
    "-EUR",
    "BTC-",
    "",
  ]) {
    assert.equal(isCryptoTicker(t), false, t);
  }
});

test("isCryptoTicker survives a non-string ticker", () => {
  for (const t of [null, undefined, 42, {}, ["BTC-EUR"]]) {
    assert.equal(isCryptoTicker(t), false, String(t));
  }
});

test("shouldDispatch fires exactly at the level, both ops", () => {
  assert.equal(shouldDispatch({ trigger: { op: ">=", level: 100 } }, 100), true);
  assert.equal(shouldDispatch({ trigger: { op: "<=", level: 100 } }, 100), true);
});

test("the tolerance band widens on the loose side only", () => {
  const up = { trigger: { op: ">=", level: 100 } };
  // Just under the level: dispatch anyway, because Coinbase spot and ccxt last
  // are two different numbers and a missed fire is the expensive error.
  assert.equal(shouldDispatch(up, 100 * (1 - TOLERANCE / 2)), true);
  // A full 1% below is outside the band — the gate is not a rubber stamp.
  assert.equal(shouldDispatch(up, 99), false);

  const down = { trigger: { op: "<=", level: 100 } };
  assert.equal(shouldDispatch(down, 100 * (1 + TOLERANCE / 2)), true);
  assert.equal(shouldDispatch(down, 101), false);
});

test("the band is strictly one-sided", () => {
  // A >= trigger must never be suppressed by a price ABOVE its level, and the
  // widening must not leak into the other direction for a <= trigger.
  assert.equal(shouldDispatch({ trigger: { op: ">=", level: 100 } }, 1e9), true);
  assert.equal(shouldDispatch({ trigger: { op: "<=", level: 100 } }, 0), true);
});

test("shouldDispatch refuses what it cannot evaluate", () => {
  assert.equal(shouldDispatch({}, 100), false);
  assert.equal(shouldDispatch({ trigger: null }, 100), false);
  assert.equal(shouldDispatch({ trigger: { op: ">", level: 100 } }, 100), false);
  assert.equal(shouldDispatch({ trigger: { op: ">=", level: "100" } }, 100), false);
  assert.equal(shouldDispatch({ trigger: { op: ">=", level: NaN } }, 100), false);
  assert.equal(shouldDispatch({ trigger: { op: ">=", level: 100 } }, NaN), false);
  assert.equal(shouldDispatch({ trigger: { op: ">=", level: 100 } }, undefined), false);
});

test("isLive is inclusive at the expiry date, like engine.triggers.is_expired", () => {
  const order = { expires: "2026-09-30" };
  assert.equal(isLive(order, "2026-09-29"), true);
  // TRIGGER_EXPIRED on the expires date itself — not live.
  assert.equal(isLive(order, "2026-09-30"), false);
  assert.equal(isLive(order, "2026-10-01"), false);
});

test("a missing expires never expires, matching Python", () => {
  // is_expired returns False when order.expires is None. Treating it as dead
  // here would under-dispatch, which is the one error direction the gate must
  // not make.
  assert.equal(isLive({}, "2026-09-30"), true);
  assert.equal(isLive({ expires: null }, "2026-09-30"), true);
});

test("gateable keeps crypto-and-live only", () => {
  const orders = [
    { ticker: "BTC-EUR", expires: "2026-12-31" }, // kept
    { ticker: "BTC-EUR", expires: "2026-01-01" }, // expired
    { ticker: "MC.PA", expires: "2026-12-31" }, // not crypto
    { ticker: "ETH-EUR" }, // no expiry -> live
    null,
  ];
  assert.deepEqual(
    gateable(orders, "2026-08-18").map((o) => o.ticker),
    ["BTC-EUR", "ETH-EUR"],
  );
});
