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
 *   - Weekday session (every Mon-Fri 22:00 UTC since 2026-09-28; 20:00 before)
 *   - Weekend valuation refresh (dispatched after the Sun/Mon crypto fetch; Sat/Sun 20:00 UTC crons as fallback)
 *   - Trigger watcher when a conditional order fires: crypto whenever the
 *     Cloudflare Worker gate (workers/trigger-gate/) sees a pending trigger at
 *     its level and dispatches check-triggers-crypto.yml; everything else at
 *     the daily 13:00 UTC check-triggers.yml sweep
 *
 * Returns null when the file doesn't exist (initial-deploy fallback path
 * before the first session/refresh has written it).
 */
export function loadCurrentLeaderboard(): CurrentLeaderboard | null {
  if (!fs.existsSync(CURRENT_PATH)) return null;
  const raw = fs.readFileSync(CURRENT_PATH, "utf-8");
  return JSON.parse(raw) as CurrentLeaderboard;
}
