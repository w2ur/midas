/**
 * Build-time figures for the /open-source page.
 *
 * Everything here is derived from committed artifacts at build time — the page
 * is static, but the numbers on it move with the desk.
 */

import { loadAllOrders } from "./orders";
import { currentDayNumber } from "./session";
import { BROKER_RAILS, WATCHER_RAILS } from "./rails";

export interface OssStats {
  /** Narrative session count — the Oracle's "Day N". */
  sessions: number;
  /** Orders that actually reached a fill, across every session. */
  fills: number;
  /** Reason codes the broker can emit. */
  brokerRails: number;
  /** Broker codes plus the watcher's, across the whole Hands. */
  handsRails: number;
}

export function ossStats(): OssStats {
  const fills = loadAllOrders().filter((o) => o.status === "filled").length;
  return {
    sessions: currentDayNumber(),
    fills,
    brokerRails: BROKER_RAILS.length,
    handsRails: BROKER_RAILS.length + WATCHER_RAILS.length,
  };
}
