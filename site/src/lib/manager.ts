import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

// The Manager is a `role: allocator` in roster.yaml — NOT a trading agent. It is
// deliberately kept out of roster.ts's AgentId/TRADING_AGENTS so it never lands
// on the ranked leaderboard: different capital (EUR 2,000 vs 10,000), different
// inception (2026-06-16 vs 2026-04-17), different mandate. Its display manifest
// and data loaders live here instead. Mirrors the `the-manager` block in
// roster.yaml — keep in sync if that block changes.

export const MANAGER_ID = "the-manager" as const;

export const MANAGER = {
  id: MANAGER_ID,
  display_name: "The Manager", // roster.yaml display_name
  archetype: "Allocator who weighs the analysts' convictions and commits the capital",
  base_currency: "EUR" as const,
  initial_capital: 2000,
  inception: "2026-06-16",
  public_since: "2026-07-24",
  // Slate-steel: an administrative, off-to-the-side kit colour, distinct from
  // the bright trader kits and the cyan --ref line. Contrast-guarded in both
  // themes by tests/contrast.test.ts. Mirror in global.css [data-agent].
  signatureColor: { light: "#46607a", dark: "#8aa6c4" },
};

const MANAGER_DIR = path.join(DATA_DIR, "portfolios", "the-manager");
const BASELINE_DIR = path.join(DATA_DIR, "portfolios", "baseline-manager");
const REVIEW_DIR = path.join(DATA_DIR, "orders", "manager-review");
const OUTBOX_DIR = path.join(DATA_DIR, "orders", "manager-outbox");
const INBOX_DIR = path.join(DATA_DIR, "orders", "manager-inbox");
const DATE_RE = /^(\d{4}-\d{2}-\d{2})\.jsonl$/;

function readJson<T>(p: string): T | null {
  if (!fs.existsSync(p)) return null;
  // Snapshot files may carry bare `NaN` tokens (not valid JSON); null them out
  // before parsing, mirroring lib/portfolios.ts.
  const text = fs.readFileSync(p, "utf-8").replace(/:\s*NaN/g, ": null");
  return JSON.parse(text) as T;
}

function readJsonl<T>(file: string): T[] {
  if (!fs.existsSync(file)) return [];
  return fs
    .readFileSync(file, "utf-8")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as T);
}

// ── Portfolio ──────────────────────────────────────────────────────────────

export type ManagerPosition = {
  ticker: string;
  shares: number;
  avg_cost: number;
  date_opened: string;
  grid_level: number;
};

export type ManagerPortfolio = {
  cash: number;
  currency: string;
  last_updated: string;
  positions: ManagerPosition[];
};

export function loadManagerPortfolio(): ManagerPortfolio | null {
  return readJson<ManagerPortfolio>(path.join(MANAGER_DIR, "portfolio.json"));
}

// ── Snapshots (value chart) ──────────────────────────────────────────────────

export type ManagerSnapshot = {
  date: string;
  portfolio_value: number;
  cash: number;
  positions_value: number;
};

function loadSnapshots(dir: string): ManagerSnapshot[] {
  const raw = readJson<ManagerSnapshot[]>(path.join(dir, "snapshots.json")) ?? [];
  // Keep the last occurrence per date, sorted — same discipline as
  // lib/portfolios.ts (snapshot files can carry duplicate dates).
  const byDate = new Map<string, ManagerSnapshot>();
  for (const s of raw) byDate.set(s.date, s);
  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
}

export function loadManagerSnapshots(): ManagerSnapshot[] {
  return loadSnapshots(MANAGER_DIR);
}

/** The deterministic baseline twin — same shape, charted as the reference line. */
export function loadManagerBaseline(): ManagerSnapshot[] {
  return loadSnapshots(BASELINE_DIR);
}

// ── Decision log (manager-review) ─────────────────────────────────────────────

export type ManagerReviewPosition = {
  ticker: string;
  action: string;
  size_eur: number;
  entry_guidance: string;
  stop_loss?: number;
  reasoning: string;
  trigger?: { op: string; level: number };
  expires?: string;
};

export type ManagerReview = {
  date: string;
  conviction: number;
  positions: ManagerReviewPosition[];
  hold_reasoning: string;
  render: string;
};

