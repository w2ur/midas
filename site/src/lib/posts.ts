import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { POSTS_DIR } from "./paths";
import { TRADING_AGENTS } from "./roster";
import { tickerSlug } from "./orders";

export type Post = {
  agent_id: string;
  text: string;
  mentions: string[];
  kind: string;
  parent_id: string | null;
  refs: Record<string, unknown>;
  post_at: string;
};

export type PostsByAgent = Record<string, Post[]>;

const DATE_RE = /^(\d{4}-\d{2}-\d{2})\.json$/;

/**
 * Resolve a "random" post_at value to a deterministic HH:MM string.
 * Mirrors engine/posts.py::resolved_post_time — same seed, same algorithm.
 * Seed: "{date}::{agent_id}" → MD5 first 8 hex chars → minute offset in 09:00–22:00 Paris window.
 */
function resolvePostAt(postAt: string, agentId: string, date: string): string {
  if (postAt !== "random") return postAt;
  const seed = `${date}::${agentId}`;
  const digest = crypto.createHash("md5").update(seed).digest("hex").slice(0, 8);
  const value = parseInt(digest, 16);
  const START_H = 9;
  const END_H = 22;
  const spanMin = (END_H - START_H) * 60;
  const offset = value % spanMin;
  const h = Math.floor(offset / 60);
  const m = offset % 60;
  return `${String(START_H + h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function listPostDates(): string[] {
  return fs
    .readdirSync(POSTS_DIR)
    .map((f) => {
      const m = f.match(DATE_RE);
      return m ? m[1] : null;
    })
    .filter((d): d is string => d !== null)
    .sort();
}

export function loadPostsByDate(date: string): PostsByAgent {
  const file = path.join(POSTS_DIR, `${date}.json`);
  if (!fs.existsSync(file)) {
    throw new Error(`Posts file not found: ${file}`);
  }
  const raw = fs.readFileSync(file, "utf-8");
  const parsed = JSON.parse(raw) as PostsByAgent;
  const resolved: PostsByAgent = {};
  for (const [agentId, posts] of Object.entries(parsed)) {
    resolved[agentId] = posts.map((p) => ({
      ...p,
      post_at: resolvePostAt(p.post_at, agentId, date),
    }));
  }
  return resolved;
}

export function loadPostsLatest(): PostsByAgent {
  const dates = listPostDates();
  if (dates.length === 0) throw new Error(`No posts in ${POSTS_DIR}`);
  return loadPostsByDate(dates[dates.length - 1]);
}

export function latestPostsDate(): string {
  const dates = listPostDates();
  if (dates.length === 0) throw new Error(`No posts in ${POSTS_DIR}`);
  return dates[dates.length - 1];
}

export function flattenChronological(byAgent: PostsByAgent): Post[] {
  const all: Post[] = [];
  for (const arr of Object.values(byAgent)) {
    for (const p of arr) all.push(p);
  }
  return all.sort((a, b) => a.post_at.localeCompare(b.post_at));
}

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

function replaceFirst(
  haystack: string,
  needle: string,
  replacement: string
): { out: string; found: boolean } {
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
