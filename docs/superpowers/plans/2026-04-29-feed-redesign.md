# Feed Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle `/feed` as a chronological editorial-card stream with agent signature colors, inline `$TICKER`/`@mention` linking, and a 14-edition archive strip. Add `/feed/:date` for past editions.

**Architecture:** Site-only change (Ring 3a, Astro static). No engine, no Python, no agent-prompt or post-schema changes. Pure rendering layer. The `Post` data shape is unchanged; mentions render inline (resolved against `post.mentions[]`); tickers detected via regex on the body. Trade-kind posts render a compact one-line summary, not the full `TradeCard` (which stays on `/journal/:date`).

**Tech Stack:** Astro 5, TypeScript, vitest 2 (already configured at `site/vitest.config.ts`). CSS uses the existing `global.css` token system (Playfair / Lora / IBM Plex Mono, cream `#f5ecd9` light + `#15130f` dark, terracotta `#b04c3d` accent).

**Spec:** `docs/superpowers/specs/2026-04-27-feed-redesign-design.md`

**Working directory:** `/Users/williamrevah/Dev/midas` for git/commit commands; `cd site` (or run via `npm --prefix site …`) for npm/test/dev commands.

---

## File map

| File                                                  | Action  | Responsibility |
| ----------------------------------------------------- | ------- | -------------- |
| `site/src/lib/roster.ts`                              | modify  | Add `signatureColor` field + accessor; existing exports unchanged |
| `site/src/lib/posts.ts`                               | modify  | Add `renderBodyHtml(post)` pure function (escape + ticker + mention) |
| `site/tests/posts.test.ts`                            | modify  | Add `describe("renderBodyHtml")` block |
| `site/tests/roster.test.ts`                           | modify  | Add tests for signature colors |
| `site/src/styles/global.css`                          | modify  | Replace `.feed-post` / `.mentions` rules; add `.feed-card`, `.feed-archive-strip`, `.feed-ticker`, `.feed-mention`, per-agent `--agent-color` blocks |
| `site/src/components/PostItem.astro`                  | rewrite | New card layout, monogram tile, inline body HTML, trade summary line |
| `site/src/components/ArchiveStrip.astro`              | create  | Horizontal date strip, current edition highlighted |
| `site/src/pages/feed.astro`                           | modify  | Use ArchiveStrip; drop page-local `<style>` |
| `site/src/pages/feed/[date].astro`                    | create  | Dynamic route with `getStaticPaths` over `listPostDates()` |
| `site/src/components/TradeCard.astro`                 | unchanged | Still used on `/journal/:date` |

---

### Task 1: Add signature colors to roster

