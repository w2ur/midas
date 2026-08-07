import { describe, it, expect } from "vitest";
import { inferCurrency, loadLastClose, loadPositionQuote, quoteUnit } from "@/lib/ohlcv";

describe("inferCurrency", () => {
  it("maps -USD / -EUR / -GBP suffix to the suffix currency", () => {
    expect(inferCurrency("BTC-USD")).toBe("USD");
    expect(inferCurrency("BTC-EUR")).toBe("EUR");
    expect(inferCurrency("XAU-GBP")).toBe("GBP");
  });

  it("resolves an FX pair to its quote leg from the vendor registry", () => {
    // Was `null` while currency came from the suffix alone: no rule on the
    // string "EURUSD=X" says which leg is quoted. data/tickers.json now
    // carries the vendor's answer, and it is the SECOND leg — the same one
    // engine.quotes resolves, so the site and the broker finally agree on
    // how monsieur-forex's AUDUSD=X position is denominated.
    expect(inferCurrency("EURUSD=X")).toBe("USD");
    expect(inferCurrency("EURGBP=X")).toBe("GBP");
    expect(inferCurrency("AUDUSD=X")).toBe("USD");
  });

  it("defaults to USD when no dot suffix is present", () => {
    expect(inferCurrency("AAPL")).toBe("USD");
    expect(inferCurrency("MSFT")).toBe("USD");
  });

  it("maps euro-zone exchange suffixes to EUR", () => {
    expect(inferCurrency("MC.PA")).toBe("EUR");   // also in overrides — overrides win first
    expect(inferCurrency("ASML.AS")).toBe("EUR");
    expect(inferCurrency("SAP.DE")).toBe("EUR");
    expect(inferCurrency("ENI.MI")).toBe("EUR");
    expect(inferCurrency("SAN.MC")).toBe("EUR");
  });

  it("maps London (.L) to GBP as an ISO code, but quotes it in pence", () => {
    // The LSE quotes in pence. `GBp` is a unit, not a currency: the ISO code
    // is GBP and the price carries the 1/100 (see loadPositionQuote).
    expect(quoteUnit("HSBA.L")).toBe("GBp");
    expect(inferCurrency("HSBA.L")).toBe("GBP");
  });

  it("does not assume every .L ticker is sterling", () => {
    // PHAG.L quotes in USD. No suffix rule can tell it from LLOY.L — this is
    // exactly what the vendor registry layer exists to carry.
    expect(inferCurrency("PHAG.L")).toBe("USD");
    expect(inferCurrency("LLOY.L")).toBe("GBP");
  });

  it("maps Swiss (.SW) to CHF, Tokyo (.T) to JPY, Toronto (.TO) to CAD", () => {
    // WDFC.SW is in the overrides file → still resolves to CHF.
    expect(inferCurrency("NESN.SW")).toBe("CHF");
    expect(inferCurrency("7203.T")).toBe("JPY");
    expect(inferCurrency("RY.TO")).toBe("CAD");
  });

  it("maps Hong Kong (.HK) to HKD and Sydney (.AX) to AUD", () => {
    expect(inferCurrency("0700.HK")).toBe("HKD");
    expect(inferCurrency("BHP.AX")).toBe("AUD");
  });

  it("returns null for unknown exchange suffixes", () => {
    expect(inferCurrency("FOO.ZZ")).toBe(null);
  });

  it("maps the Nordic exchanges to their own currencies, not EUR", () => {
    // .ST/.OL/.CO were EUR here and USD in the engine — neither is right.
    expect(inferCurrency("VOLV-B.ST")).toBe("SEK");
    expect(inferCurrency("EQNR.OL")).toBe("NOK");
    expect(inferCurrency("NOVO-B.CO")).toBe("DKK");
  });

  it("consults data/ticker_currencies.json overrides first", () => {
    // 7203.T is in the overrides file with explicit JPY — same as suffix
    // mapping. The override is the authoritative source for ambiguous cases.
    expect(inferCurrency("7203.T")).toBe("JPY");
  });
});

describe("loadLastClose", () => {
  it("returns null for a ticker with no JSONL file", () => {
    expect(loadLastClose("DOES-NOT-EXIST", "2026-04-29")).toBe(null);
  });

  it("returns the latest row on or before the requested date", () => {
    // AAPL is in the OHLCV store (committed for the universe).
    const result = loadLastClose("AAPL", "2030-01-01");
    expect(result).not.toBe(null);
    if (result === null) return;
    expect(typeof result.close).toBe("number");
    expect(result.close).toBeGreaterThan(0);
    expect(result.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(result.date <= "2030-01-01").toBe(true);
  });

  it("returns null when target date precedes the first row", () => {
    // OHLCV for AAPL begins well after 1900.
    expect(loadLastClose("AAPL", "1900-01-01")).toBe(null);
  });

  it("returns the stored quote, which is ISO-denominated", () => {
    // Since 2026-08-07 the store holds pounds, not pence — the division moved
    // to ingest (scripts/fetch_ohlcv._normalise_vendor_units). A Lloyds close
    // above 50 would mean the store had reverted to pence.
    const raw = loadLastClose("LLOY.L", "2030-01-01");
    expect(raw).not.toBe(null);
    if (raw === null) return;
    expect(raw.close).toBeGreaterThan(0.1);
    expect(raw.close).toBeLessThan(50); // pounds, ~1.16
  });
});

describe("loadPositionQuote", () => {
  it("labels an LSE quote GBP without rescaling it", () => {
    // The store is already pounds, so loadPositionQuote must pass the price
    // through untouched. Scaling here again would divide every LSE position
    // by 100 a second time — the mirror image of the original defect, and the
    // exact regression this equality is here to catch.
    const raw = loadLastClose("LLOY.L", "2030-01-01");
    const quote = loadPositionQuote("LLOY.L", "2030-01-01");
    expect(raw).not.toBe(null);
    expect(quote).not.toBe(null);
    if (raw === null || quote === null) return;
    expect(quote.currency).toBe("GBP");
    expect(quote.price).toBe(raw.close);
    expect(quote.date).toBe(raw.date);
  });

  it("leaves a whole-unit quote alone", () => {
    const raw = loadLastClose("AAPL", "2030-01-01");
    const quote = loadPositionQuote("AAPL", "2030-01-01");
    if (raw === null || quote === null) throw new Error("AAPL must be in the store");
    expect(quote.currency).toBe("USD");
    expect(quote.price).toBe(raw.close);
  });

  it("returns null for a ticker with no JSONL file", () => {
    expect(loadPositionQuote("DOES-NOT-EXIST", "2030-01-01")).toBe(null);
  });
});
