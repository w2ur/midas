import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { cadenceStats, preRegistrationStatus } from "../src/lib/cadence";
import { TRADING_AGENTS } from "../src/lib/roster";
import { listDates } from "../src/lib/output";
import { DATA_DIR } from "../src/lib/paths";
import { loadAllOrders } from "../src/lib/orders";

const ORDER_ID_DATE_RE = /^ord_(\d{4}-\d{2}-\d{2})_/;

function tradesFor(agentId: string): { id: string }[] {
  const file = path.join(DATA_DIR, "portfolios", agentId, "trades.json");
  if (!fs.existsSync(file)) return [];
  return JSON.parse(fs.readFileSync(file, "utf-8"));
}

describe("cadenceStats", () => {
  it("recomputes the total roster fill count directly from trades.json", () => {
    const expected = TRADING_AGENTS.reduce((sum, a) => sum + tradesFor(a.id).length, 0);
    expect(cadenceStats().totalFills).toBe(expected);
  });

  // Pinned literal — this is deliberate (see cadence.ts module doc): if a
  // data refresh changes the roster fill count, this test MUST fail so the
  // homepage/methodology prose gets regenerated rather than going stale.
  it("pins the current roster fill count (216) so drift breaks the build", () => {
    expect(cadenceStats().totalFills).toBe(216);
  });

  it("recomputes the session count directly from data/output/*.json", () => {
    expect(cadenceStats().sessions).toBe(listDates().length);
  });

  it("pins the current session count (87)", () => {
    expect(cadenceStats().sessions).toBe(87);
  });

  it("recomputes the distinct-days-with-a-fill count from trades.json dates", () => {
    const days = new Set<string>();
    for (const agent of TRADING_AGENTS) {
      for (const t of tradesFor(agent.id)) {
        const m = t.id.match(ORDER_ID_DATE_RE);
        if (m) days.add(m[1]);
      }
    }
    expect(cadenceStats().daysWithFill).toBe(days.size);
  });

  it("pins the current distinct-days-with-a-fill count (58)", () => {
    expect(cadenceStats().daysWithFill).toBe(58);
  });

  it("reports fewer distinct fill-days than sessions — the desk is selective, not idle", () => {
    const s = cadenceStats();
    expect(s.daysWithFill).toBeGreaterThan(0);
    expect(s.daysWithFill).toBeLessThan(s.sessions);
  });

  it("carries a per-agent entry for every roster agent, summing to the total", () => {
    const s = cadenceStats();
    expect(s.perAgent).toHaveLength(TRADING_AGENTS.length);
    expect(s.perAgent.reduce((sum, a) => sum + a.fills, 0)).toBe(s.totalFills);
  });

  // Pinned per-agent literals — the exact fill counts from the brief, recomputed
  // AND pinned so a data refresh that changes any one of them breaks the build.
  it("pins the current per-agent fill counts", () => {
    const byId = Object.fromEntries(cadenceStats().perAgent.map((a) => [a.id, a.fills]));
    expect(byId).toEqual({
      "steady-eddie-eur": 16,
      "steady-eddie-usd": 20,
      "sharp-shooter-eur": 25,
      "sharp-shooter-usd": 34,
      "yolo-sapiens-eur": 19,
      "yolo-sapiens-usd": 24,
      satoshi: 7,
      "monsieur-forex": 22,
      goldfinger: 18,
      world: 31,
    });
  });

  it("marks satoshi as the minimum-fill agent — the binding constraint on the pre-registered bar", () => {
    expect(cadenceStats().minAgent.id).toBe("satoshi");
    expect(cadenceStats().minAgent.fills).toBe(7);
  });

  it("computes dormancy as calendar days between an agent's last fill and the asOf date", () => {
    const s = cadenceStats();
    const satoshi = s.perAgent.find((a) => a.id === "satoshi")!;
    expect(satoshi.lastFillDate).toBe("2026-06-04");
    // asOf is pinned indirectly via the session-count test above (2026-08-05).
    expect(s.asOf).toBe("2026-08-05");
    expect(satoshi.daysDormant).toBe(62);
  });

  it("agrees exactly with the orders outbox/inbox join — the ledger anomaly is closed", () => {
    // This assertion used to allow a +1 gap: the orders join reported one
    // more roster fill than trades.json, because the confirmed inbox row
    // ord_2026-05-21_sharp-shooter-eur_001 had never been applied to the
    // portfolio. The 2026-08-02 reconciliation closed that gap from both
    // ends — it inserted the lost sale into trades.json and voided the
    // 2026-06-24 inbox row the corrected ledger could not support — so the
    // two sources now agree exactly, and the allowance is gone rather than
    // re-pinned to a new constant.
    //
    // Keep this at 0. A non-zero gap means a fill exists on one side of the
    // Brain/Hands boundary and not the other, which is the exact failure
    // class the 2026-05-21 incident belonged to — new information worth
    // investigating, never silently absorbing.
    const roster = new Set(TRADING_AGENTS.map((a) => a.id));
    const joinFilled = loadAllOrders().filter(
      (o) => o.status === "filled" && roster.has(o.agent_id as (typeof TRADING_AGENTS)[number]["id"]),
    ).length;
    expect(joinFilled - cadenceStats().totalFills).toBe(0);
  });
});

describe("preRegistrationStatus", () => {
  it("uses the pre-registered bar from METHODOLOGY.md: 6 months, 100 fills/agent", () => {
    const status = preRegistrationStatus();
    expect(status.monthsRequired).toBe(6);
    expect(status.fillsPerAgentRequired).toBe(100);
  });

  it("ties the binding constraint to the minimum-fill agent, not the aggregate", () => {
    const status = preRegistrationStatus();
    expect(status.minAgent.id).toBe(cadenceStats().minAgent.id);
    expect(status.minAgent.fills).toBe(cadenceStats().minAgent.fills);
  });

  it("reports not on track at the current per-agent fill rate", () => {
    // 7 fills over ~98 days for the minimum agent projects to years, not
    // months, before the ≥100-fills-per-agent bar is cleared.
    const status = preRegistrationStatus();
    expect(status.onTrack).toBe(false);
    expect(status.projectedMonthsToReachBar).not.toBeNull();
    expect(status.projectedMonthsToReachBar as number).toBeGreaterThan(status.monthsRequired);
  });
});
