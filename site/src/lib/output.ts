import * as fs from "node:fs";
import * as path from "node:path";
import { OUTPUT_DIR } from "./paths";

export type LeaderboardRow = {
  agent: string;
  return_pct: number;
  rank: number;
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
