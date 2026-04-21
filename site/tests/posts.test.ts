import { describe, it, expect } from "vitest";
import { listPostDates, loadPostsLatest, flattenChronological } from "@/lib/posts";

describe("posts loader", () => {
  it("listPostDates returns dates ascending", () => {
    const dates = listPostDates();
    expect(dates.length).toBeGreaterThanOrEqual(3);
  });

  it("loadPostsLatest returns a dict keyed by agent_id, each value an array", () => {
    const byAgent = loadPostsLatest();
    const ids = Object.keys(byAgent);
    expect(ids.length).toBeGreaterThan(0);
    for (const [id, arr] of Object.entries(byAgent)) {
      expect(typeof id).toBe("string");
      expect(Array.isArray(arr)).toBe(true);
      for (const p of arr) {
        expect(typeof p.text).toBe("string");
        expect(typeof p.post_at).toBe("string");
        expect(p.post_at).toMatch(/^\d{2}:\d{2}$/);
      }
    }
  });

  it("flattenChronological returns posts sorted by post_at", () => {
    const byAgent = loadPostsLatest();
    const flat = flattenChronological(byAgent);
    expect(flat.length).toBeGreaterThan(0);
    for (let i = 1; i < flat.length; i++) {
      expect(flat[i].post_at >= flat[i - 1].post_at).toBe(true);
    }
  });
});
