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

  // These were exact literals (216 / 87 / 58 / a per-agent map) on the stated
  // rationale that drift "MUST fail so the homepage/methodology prose gets
  // regenerated rather than going stale". That rationale no longer holds:
  // every consumer — PreRegistrationStatus, MethodologyFacts, oss-stats and
  // the homepage — calls cadenceStats() at build time, so no figure is
  // transcribed anywhere and none can go stale. What the pins actually did
  // was go red on every session that filled a trade, days later, on an
  // unrelated PR: `tests.yml` has `paths-ignore: data/**`, and a session
  // commit's only footprint IS data/, so the suite that reads it never runs
  // on the commit that moved it. 2026-08-07 left main red exactly this way.
  //
  // Floors instead, in the shape the Python side already uses for the same
  // ledger (`test_rails_live_coverage.py`: `assert len(fills) > 100`). This
  // is not a weaker test of a different thing — it is the right test of the
  // real risk. The ledger only ever grows, so a *decrease* is the failure
  // mode that has actually occurred here: the 2026-05-18..23 `commit_and_push`
  // pathspec bug silently dropped fills for two months.
  it("never regresses below the fills already published", () => {
    expect(cadenceStats().totalFills).toBeGreaterThanOrEqual(226);
  });

  it("recomputes the session count directly from data/output/*.json", () => {
    expect(cadenceStats().sessions).toBe(listDates().length);
  });

  it("never regresses below the sessions already published", () => {
    expect(cadenceStats().sessions).toBeGreaterThanOrEqual(88);
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

  it("never regresses below the fill-days already published", () => {
    expect(cadenceStats().daysWithFill).toBeGreaterThanOrEqual(59);
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

  // Per-agent floors, not an exact map. Strictly more sensitive than the
  // roster-wide floor above: a session that adds fills to one book while
  // silently dropping them from another can leave the total flat, and only a
  // per-book comparison sees it. Ratchet these up when a floor becomes so
  // stale it stops meaning anything; never edit one downward without an
  // explanation of where the published fills went.
  it("never regresses below the per-agent fills already published", () => {
    const floors: Record<string, number> = {
      "steady-eddie-eur": 16,
      "steady-eddie-usd": 20,
      "sharp-shooter-eur": 27,
      "sharp-shooter-usd": 37,
      "yolo-sapiens-eur": 20,
      "yolo-sapiens-usd": 26,
      satoshi: 7,
      "monsieur-forex": 22,
      goldfinger: 18,
      world: 33,
    };
    const byId = Object.fromEntries(cadenceStats().perAgent.map((a) => [a.id, a.fills]));
    expect(Object.keys(byId).sort()).toEqual(Object.keys(floors).sort());
    for (const [id, floor] of Object.entries(floors)) {
      expect(byId[id], `${id} lost published fills`).toBeGreaterThanOrEqual(floor);
    }
  });

  it("marks satoshi as the minimum-fill agent — the binding constraint on the pre-registered bar", () => {
    const s = cadenceStats();
    expect(s.minAgent.id).toBe("satoshi");
    // Derived, not transcribed. `toBe(7)` was correct until 2026-08-21T10:02Z,
    // when two pending satoshi triggers fired and took the count to 9 — and
    // because tests.yml carries `paths-ignore: data/**`, the commits that broke
    // it ran no CI at all. The invariant this test is about is that minAgent
    // really is the argmin, which no session can invalidate.
    expect(s.minAgent.fills).toBe(Math.min(...s.perAgent.map((a) => a.fills)));
  });

  it("computes dormancy as calendar days between an agent's last fill and the asOf date", () => {
    const s = cadenceStats();
    const satoshi = s.perAgent.find((a) => a.id === "satoshi")!;

    // asOf is the newest session on record, not a transcribed date — pinning
    // it to a literal made this test fail on the next session that ran, for
    // no defect. The arithmetic is what this test is about, so assert that.
    expect(s.asOf).toBe(listDates()[listDates().length - 1]);
    // lastFillDate is read from the ledger for the same reason asOf is: it was
    // pinned to "2026-06-04" and satoshi filled again on 2026-08-19, so the
    // literal failed on a correct session. The arithmetic is the subject here.
    expect(satoshi.lastFillDate).toBeTruthy();

    const expectedDays = Math.round(
      (Date.parse(`${s.asOf}T00:00:00Z`) - Date.parse(`${satoshi.lastFillDate}T00:00:00Z`)) /
        86_400_000,
    );
    expect(satoshi.daysDormant).toBe(expectedDays);
    // Non-negative, and never ahead of asOf. The previous directional check
    // asserted the gap "only widens" because satoshi had not filled since
    // June; that premise died the moment it filled again. Dormancy is not
    // monotonic — a fill resets it, which is the system working.
    expect(satoshi.daysDormant!).toBeGreaterThanOrEqual(0);
    expect(satoshi.lastFillDate! <= s.asOf).toBe(true);
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
