import { describe, it, expect } from "vitest";
import {
  listOrderDates,
  loadOrdersForDate,
  loadOrdersByAgentForDate,
  loadAllOrders,
  tickerSlug,
  buildTickerIndex,
} from "@/lib/orders";

describe("orders loader", () => {
  it("listOrderDates returns ascending date strings", () => {
    const dates = listOrderDates();
    expect(dates.length).toBeGreaterThan(0);
    for (let i = 1; i < dates.length; i++) {
      expect(dates[i] > dates[i - 1]).toBe(true);
    }
    for (const d of dates) expect(d).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("loadOrdersForDate joins outbox and inbox by order_id", () => {
    const dates = listOrderDates();
    const latest = dates[dates.length - 1];
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

  it("loadOrdersByAgentForDate filters by agent_id", () => {
    const dates = listOrderDates();
    const latest = dates[dates.length - 1];
    const all = loadOrdersForDate(latest);
    const agent = all[0].agent_id;
    const filtered = loadOrdersByAgentForDate(latest, agent);
    expect(filtered.length).toBeGreaterThan(0);
    for (const o of filtered) expect(o.agent_id).toBe(agent);
  });

  it("loadAllOrders returns orders across every date in the store", () => {
    const dates = listOrderDates();
    const all = loadAllOrders();
    const uniqueDates = new Set(all.map((o) => o.date));
    expect(uniqueDates.size).toBe(dates.length);
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
});
