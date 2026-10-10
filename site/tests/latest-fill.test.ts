import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { latestFill, rawRecordUrl, byFillTimeDesc } from "@/lib/latest-fill";
import { formatPrice, formatShares } from "@/lib/format";
import { DATA_DIR } from "@/lib/paths";
import { loadAllOrders, type Order } from "@/lib/orders";
import { isTradingAgent } from "@/lib/roster";

function order(p: Partial<Order>): Order {
  return {
    order_id: "ord_x",
    agent_id: "satoshi",
    date: "2026-10-01",
    action: "BUY",
    ticker: "BTC-EUR",
    shares: 1,
    reasoning: "r",
    currency: "EUR",
    status: "filled",
    fill_price: 1,
    fill_currency: "EUR",
    fees: 0,
    reject_reason: null,
    ts_filled: null,
    ...p,
  };
}

const traders = (id: string) => id !== "the-manager";

describe("latestFill", () => {
  it("leads with the newest confirmation, not the newest authored date", () => {
    // A conditional order authored weeks earlier that fired today is today's fill.
    const old = order({ order_id: "a", date: "2026-09-16", ts_filled: "2026-10-10T17:41:10Z" });
    const recent = order({ order_id: "b", date: "2026-10-09", ts_filled: "2026-10-09T22:30:00Z" });
    expect(latestFill([recent, old], traders)?.order_id).toBe("a");
  });

  it("ignores rejected and pending orders", () => {
    const fill = order({ order_id: "f", ts_filled: "2026-10-01T22:00:00Z" });
    const rej = order({ order_id: "r", status: "rejected", ts_filled: "2026-10-05T22:00:00Z" });
    const pen = order({ order_id: "p", status: "pending", date: "2026-10-06" });
    expect(latestFill([rej, fill, pen], traders)?.order_id).toBe("f");
  });

  it("excludes agents the predicate rejects (the Manager)", () => {
    const mgr = order({ order_id: "m", agent_id: "the-manager", ts_filled: "2026-10-10T22:00:00Z" });
    const t = order({ order_id: "t", ts_filled: "2026-10-01T22:00:00Z" });
    expect(latestFill([mgr, t], traders)?.order_id).toBe("t");
  });

  it("falls back to an earlier fill rather than hiding, and is null with no fills", () => {
    expect(latestFill([order({ order_id: "only", date: "2026-04-17" })], traders)?.order_id).toBe("only");
    expect(latestFill([order({ status: "rejected" })], traders)).toBeNull();
    expect(latestFill([], traders)).toBeNull();
  });

  it("an unconfirmed time sorts before a confirmed one on the same day", () => {
    const bare = order({ order_id: "z", date: "2026-10-10", ts_filled: null });
    const timed = order({ order_id: "a", date: "2026-10-10", ts_filled: "2026-10-10T01:00:00Z" });
    expect(latestFill([bare, timed], traders)?.order_id).toBe("a");
  });

  it("finds a trader fill in the committed ledger, with a line address", () => {
    const o = latestFill(loadAllOrders(), isTradingAgent);
    expect(o).not.toBeNull();
    expect(o!.status).toBe("filled");
    expect(o!.reasoning.length).toBeGreaterThan(0);
    expect(rawRecordUrl(o!)).toMatch(/\/outbox\/\d{4}-\d{2}-\d{2}\.jsonl#L\d+$/);
  });
});

describe("byFillTimeDesc", () => {
  it("puts the hero's fill first, so the tape and the hero agree", () => {
    const fired = order({ order_id: "a", date: "2026-09-16", ts_filled: "2026-10-10T17:41:10Z" });
    const recent = order({ order_id: "b", date: "2026-10-09", ts_filled: "2026-10-09T22:30:00Z" });
    const sorted = [recent, fired].sort(byFillTimeDesc);
    expect(sorted[0]).toBe(latestFill([recent, fired], traders));
  });
});

describe("raw record address", () => {
  it("every outbox order's line is its physical line in the file", () => {
    for (const o of loadAllOrders().slice(-200)) {
      const lines = fs.readFileSync(path.join(DATA_DIR, "orders", "outbox", `${o.date}.jsonl`), "utf-8").split("\n");
      expect(JSON.parse(lines[o.line! - 1]).order_id).toBe(o.order_id);
    }
  });
});

describe("formatShares", () => {
  it("drops trailing zeros at each precision band", () => {
    expect(formatShares(185)).toBe("185");
    expect(formatShares(1.5)).toBe("1.5");
    expect(formatShares(0.015)).toBe("0.015");
  });
});

describe("formatPrice", () => {
  it("groups thousands behind the ISO code", () => {
    expect(formatPrice(73348.92, "EUR")).toBe("EUR 73,348.92");
  });
  it("keeps four decimals under 10 and two above", () => {
    expect(formatPrice(1.08321, "USD")).toBe("USD 1.0832");
    expect(formatPrice(385.45001220703125, "USD")).toBe("USD 385.45");
  });
  it("prints a dash for a missing price and omits a missing code", () => {
    expect(formatPrice(null, "EUR")).toBe("—");
    expect(formatPrice(12, null)).toBe("12.00");
  });
});
