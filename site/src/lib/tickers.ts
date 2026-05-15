import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

const REGISTRY_FILE = path.join(DATA_DIR, "tickers.json");

type TickerInfo = {
  name: string | null;
  type: "equity" | "etf" | "crypto" | "forex" | "unknown";
};

type Registry = Record<string, TickerInfo>;

let cached: Registry | null = null;

function loadRegistry(): Registry {
  if (cached) return cached;
  if (!fs.existsSync(REGISTRY_FILE)) {
    cached = {};
    return cached;
  }
  cached = JSON.parse(fs.readFileSync(REGISTRY_FILE, "utf-8")) as Registry;
  return cached;
}

export function tickerName(symbol: string): string | null {
  return loadRegistry()[symbol]?.name ?? null;
}

export function tickerType(symbol: string): TickerInfo["type"] {
  return loadRegistry()[symbol]?.type ?? "unknown";
}
