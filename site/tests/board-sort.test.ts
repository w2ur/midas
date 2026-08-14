import { describe, it, expect } from "vitest";
import { boardLayout, orderRows, parseValue, type SortRec } from "../src/lib/board-sort";

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

  it("orders multiple nulls among themselves by raw return desc — the engine's null-tail rule", () => {
    // Regression: review 2026-08-14. The null tail used to order
    // alphabetically while engine rank_leaderboard_rows orders it by raw
    // EUR return, so a client re-sort could renumber engine-ranked rows.
    const withNulls: SortRec[] = [
      { agent: "aardvark", return_pct: -20, vsBench: null, vsCoin: null },
      { agent: "zulu", return_pct: 10, vsBench: null, vsCoin: null },
      { agent: "mike", return_pct: 0, vsBench: 1, vsCoin: 1 },
    ];
    expect(orderRows(withNulls, "vsBench", "desc")).toEqual(["mike", "zulu", "aardvark"]);
    // Equal raw returns fall back to agent id, deterministically.
    const tiedNulls: SortRec[] = [
      { agent: "zulu", return_pct: 0, vsBench: null, vsCoin: null },
      { agent: "alpha", return_pct: 0, vsBench: null, vsCoin: null },
    ];
    expect(orderRows(tiedNulls, "vsBench", "desc")).toEqual(["alpha", "zulu"]);
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

describe("boardLayout", () => {
  const LIVE = [
    { vs_benchmark_pp: 6.62 },
    { vs_benchmark_pp: 5.55, fx_translation_pp: 2.19 },
    { vs_benchmark_pp: -13.07 },
  ];
  // A bundle published before 2026-08-14: rank came from raw EUR return and
  // no row carries an excess figure.
  const LEGACY = [{}, {}, {}];

  it("leads on excess when the rows carry one", () => {
    expect(boardLayout(LIVE, true).leadIsExcess).toBe(true);
  });

  it("keeps a pre-2026-08-14 bundle on its published return-led rendering", () => {
    // The archive passes showVsBench=false AND its rows lack the field, so
    // the two halves of the guard mask each other on every real archive page.
    // Asserted separately here so neither can rot unnoticed.
    expect(boardLayout(LEGACY, true).leadIsExcess).toBe(false);
    expect(boardLayout(LIVE, false).leadIsExcess).toBe(false);
  });

  it("treats an explicit null the same as an absent field", () => {
    expect(boardLayout([{ vs_benchmark_pp: null }], true).leadIsExcess).toBe(false);
  });

  it("leads on excess when only some agents have a baseline", () => {
    // A desk where one book has no benchmark series: that row ranks null-last
    // and renders "—". Demoting the whole board for it would lose the metric
    // for the nine agents that do have one.
    expect(boardLayout([{ vs_benchmark_pp: 6.62 }, { vs_benchmark_pp: null }], true).leadIsExcess).toBe(true);
  });

  it("shows the decomposition pair only when a book sits outside EUR", () => {
    expect(boardLayout(LIVE, true).showDecomp).toBe(true);
    // All-EUR desk: return_local_pct would equal return_pct on every row and
    // every FX cell would read "—".
    expect(boardLayout([{ vs_benchmark_pp: 6.62 }, { vs_benchmark_pp: 1.0 }], true).showDecomp).toBe(false);
  });

  it("never shows the decomposition pair on a return-led board", () => {
    // Guards the column-count invariant: the legacy grid has no slot for them.
    expect(boardLayout([{ fx_translation_pp: 2.19 }], true).showDecomp).toBe(false);
    expect(boardLayout(LIVE, false).showDecomp).toBe(false);
  });

  it("handles an empty board", () => {
    expect(boardLayout([], true)).toEqual({ leadIsExcess: false, showDecomp: false });
  });
});