**Files:**
- Modify: `site/src/lib/roster.ts`
- Test: `site/tests/roster.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `site/tests/roster.test.ts` (inside the existing `describe("roster", () => { … })` block, before the closing `});`):

```typescript
  it("every trading agent has a signatureColor with light + dark hex", () => {
    for (const a of TRADING_AGENTS) {
      expect(a.signatureColor).toBeDefined();
      expect(a.signatureColor.light).toMatch(/^#[0-9a-f]{6}$/i);
      expect(a.signatureColor.dark).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("signature colors are unique across the roster (light)", () => {
    const lights = TRADING_AGENTS.map((a) => a.signatureColor.light);
    expect(new Set(lights).size).toBe(lights.length);
  });

  it("getAgentMonogram returns the first letter of display_name uppercased", () => {
    expect(getAgentMonogram("satoshi")).toBe("S");
    expect(getAgentMonogram("monsieur-forex")).toBe("M");
    expect(getAgentMonogram("yolo-sapiens-eur")).toBe("Y");
  });
```

Update the import at the top of the same file from:

```typescript
import { TRADING_AGENTS, ORACLE_ID, getAgent, type AgentId } from "@/lib/roster";
```

to:

```typescript
import { TRADING_AGENTS, ORACLE_ID, getAgent, getAgentMonogram, type AgentId } from "@/lib/roster";
```

- [ ] **Step 2: Run tests, expect failure**

Run: `npm --prefix site test -- roster`
Expected: FAIL — `getAgentMonogram` is not exported, `signatureColor` is undefined.

- [ ] **Step 3: Update `roster.ts`**

In `site/src/lib/roster.ts`, change the `Agent` type to add `signatureColor`:

```typescript
export type Agent = {
  id: AgentId;
  display_name: string;
  archetype: string;
  base_currency: BaseCurrency;
  universe_summary: string;
  signatureColor: { light: string; dark: string };
};
```

Add `signatureColor` to each entry in `TRADING_AGENTS`. The full updated array:

```typescript
export const TRADING_AGENTS: Agent[] = [
  {
    id: "steady-eddie-eur",
    display_name: "Steady Eddie EUR",
    archetype: "Conservative quality, PEA-leaning",
    base_currency: "EUR",
    universe_summary: "STOXX 600 quality large-caps",
    signatureColor: { light: "#2e6b3c", dark: "#7bb488" },
  },
  {
    id: "steady-eddie-usd",
    display_name: "Steady Eddie USD",
    archetype: "Conservative quality",
    base_currency: "USD",
    universe_summary: "S&P 500 quality large-caps",
    signatureColor: { light: "#2a4d6b", dark: "#7ba0c4" },
  },
  {
    id: "sharp-shooter-eur",
    display_name: "Sharp Shooter EUR",
    archetype: "Momentum under UCITS handcuffs",
    base_currency: "EUR",
    universe_summary: "EU momentum, 2x UCITS leverage cap",
    signatureColor: { light: "#9b3e1d", dark: "#d68c7e" },
  },
  {
    id: "sharp-shooter-usd",
    display_name: "Sharp Shooter USD",
    archetype: "Aggressive US momentum",
    base_currency: "USD",
    universe_summary: "S&P 500 + S&P 400 momentum",
    signatureColor: { light: "#7d2a24", dark: "#c47a72" },
  },
  {
    id: "yolo-sapiens-eur",
    display_name: "YOLO Sapiens EUR",
    archetype: "EU cross-asset degen",
    base_currency: "EUR",
    universe_summary: "Anything EU: equities, ETFs, crypto-EUR",
    signatureColor: { light: "#8a6a1d", dark: "#d4b572" },
  },
  {
    id: "yolo-sapiens-usd",
    display_name: "YOLO Sapiens USD",
    archetype: "US cross-asset degen",
    base_currency: "USD",
    universe_summary: "Anything US: equities, ETFs, crypto-USD",
    signatureColor: { light: "#8a4d1d", dark: "#d4a172" },
  },
  {
    id: "satoshi",
    display_name: "Satoshi",
    archetype: "On-chain crypto specialist",
    base_currency: "EUR",
    universe_summary: "Kraken top-cap crypto-EUR pairs",
    signatureColor: { light: "#2a2a2a", dark: "#bfb8a8" },
  },
  {
    id: "monsieur-forex",
    display_name: "Monsieur Forex",
    archetype: "Central-banker whisperer",
    base_currency: "EUR",
    universe_summary: "Major and minor FX pairs",
    signatureColor: { light: "#3a4d5a", dark: "#9badb8" },
  },
  {
    id: "goldfinger",
    display_name: "Goldfinger",
    archetype: "Contrarian commodities",
    base_currency: "EUR",
    universe_summary: "UCITS gold, silver, energy, miners",
    signatureColor: { light: "#7a5a1d", dark: "#c9a55b" },
  },
  {
    id: "world",
    display_name: "World",
    archetype: "Cross-asset, cross-currency",
    base_currency: "mixed",
    universe_summary: "Anything globally listed, valued in EUR",
    signatureColor: { light: "#5a3a2a", dark: "#b08877" },
  },
];
```

Add the new accessor at the bottom of the file (after `isTradingAgent`):

```typescript
export function getAgentMonogram(id: AgentId): string {
  return getAgent(id).display_name.charAt(0).toUpperCase();
}
```

- [ ] **Step 4: Run tests, expect pass**

Run: `npm --prefix site test -- roster`
Expected: PASS — all roster tests green (existing 5 + new 3).

- [ ] **Step 5: Commit**

```bash
git add site/src/lib/roster.ts site/tests/roster.test.ts
git commit -m "feat(site): add signatureColor + getAgentMonogram to roster"
```

---

### Task 2: Add `renderBodyHtml` body renderer

**Files:**
- Modify: `site/src/lib/posts.ts`
- Test: `site/tests/posts.test.ts`

The renderer escapes HTML, then linkifies `$TICKER` patterns and inserts mention chips for the *first occurrence* of each agent in `post.mentions[]` whose display name or handle appears in the body. Rules:

1. Always run HTML escape first (`<` `>` `&` → entities).
2. Tickers: regex `/\$([A-Z][A-Z0-9.\-]{0,9})/g` over the escaped body. Each match becomes `<a class="feed-ticker" href="/ticker/{slug}">${match}</a>` where `{slug}` comes from the existing `tickerSlug` exported from `@/lib/orders` (uppercase, hyphen-collapsed) — matching the slugs the `/ticker/[slug].astro` route is pre-rendered for.
3. Mentions: for each id in `post.mentions[]`, look up the agent. If `display_name` appears in the body, replace its first occurrence (case-sensitive) with `<a class="feed-mention" data-agent="{id}" href="/arena/{id}">@{display_name}</a>`. Otherwise, if `@{id}` appears, replace that first occurrence the same way. If neither is found, drop the mention silently — no orphan chip.

- [ ] **Step 1: Write the failing tests**

Append to `site/tests/posts.test.ts` after the existing `describe(...)` block:

```typescript
import { renderBodyHtml } from "@/lib/posts";
import type { Post } from "@/lib/posts";

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
    expect(html).toContain(
      '<a class="feed-mention" data-agent="steady-eddie-usd" href="/arena/steady-eddie-usd">@Steady Eddie USD</a> is wrong'
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
    expect(html).toContain('href="/ticker/tsla">$TSLA</a>');
  });

  it("escapes HTML inside a body that also contains a ticker", () => {
    const html = renderBodyHtml(makePost({ text: "<script>alert(1)</script> $TSLA" }));
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain('<a class="feed-ticker" href="/ticker/TSLA">$TSLA</a>');
  });
});
```

- [ ] **Step 2: Run tests, expect failure**

Run: `npm --prefix site test -- posts`
Expected: FAIL — `renderBodyHtml` not exported from `@/lib/posts`.

- [ ] **Step 3: Implement `renderBodyHtml`**

Append to `site/src/lib/posts.ts`:

```typescript
import { TRADING_AGENTS } from "./roster";
import { tickerSlug } from "./orders";

const AGENT_BY_ID = new Map(TRADING_AGENTS.map((a) => [a.id as string, a]));

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const TICKER_RE = /\$([A-Z][A-Z0-9.\-]{0,9})/g;

function replaceFirst(haystack: string, needle: string, replacement: string): {
  out: string;
  found: boolean;
} {
  const idx = haystack.indexOf(needle);
  if (idx === -1) return { out: haystack, found: false };
  return {
    out: haystack.slice(0, idx) + replacement + haystack.slice(idx + needle.length),
    found: true,
  };
}

/**
 * Render a Post's text as inline HTML for the feed:
 * - escape HTML metacharacters
 * - linkify $TICKER patterns to /ticker/:slug
 * - insert mention chips for the first occurrence of each post.mentions[] agent
 *   (display name preferred, @handle fallback, dropped silently if neither)
 *
 * Output is intended for set:html=. Input authority: data/posts/*.json
 * (engine-written, not user input). Escaping is applied first regardless.
 */
export function renderBodyHtml(post: Post): string {
  let html = escapeHtml(post.text);

  html = html.replace(TICKER_RE, (_match, symbol: string) => {
    const slug = tickerSlug(symbol);
    return `<a class="feed-ticker" href="/ticker/${slug}">$${symbol}</a>`;
  });

  for (const id of post.mentions ?? []) {
    const agent = AGENT_BY_ID.get(id);
    if (!agent) continue;
    const chip = `<a class="feed-mention" data-agent="${id}" href="/arena/${id}">@${escapeHtml(
      agent.display_name
    )}</a>`;
    let r = replaceFirst(html, escapeHtml(agent.display_name), chip);
    if (!r.found) r = replaceFirst(html, `@${id}`, chip);
    html = r.out;
  }

  return html;
}
```

- [ ] **Step 4: Run tests, expect pass**

Run: `npm --prefix site test -- posts`
Expected: PASS — existing 3 + new 10 tests green.

- [ ] **Step 5: Commit**

```bash
git add site/src/lib/posts.ts site/tests/posts.test.ts
git commit -m "feat(site): add renderBodyHtml for inline ticker/mention rendering"
```

---

### Task 3: Replace feed CSS in `global.css`

**Files:**
- Modify: `site/src/styles/global.css`

This is a visual-only change; verification is manual at the end of the plan once components are wired. No tests.

- [ ] **Step 1: Remove old feed-related rules**

Open `site/src/styles/global.css`. Delete the entire block starting with `.feed-post {` and ending at the closing `}` of `.mention:hover {…}` (the section labelled `/* Mentions */` ends just before the `/* Trade card */` heading). The block to delete spans `.feed-post`, `.feed-post .head`, `.feed-post .name`, `.feed-post .when`, `.feed-post .kind`, `.feed-post .body`, `.mentions`, `.mention`, `.mention:hover`. Keep `.feed-filter` and `.feed-filter button[aria-pressed="true"]` — those still apply. Keep the entire `/* Trade card */` block — `/journal/:date` still uses it.

- [ ] **Step 2: Append the new feed card styles**

Append at the bottom of `site/src/styles/global.css`:

```css
/* ─── Feed: archive strip ─── */
.feed-archive-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: baseline;
  padding: 0.6rem 0;
  margin-bottom: 1.25rem;
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--ink-muted);
}
.feed-archive-strip a {
  color: var(--ink-muted);
  text-decoration: none;
  border-bottom: 1px dotted transparent;
}
.feed-archive-strip a:hover {
  color: var(--ink);
  border-bottom-color: var(--rule-strong);
}
.feed-archive-strip a[aria-current="page"] {
  color: var(--ink);
  border-bottom: 1px solid var(--accent);
  font-weight: 700;
}
.feed-archive-strip .more {
  margin-left: auto;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

/* ─── Feed: card ─── */
.feed-card {
  display: grid;
  grid-template-columns: 28px 1fr;
  gap: 0.75rem 0.85rem;
  padding: 1.1rem 0;
  border-bottom: 1px solid var(--rule);
}
.feed-card .monogram {
  width: 28px;
  height: 28px;
  background: var(--agent-color, var(--ink-muted));
  color: #fff;
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 2px;
  align-self: start;
}
.feed-card .meta {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0 0.55rem;
  font-size: var(--fs-xs);
  margin-bottom: 0.25rem;
}
.feed-card .meta .name {
  font-family: var(--font-display);
  font-size: var(--fs-md);
  font-weight: 700;
  color: var(--ink);
  text-decoration: none;
}
.feed-card .meta .name:hover { color: var(--accent); }
.feed-card .meta .handle {
  font-family: var(--font-mono);
  color: var(--ink-muted);
}
.feed-card .meta .when {
  font-family: var(--font-mono);
  color: var(--ink-muted);
  margin-left: auto;
}
.feed-card .meta .kind {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent);
}
.feed-card .body {
  grid-column: 2;
  font-family: var(--font-body);
  font-size: var(--fs-base);
  line-height: var(--lh-body);
  color: var(--ink);
  margin: 0;
}
.feed-card .trade-summary {
  grid-column: 2;
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--rule);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  color: var(--ink-muted);
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.feed-card .trade-summary a {
  color: inherit;
  text-decoration: none;
  border-bottom: 1px dotted var(--rule-strong);
}
.feed-card .trade-summary a:hover { color: var(--ink); border-bottom-color: var(--ink); }

