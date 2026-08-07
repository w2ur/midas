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

/**
 * The stored close, already in the ticker's ISO currency.
 *
 * This said "in whatever unit the vendor quotes, pence included" until
 * 2026-08-07. It is no longer true — the store is ISO-denominated at ingest —
 * and it was an instruction to divide, which is how the double-divide gets
 * reintroduced. Use `loadPositionQuote` when you need the currency label too.
 */
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

/**
 * Vendor sub-unit → ISO currency. Case-sensitive: GBp ≠ GBP.
 *
 * The 0.01 multipliers that used to sit here were dead — nothing read them
 * once the pence→pounds division moved to ingest (2026-08-07) — and a dead
 * multiplier next to a price is an invitation. Deleted rather than kept
 * "for reference": the one thing this file must not do is scale.
 */
const SUB_UNITS: Record<string, string> = {
  GBp: "GBP",
  GBX: "GBP",
  ZAc: "ZAR",
  ILA: "ILS",
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
  return SUB_UNITS[unit] ?? unit.toUpperCase();
}

/**
 * A position's latest close, denominated in an ISO currency.
 *
 * Applies **no** scaling: since 2026-08-07 the store is ISO-denominated, the
 * pence→pounds division having moved to ingest
 * (`scripts.fetch_ohlcv._normalise_vendor_units`). This mirrors
 * `engine.quotes.store_quote`. Scaling here now would divide every LSE
 * position by 100 a second time — the mirror image of the original defect,
 * where PortfolioTable rendered `world`'s 8 LLOY.L as GBP 932.80.
 *
 * It stays a named function rather than collapsing into `loadLastClose`
 * because the currency label is the part callers must not hand-roll.
 */
export function loadPositionQuote(
  ticker: string,
  onOrBefore: string,
): { price: number; currency: string | null; date: string } | null {
  const row = loadLastClose(ticker, onOrBefore);
  if (row === null) return null;
  return { price: row.close, currency: inferCurrency(ticker), date: row.date };
}
