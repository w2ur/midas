import type { APIRoute } from "astro";
import { loadAllOrders } from "@/lib/orders";
import { latestFill, toHubFill } from "@/lib/latest-fill";
import { getAgent, isTradingAgent } from "@/lib/roster";

export const prerender = true;

// The front page's exhibit, for william.revah.paris: the newest broker-confirmed
// fill by a trader, read by the hub at ITS build time for the Midas story page.
// Like /latest.json and /oracle-latest.json, a loader failure answers with an
// empty payload rather than a 500, so a bad ledger read hides the hub's card
// instead of breaking a page in another repo.
export const GET: APIRoute = () => {
  try {
    const o = latestFill(loadAllOrders(), isTradingAgent);
    const fill = o && isTradingAgent(o.agent_id) ? toHubFill(o, getAgent(o.agent_id).display_name) : null;
    return json({ generated_at: new Date().toISOString(), fill });
  } catch {
    return json({ generated_at: null, fill: null });
  }
};

function json(payload: unknown): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
