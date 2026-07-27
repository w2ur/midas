/**
 * Build-time cadence figures — how often the desk actually trades, in
 * aggregate and per agent. Every number here is derived from committed
 * artifacts at build time so homepage/methodology prose can never assert a
 * cadence, fill count, or pre-registration status the data doesn't support.
 *
 * ── Fill source: trades.json, not the orders outbox/inbox join ──
 * Fills are counted from `data/portfolios/<id>/trades.json` — the ledger
 * written by the same code path that actually mutates a portfolio on a
 * fill — rather than from `loadAllOrders().filter(o => o.status ===
 * "filled")` (the join over `data/orders/{outbox,inbox}` that `oss-stats.ts`
 * and the rest of the site use). **The two sources disagree, and the
 * disagreement is real, not a modelling choice:** at time of writing,
 * trades.json reports 165 roster fills; the orders join reports only 125.
 * The gap traces to incomplete inbox data, not to trades that didn't
 * happen — 10 session dates are missing their `data/orders/inbox/*.jsonl`
 * file entirely (e.g. 2026-05-20, 7 orders authored, no inbox file at
 * all), and roughly 30 further fills are missing their individual
 * confirmation row from inbox files that do otherwise exist, even though
 * the corresponding portfolio was mutated. trades.json has no such gap —
 * it is the ground truth for "did this order fill." (There is one fill in
 * the opposite direction — `ord_2026-05-21_sharp-shooter-eur_001` reads
 * "filled" in the orders join but never landed in trades.json — a single
 * anomaly, not a pattern.) This is a pre-existing gap in the inbox ledger
 * outside this module's scope to fix (it lives in `data/orders/`, written
 * by the broker in `engine/`); it is flagged here, in the stale-data test,
 * and in the implementer's handoff report so it isn't silently absorbed.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { DATA_DIR } from "./paths";
import { TRADING_AGENTS, type AgentId } from "./roster";
import { listDates } from "./output";
import { DAY_ONE } from "./session";

const PORTFOLIOS_DIR = path.join(DATA_DIR, "portfolios");
const ORDER_ID_DATE_RE = /^ord_(\d{4}-\d{2}-\d{2})_/;

/** Average days in a calendar month — good enough for a "months elapsed" estimate. */
const DAYS_PER_MONTH = 30.44;

type Trade = { id: string };

function loadTradeDates(agentId: AgentId): string[] {
  const file = path.join(PORTFOLIOS_DIR, agentId, "trades.json");
  if (!fs.existsSync(file)) return [];
  const trades = JSON.parse(fs.readFileSync(file, "utf-8")) as Trade[];
  return trades
    .map((t) => {
      const m = t.id.match(ORDER_ID_DATE_RE);
      if (!m) throw new Error(`Trade id has no parseable date: ${t.id}`);
      return m[1];
    })
    .sort();
}

function daysBetween(fromIso: string, toIso: string): number {
  const [fy, fm, fd] = fromIso.split("-").map(Number);
  const [ty, tm, td] = toIso.split("-").map(Number);
  const msPerDay = 24 * 60 * 60 * 1000;
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / msPerDay);
}

export interface AgentCadence {
  id: AgentId;
  fills: number;
  /** Most recent date this agent's fill landed, or null if it has never filled. */
  lastFillDate: string | null;
  /** Calendar days between lastFillDate and the `asOf` reference date. */
  daysDormant: number | null;
}

export interface CadenceStats {
  /** Roster fills, summed from data/portfolios/<id>/trades.json — see module doc. */
  totalFills: number;
  /** Session count — one entry per data/output/*.json file (the Oracle's day count). */
  sessions: number;
  /** Distinct calendar days on which at least one roster agent filled. */
  daysWithFill: number;
  /** The latest session date. Used as "today" for dormancy math so the
   *  figures are build-reproducible instead of drifting with wall-clock time. */
  asOf: string;
  perAgent: AgentCadence[];
  /** The agent with the fewest fills — binding constraint on the
   *  pre-registered ≥100-fills bar, since that bar is per-agent, not aggregate. */
  minAgent: AgentCadence;
}

export function cadenceStats(): CadenceStats {
  const dates = listDates();
  if (dates.length === 0) throw new Error("No session dates in data/output");
  const asOf = dates[dates.length - 1];

  const daysWithFillSet = new Set<string>();
  let totalFills = 0;
  const perAgent: AgentCadence[] = TRADING_AGENTS.map((agent) => {
    const fillDates = loadTradeDates(agent.id);
    totalFills += fillDates.length;
    for (const d of fillDates) daysWithFillSet.add(d);
    const lastFillDate = fillDates.length > 0 ? fillDates[fillDates.length - 1] : null;
    return {
      id: agent.id,
      fills: fillDates.length,
      lastFillDate,
      daysDormant: lastFillDate ? daysBetween(lastFillDate, asOf) : null,
    };
  });

  const minAgent = perAgent.reduce((min, a) => (a.fills < min.fills ? a : min));

  return {
    totalFills,
    sessions: dates.length,
    daysWithFill: daysWithFillSet.size,
    asOf,
    perAgent,
    minAgent,
  };
}

export interface PreRegistrationStatus {
  /** METHODOLOGY.md's pre-registered bar: no skill claim before this many
   *  months AND this many fills accumulated by *every* agent. */
  monthsRequired: number;
  fillsPerAgentRequired: number;
  daysSinceInception: number;
  monthsSinceInception: number;
  /** The binding constraint: the agent furthest from the per-agent fills bar. */
  minAgent: AgentCadence;
  /** minAgent's fills-per-day since DAY_ONE, projected out to the fills bar. Null if the agent has never filled. */
  projectedDaysToReachBar: number | null;
  projectedMonthsToReachBar: number | null;
  /** True only if the fills bar is projected to be reached inside monthsRequired. */
  onTrack: boolean;
}

export function preRegistrationStatus(): PreRegistrationStatus {
  const stats = cadenceStats();
  const daysSinceInception = daysBetween(DAY_ONE, stats.asOf);
  const monthsSinceInception = daysSinceInception / DAYS_PER_MONTH;
  const monthsRequired = 6;
  const fillsPerAgentRequired = 100;
  const minAgent = stats.minAgent;

  const rate = daysSinceInception > 0 ? minAgent.fills / daysSinceInception : 0;
  const projectedDaysToReachBar = rate > 0 ? fillsPerAgentRequired / rate : null;
  const projectedMonthsToReachBar =
    projectedDaysToReachBar !== null ? projectedDaysToReachBar / DAYS_PER_MONTH : null;
  const onTrack = projectedMonthsToReachBar !== null && projectedMonthsToReachBar <= monthsRequired;

  return {
    monthsRequired,
    fillsPerAgentRequired,
    daysSinceInception,
    monthsSinceInception,
    minAgent,
    projectedDaysToReachBar,
    projectedMonthsToReachBar,
    onTrack,
  };
}
