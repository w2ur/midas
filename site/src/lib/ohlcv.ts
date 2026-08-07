import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";

const OHLCV_DIR = path.join(DATA_DIR, "market", "ohlcv");
const CURRENCY_OVERRIDES_FILE = path.join(DATA_DIR, "ticker_currencies.json");
const REGISTRY_FILE = path.join(DATA_DIR, "tickers.json");

type OHLCVRow = {
  date: string;
  close: number;
  adj_close?: number;
};

const ohlcvCache = new Map<string, OHLCVRow[]>();
let currencyOverrides: Record<string, string> | null = null;
let registryCurrencies: Record<string, string> | null = null;

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

/** The raw stored close — in whatever unit the vendor quotes, pence included. */
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

const CURRENCY_CODE = /^[A-Za-z]{3}$/;

/**
 * Vendor-captured quote units from data/tickers.json, written by
 * scripts/fetch_ohlcv.py. Values are verbatim, so `GBp` (pence) survives —
 * see quoteUnit / inferCurrency below. Malformed values are dropped: the
 * vendor really did answer "3.3" for ENX.AS on 2026-08-07.
 */
function loadRegistryCurrencies(): Record<string, string> {
  if (registryCurrencies) return registryCurrencies;
  const out: Record<string, string> = {};
  if (fs.existsSync(REGISTRY_FILE)) {
    const raw = JSON.parse(fs.readFileSync(REGISTRY_FILE, "utf-8")) as Record<string, { currency?: string | null }>;
    for (const [symbol, info] of Object.entries(raw ?? {})) {
      const ccy = info?.currency;
      if (typeof ccy === "string" && CURRENCY_CODE.test(ccy.trim())) out[symbol] = ccy.trim();
    }
  }
  registryCurrencies = out;
  return registryCurrencies;
}

/** Vendor sub-unit → [ISO currency, price multiplier]. Case-sensitive: GBp ≠ GBP. */
const SUB_UNITS: Record<string, [string, number]> = {
  GBp: ["GBP", 0.01],
  GBX: ["GBP", 0.01],
  ZAc: ["ZAR", 0.01],
  ILA: ["ILS", 0.01],
};

/**
 * The unit this ticker's stored prices are denominated in — may be a
 * sub-unit code such as `GBp` (LSE pence). Mirrors
 * `engine.quotes.quote_currency`: override map, then the vendor registry,
 * then the suffix heuristic. Returns null when nothing can answer.
 */
export function quoteUnit(ticker: string): string | null {
  const overrides = loadCurrencyOverrides();
  if (ticker in overrides) return overrides[ticker];
  const registry = loadRegistryCurrencies();
  if (ticker in registry) return registry[ticker];

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
    case "HE": case "LU": case "AT":
      return "EUR";
    case "ST":
      return "SEK";
    case "OL":
      return "NOK";
    case "CO":
      return "DKK";
    case "WA":
      return "PLN";
    case "L":
      // The LSE's default quoting convention is pence, not pounds.
      return "GBp";
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

/** ISO 4217 code for a ticker's quote currency, or null if unknown. `GBp` → `GBP`. */
export function inferCurrency(ticker: string): string | null {
  const unit = quoteUnit(ticker);
  if (unit === null) return null;
  return SUB_UNITS[unit]?.[0] ?? unit.toUpperCase();
}

/**
 * A position's latest close, already denominated in an ISO currency.
 *
 * The one place on the site where a pence quote becomes pounds — mirroring
 * `engine.quotes.latest_price`, which is the one place in the engine. Before
 * this existed, PortfolioTable rendered `close * shares` labelled with the
 * suffix-guessed currency, so `world`'s 8 LLOY.L showed as GBP 932.80 rather
 * than GBP 9.33.
 */
export function loadPositionQuote(
  ticker: string,
  onOrBefore: string,
): { price: number; currency: string | null; date: string } | null {
  const row = loadLastClose(ticker, onOrBefore);
  if (row === null) return null;
  const unit = quoteUnit(ticker);
  const scale = unit === null ? 1 : (SUB_UNITS[unit]?.[1] ?? 1);
  return { price: row.close * scale, currency: inferCurrency(ticker), date: row.date };
}
