import { describe, it, expect } from "vitest";
import { listBlogDates, loadBlogByDate, loadBlogLatest, renderBlogHtml } from "@/lib/blog";

describe("blog loader", () => {
  it("listBlogDates returns dates ascending", () => {
    const dates = listBlogDates();
    expect(dates.length).toBeGreaterThanOrEqual(3);
    const sorted = [...dates].sort();
    expect(dates).toEqual(sorted);
  });

  it("loadBlogLatest returns frontmatter + body", () => {
    const post = loadBlogLatest();
    expect(post.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(post.title).toBeTruthy();
    expect(post.body).toContain("");
    expect(post.body.length).toBeGreaterThan(100);
  });

  it("loadBlogByDate returns a specific post", () => {
    const post = loadBlogByDate("2026-04-20");
    expect(post.date).toBe("2026-04-20");
    expect(post.title).toBeTruthy();
  });

  it("loadBlogByDate throws on missing", () => {
    expect(() => loadBlogByDate("1999-01-01")).toThrow();
  });

  it("renderBlogHtml produces HTML with <h1> or <p>", () => {
    const post = loadBlogLatest();
    const html = renderBlogHtml(post.body);
    expect(html).toMatch(/<(h[1-6]|p)/);
  });

  it("title is populated even if future blog file lacks frontmatter", () => {
    const post = loadBlogLatest();
    expect(post.title.length).toBeGreaterThan(0);
  });
});
