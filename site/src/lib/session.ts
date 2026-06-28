import { listBlogDates, loadBlogLatest, loadBlogByDate } from "./blog";

/**
 * The experiment's inception — Day 1, first session at 2026-04-17 20:00 UTC.
 * (Relocated here from the now-deleted SimulateForm, which used it as a
 * mirror-portfolio anchor.)
 */
export const DAY_ONE = "2026-04-17";

/** Parse the Oracle's narrative day number ("Day 61: …") from a column title. */
export function parseDayNumber(title: string): number | null {
  const m = title.match(/\bDay\s+(\d+)\b/i);
  return m ? Number(m[1]) : null;
}

/**
 * The current narrative day number — the Oracle's "Day N", read from the
 * latest column title so the status rail can never contradict the story.
 * Falls back to the session (blog) count, which equals the day number by
 * construction (Day 1 == the first column).
 */
export function currentDayNumber(): number {
  try {
    return parseDayNumber(loadBlogLatest().title) ?? listBlogDates().length;
  } catch {
    return listBlogDates().length;
  }
}

/** Day number for a specific session date, or null if no column exists. */
export function dayNumberFor(date: string): number | null {
  try {
    return parseDayNumber(loadBlogByDate(date).title) ?? null;
  } catch {
    return null;
  }
}
