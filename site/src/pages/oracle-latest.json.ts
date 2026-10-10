import type { APIRoute } from "astro";
import { jsonResponse } from "@/lib/json-response";
import { loadBlogLatest, excerpt } from "@/lib/blog";

export const prerender = true;

export const GET: APIRoute = ({ site }) => {
  try {
    // astro.config sets `site`; without it there is no canonical host to link.
    if (!site) throw new Error("astro.config has no `site`");
    const post = loadBlogLatest();
    const payload = {
      date: post.date,
      title: post.title,
      slug: post.slug,
      excerpt: excerpt(post.body, 320),
      permalink: new URL(`/journal/${post.date}`, site).href,
      generated_at: new Date().toISOString(),
    };
    return jsonResponse(payload);
  } catch {
    // Literal JSON null: what the consumer's own fallback expects.
    return jsonResponse(null);
  }
};