/** Per-session decision records, reverse-chronological (the journal substitute). */
export function loadManagerReviews(): ManagerReview[] {
  if (!fs.existsSync(REVIEW_DIR)) return [];
  const files = fs
    .readdirSync(REVIEW_DIR)
    .filter((f) => /^\d{4}-\d{2}-\d{2}\.json$/.test(f));
  const reviews: ManagerReview[] = [];
  for (const f of files) {
    const r = readJson<ManagerReview>(path.join(REVIEW_DIR, f));
    if (r) reviews.push(r);
  }
  return reviews.sort((a, b) => b.date.localeCompare(a.date));
}

// ── Resolved outcomes (memory) ────────────────────────────────────────────────

export type ManagerResolved = {
  date: string;
  ticker: string;
  action: string;
  realized_return_pct: number;
  alpha_vs_msci_pct: number;
};

export function loadManagerResolved(): ManagerResolved[] {
  const raw = readJson<ManagerResolved[]>(path.join(REVIEW_DIR, "resolved.json")) ?? [];
  return raw.slice().sort((a, b) => b.date.localeCompare(a.date));
}

// ── Fills (manager-outbox + manager-inbox join) ───────────────────────────────

type ManagerOutboxRow = {
  order_id: string;
  ts: string;
  agent_id: string;
  action: "BUY" | "SELL";
  ticker: string;
  shares: number;
  reasoning: string;
  currency: string;
  trigger?: { op: string; level: number };
  expires?: string;
};

type ManagerInboxRow = {
  order_id: string;
  ts_filled: string;
  status: "filled" | "rejected";
  fill_price: number | null;
  fill_currency: string | null;
  notional_base: number | null;
  fees: number | null;
  reason: string | null;
  trigger_fired?: boolean;
};

export type ManagerFill = {
  order_id: string;
  date: string;
  action: "BUY" | "SELL";
  ticker: string;
  shares: number;
  reasoning: string;
  currency: string;
  trigger: { op: string; level: number } | null;
  expires: string | null;
  status: "filled" | "rejected" | "pending";
  fill_price: number | null;
  fill_currency: string | null;
  notional_base: number | null;
  fees: number | null;
  reject_reason: string | null;
  trigger_fired: boolean;
};

/** Join the isolated Manager order channels by order_id, reverse-chronological. */
export function loadManagerFills(): ManagerFill[] {
  if (!fs.existsSync(OUTBOX_DIR)) return [];
  const dates = fs
    .readdirSync(OUTBOX_DIR)
    .map((f) => f.match(DATE_RE)?.[1] ?? null)
    .filter((d): d is string => d !== null)
    .sort();

  // Inbox rows are keyed only by order_id (fills land on a different date than
  // the authoring session, so scan every inbox file into one index).
  const inbox = new Map<string, ManagerInboxRow>();
  if (fs.existsSync(INBOX_DIR)) {
    for (const f of fs.readdirSync(INBOX_DIR)) {
      if (!DATE_RE.test(f)) continue;
      for (const r of readJsonl<ManagerInboxRow>(path.join(INBOX_DIR, f))) {
        inbox.set(r.order_id, r);
      }
    }
  }

  const fills: ManagerFill[] = [];
  for (const date of dates) {
    for (const o of readJsonl<ManagerOutboxRow>(path.join(OUTBOX_DIR, `${date}.jsonl`))) {
      const f = inbox.get(o.order_id);
      fills.push({
        order_id: o.order_id,
        date,
        action: o.action,
        ticker: o.ticker,
        shares: o.shares,
        reasoning: o.reasoning,
        currency: o.currency,
        trigger: o.trigger ?? null,
        expires: o.expires ?? null,
        status: f?.status ?? "pending",
        fill_price: f?.fill_price ?? null,
        fill_currency: f?.fill_currency ?? null,
        notional_base: f?.notional_base ?? null,
        fees: f?.fees ?? null,
        reject_reason: f?.reason ?? null,
        trigger_fired: f?.trigger_fired ?? false,
      });
    }
  }
  return fills.sort((a, b) =>
    a.date === b.date ? b.order_id.localeCompare(a.order_id) : b.date.localeCompare(a.date),
  );
}

/** (latest - initial) / initial as a percent over a snapshot series. null if empty. */
export function snapshotReturnPct(series: ManagerSnapshot[]): number | null {
  if (series.length === 0) return null;
  const first = series[0].portfolio_value;
  const last = series[series.length - 1].portfolio_value;
  if (first === 0) return null;
  return ((last - first) / first) * 100;
}
