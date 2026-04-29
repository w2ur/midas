import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import { TRADING_AGENTS, ORACLE_ID, getAgent, getAgentMonogram, type AgentId } from "@/lib/roster";
import { AGENTS_DIR } from "@/lib/paths";

describe("roster", () => {
  it("has exactly 10 trading agents", () => {
    expect(TRADING_AGENTS).toHaveLength(10);
  });

  it("every trading agent has a persona file in .claude/agents", () => {
    for (const a of TRADING_AGENTS) {
      const file = `${AGENTS_DIR}/${a.id}.md`;
      expect(fs.existsSync(file), `missing persona: ${file}`).toBe(true);
    }
  });

  it("Oracle has a persona file and is not in the trading roster", () => {
    expect(fs.existsSync(`${AGENTS_DIR}/${ORACLE_ID}.md`)).toBe(true);
    expect(TRADING_AGENTS.map((a) => a.id)).not.toContain(ORACLE_ID);
  });

  it("every trading agent has display_name, archetype, base_currency, universe_summary", () => {
    for (const a of TRADING_AGENTS) {
      expect(a.display_name).toBeTruthy();
      expect(a.archetype).toBeTruthy();
      expect(["EUR", "USD", "mixed"]).toContain(a.base_currency);
      expect(a.universe_summary).toBeTruthy();
    }
  });

  it("getAgent returns the matching agent, throws on unknown id", () => {
    const satoshi = getAgent("satoshi");
    expect(satoshi.display_name).toBe("Satoshi");
    expect(() => getAgent("not-an-agent" as AgentId)).toThrow();
  });

  it("every trading agent has a signatureColor with light + dark hex", () => {
    for (const a of TRADING_AGENTS) {
      expect(a.signatureColor).toBeDefined();
      expect(a.signatureColor.light).toMatch(/^#[0-9a-f]{6}$/i);
      expect(a.signatureColor.dark).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("signature colors are unique across the roster (light)", () => {
    const lights = TRADING_AGENTS.map((a) => a.signatureColor.light);
    expect(new Set(lights).size).toBe(lights.length);
  });

  it("signature colors are unique across the roster (dark)", () => {
    const darks = TRADING_AGENTS.map((a) => a.signatureColor.dark);
    expect(new Set(darks).size).toBe(darks.length);
  });

  it("getAgentMonogram returns the first letter of display_name uppercased", () => {
    expect(getAgentMonogram("satoshi")).toBe("S");
    expect(getAgentMonogram("monsieur-forex")).toBe("M");
    expect(getAgentMonogram("yolo-sapiens-eur")).toBe("Y");
  });
});
