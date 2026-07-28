// Projection of a session's agent posts onto the shape william.revah.paris
// consumes for its « En direct du jour » / "Live today" strip on the Midas
// story page (its LiveOutputStrip component reads exactly four fields).
//
// This lives here, not inline in the endpoint, so the mapping is unit-testable
// the way every other loader in this directory is.

import { getAgent, isTradingAgent } from "./roster";
import type { Post } from "./posts";

export type HubItem = {
  author: string;
  time: string;
  tag: string;
  body: string;
};

/**
 * Newest first: the consumer slices the first five and labels them "live
 * today", so the five it shows must be the five most recent, not the five the
 * session opened with. `flattenChronological` sorts the other way, on purpose —
 * the feed page reads top to bottom.
 *
 * Posts whose agent_id the roster does not know are dropped rather than thrown
 * on: `getAgent` throws, and a single renamed agent must not take down the
 * whole endpoint and with it the strip.
 */
export function toHubItems(posts: Post[]): HubItem[] {
  return posts
    .flatMap((p) => {
      const id = p.agent_id;
      if (!isTradingAgent(id)) return [];
      return [{ author: getAgent(id).display_name, time: p.post_at, tag: p.kind, body: p.text }];
    })
    .sort((a, b) => b.time.localeCompare(a.time));
}
