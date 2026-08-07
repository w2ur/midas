import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import {
  isMixedCurrency,
  loadPortfolioSnapshots,
  positionWeights,
} from "@/lib/portfolios";
import { DATA_DIR } from "@/lib/paths";

describe("loadPortfolioSnapshots", () => {
  it("returns [] for an unknown agent", () => {
    expect(loadPortfolioSnapshots("does-not-exist")).toEqual([]);
  });

  it("returns snapshots sorted ascending by date", () => {
    const snaps = loadPortfolioSnapshots("satoshi");
    expect(snaps.length).toBeGreaterThan(0);
    for (let i = 1; i < snaps.length; i++) {
      expect(snaps[i].date >= snaps[i - 1].date).toBe(true);
    }
  });

  it("deduplicates dates (last occurrence wins)", () => {
    const snaps = loadPortfolioSnapshots("satoshi");
    const dates = snaps.map((s) => s.date);
    expect(new Set(dates).size).toBe(dates.length);
  });

  it("returns objects with date + portfolio_value", () => {
    const snaps = loadPortfolioSnapshots("satoshi");
    for (const s of snaps) {
      expect(s.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(typeof s.portfolio_value).toBe("number");
    }
  });

  it("survives bare NaN tokens in the source JSON", () => {
    // Manufacture a snapshots.json with a bare NaN in a position sub-field
    // (the real-world failure mode this loader was hardened against).
    const tmpDir = fs.mkdtempSync(path.join(DATA_DIR, "portfolios", "_tmp_test_"));
    try {
      fs.writeFileSync(
        path.join(tmpDir, "snapshots.json"),
        '[{"date": "2026-01-01", "portfolio_value": 10000, "positions": {"X": {"unrealized_pnl": NaN}}}, {"date": "2026-01-02", "portfolio_value": 10100}]',
      );
      const agentId = path.basename(tmpDir);
      const snaps = loadPortfolioSnapshots(agentId);
      expect(snaps).toHaveLength(2);
      expect(snaps[0].portfolio_value).toBe(10000);
      expect(snaps[1].portfolio_value).toBe(10100);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// Weight column — cross-currency sums (2026-08-07 review, W7.2)
// ---------------------------------------------------------------------------

describe("positionWeights", () => {
  it("computes share-of-total for a single-currency book", () => {
    expect(
      positionWeights([
        { value: 750, currency: "EUR" },
        { value: 250, currency: "EUR" },
      ]),
    ).toEqual([75, 25]);
  });

  it("returns null for every row once the book mixes currencies", () => {
    // `world` really does hold CHF, EUR and GBP at once. Summing them made
    // every row's weight wrong, not only the foreign one's.
    expect(
      positionWeights([
        { value: 750, currency: "EUR" },
        { value: 250, currency: "GBP" },
      ]),
    ).toEqual([null, null]);
  });

  it("excludes unpriced rows from the denominator without killing the column", () => {
    expect(
      positionWeights([
        { value: 750, currency: "EUR" },
        { value: 250, currency: "EUR" },
        { value: null, currency: "EUR" },
      ]),
    ).toEqual([75, 25, null]);
  });

  it("suppresses weights when nothing is valued", () => {
    expect(positionWeights([{ value: null, currency: "EUR" }])).toEqual([null]);
  });

  it("treats an unlabelled value as its own currency bucket", () => {
    // A value whose currency could not be resolved cannot be proven to share
    // a unit with the others — fail closed, same as the engine.
    expect(
      positionWeights([
        { value: 750, currency: "EUR" },
        { value: 250, currency: null },
      ]),
    ).toEqual([null, null]);
  });
});

describe("isMixedCurrency", () => {
  it("is false for one currency and true for two", () => {
    expect(isMixedCurrency([{ value: 1, currency: "EUR" }])).toBe(false);
    expect(
      isMixedCurrency([
        { value: 1, currency: "EUR" },
        { value: 1, currency: "CHF" },
      ]),
    ).toBe(true);
  });

  it("ignores unpriced rows", () => {
    expect(
      isMixedCurrency([
        { value: 1, currency: "EUR" },
        { value: null, currency: "GBP" },
      ]),
    ).toBe(false);
  });
});
