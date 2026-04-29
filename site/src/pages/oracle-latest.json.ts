import type { APIRoute } from "astro";
import { loadBlogLatest, excerpt } from "@/lib/blog";

export const prerender = true;

export const GET: APIRoute = () => {
  try {
    const post = loadBlogLatest();
    const payload = {
      date: post.date,
      title: post.title,
      slug: post.slug,
      excerpt: excerpt(post.body, 320),
      permalink: `https://midas.revah.paris/journal/${post.date}`,
      generated_at: new Date().toISOString(),
    };
    return new Response(JSON.stringify(payload, null, 2), {
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  } catch {
    return new Response("null", {
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  }
};
