import * as fs from "node:fs";
import * as path from "node:path";
import matter from "gray-matter";
import { marked } from "marked";
import { BLOG_DIR } from "./paths";
import { sanitizeLlmHtml } from "./sanitize";

export type BlogPost = {
  date: string;
  title: string;
  slug: string;
  body: string;
};

const DATE_RE = /^(\d{4}-\d{2}-\d{2})\.md$/;

export function listBlogDates(): string[] {
  return fs
    .readdirSync(BLOG_DIR)
    .map((f) => {
      const m = f.match(DATE_RE);
      return m ? m[1] : null;
    })
    .filter((d): d is string => d !== null)
    .sort();
}

export function loadBlogByDate(date: string): BlogPost {
  const file = path.join(BLOG_DIR, `${date}.md`);
  if (!fs.existsSync(file)) {
    throw new Error(`Blog post not found: ${file}`);
  }
  const raw = fs.readFileSync(file, "utf-8");
  const parsed = matter(raw);
  const fmTitle = typeof parsed.data.title === "string" ? parsed.data.title : "";
  const fmSlug = typeof parsed.data.slug === "string" ? parsed.data.slug : "";
  const body = parsed.content;

  let title = fmTitle;
  if (!title) {
    const h1 = body.match(/^#\s+(.+)$/m);
    title = h1 ? h1[1].trim() : `Dispatch ${date}`;
  }
  const slug = fmSlug || date;

  return { date, title, slug, body };
}

export function loadBlogLatest(): BlogPost {
  const dates = listBlogDates();
  if (dates.length === 0) throw new Error(`No blog posts in ${BLOG_DIR}`);
  return loadBlogByDate(dates[dates.length - 1]);
}

export function renderBlogHtml(markdown: string): string {
  const raw = marked.parse(markdown, { async: false }) as string;
  return sanitizeLlmHtml(raw);
}

export function excerpt(body: string, maxChars = 220): string {
  const stripped = body
    .replace(/^#+\s.*$/gm, "")
    .replace(/\*\*|__|\*|_|`/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .trim();
  const firstPara = stripped.split(/\n\s*\n/).find((p) => p.trim().length > 0) ?? "";
  if (firstPara.length <= maxChars) return firstPara.trim();
  return firstPara.slice(0, maxChars).trim() + "…";
}
