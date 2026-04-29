import { describe, it, expect } from "vitest";
import { listPostDates, loadPostsLatest, flattenChronological, renderBodyHtml } from "@/lib/posts";
import type { Post } from "@/lib/posts";

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

function makePost(overrides: Partial<Post> = {}): Post {
  return {
    agent_id: "satoshi",
    text: "",
    mentions: [],
    kind: "market-take",
    parent_id: null,
    refs: {},
    post_at: "12:00",
    ...overrides,
  };
}

describe("renderBodyHtml", () => {
  it("escapes HTML metacharacters in plain text", () => {
    const html = renderBodyHtml(makePost({ text: "1 < 2 && 3 > 2" }));
    expect(html).toBe("1 &lt; 2 &amp;&amp; 3 &gt; 2");
  });

  it("linkifies a single ticker (uppercase slug, matching /ticker route)", () => {
    const html = renderBodyHtml(makePost({ text: "Bought $TSLA today." }));
    expect(html).toBe(
      'Bought <a class="feed-ticker" href="/ticker/TSLA">$TSLA</a> today.'
    );
  });

  it("linkifies multiple tickers including hyphenated symbols", () => {
    const html = renderBodyHtml(makePost({ text: "$BTC-EUR up, $TSLA flat." }));
    expect(html).toContain('href="/ticker/BTC-EUR">$BTC-EUR</a>');
    expect(html).toContain('href="/ticker/TSLA">$TSLA</a>');
  });

  it("does not match $lowercase or $123 as tickers", () => {
    const html = renderBodyHtml(makePost({ text: "$tsla and $123 and $1k." }));
    expect(html).toBe("$tsla and $123 and $1k.");
  });

  it("renders a mention as a chip linking to the agent dossier", () => {
    const html = renderBodyHtml(
      makePost({
        text: "Steady Eddie USD ought to read a balance sheet.",
        mentions: ["steady-eddie-usd"],
      })
    );
    expect(html).toBe(
      '<a class="feed-mention" data-agent="steady-eddie-usd" href="/arena/steady-eddie-usd">@Steady Eddie USD</a> ought to read a balance sheet.'
    );
  });

  it("falls back to the @handle form when display_name is not in the body", () => {
    const html = renderBodyHtml(
      makePost({
        text: "@steady-eddie-usd is wrong about TSLA.",
        mentions: ["steady-eddie-usd"],
      })
    );
    expect(html).toBe(
      '<a class="feed-mention" data-agent="steady-eddie-usd" href="/arena/steady-eddie-usd">@Steady Eddie USD</a> is wrong about TSLA.'
    );
  });

  it("drops a mention silently when neither name nor handle appears in the body", () => {
    const html = renderBodyHtml(
      makePost({
        text: "Markets were quiet.",
        mentions: ["steady-eddie-usd"],
      })
    );
    expect(html).toBe("Markets were quiet.");
  });

  it("only replaces the first occurrence of a mentioned agent's name", () => {
    const html = renderBodyHtml(
      makePost({
        text: "Goldfinger said it. Goldfinger meant it.",
        mentions: ["goldfinger"],
      })
    );
    const firstChip = '<a class="feed-mention" data-agent="goldfinger" href="/arena/goldfinger">@Goldfinger</a> said it. Goldfinger meant it.';
    expect(html).toBe(firstChip);
  });

  it("handles a body containing both tickers and mentions", () => {
    const html = renderBodyHtml(
      makePost({
        text: "Sharp Shooter USD doubled $TSLA again.",
        mentions: ["sharp-shooter-usd"],
      })
    );
    expect(html).toContain('href="/arena/sharp-shooter-usd">@Sharp Shooter USD</a>');
    expect(html).toContain('href="/ticker/TSLA">$TSLA</a>');
  });

  it("escapes HTML inside a body that also contains a ticker", () => {
    const html = renderBodyHtml(makePost({ text: "<script>alert(1)</script> $TSLA" }));
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain('<a class="feed-ticker" href="/ticker/TSLA">$TSLA</a>');
  });

  it("ignores mentions that are not in the trading roster", () => {
    const html = renderBodyHtml(
      makePost({
        text: "Some random handle was here.",
        mentions: ["not-an-agent"],
      })
    );
    expect(html).toBe("Some random handle was here.");
  });

  it("does not capture trailing punctuation as part of a ticker", () => {
    const html = renderBodyHtml(makePost({ text: "Bought $AAPL. End of line." }));
    expect(html).toBe(
      'Bought <a class="feed-ticker" href="/ticker/AAPL">$AAPL</a>. End of line.'
    );
  });

  it("matches a single-letter ticker", () => {
    const html = renderBodyHtml(makePost({ text: "Long $F today." }));
    expect(html).toBe(
      'Long <a class="feed-ticker" href="/ticker/F">$F</a> today.'
    );
  });

  it("matches a dotted ticker like $BRK.B", () => {
    const html = renderBodyHtml(makePost({ text: "Held $BRK.B for years." }));
    expect(html).toBe(
      'Held <a class="feed-ticker" href="/ticker/BRK-B">$BRK.B</a> for years.'
    );
  });
});
