import { describe, it, expect } from "vitest";
import { orderRows, parseValue, type SortRec } from "../src/lib/board-sort";

const RECS: SortRec[] = [
  { agent: "satoshi", return_pct: 8.1, vsBench: 1.2, vsCoin: 3.0 },
  { agent: "goldfinger", return_pct: 2.4, vsBench: 4.9, vsCoin: -1.0 },
  { agent: "forex", return_pct: 1.1, vsBench: -0.3, vsCoin: 0.5 },
  { agent: "world", return_pct: -0.9, vsBench: null, vsCoin: null },
];

describe("orderRows", () => {
  it("sorts by return descending by default usage", () => {
    expect(orderRows(RECS, "return", "desc")).toEqual([
      "satoshi", "goldfinger", "forex", "world",
    ]);
  });

  it("sorts by return ascending", () => {
    expect(orderRows(RECS, "return", "asc")).toEqual([
      "world", "forex", "goldfinger", "satoshi",
    ]);
  });

  it("sorts by alpha vs benchmark, which reorders the board", () => {
    expect(orderRows(RECS, "vsBench", "desc")).toEqual([
      "goldfinger", "satoshi", "forex", "world",
    ]);
  });

  it("sorts by vs coin flip", () => {
    expect(orderRows(RECS, "vsCoin", "desc")).toEqual([
      "satoshi", "forex", "goldfinger", "world",
    ]);
  });

  it("puts nulls last when descending", () => {
    expect(orderRows(RECS, "vsBench", "desc").at(-1)).toBe("world");
  });

  it("puts nulls last when ascending too — a missing baseline is not a worst score", () => {
    expect(orderRows(RECS, "vsBench", "asc").at(-1)).toBe("world");
  });

  it("orders multiple nulls among themselves by agent id", () => {
    const withNulls: SortRec[] = [
      { agent: "zulu", return_pct: 0, vsBench: null, vsCoin: null },
      { agent: "alpha", return_pct: 0, vsBench: null, vsCoin: null },
      { agent: "mike", return_pct: 0, vsBench: 1, vsCoin: 1 },
    ];
    expect(orderRows(withNulls, "vsBench", "desc")).toEqual(["mike", "alpha", "zulu"]);
  });

  it("breaks ties deterministically by agent id", () => {
    const tied: SortRec[] = [
      { agent: "charlie", return_pct: 5, vsBench: 1, vsCoin: 1 },
      { agent: "alpha", return_pct: 5, vsBench: 1, vsCoin: 1 },
      { agent: "bravo", return_pct: 5, vsBench: 1, vsCoin: 1 },
    ];
    expect(orderRows(tied, "return", "desc")).toEqual(["alpha", "bravo", "charlie"]);
    expect(orderRows(tied, "return", "asc")).toEqual(["alpha", "bravo", "charlie"]);
  });

  it("does not mutate its input", () => {
    const copy = RECS.map((r) => ({ ...r }));
    orderRows(RECS, "vsBench", "asc");
    expect(RECS).toEqual(copy);
  });

  it("handles an empty board", () => {
    expect(orderRows([], "return", "desc")).toEqual([]);
  });
});

describe("parseValue", () => {
  it("reads a numeric data attribute", () => {
    expect(parseValue("4.90")).toBe(4.9);
    expect(parseValue("-0.30")).toBe(-0.3);
    expect(parseValue("0")).toBe(0);
  });

  it("treats empty, missing and non-numeric attributes as null", () => {
    expect(parseValue("")).toBeNull();
    expect(parseValue("   ")).toBeNull();
    expect(parseValue(null)).toBeNull();
    expect(parseValue(undefined)).toBeNull();
    expect(parseValue("—")).toBeNull();
    expect(parseValue("NaN")).toBeNull();
    expect(parseValue("Infinity")).toBeNull();
  });
});
