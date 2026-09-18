import type { APIRoute } from "astro";
import { loadCurrentLeaderboard } from "@/lib/leaderboard";

export const prerender = true;

// Companion to /latest.json and /oracle-latest.json: the ranked board of the
// most recent session, consumed by william.revah.paris at build time for its
// « Chez Midas, aujourd'hui » column. Empty rows on failure, on purpose — the
// consumer hides the column rather than failing its build.
export const GET: APIRoute = () => {
  try {
    const board = loadCurrentLeaderboard();
    return json({
      generated_at: new Date().toISOString(),
      updated_at: board?.updated_at ?? null,
      rows: board?.rows ?? [],
    });
  } catch {
    return json({ generated_at: null, updated_at: null, rows: [] });
  }
};

function json(payload: unknown): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