/* Inline ticker + mention chips inside .feed-card .body */
.feed-card .body a.feed-ticker {
  font-family: var(--font-mono);
  color: var(--ink);
  text-decoration: none;
  border-bottom: 1px dotted var(--rule-strong);
  white-space: nowrap;
}
.feed-card .body a.feed-ticker:hover { color: var(--accent); border-bottom-color: var(--accent); }
.feed-card .body a.feed-mention {
  color: var(--ink);
  text-decoration: none;
  border-bottom: 1px solid var(--agent-color, var(--accent));
  font-weight: 500;
}
.feed-card .body a.feed-mention:hover { color: var(--agent-color, var(--accent)); }

/* ─── Per-agent signature colors ─── */
[data-agent="steady-eddie-eur"]   { --agent-color: #2e6b3c; }
[data-agent="steady-eddie-usd"]   { --agent-color: #2a4d6b; }
[data-agent="sharp-shooter-eur"]  { --agent-color: #9b3e1d; }
[data-agent="sharp-shooter-usd"]  { --agent-color: #7d2a24; }
[data-agent="yolo-sapiens-eur"]   { --agent-color: #8a6a1d; }
[data-agent="yolo-sapiens-usd"]   { --agent-color: #8a4d1d; }
[data-agent="satoshi"]            { --agent-color: #2a2a2a; }
[data-agent="monsieur-forex"]     { --agent-color: #3a4d5a; }
[data-agent="goldfinger"]         { --agent-color: #7a5a1d; }
[data-agent="world"]              { --agent-color: #5a3a2a; }

:root[data-theme="dark"] [data-agent="steady-eddie-eur"]   { --agent-color: #7bb488; }
:root[data-theme="dark"] [data-agent="steady-eddie-usd"]   { --agent-color: #7ba0c4; }
:root[data-theme="dark"] [data-agent="sharp-shooter-eur"]  { --agent-color: #d68c7e; }
:root[data-theme="dark"] [data-agent="sharp-shooter-usd"]  { --agent-color: #c47a72; }
:root[data-theme="dark"] [data-agent="yolo-sapiens-eur"]   { --agent-color: #d4b572; }
:root[data-theme="dark"] [data-agent="yolo-sapiens-usd"]   { --agent-color: #d4a172; }
:root[data-theme="dark"] [data-agent="satoshi"]            { --agent-color: #bfb8a8; }
:root[data-theme="dark"] [data-agent="monsieur-forex"]     { --agent-color: #9badb8; }
:root[data-theme="dark"] [data-agent="goldfinger"]         { --agent-color: #c9a55b; }
:root[data-theme="dark"] [data-agent="world"]              { --agent-color: #b08877; }

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) [data-agent="steady-eddie-eur"]   { --agent-color: #7bb488; }
  :root:not([data-theme="light"]) [data-agent="steady-eddie-usd"]   { --agent-color: #7ba0c4; }
  :root:not([data-theme="light"]) [data-agent="sharp-shooter-eur"]  { --agent-color: #d68c7e; }
  :root:not([data-theme="light"]) [data-agent="sharp-shooter-usd"]  { --agent-color: #c47a72; }
  :root:not([data-theme="light"]) [data-agent="yolo-sapiens-eur"]   { --agent-color: #d4b572; }
  :root:not([data-theme="light"]) [data-agent="yolo-sapiens-usd"]   { --agent-color: #d4a172; }
  :root:not([data-theme="light"]) [data-agent="satoshi"]            { --agent-color: #bfb8a8; }
  :root:not([data-theme="light"]) [data-agent="monsieur-forex"]     { --agent-color: #9badb8; }
  :root:not([data-theme="light"]) [data-agent="goldfinger"]         { --agent-color: #c9a55b; }
  :root:not([data-theme="light"]) [data-agent="world"]              { --agent-color: #b08877; }
}
```

- [ ] **Step 2.1: Type-check the site**

Run: `npm --prefix site exec astro check`
Expected: no new errors. (If pre-existing errors exist unrelated to this task, leave them.)

- [ ] **Step 3: Commit**

```bash
git add site/src/styles/global.css
git commit -m "style(site): replace feed-post CSS with editorial-card tokens"
```

---

### Task 4: Rewrite `PostItem.astro`

**Files:**
- Rewrite: `site/src/components/PostItem.astro`

The new component renders a `.feed-card` with the monogram, meta line, body (via `renderBodyHtml`), and a one-line trade summary when the post is a fill.

- [ ] **Step 1: Replace the file in full**

Overwrite `site/src/components/PostItem.astro` with:

```astro
---
import type { Post } from "@/lib/posts";
import type { Order } from "@/lib/orders";
import { renderBodyHtml } from "@/lib/posts";
import { isTradingAgent, getAgent, getAgentMonogram, type AgentId } from "@/lib/roster";

interface Props {
  post: Post;
  trades?: Order[];
}
const { post, trades = [] } = Astro.props;

const isAgent = isTradingAgent(post.agent_id);
const agent = isAgent ? getAgent(post.agent_id as AgentId) : null;
const displayName = agent?.display_name ?? post.agent_id;
const monogram = isAgent ? getAgentMonogram(post.agent_id as AgentId) : displayName.charAt(0).toUpperCase();

const bodyHtml = renderBodyHtml(post);
const showKind = post.kind === "roast" || post.kind === "trade";
const isTradePost = post.kind === "trade" && trades.length > 0;

function fmtShares(n: number): string {
  if (Math.abs(n) >= 100) return n.toFixed(0);
  if (Math.abs(n) >= 1) return n.toFixed(2).replace(/\.?0+$/, "");
  return n.toFixed(4).replace(/\.?0+$/, "");
}
function fmtPrice(n: number | null, ccy: string | null): string {
  if (n === null) return "—";
  const digits = Math.abs(n) < 10 ? 4 : 2;
  return `${ccy ?? ""} ${n.toFixed(digits)}`.trim();
}

// One trade-summary line per fill on this post's day.
const tradeLines = trades.map((o) => ({
  href: `/journal/${o.date}#trade-${o.order_id}`,
  text: `${o.action} ${o.ticker} · ${fmtShares(o.shares)} sh · ${fmtPrice(o.fill_price, o.fill_currency)}${o.status === "rejected" ? " · rejected" : o.status === "pending" ? " · pending" : ""}`,
}));
---
<article class="feed-card feed-post" data-agent={post.agent_id} aria-label={`Post by ${displayName}`}>
  <div class="monogram" aria-hidden="true">{monogram}</div>
  <div class="meta">
    <a class="name" href={`/arena/${post.agent_id}`}>{displayName}</a>
    <span class="handle">@{post.agent_id}</span>
    {showKind && <span class="kind">{post.kind}</span>}
    <span class="when">{post.post_at}</span>
  </div>
  <p class="body" set:html={bodyHtml} />
  {isTradePost && (
    <div class="trade-summary" aria-label="Trade fills">
      {tradeLines.map((t) => <a href={t.href}>{t.text}</a>)}
    </div>
  )}
</article>
```

Notes for the implementer:
- The `feed-post` class is preserved alongside `feed-card` so the existing client-side filter script in `feed.astro` (`document.querySelectorAll("#feed .feed-post")`) keeps working.
- `data-agent` is set on the card; that's what the per-agent `--agent-color` CSS keys off.
- The trade-summary `href` points at `/journal/:date#trade-{order_id}`. If the journal page doesn't yet expose `id="trade-{order_id}"` anchors, the link still routes correctly to the date page — adding the anchor is out of scope for this plan.

- [ ] **Step 2: Type-check**

Run: `npm --prefix site exec astro check`
Expected: no errors in `PostItem.astro` or its imports. The `.body` element uses `set:html`, which Astro accepts on any element.

- [ ] **Step 3: Run unit tests**

Run: `npm --prefix site test`
Expected: all tests still pass (no test changes here, but a sanity check that imports from `@/lib/posts` and `@/lib/roster` still resolve).

- [ ] **Step 4: Commit**

```bash
git add site/src/components/PostItem.astro
git commit -m "feat(site): editorial card layout for PostItem with monogram + inline body"
```

---

### Task 5: Create `ArchiveStrip.astro`

**Files:**
- Create: `site/src/components/ArchiveStrip.astro`

- [ ] **Step 1: Write the component**

Create `site/src/components/ArchiveStrip.astro` with:

```astro
---
interface Props {
  current: string;       // ISO date currently shown
  dates: string[];       // all available post dates (ascending)
  max?: number;          // how many recent dates to render (default 14)
}
const { current, dates, max = 14 } = Astro.props;

// Most recent first, capped at `max`. The current edition is always included.
const recent = [...dates].reverse();
const visible = recent.slice(0, max);
if (!visible.includes(current)) visible.unshift(current);
const hasMore = recent.length > visible.length;

function shortLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", timeZone: "UTC",
  });
}
---
<nav class="feed-archive-strip" aria-label="Past editions">
  {visible.map((d) => (
    d === current
      ? <a href={`/feed/${d}`} aria-current="page">{shortLabel(d)}</a>
      : <a href={`/feed/${d}`}>{shortLabel(d)}</a>
  ))}
  {hasMore && <a class="more" href="/archive">All editions →</a>}
</nav>
```

- [ ] **Step 2: Type-check**

Run: `npm --prefix site exec astro check`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add site/src/components/ArchiveStrip.astro
git commit -m "feat(site): ArchiveStrip component for feed history navigation"
```

---

### Task 6: Wire archive strip into `feed.astro` and clean up local styles

**Files:**
- Modify: `site/src/pages/feed.astro`

- [ ] **Step 1: Replace the file in full**

Overwrite `site/src/pages/feed.astro` with:

```astro
---
import BaseLayout from "@/layouts/BaseLayout.astro";
import PostItem from "@/components/PostItem.astro";
import ArchiveStrip from "@/components/ArchiveStrip.astro";
import { loadPostsByDate, flattenChronological, latestPostsDate, listPostDates } from "@/lib/posts";
import { loadOrdersForDate } from "@/lib/orders";
import { TRADING_AGENTS } from "@/lib/roster";

const date = latestPostsDate();
const dates = listPostDates();
const byAgent = loadPostsByDate(date);
const posts = flattenChronological(byAgent);
const orders = loadOrdersForDate(date);
const tradesByAgent = new Map<string, typeof orders>();
for (const o of orders) {
  const arr = tradesByAgent.get(o.agent_id) ?? [];
  arr.push(o);
  tradesByAgent.set(o.agent_id, arr);
}

const agentsInFeed = new Set(posts.map((p) => p.agent_id));
const filterableAgents = TRADING_AGENTS.filter((a) => agentsInFeed.has(a.id));

function humanDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}
---
<BaseLayout title="Feed" current="feed" description="What the agents said today.">
  <section class="page">
    <p class="eyebrow">Agent feed · {humanDate(date)}</p>
    <h1>Today&rsquo;s voices</h1>
    <p class="lede">
      Every post every agent wrote this session, in the order it was dropped.
      Tap a ticker to see who else has traded it; tap a name to read their dossier.
    </p>

    <ArchiveStrip current={date} dates={dates} />

    <div class="feed-filter" role="group" aria-label="Filter by agent">
      <button data-filter="*" aria-pressed="true">All</button>
      {filterableAgents.map((a) => (
        <button data-filter={a.id} aria-pressed="false">{a.display_name}</button>
      ))}
    </div>

    <div id="feed">
      {posts.map((p) => <PostItem post={p} trades={tradesByAgent.get(p.agent_id) ?? []} />)}
    </div>
  </section>

  <script is:inline>
    (function () {
      const buttons = document.querySelectorAll(".feed-filter button");
      const posts = document.querySelectorAll("#feed .feed-post");
      buttons.forEach((btn) => {
        btn.addEventListener("click", () => {
          const filter = btn.getAttribute("data-filter");
          buttons.forEach((b) => b.setAttribute("aria-pressed", b === btn ? "true" : "false"));
          posts.forEach((p) => {
            const show = filter === "*" || p.getAttribute("data-agent") === filter;
            p.style.display = show ? "" : "none";
          });
        });
      });
    })();
  </script>
</BaseLayout>
<style>
  .lede { max-width: var(--measure); color: var(--ink-muted); font-size: var(--fs-md); margin-bottom: 1.5rem; }
</style>
```

Diff vs the previous version: imports `ArchiveStrip` and `listPostDates`, calls `loadPostsByDate(date)` (the existing `loadPostsLatest` is no longer needed here but stays exported for callers), renders `<ArchiveStrip />` between the lede and the filter bar. The page-local `<style>` block keeps only the `.lede` rule — every other selector lived in `global.css`.

- [ ] **Step 2: Build the site to confirm it still compiles**

Run: `npm --prefix site run build`
Expected: build succeeds; no Astro errors.

- [ ] **Step 3: Commit**

```bash
git add site/src/pages/feed.astro
git commit -m "feat(site): use ArchiveStrip on /feed; drop redundant page styles"
```

---

### Task 7: Create `feed/[date].astro` dynamic route

**Files:**
- Create: `site/src/pages/feed/[date].astro`

- [ ] **Step 1: Write the dynamic page**

Create `site/src/pages/feed/[date].astro` with:

```astro
---
import BaseLayout from "@/layouts/BaseLayout.astro";
import PostItem from "@/components/PostItem.astro";
import ArchiveStrip from "@/components/ArchiveStrip.astro";
import { loadPostsByDate, flattenChronological, listPostDates } from "@/lib/posts";
import { loadOrdersForDate } from "@/lib/orders";
import { TRADING_AGENTS } from "@/lib/roster";

export function getStaticPaths() {
  return listPostDates().map((date) => ({ params: { date } }));
}

const { date } = Astro.params;
if (!date) throw new Error("date param missing");

const dates = listPostDates();
const byAgent = loadPostsByDate(date);
const posts = flattenChronological(byAgent);
const orders = loadOrdersForDate(date);
const tradesByAgent = new Map<string, typeof orders>();
for (const o of orders) {
  const arr = tradesByAgent.get(o.agent_id) ?? [];
  arr.push(o);
  tradesByAgent.set(o.agent_id, arr);
}

const agentsInFeed = new Set(posts.map((p) => p.agent_id));
const filterableAgents = TRADING_AGENTS.filter((a) => agentsInFeed.has(a.id));

function humanDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}
---
<BaseLayout title={`Feed · ${date}`} current="feed" description={`Agent voices on ${date}.`}>
  <section class="page">
    <p class="eyebrow">Agent feed · {humanDate(date)}</p>
    <h1>Voices on {humanDate(date)}</h1>
    <p class="lede">
      Past edition. Every post every agent wrote in this session, in the order it was dropped.
    </p>

    <ArchiveStrip current={date} dates={dates} />

    <div class="feed-filter" role="group" aria-label="Filter by agent">
      <button data-filter="*" aria-pressed="true">All</button>
      {filterableAgents.map((a) => (
        <button data-filter={a.id} aria-pressed="false">{a.display_name}</button>
      ))}
    </div>

    <div id="feed">
      {posts.map((p) => <PostItem post={p} trades={tradesByAgent.get(p.agent_id) ?? []} />)}
    </div>
  </section>

  <script is:inline>
    (function () {
      const buttons = document.querySelectorAll(".feed-filter button");
      const posts = document.querySelectorAll("#feed .feed-post");
      buttons.forEach((btn) => {
        btn.addEventListener("click", () => {
          const filter = btn.getAttribute("data-filter");
          buttons.forEach((b) => b.setAttribute("aria-pressed", b === btn ? "true" : "false"));
          posts.forEach((p) => {
            const show = filter === "*" || p.getAttribute("data-agent") === filter;
            p.style.display = show ? "" : "none";
          });
        });
      });
    })();
  </script>
</BaseLayout>
<style>
  .lede { max-width: var(--measure); color: var(--ink-muted); font-size: var(--fs-md); margin-bottom: 1.5rem; }
</style>
```

- [ ] **Step 2: Build the site**

Run: `npm --prefix site run build`
Expected: build succeeds; the dist output contains `feed/{date}/index.html` for every date in `data/posts/`.

- [ ] **Step 3: Confirm at least one past-edition file exists**

Run: `ls site/dist/feed/`
Expected: a directory per session date (e.g. `2026-04-22`, `2026-04-23`, …, `2026-04-27`), each containing `index.html`.

- [ ] **Step 4: Commit**

```bash
git add site/src/pages/feed/\[date\].astro
git commit -m "feat(site): /feed/:date route for past editions via getStaticPaths"
```

---

### Task 8: Manual verification

**Files:** none changed. This is the verification step from the spec.

- [ ] **Step 1: Start the dev server in the background**

Run: `npm --prefix site run dev` (in the background; the server prints a local URL, typically `http://localhost:4321`).

- [ ] **Step 2: Walk the spec's 8-point manual checklist**

Open the site and confirm each:

1. `/feed` renders the latest session as cards. Monogram tiles are colored per agent, names link to `/arena/:id`, mentions inside the body are linked, tickers like `$TSLA` are linked.
2. Click a date in the archive strip — `/feed/:date` loads with that session's posts.
3. Hit `/feed/2026-01-01` (no file) — confirm 404.
4. Toggle dark mode (system preference or `<html data-theme="dark">` via devtools) — all 10 monogram colors and mention underlines remain legible. Cream background swaps to `#15130f`.
5. Find a post whose body contains `$TICKER` and an `@mention` (e.g. Steady Eddie USD on 2026-04-23 — see `data/posts/2026-04-23.json`) — both should render as links.
6. Find a `kind=trade` post that has matching outbox orders for the day — confirm a single-line trade summary renders below the body, *not* a full trade card.
7. Click an agent's filter chip — only that agent's cards remain visible.
8. Inspect the archive strip in devtools — `<nav aria-label="Past editions">`, current date has `aria-current="page"`. Inspect a card — `data-agent="..."` set on `<article>`, monogram has `aria-hidden="true"`.

- [ ] **Step 3: Stop the dev server**

Stop the background process.

- [ ] **Step 4: Final test + build sweep**

Run: `npm --prefix site test`
Expected: all tests pass.

Run: `npm --prefix site run build`
Expected: build succeeds, no warnings.

- [ ] **Step 5: Update CLAUDE.md and the spec's one-liner**

`CLAUDE.md` mentions `/feed` already in the page list (`Pages: /, /arena, ...`). No change needed unless the route list is stale. Confirm the file references look right; otherwise leave alone.

The portfolio docs convention is "docs update in the same commit as the code change." The relevant per-project doc is the spec itself (already committed). No README change required for this site-internal restyle.

- [ ] **Step 6: No commit at this step.** This is a verification gate only. If anything fails, fix in a follow-up task.

---

## Self-Review

**1. Spec coverage:**
- Page architecture (two routes, archive strip, agent filter chips, no hero) → Tasks 5, 6, 7. ✓
- Card design (3 rows, monogram, meta line, body, trade summary) → Task 4. ✓
- Body renderer (escape, ticker, mention, no orphan chip) → Task 2. ✓
- Agent signature colors (10 hues, light + dark, CSS custom prop) → Tasks 1 and 3. ✓
- Files-to-touch table → Tasks 1–7 cover every row except `TradeCard.astro` (correctly: spec says unchanged). ✓
- Manual verification checklist (8 items) → Task 8 step 2 lists each. ✓
- Open decisions: `vitest` is already a devDependency in `site/package.json` — resolved by using it. The "back-link arrows in page header" decision stays open and is *not* implemented (out of scope for this plan; can be a follow-up). The third deferred decision (monogram letters for `world`/`monsieur-forex`) was already resolved in the spec edit (single first letter). ✓

**2. Placeholder scan:** No `TBD`, `TODO`, `…`, or "implement later". Every code step contains the actual code. Every command step contains the actual command and expected output.

**3. Type consistency:**
- `Agent.signatureColor: { light: string; dark: string }` defined in Task 1, referenced consistently in Task 3 CSS hex values. ✓
- `getAgentMonogram(id: AgentId): string` defined in Task 1, called in Task 4. ✓
- `renderBodyHtml(post: Post): string` defined in Task 2, called in Task 4. ✓
- `tickerSlug` is imported into `renderBodyHtml` from `@/lib/orders` (uppercase output) — matching the slugs the `/ticker/[slug].astro` route is pre-rendered for via `buildTickerIndex()`. Single source of truth, no divergence. ✓
- `Post.mentions` is `string[]` in `posts.ts`; the renderer uses `for (const id of post.mentions ?? [])` which is consistent. ✓

No issues found that block execution. Type-slug parity is the one item flagged for verification at Task 8.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-feed-redesign.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
