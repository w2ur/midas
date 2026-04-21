import { describe, it, expect } from "vitest";
import { loadMemory, renderMemoryHtml, hasMemory } from "@/lib/memory";
import { TRADING_AGENTS } from "@/lib/roster";

describe("memory loader", () => {
  it("every trading agent has a memory file", () => {
    for (const a of TRADING_AGENTS) {
      expect(hasMemory(a.id), `missing memory for ${a.id}`).toBe(true);
    }
  });

  it("loadMemory returns raw markdown string", () => {
    const md = loadMemory("satoshi");
    expect(typeof md).toBe("string");
    expect(md.length).toBeGreaterThan(50);
  });

  it("renderMemoryHtml returns HTML", () => {
    const md = loadMemory("satoshi");
    const html = renderMemoryHtml(md);
    expect(html).toMatch(/<(h[1-6]|p|ul|ol)/);
  });

  it("loadMemory throws on unknown agent id", () => {
    expect(() => loadMemory("not-an-agent" as never)).toThrow();
  });
});
