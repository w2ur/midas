// Handler-level tests. `fetch` is stubbed, so no network and no credential.
//
// These exist because the interesting failures are in the ORCHESTRATION, not
// in gate.js: what the handler does when one exchange call fails, and whether
// it dispatches at all.

import { test } from "node:test";
import assert from "node:assert/strict";

import worker from "../src/index.js";

const ENV = { GITHUB_PAT: "test-token" };

/** Build a fetch stub. `deadPairs` throw a 404 the way Coinbase does. */
function stubFetch({ orders, prices, deadPairs = [], openIssues = [] }) {
  const calls = { dispatches: 0, issuesCreated: [], issuesClosed: [], comments: 0 };
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    const ok = (body) => new Response(JSON.stringify(body), { status: 200 });

    if (u.endsWith("/graphql")) {
      const entries = orders.map((o, i) => ({
        name: `ord_${i}.json`,
        object: { text: JSON.stringify(o) },
      }));
      return ok({ data: { repository: { pub: { entries }, mgr: null } } });
    }
    if (u.includes("api.coinbase.com")) {
      const pair = u.match(/prices\/([^/]+)\/spot/)[1];
      if (deadPairs.includes(pair)) return new Response("not found", { status: 404 });
      return ok({ data: { amount: String(prices[pair]) } });
    }
    if (u.includes("/dispatches")) {
      calls.dispatches += 1;
      return new Response(null, { status: 204 });
    }
    if (u.includes("/issues?state=open")) return ok(openIssues);
    if (/\/issues\/\d+\/comments$/.test(u)) {
      calls.comments += 1;
      return ok({});
    }
    if (/\/issues\/\d+$/.test(u) && init.method === "PATCH") {
      calls.issuesClosed.push(u);
      return ok({});
    }
    if (u.endsWith("/issues")) {
      calls.issuesCreated.push(JSON.parse(init.body).title);
      return ok({ number: 1 });
    }
    throw new Error(`unstubbed fetch: ${u}`);
  };
  return calls;
}

const LIVE = "2999-12-31";
const btc = { order_id: "ord_btc", ticker: "BTC-EUR", expires: LIVE, trigger: { op: ">=", level: 100 } };
const dead = { order_id: "ord_matic", ticker: "MATIC-EUR", expires: LIVE, trigger: { op: ">=", level: 1 } };

test("one unpriceable pair does not suppress the others", async () => {
  // Regression: a bare `await spotPrice(pair)` in the loop threw on the first
  // dead symbol and aborted the whole invocation, so a single permanently dead
  // pending order (MATIC-USD has served nothing since 2025-03-24) suppressed
  // every other order until it expired. Systematic under-dispatch — the one
  // error direction this gate must never make.
  const calls = stubFetch({
    orders: [dead, btc],
    prices: { "BTC-EUR": 150 },
    deadPairs: ["MATIC-EUR"],
  });
  await worker.scheduled({}, ENV, {});
  assert.equal(calls.dispatches, 1, "the live BTC trigger was not dispatched");
});

test("a pair that cannot be priced is never dispatched on", async () => {
  // BTC prices fine but sits below its level, so the run is not systemic and
  // must not throw; MATIC cannot be priced and must not cause a dispatch on
  // its own account (an `undefined` price must never read as a hit).
  const calls = stubFetch({
    orders: [dead, btc],
    prices: { "BTC-EUR": 50 },
    deadPairs: ["MATIC-EUR"],
  });
  await worker.scheduled({}, ENV, {});
  assert.equal(calls.dispatches, 0);
});

test("every pair failing is systemic, and is escalated", async () => {
  // A dead symbol must not page anyone hourly; the exchange being down must.
  const calls = stubFetch({
    orders: [btc, dead],
    prices: {},
    deadPairs: ["BTC-EUR", "MATIC-EUR"],
  });
  await assert.rejects(() => worker.scheduled({}, ENV, {}), /no pair could be priced/);
  assert.deepEqual(calls.issuesCreated, ["trigger-gate worker failing"]);
});

test("no dispatch when nothing is at its level", async () => {
  const calls = stubFetch({ orders: [btc], prices: { "BTC-EUR": 50 } });
  await worker.scheduled({}, ENV, {});
  assert.equal(calls.dispatches, 0);
});

test("a clean invocation closes an open failure issue", async () => {
  const calls = stubFetch({
    orders: [btc],
    prices: { "BTC-EUR": 50 },
    openIssues: [{ number: 7, title: "trigger-gate worker failing" }],
  });
  await worker.scheduled({}, ENV, {});
  assert.equal(calls.issuesClosed.length, 1, "the issue was left open after recovery");
});

test("an unrelated open issue is not closed", async () => {
  const calls = stubFetch({
    orders: [btc],
    prices: { "BTC-EUR": 50 },
    openIssues: [{ number: 9, title: "something else entirely" }],
  });
  await worker.scheduled({}, ENV, {});
  assert.equal(calls.issuesClosed.length, 0);
});

test("a GraphQL error is not read as an empty desk", async () => {
  // 200-with-errors is how GraphQL reports a broken query; without the check
  // it parses as "no pending orders", which is a silent permanent no-dispatch.
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ errors: [{ message: "bad" }] }), { status: 200 });
  await assert.rejects(() => worker.scheduled({}, ENV, {}), /GraphQL/);
});
