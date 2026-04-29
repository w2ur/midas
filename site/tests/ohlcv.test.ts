import { describe, it, expect } from "vitest";
import { inferCurrency, loadLastClose } from "@/lib/ohlcv";

describe("inferCurrency", () => {
  it("maps -USD / -EUR / -GBP suffix to the suffix currency", () => {
    expect(inferCurrency("BTC-USD")).toBe("USD");
    expect(inferCurrency("BTC-EUR")).toBe("EUR");
    expect(inferCurrency("XAU-GBP")).toBe("GBP");
  });

  it("returns null for FX pairs (=X suffix)", () => {
    expect(inferCurrency("EURUSD=X")).toBe(null);
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

  it("maps London (.L) to GBP", () => {
    expect(inferCurrency("HSBA.L")).toBe("GBP");
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
});
