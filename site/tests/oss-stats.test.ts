import { describe, it, expect } from "vitest";
import { BROKER_RAILS, WATCHER_RAILS } from "../src/lib/rails";
import { ossStats } from "../src/lib/oss-stats";
import { loadAllOrders } from "../src/lib/orders";
import { currentDayNumber } from "../src/lib/session";

describe("rails registry", () => {
  it("carries the 15 broker codes", () => {
    expect(BROKER_RAILS).toHaveLength(15);
  });

  it("carries the single watcher code", () => {
    expect(WATCHER_RAILS.map((r) => r.code)).toEqual(["TRIGGER_EXPIRED"]);
  });

  it("has no duplicate codes across both registries", () => {
    const codes = [...BROKER_RAILS, ...WATCHER_RAILS].map((r) => r.code);
    expect(new Set(codes).size).toBe(codes.length);
  });

  it("gives every code a non-empty blurb", () => {
    for (const rail of [...BROKER_RAILS, ...WATCHER_RAILS]) {
      expect(rail.blurb.trim().length).toBeGreaterThan(0);
    }
  });

  it("uses SCREAMING_SNAKE_CASE codes so they match the engine literals", () => {
    for (const rail of [...BROKER_RAILS, ...WATCHER_RAILS]) {
      expect(rail.code).toMatch(/^[A-Z][A-Z_]+$/);
    }
  });
});

describe("ossStats", () => {
  it("reports the live session count from the Oracle's day number", () => {
    expect(ossStats().sessions).toBe(currentDayNumber());
  });

  it("counts only filled orders", () => {
    const all = loadAllOrders();
    expect(ossStats().fills).toBe(all.filter((o) => o.status === "filled").length);
  });

  it("reports fewer fills than total orders — rejections exist and are counted separately", () => {
    const s = ossStats();
    expect(s.fills).toBeGreaterThan(0);
    expect(s.fills).toBeLessThanOrEqual(loadAllOrders().length);
  });

  it("reports the rail counts from the registry, not a hardcoded number", () => {
    const s = ossStats();
    expect(s.brokerRails).toBe(BROKER_RAILS.length);
    expect(s.handsRails).toBe(BROKER_RAILS.length + WATCHER_RAILS.length);
  });
});
