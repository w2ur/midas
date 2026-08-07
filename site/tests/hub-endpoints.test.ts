import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * The two prerendered JSON endpoints exist for william.revah.paris, not for
 * this site, and nothing here links them — so they are easy to break without
 * noticing. `/latest.json` was in fact missing for as long as the hub had been
 * asking for it: the hub's build-prefetch fetched it, the endpoint had never
 * existed, and the strip rendered nothing while failing silently on both sides.
 *
 * The contract these tests pin is the one that protects the *other* repo: the
 * consumer fetches at ITS build time, so a throw here degrades a page in a
 * different project. Both endpoints must answer with a well-formed empty
 * payload instead of propagating a loader failure.
 *
 * Each endpoint is imported fresh per test (`vi.resetModules`) because the
 * module registers its mocked dependencies at import time.
 */

const okPost = {
  date: "2026-08-07",
  title: "The desk holds its nerve",
  slug: "the-desk-holds-its-nerve",
  body: "A long body ".repeat(60),
};

async function getLatest() {
  const mod = await import("@/pages/latest.json.ts");
  return mod.GET({} as never);
}

async function getOracleLatest() {
  const mod = await import("@/pages/oracle-latest.json.ts");
  return mod.GET({} as never);
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.doUnmock("@/lib/posts");
  vi.doUnmock("@/lib/blog");
});

describe("/latest.json", () => {
  it("is prerendered — a non-prerendered route emits no file in a static build", async () => {
    const mod = await import("@/pages/latest.json.ts");
    expect(mod.prerender).toBe(true);
  });

  it("serves the latest session's posts projected onto the hub's four fields", async () => {
    const res = await getLatest();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    const payload = JSON.parse(await res.text());
    expect(payload).toHaveProperty("generated_at");
    expect(payload).toHaveProperty("date");
    expect(Array.isArray(payload.items)).toBe(true);
    for (const item of payload.items) {
      expect(Object.keys(item).sort()).toEqual(["author", "body", "tag", "time"]);
    }
  });

  it("answers with an empty envelope when the loader throws, rather than throwing", async () => {
    vi.doMock("@/lib/posts", () => ({
      latestPostsDate: () => {
        throw new Error("no committed posts at this data root");
      },
      loadPostsLatest: () => {
        throw new Error("unreachable");
      },
      flattenChronological: () => [],
    }));
    const res = await getLatest();
    expect(res.status).toBe(200);
    expect(JSON.parse(await res.text())).toEqual({
      generated_at: null,
      date: null,
      items: [],
    });
  });

  it("still emits parseable JSON when the projection itself throws", async () => {
    vi.doMock("@/lib/hub-feed", () => ({
      toHubItems: () => {
        throw new Error("roster lookup exploded");
      },
    }));
    const res = await getLatest();
    const payload = JSON.parse(await res.text());
    expect(payload.items).toEqual([]);
  });
});

describe("/oracle-latest.json", () => {
  it("is prerendered", async () => {
    const mod = await import("@/pages/oracle-latest.json.ts");
    expect(mod.prerender).toBe(true);
  });

  it("serves the newest Oracle post with a permalink the hub can link", async () => {
    vi.doMock("@/lib/blog", () => ({
      loadBlogLatest: () => okPost,
      excerpt: (body: string, n: number) => body.slice(0, n),
    }));
    const res = await getOracleLatest();
    expect(res.headers.get("content-type")).toContain("application/json");
    const payload = JSON.parse(await res.text());
    expect(payload.date).toBe("2026-08-07");
    expect(payload.slug).toBe("the-desk-holds-its-nerve");
    expect(payload.permalink).toBe(
      "https://midas.revah.paris/journal/2026-08-07",
    );
    expect(payload.excerpt.length).toBeLessThanOrEqual(320);
  });

  it("answers with JSON null when there is no post, rather than throwing", async () => {
    vi.doMock("@/lib/blog", () => ({
      loadBlogLatest: () => {
        throw new Error("no blog drafts committed");
      },
      excerpt: (s: string) => s,
    }));
    const res = await getOracleLatest();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    // Literal "null" — valid JSON, and what the consumer's own fallback expects.
    expect(JSON.parse(await res.text())).toBeNull();
  });

  it("permalink points at the canonical host, not a relative path", async () => {
    vi.doMock("@/lib/blog", () => ({
      loadBlogLatest: () => okPost,
      excerpt: (s: string) => s,
    }));
    const payload = JSON.parse(await (await getOracleLatest()).text());
    expect(payload.permalink.startsWith("https://midas.revah.paris/")).toBe(true);
  });
});

describe("the control", () => {
  it("a thrown loader would be visible if the endpoint did not catch it", async () => {
    // Guards the guard: if the try/catch were removed, the tests above must
    // fail rather than silently pass on a payload that happens to look empty.
    vi.doMock("@/lib/blog", () => ({
      loadBlogLatest: () => {
        throw new Error("boom");
      },
      excerpt: (s: string) => s,
    }));
    const { loadBlogLatest } = await import("@/lib/blog");
    expect(() => loadBlogLatest()).toThrow("boom");
  });
});
