import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/json-response";
import { loadCurrentLeaderboard } from "@/lib/leaderboard";

export const prerender = true;

// Companion to /latest.json and /oracle-latest.json: the ranked board of the
// most recent session, consumed by william.revah.paris at build time for its
// « Chez Midas, aujourd'hui » column. Empty rows on failure, on purpose — the
// consumer hides the column rather than failing its build.
export const GET: APIRoute = () => {
  try {
    const board = loadCurrentLeaderboard();
    return jsonResponse({
      generated_at: new Date().toISOString(),
      updated_at: board?.updated_at ?? null,
      rows: board?.rows ?? [],
    });
  } catch {
    return jsonResponse({ generated_at: null, updated_at: null, rows: [] });
  }
};
