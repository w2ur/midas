import { describe, it, expect } from "vitest";
import { listDates, loadByDate, loadLatest, type OutputBundle } from "@/lib/output";

describe("output loader", () => {
  it("listDates returns at least 3 dates, sorted ascending", () => {
    const dates = listDates();
    expect(dates.length).toBeGreaterThanOrEqual(3);
    const sorted = [...dates].sort();
    expect(dates).toEqual(sorted);
    for (const d of dates) {
      expect(d).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
  });

  it("loadLatest returns the highest-date bundle with leaderboard and agents", () => {
    const bundle = loadLatest();
    expect(bundle.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(Array.isArray(bundle.leaderboard)).toBe(true);
    expect(bundle.leaderboard.length).toBe(10);
    for (const row of bundle.leaderboard) {
      expect(typeof row.agent).toBe("string");
      expect(typeof row.return_pct).toBe("number");
      expect(typeof row.rank).toBe("number");
    }
    expect(Object.keys(bundle.agents).length).toBeGreaterThanOrEqual(10);
  });

  it("loadByDate('2026-04-20') loads that specific bundle", () => {
    const bundle = loadByDate("2026-04-20");
    expect(bundle.date).toBe("2026-04-20");
  });

  it("loadByDate throws on missing date", () => {
    expect(() => loadByDate("1999-01-01")).toThrow();
  });

  it("portfolio fields are typed (cash, deployed, positions, currency)", () => {
    const bundle: OutputBundle = loadLatest();
    for (const [, data] of Object.entries(bundle.agents)) {
      expect(typeof data.portfolio.cash).toBe("number");
      expect(typeof data.portfolio.deployed).toBe("number");
      expect(Array.isArray(data.portfolio.positions)).toBe(true);
      expect(["EUR", "USD"]).toContain(data.portfolio.currency);
    }
  });
});
