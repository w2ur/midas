import { describe, it, expect } from "vitest";
import { CREST_PATHS, isCrestId, type CrestId } from "@/lib/crests";
import { TRADING_AGENTS, ORACLE_ID } from "@/lib/roster";

describe("crests", () => {
  it("every trading agent + the Oracle has a non-empty crest path", () => {
    const ids: CrestId[] = [...TRADING_AGENTS.map((a) => a.id), ORACLE_ID];
    for (const id of ids) {
      expect(CREST_PATHS[id], `missing crest: ${id}`).toBeTruthy();
      expect(CREST_PATHS[id].length).toBeGreaterThan(10);
    }
  });
  it("has exactly 11 crests, no extras", () => {
    expect(Object.keys(CREST_PATHS)).toHaveLength(11);
  });
  it("isCrestId accepts agents + Oracle, rejects others", () => {
    expect(isCrestId("satoshi")).toBe(true);
    expect(isCrestId("the-oracle")).toBe(true);
    expect(isCrestId("nope")).toBe(false);
  });
  it("twins share a glyph (differ only by colour)", () => {
    expect(CREST_PATHS["steady-eddie-eur"]).toBe(CREST_PATHS["steady-eddie-usd"]);
    expect(CREST_PATHS["sharp-shooter-eur"]).toBe(CREST_PATHS["sharp-shooter-usd"]);
    expect(CREST_PATHS["yolo-sapiens-eur"]).toBe(CREST_PATHS["yolo-sapiens-usd"]);
  });
});
