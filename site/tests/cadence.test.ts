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
  it("pins the current roster fill count (165) so drift breaks the build", () => {
    expect(cadenceStats().totalFills).toBe(165);
  });

  it("recomputes the session count directly from data/output/*.json", () => {
    expect(cadenceStats().sessions).toBe(listDates().length);
  });

  it("pins the current session count (80)", () => {
    expect(cadenceStats().sessions).toBe(80);
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

  it("pins the current distinct-days-with-a-fill count (50)", () => {
    expect(cadenceStats().daysWithFill).toBe(50);
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
      "steady-eddie-eur": 12,
      "steady-eddie-usd": 15,
      "sharp-shooter-eur": 20,
      "sharp-shooter-usd": 24,
      "yolo-sapiens-eur": 17,
      "yolo-sapiens-usd": 19,
      satoshi: 7,
      "monsieur-forex": 16,
      goldfinger: 17,
      world: 18,
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
    // asOf is pinned indirectly via the session-count test above (2026-07-24).
    expect(s.asOf).toBe("2026-07-24");
    expect(satoshi.daysDormant).toBe(50);
  });

  it("agrees with the orders outbox/inbox join to within the one known ledger anomaly", () => {
    // See cadence.ts's module doc: the orders join (loadAllOrders(), now
    // fixed to resolve conditional-order fills across dates) reports 166
    // roster fills; trades.json reports 165. The +1 is a single stray inbox
    // row (ord_2026-05-21_sharp-shooter-eur_001) that was never applied to
    // the portfolio — a genuine ledger anomaly, not a counting bug. If this
    // gap ever changes, that's new information worth re-investigating, not
    // silently absorbing.
    const roster = new Set(TRADING_AGENTS.map((a) => a.id));
    const joinFilled = loadAllOrders().filter(
      (o) => o.status === "filled" && roster.has(o.agent_id as (typeof TRADING_AGENTS)[number]["id"]),
    ).length;
    expect(joinFilled - cadenceStats().totalFills).toBe(1);
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
