import type { APIRoute } from "astro";
import { flattenChronological, latestPostsDate, loadPostsLatest } from "@/lib/posts";
import { toHubItems } from "@/lib/hub-feed";

export const prerender = true;

// Companion to /oracle-latest.json: the agent posts of the most recent session,
// consumed by william.revah.paris at build time for its "live today" strip.
// The empty-items payload on failure is deliberate — it is exactly the fallback
// the consumer already writes for itself, so a bad session hides the strip
// rather than breaking the page that embeds it.
export const GET: APIRoute = () => {
  try {
    const date = latestPostsDate();
    const payload = {
      generated_at: new Date().toISOString(),
      date,
      items: toHubItems(flattenChronological(loadPostsLatest())),
    };
    return json(payload);
  } catch {
    return json({ generated_at: null, date: null, items: [] });
  }
};

function json(payload: unknown): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
