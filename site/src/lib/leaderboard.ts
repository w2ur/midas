import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";
import type { LeaderboardRow } from "./output";

export type CurrentLeaderboard = {
  updated_at: string;
  trigger: string;
  rows: LeaderboardRow[];
};

const CURRENT_PATH = path.join(DATA_DIR, "leaderboard", "current.json");

/**
 * Live leaderboard artifact. Written by three update paths:
 *   - Weekday session (every Mon-Fri 20:00 UTC)
 *   - Weekend valuation refresh (Sat/Sun 20:00 UTC)
 *   - Trigger watcher when a conditional order fires (every 15 min)
 *
 * Returns null when the file doesn't exist (initial-deploy fallback path
 * before the first session/refresh has written it).
 */
export function loadCurrentLeaderboard(): CurrentLeaderboard | null {
  if (!fs.existsSync(CURRENT_PATH)) return null;
  const raw = fs.readFileSync(CURRENT_PATH, "utf-8");
  return JSON.parse(raw) as CurrentLeaderboard;
}
