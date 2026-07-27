/**
 * Build-time figures for the /open-source page.
 *
 * Everything here is derived from committed artifacts at build time — the page
 * is static, but the numbers on it move with the desk.
 */

import { currentDayNumber } from "./session";
import { BROKER_RAILS, WATCHER_RAILS } from "./rails";
import { cadenceStats } from "./cadence";

export interface OssStats {
  /** Narrative session count — the Oracle's "Day N". */
  sessions: number;
  /** Roster fills — same figure and source as cadenceStats().totalFills, so
   *  this page can never show a different fill count than the homepage or
   *  methodology page. (Previously this counted loadAllOrders().filter(o =>
   *  o.status === "filled").length directly; that read 166, one more than
   *  trades.json's 165, because of a single stray inbox row — see the
   *  "known ledger anomaly" note in cadence.ts.) */
  fills: number;
  /** Reason codes the broker can emit. */
  brokerRails: number;
  /** Broker codes plus the watcher's, across the whole Hands. */
  handsRails: number;
}

export function ossStats(): OssStats {
  return {
    sessions: currentDayNumber(),
    fills: cadenceStats().totalFills,
    brokerRails: BROKER_RAILS.length,
    handsRails: BROKER_RAILS.length + WATCHER_RAILS.length,
  };
}
