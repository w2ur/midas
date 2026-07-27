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
 * also reads, via this module — see below).
 *
 * These two sources used to disagree by 40 fills (165 vs 125). That was a
 * real bug, not a modelling choice: `loadOrdersForDate()` joined outbox and
 * inbox rows scoped to the *same* date file, but a conditional (trigger)
 * order is authored on one date and can fire — and get its inbox
 * confirmation written — on a *later* date. 41 roster fills fell into that
 * gap and silently read back as "pending" forever. Fixed in `orders.ts`
 * (global order_id → inbox-row index instead of a per-date join; see its
 * module comments and `tests/orders.test.ts`). The two sources now agree to
 * within one row (166 orders-join vs 165 here) — see the anomaly below.
 *
 * ── Known ledger anomaly: `ord_2026-05-21_sharp-shooter-eur_001` ──
 * This order's inbox row reads `status: "filled"` (trigger fired same day,
 * `fill_price: 1249.0`), but it never landed in
 * `data/portfolios/sharp-shooter-eur/trades.json`, and the portfolio's own
 * cash snapshots show no credit around 2026-05-21 (cash is flat at
 * 3217.040054321289 across every snapshot from 2026-05-20 to 2026-05-27) —
 * plus a later order (2026-06-24) still references holding and trimming the
 * same ASML.AS position as if this sale never happened. That's a genuine
 * ledger inconsistency (the broker wrote a fill confirmation for a trade
 * that was never applied to the portfolio), not a site display bug, and not
 * something this module papers over: trades.json is deliberately still the
 * fill source of truth here, so this one stray inbox row is simply not
 * counted. It lives in `data/orders/`, written by the broker in `engine/` —
 * out of this module's (and this codebase area's) scope to fix. Flagged for
 * investigation, not silently absorbed.
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
