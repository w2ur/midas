import { describe, it, expect } from "vitest";
import {
  loadBenchmark,
  loadCoinFlip,
  loadGlobalReference,
  returnPct,
  AGENT_BENCHMARK_LABELS,
  type BaselineSnapshot,
} from "@/lib/baselines";

const ROSTER = [
  "satoshi",
  "monsieur-forex",
  "goldfinger",
  "world",
  "steady-eddie-eur",
  "steady-eddie-usd",
  "sharp-shooter-eur",
  "sharp-shooter-usd",
  "yolo-sapiens-eur",
  "yolo-sapiens-usd",
];

function snap(date: string, value: number): BaselineSnapshot {
  return {
    date,
    portfolio_value: value,
    cash: 0,
    positions_value: value,
    currency: "EUR",
  };
}

describe("returnPct", () => {
  it("returns null for an empty series", () => {
    expect(returnPct([])).toBe(null);
  });

  it("returns 0 for a single-element series", () => {
    expect(returnPct([snap("2026-01-01", 10000)])).toBe(0);
  });

  it("computes (last - first) / first as a percent", () => {
    const series = [snap("2026-01-01", 10000), snap("2026-01-02", 11000)];
    expect(returnPct(series)).toBeCloseTo(10);
  });

  it("handles negative returns", () => {
    const series = [snap("2026-01-01", 10000), snap("2026-01-02", 9500)];
    expect(returnPct(series)).toBeCloseTo(-5);
  });

  it("returns null when first value is zero (avoids div-by-zero)", () => {
    const series = [snap("2026-01-01", 0), snap("2026-01-02", 1000)];
    expect(returnPct(series)).toBe(null);
  });

  it("uses only the first and last points (ignores middle volatility)", () => {
    const series = [
      snap("2026-01-01", 10000),
      snap("2026-01-02", 50000),    // spike — ignored
      snap("2026-01-03", 100),       // crash — ignored
      snap("2026-01-04", 11000),
    ];
    expect(returnPct(series)).toBeCloseTo(10);
  });
});

describe("loaders", () => {
  it("loadBenchmark returns a non-empty series for satoshi", () => {
    const series = loadBenchmark("satoshi");
    expect(series.length).toBeGreaterThan(0);
    for (const s of series) {
      expect(s.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(typeof s.portfolio_value).toBe("number");
    }
  });

  it("loadCoinFlip returns a series for satoshi", () => {
    const series = loadCoinFlip("satoshi");
    expect(series.length).toBeGreaterThan(0);
  });

  it("loadBenchmark returns [] for an unknown agent", () => {
    expect(loadBenchmark("does-not-exist")).toEqual([]);
  });

  it("loadCoinFlip returns [] for an unknown agent", () => {
    expect(loadCoinFlip("does-not-exist")).toEqual([]);
  });

  it("loadGlobalReference returns the MSCI World series", () => {
    const series = loadGlobalReference();
    expect(series.length).toBeGreaterThan(0);
  });
});

describe("AGENT_BENCHMARK_LABELS", () => {
  it("covers every agent in the 10-agent roster", () => {
    for (const aid of ROSTER) {
      expect(AGENT_BENCHMARK_LABELS[aid]).toBeTypeOf("string");
      expect(AGENT_BENCHMARK_LABELS[aid].length).toBeGreaterThan(0);
    }
  });

  it("contains exactly the 10 roster agents (no orphans)", () => {
    const keys = Object.keys(AGENT_BENCHMARK_LABELS).sort();
    expect(keys).toEqual([...ROSTER].sort());
  });
});
