import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

const OHLCV_DIR = path.join(DATA_DIR, "market", "ohlcv");
const CURRENCY_OVERRIDES_FILE = path.join(DATA_DIR, "ticker_currencies.json");

type OHLCVRow = {
  date: string;
  close: number;
  adj_close?: number;
};

const ohlcvCache = new Map<string, OHLCVRow[]>();
let currencyOverrides: Record<string, string> | null = null;

function loadOHLCV(ticker: string): OHLCVRow[] | null {
  const cached = ohlcvCache.get(ticker);
  if (cached) return cached;
  const file = path.join(OHLCV_DIR, `${ticker}.jsonl`);
  if (!fs.existsSync(file)) return null;
  const rows = fs
    .readFileSync(file, "utf-8")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as OHLCVRow);
  ohlcvCache.set(ticker, rows);
  return rows;
}

export function loadLastClose(ticker: string, onOrBefore: string): { close: number; date: string } | null {
  const rows = loadOHLCV(ticker);
  if (!rows || rows.length === 0) return null;
  let best: OHLCVRow | null = null;
  for (const r of rows) {
    if (r.date <= onOrBefore && (best === null || r.date > best.date)) {
      best = r;
    }
  }
  if (best === null) return null;
  return { close: best.close, date: best.date };
}

function loadCurrencyOverrides(): Record<string, string> {
  if (currencyOverrides) return currencyOverrides;
  if (fs.existsSync(CURRENCY_OVERRIDES_FILE)) {
    currencyOverrides = JSON.parse(fs.readFileSync(CURRENCY_OVERRIDES_FILE, "utf-8")) as Record<string, string>;
  } else {
    currencyOverrides = {};
  }
  return currencyOverrides;
}

/** Infer quote currency from ticker suffix. Returns null if unknown. */
export function inferCurrency(ticker: string): string | null {
  const overrides = loadCurrencyOverrides();
  if (ticker in overrides) return overrides[ticker];

  if (/-USD$/i.test(ticker)) return "USD";
  if (/-EUR$/i.test(ticker)) return "EUR";
  if (/-GBP$/i.test(ticker)) return "GBP";
  if (/=X$/.test(ticker)) return null;

  const dot = ticker.lastIndexOf(".");
  if (dot === -1) return "USD";
  const suffix = ticker.slice(dot + 1).toUpperCase();
  switch (suffix) {
    case "PA": case "AS": case "BR": case "MC": case "MI":
    case "DE": case "F": case "LS": case "VI": case "IR":
    case "HE": case "ST": case "CO": case "OL":
      return "EUR";
    case "L":
      return "GBP";
    case "SW":
      return "CHF";
    case "T":
      return "JPY";
    case "TO": case "V":
      return "CAD";
    case "HK":
      return "HKD";
    case "AX":
      return "AUD";
    default:
      return null;
  }
}
