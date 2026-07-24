import rss from "@astrojs/rss";
import type { APIContext } from "astro";
import { listBlogDates, loadBlogByDate, excerpt } from "@/lib/blog";

export const prerender = true;

export function GET(context: APIContext) {
  const dates = listBlogDates();
  const items = dates
    .slice()
    .reverse()
    .map((date) => {
      const post = loadBlogByDate(date);
      return {
        title: post.title,
        description: excerpt(post.body, 320),
        pubDate: new Date(`${post.date}T00:00:00Z`),
        link: `/journal/${post.date}`,
      };
    });

  return rss({
    title: "Midas — The Oracle",
    description: "Daily dispatches from the Oracle, narrating ten AI agents running real-market paper simulations.",
    site: context.site ?? "https://midas.revah.paris",
    items,
    customData: `<language>en</language>`,
  });
}
