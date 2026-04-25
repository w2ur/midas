import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

export type PortfolioSnapshot = {
  date: string;
  portfolio_value: number;
};

export function loadPortfolioSnapshots(agentId: string): PortfolioSnapshot[] {
  const p = path.join(DATA_DIR, "portfolios", agentId, "snapshots.json");
  if (!fs.existsSync(p)) return [];
  // Snapshots files may contain bare `NaN` tokens (not valid JSON) in position
  // sub-fields. Replace them with null before parsing so JSON.parse doesn't throw.
  const text = fs.readFileSync(p, "utf-8").replace(/:\s*NaN/g, ": null");
  const raw = JSON.parse(text) as PortfolioSnapshot[];
  // Snapshots can carry duplicate dates (verified against satoshi/snapshots.json).
  // Keep the last occurrence per date.
  const byDate = new Map<string, PortfolioSnapshot>();
  for (const s of raw) byDate.set(s.date, s);
  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
}
