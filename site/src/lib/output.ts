import * as fs from "node:fs";
import * as path from "node:path";
import { OUTPUT_DIR } from "./paths";

export type LeaderboardRow = {
  agent: string;
  return_pct: number;
  rank: number;
  /**
   * The book's denomination — the unit `return_local_pct` is measured in.
   * Published by the engine (2026-08-14) rather than read off the site's
   * roster, which answers "mixed" for `world`: that describes its holdings,
   * not the currency its book is kept in. Absent in older artifacts.
   */
  currency?: string;
  /**
   * Book-currency return minus the agent's own benchmark return, in
   * percentage points — FX-free by construction, and since 2026-08-14 the
   * quantity `rank` is ordered by. Absent in artifacts written before that
   * date; null when the agent has no baseline series (rows rank after the
   * measured ones, by raw EUR return — same null-last rule as board-sort).
   */
  vs_benchmark_pp?: number | null;
  /** Same measure against the agent's coin-flip control. */
  vs_coinflip_pp?: number | null;
  /**
   * Book-currency return since inception, in percent — the figure the two
   * `vs_*` fields are subtractions on, and the leg that closes the identity
   * `(1 + return_pct) = (1 + return_local_pct) × (1 + fx_translation_pp)`.
   * Equal to `return_pct` on EUR books. Absent in artifacts written before
   * 2026-08-14; null when the book cannot be valued.
   */
  return_local_pct?: number | null;
  /**
   * EUR value of the book's currency vs day one, in percentage points — the
   * leaderboard tailwind a flat non-EUR book would show. Absent on EUR books.
   */
  fx_translation_pp?: number;
};

export type AgentPortfolio = {
  cash: number;
  deployed: number;
  positions: { ticker: string; shares: number }[];
  currency: "EUR" | "USD";
};

export type AgentDailyData = {
  // null when the agent didn't run this session (e.g. weekend cadence skips
  // the equity-only roster). Bundle still contains every agent every day.
  commentary: string | null;
  trades: unknown[];
  portfolio: AgentPortfolio;
  posts: unknown[];
};

export type OutputBundle = {
  date: string;
  market_snapshot: Record<string, unknown>;
  agents: Record<string, AgentDailyData>;
  narrator: Record<string, unknown>;
  leaderboard: LeaderboardRow[];
};

const DATE_RE = /^(\d{4}-\d{2}-\d{2})\.json$/;

export function listDates(): string[] {
  return fs
    .readdirSync(OUTPUT_DIR)
    .map((f) => {
      const m = f.match(DATE_RE);
      return m ? m[1] : null;
    })
    .filter((d): d is string => d !== null)
    .sort();
}

export function loadByDate(date: string): OutputBundle {
  const file = path.join(OUTPUT_DIR, `${date}.json`);
  if (!fs.existsSync(file)) {
    throw new Error(`Output bundle not found: ${file}`);
  }
  const raw = fs.readFileSync(file, "utf-8");
  return JSON.parse(raw) as OutputBundle;
}

export function loadLatest(): OutputBundle {
  const dates = listDates();
  if (dates.length === 0) throw new Error(`No output bundles in ${OUTPUT_DIR}`);
  return loadByDate(dates[dates.length - 1]);
}
