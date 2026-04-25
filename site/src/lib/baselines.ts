import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

const BASELINES_DIR = path.join(DATA_DIR, "baselines");

export type BaselineSnapshot = {
  date: string;
  portfolio_value: number;
  cash: number;
  positions_value: number;
  currency: "EUR" | "USD";
};

function readJson<T>(p: string): T | null {
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

export function loadBenchmark(agentId: string): BaselineSnapshot[] {
  return readJson<BaselineSnapshot[]>(path.join(BASELINES_DIR, agentId, "benchmark.json")) ?? [];
}

export function loadCoinFlip(agentId: string): BaselineSnapshot[] {
  return readJson<BaselineSnapshot[]>(path.join(BASELINES_DIR, agentId, "coinflip.json")) ?? [];
}

export function loadGlobalReference(): BaselineSnapshot[] {
  return readJson<BaselineSnapshot[]>(path.join(BASELINES_DIR, "global", "msci_world.json")) ?? [];
}

/** Return (latest - initial) / initial as a percent. null if series empty. */
export function returnPct(series: BaselineSnapshot[]): number | null {
  if (series.length === 0) return null;
  const first = series[0].portfolio_value;
  const last = series[series.length - 1].portfolio_value;
  if (first === 0) return null;
  return ((last - first) / first) * 100;
}
