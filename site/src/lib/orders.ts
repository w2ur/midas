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

function datesInDir(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .map((f) => f.match(DATE_RE)?.[1] ?? null)
    .filter((d): d is string => d !== null);
}

/**
 * Every date that has either an outbox or an inbox file — the union, not just
 * the outbox. A conditional (trigger) order is authored on one date and can
 * fire on a later one, so a date can carry fill confirmations (inbox) with no
 * newly-authored orders (outbox) at all — e.g. a weekend day the watcher
 * fires on but no session runs. Unioning means such a date is never invisible
 * to callers that iterate `listOrderDates()` (loadAllOrders(), most notably).
 */
export function listOrderDates(): string[] {
  return Array.from(new Set([...datesInDir(OUTBOX_DIR), ...datesInDir(INBOX_DIR)])).sort();
}

/**
 * Global order_id → inbox row index, built across *every* inbox file. Fill
 * confirmations must be looked up this way rather than scoped to a single
 * date, because a conditional order's fill can land on a different date file
 * than the one it was placed on (see loadOrdersForDate's doc comment).
 */
function loadInboxIndex(): Map<string, InboxRow> {
  const byId = new Map<string, InboxRow>();
  for (const date of datesInDir(INBOX_DIR)) {
    for (const row of readJsonl<InboxRow>(path.join(INBOX_DIR, `${date}.jsonl`))) {
      byId.set(row.order_id, row);
    }
  }
  return byId;
}

/**
 * Orders authored (outbox) on `date`, each resolved to its current status
 * from the *global* inbox index — not an inbox file scoped to this same
 * date. `date` is always the order's placement date, never its fill date:
 * a conditional order that fires later keeps the date it was authored on, so
 * callers that bucket orders by date (the per-day archive/feed pages) see it
 * exactly once, on the day the agent decided to place it. Regression: a
 * conditional order placed on `date` but fired (and inbox-confirmed) on a
 * later date used to be joined against that later date's inbox file only,
 * which doesn't exist here — it silently read back as "pending" forever even
 * after it had genuinely filled. See tests/orders.test.ts.
 */
function ordersForDate(date: string, inboxIndex: Map<string, InboxRow>): Order[] {
  const outbox = readJsonl<OutboxRow>(path.join(OUTBOX_DIR, `${date}.jsonl`));
  return outbox.map((o) => {
    const fill = inboxIndex.get(o.order_id);
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

export function loadOrdersForDate(date: string): Order[] {
  return ordersForDate(date, loadInboxIndex());
}

export function loadOrdersByAgentForDate(date: string, agentId: string): Order[] {
  return loadOrdersForDate(date).filter((o) => o.agent_id === agentId);
}

export function loadAllOrders(): Order[] {
  // Build the inbox index once and reuse it across every date, rather than
  // re-reading every inbox file per date (loadOrdersForDate's default path).
  const inboxIndex = loadInboxIndex();
  const all: Order[] = [];
  for (const date of listOrderDates()) {
    all.push(...ordersForDate(date, inboxIndex));
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
