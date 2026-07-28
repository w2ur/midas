import { describe, it, expect } from "vitest";
import { toHubItems } from "@/lib/hub-feed";
import { flattenChronological, loadPostsLatest } from "@/lib/posts";
import type { Post } from "@/lib/posts";

function post(agent_id: string, post_at: string, kind: string, text: string): Post {
  return { agent_id, post_at, kind, text, mentions: [], parent_id: null, refs: {} };
}

describe("hub feed projection", () => {
  it("maps a post onto the four fields the hub strip renders", () => {
    const [item] = toHubItems([post("satoshi", "10:30", "trade", "Bought the dip.")]);
    expect(item).toEqual({
      author: "Satoshi",
      time: "10:30",
      tag: "trade",
      body: "Bought the dip.",
    });
  });

  it("orders newest first — the hub takes the first five and calls it « en direct »", () => {
    const items = toHubItems([
      post("satoshi", "09:15", "trade", "early"),
      post("goldfinger", "18:40", "roast", "late"),
      post("world", "13:00", "market-take", "middle"),
    ]);
    expect(items.map((i) => i.time)).toEqual(["18:40", "13:00", "09:15"]);
  });

  it("drops posts from ids the roster does not know rather than throwing", () => {
    // getAgent throws on an unknown id; a renamed agent must not 500 the endpoint
    // and take the whole strip down with it.
    const items = toHubItems([
      post("no-such-agent", "11:00", "trade", "orphan"),
      post("satoshi", "10:00", "trade", "kept"),
    ]);
    expect(items.map((i) => i.body)).toEqual(["kept"]);
  });

  it("projects the real latest session into the shape the hub consumes", () => {
    const items = toHubItems(flattenChronological(loadPostsLatest()));
    expect(items.length).toBeGreaterThan(0);
    for (const it of items) {
      expect(typeof it.author).toBe("string");
      expect(it.author.length).toBeGreaterThan(0);
      expect(it.time).toMatch(/^\d{2}:\d{2}$/);
      expect(typeof it.tag).toBe("string");
      expect(typeof it.body).toBe("string");
      expect(Object.keys(it).sort()).toEqual(["author", "body", "tag", "time"]);
    }
  });
});
