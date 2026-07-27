import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import {
  listOrderDates,
  loadOrdersForDate,
  loadOrdersByAgentForDate,
  loadAllOrders,
  tickerSlug,
  buildTickerIndex,
} from "@/lib/orders";
import { DATA_DIR } from "@/lib/paths";

const OUTBOX_DIR = path.join(DATA_DIR, "orders", "outbox");
const INBOX_DIR = path.join(DATA_DIR, "orders", "inbox");
const DATE_RE = /^(\d{4}-\d{2}-\d{2})\.jsonl$/;

function datesInDir(dir: string): string[] {
  return fs
    .readdirSync(dir)
    .map((f) => f.match(DATE_RE)?.[1] ?? null)
    .filter((d): d is string => d !== null);
}

/** A date with at least one authored order — i.e. an outbox file exists.
 *  Not every date in listOrderDates() has one: a date can be inbox-only when
 *  a conditional order fired that day but nothing new was authored. */
function latestOutboxDate(): string {
  const dates = datesInDir(OUTBOX_DIR).sort();
  return dates[dates.length - 1];
}

describe("orders loader", () => {
  it("listOrderDates returns ascending date strings", () => {
    const dates = listOrderDates();
    expect(dates.length).toBeGreaterThan(0);
    for (let i = 1; i < dates.length; i++) {
      expect(dates[i] > dates[i - 1]).toBe(true);
    }
    for (const d of dates) expect(d).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("listOrderDates is the union of outbox and inbox dates, not outbox alone", () => {
    const expected = new Set([...datesInDir(OUTBOX_DIR), ...datesInDir(INBOX_DIR)]);
    expect(new Set(listOrderDates())).toEqual(expected);
    // Pin today's concrete gap: dates where the watcher fired a conditional
    // order but no session authored anything new that day, so there's no
    // outbox file at all — these must still show up in listOrderDates().
    const inboxOnly = datesInDir(INBOX_DIR).filter((d) => !datesInDir(OUTBOX_DIR).includes(d));
    expect(inboxOnly.length).toBeGreaterThan(0);
    for (const d of inboxOnly) expect(listOrderDates()).toContain(d);
  });

  it("loadOrdersForDate joins outbox and inbox by order_id", () => {
    const latest = latestOutboxDate();
    const orders = loadOrdersForDate(latest);
    expect(orders.length).toBeGreaterThan(0);
    for (const o of orders) {
      expect(o.order_id).toMatch(/^ord_/);
      expect(o.date).toBe(latest);
      expect(["BUY", "SELL"]).toContain(o.action);
      expect(typeof o.ticker).toBe("string");
      expect(typeof o.shares).toBe("number");
      expect(["filled", "rejected", "pending"]).toContain(o.status);
      if (o.status === "filled") {
        expect(typeof o.fill_price).toBe("number");
      }
    }
  });

  it("loadOrdersForDate returns an empty array for an inbox-only date (no new orders authored)", () => {
    const inboxOnly = datesInDir(INBOX_DIR).filter((d) => !datesInDir(OUTBOX_DIR).includes(d));
    expect(inboxOnly.length).toBeGreaterThan(0);
    for (const d of inboxOnly) {
      expect(loadOrdersForDate(d)).toEqual([]);
    }
  });

  it("loadOrdersByAgentForDate filters by agent_id", () => {
    const latest = latestOutboxDate();
    const all = loadOrdersForDate(latest);
    const agent = all[0].agent_id;
    const filtered = loadOrdersByAgentForDate(latest, agent);
    expect(filtered.length).toBeGreaterThan(0);
    for (const o of filtered) expect(o.agent_id).toBe(agent);
  });

  it("loadAllOrders returns exactly one entry per order_id — no double-counting from the union of dates", () => {
    const all = loadAllOrders();
    const ids = all.map((o) => o.order_id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("loadAllOrders' order dates are exactly the outbox dates (placement dates), a subset of listOrderDates()", () => {
    const all = loadAllOrders();
    const uniqueDates = new Set(all.map((o) => o.date));
    expect(uniqueDates).toEqual(new Set(datesInDir(OUTBOX_DIR)));
  });

  it("tickerSlug produces URL-safe upper-case slugs", () => {
    expect(tickerSlug("EURUSD=X")).toBe("EURUSD-X");
    expect(tickerSlug("SAP.DE")).toBe("SAP-DE");
    expect(tickerSlug("BTC-EUR")).toBe("BTC-EUR");
    expect(tickerSlug("AAPL")).toBe("AAPL");
    expect(tickerSlug("^GSPC")).toBe("-GSPC");
  });

  it("buildTickerIndex maps slugs back to the canonical ticker", () => {
    const idx = buildTickerIndex();
    expect(idx.size).toBeGreaterThan(0);
    for (const [slug, ticker] of idx.entries()) {
      expect(tickerSlug(ticker)).toBe(slug);
    }
  });

  // Regression: 409a6367b67a83ea5f6da791f80b42958423bc9b — a conditional
  // (trigger) order is authored on one date
  // (data/orders/outbox/<placement-date>.jsonl) but fires and lands its fill
  // confirmation on a later date (data/orders/inbox/<fill-date>.jsonl). The
  // old loadOrdersForDate() joined outbox and inbox scoped to the *same*
  // date file, so any trigger fire that landed on a different day than it
  // was placed silently fell back to status "pending" forever — even though
  // the fill genuinely happened and mutated the portfolio. This dropped 41
  // of 166 roster fills (~25%) from every site page built on loadAllOrders()
  // / loadOrdersForDate() (ossStats().fills, buildTickerIndex(), per-agent
  // and per-ticker order tables), while data/portfolios/*/trades.json (and
  // cadence.ts, which reads it) had the correct count all along.
  it("resolves a conditional order to 'filled' even when its inbox confirmation lands on a later date", () => {
    // sharp-shooter-eur SELL 5 MC.PA: placed 2026-06-18, trigger fired and
    // confirmed on 2026-06-23 (data/orders/inbox/2026-06-23.jsonl).
    const placementDate = "2026-06-18";
    const orderId = "ord_2026-06-18_sharp-shooter-eur_001";
    const orders = loadOrdersForDate(placementDate);
    const order = orders.find((o) => o.order_id === orderId);
    expect(order).toBeDefined();
    expect(order?.status).toBe("filled");
    expect(order?.fill_price).toBeCloseTo(499.25, 2);
  });
});
