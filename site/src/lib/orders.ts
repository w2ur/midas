import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

const OUTBOX_DIR = path.join(DATA_DIR, "orders", "outbox");
const INBOX_DIR = path.join(DATA_DIR, "orders", "inbox");
const DATE_RE = /^(\d{4}-\d{2}-\d{2})\.jsonl$/;

export type OrderStatus = "filled" | "rejected" | "pending";

export type Order = {
  order_id: string;
  agent_id: string;
  date: string;
  action: "BUY" | "SELL";
  ticker: string;
  shares: number;
  reasoning: string;
  currency: string;
  status: OrderStatus;
  fill_price: number | null;
  fill_currency: string | null;
  fees: number | null;
  reject_reason: string | null;
};

type OutboxRow = {
  order_id: string;
  ts: string;
  agent_id: string;
  action: "BUY" | "SELL";
  ticker: string;
  shares: number;
  reasoning: string;
  currency: string;
};

type InboxRow = {
  order_id: string;
  ts_filled: string;
  status: OrderStatus;
  fill_price: number | null;
  fill_currency: string | null;
  notional_base: number | null;
  fees: number | null;
  reason: string | null;
};

function readJsonl<T>(file: string): T[] {
  if (!fs.existsSync(file)) return [];
  return fs
    .readFileSync(file, "utf-8")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as T);
}

export function listOrderDates(): string[] {
  if (!fs.existsSync(OUTBOX_DIR)) return [];
  return fs
    .readdirSync(OUTBOX_DIR)
    .map((f) => f.match(DATE_RE)?.[1] ?? null)
    .filter((d): d is string => d !== null)
    .sort();
}

export function loadOrdersForDate(date: string): Order[] {
  const outbox = readJsonl<OutboxRow>(path.join(OUTBOX_DIR, `${date}.jsonl`));
  const inbox = readJsonl<InboxRow>(path.join(INBOX_DIR, `${date}.jsonl`));
  const byId = new Map(inbox.map((r) => [r.order_id, r]));
  return outbox.map((o) => {
    const fill = byId.get(o.order_id);
    return {
      order_id: o.order_id,
      agent_id: o.agent_id,
      date,
      action: o.action,
      ticker: o.ticker,
      shares: o.shares,
      reasoning: o.reasoning,
      currency: o.currency,
      status: fill?.status ?? "pending",
      fill_price: fill?.fill_price ?? null,
      fill_currency: fill?.fill_currency ?? null,
      fees: fill?.fees ?? null,
      reject_reason: fill?.reason ?? null,
    };
  });
}

export function loadOrdersByAgentForDate(date: string, agentId: string): Order[] {
  return loadOrdersForDate(date).filter((o) => o.agent_id === agentId);
}

export function loadAllOrders(): Order[] {
  const all: Order[] = [];
  for (const date of listOrderDates()) {
    all.push(...loadOrdersForDate(date));
  }
  return all;
}

/** URL-safe slug for a ticker: keep letters/digits, collapse other chars to `-`. */
export function tickerSlug(ticker: string): string {
  return ticker.replace(/[^A-Za-z0-9]/g, "-").replace(/-+$/, "").toUpperCase();
}

/** Inverse map from slug → canonical ticker, built from all known orders. */
export function buildTickerIndex(): Map<string, string> {
  const map = new Map<string, string>();
  for (const o of loadAllOrders()) {
    const slug = tickerSlug(o.ticker);
    if (!map.has(slug)) map.set(slug, o.ticker);
  }
  return map;
}
