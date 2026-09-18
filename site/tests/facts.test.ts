import { describe, it, expect } from "vitest";
import { experimentFacts, pickFacts } from "../src/lib/facts";
import { cadenceStats, preRegistrationStatus } from "../src/lib/cadence";
import { TRADING_AGENTS } from "../src/lib/roster";

// lib/facts.ts is the ONE source both /methodology (all six) and the homepage
// (four) read. These tests pin its derivation to cadence.ts so a fact strip can
// never carry a fill or session count the data does not support — the same
// property tests/oss-stats.test.ts holds for the open-source page.
describe("experimentFacts", () => {
  it("derives sessions and fills from cadenceStats, not from literals", () => {
    const cadence = cadenceStats();
    const byKey = Object.fromEntries(experimentFacts().map((f) => [f.key, f]));
    expect(byKey.sessions.value).toBe(String(cadence.sessions));
    expect(byKey.fills.value).toBe(String(cadence.totalFills));
    expect(byKey.fills.note).toContain(String(cadence.daysWithFill));
  });

  it("states the roster size and the pre-registered bar from their sources", () => {
    const prereg = preRegistrationStatus();
    const byKey = Object.fromEntries(experimentFacts().map((f) => [f.key, f]));
    expect(byKey.desk.value).toBe(`${TRADING_AGENTS.length} agents`);
    expect(byKey.skill.value).toBe("Not yet");
    expect(byKey.skill.note).toContain(`${prereg.monthsRequired} months`);
    expect(byKey.skill.note).toContain(`${prereg.fillsPerAgentRequired} fills per agent`);
    expect(byKey.money.value).toBe("None");
  });

  it("returns six distinct keys", () => {
    const keys = experimentFacts().map((f) => f.key);
    expect(new Set(keys).size).toBe(6);
    expect(keys).toEqual(["money", "desk", "sessions", "fills", "controls", "skill"]);
  });
});

describe("pickFacts", () => {
  it("returns the requested subset in the requested order", () => {
    const picked = pickFacts(["skill", "money"]);
    expect(picked.map((f) => f.key)).toEqual(["skill", "money"]);
  });

  it("is the same objects the full list carries — one source, two consumers", () => {
    const full = experimentFacts();
    const picked = pickFacts(["money", "sessions", "fills", "skill"]);
    for (const p of picked) {
      const twin = full.find((f) => f.key === p.key);
      expect(twin).toEqual(p);
    }
  });
});
