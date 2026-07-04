import { describe, it, expect } from "vitest";
import { sanitizeLlmHtml } from "@/lib/sanitize";
import { renderMemoryHtml } from "@/lib/memory";
import { renderBlogHtml } from "@/lib/blog";

describe("sanitizeLlmHtml", () => {
  it("strips <script> tags and content", () => {
    const input = "<p>Hello</p><script>alert(1)</script>";
    const out = sanitizeLlmHtml(input);
    expect(out).not.toContain("<script");
    expect(out).not.toContain("alert(1)");
  });

  it("strips onerror event handler on img", () => {
    const input = '<img src="x" onerror="alert(1)">';
    const out = sanitizeLlmHtml(input);
    expect(out).not.toContain("onerror");
    expect(out).not.toContain("alert(1)");
    expect(out).not.toContain("<img");
  });

  it("strips javascript: href", () => {
    const input = '<a href="javascript:alert(1)">click</a>';
    const out = sanitizeLlmHtml(input);
    expect(out).not.toContain("javascript:");
    expect(out).not.toContain("alert(1)");
  });

  it("preserves bold text", () => {
    const input = "<p>This is <strong>important</strong>.</p>";
    const out = sanitizeLlmHtml(input);
    expect(out).toContain("<strong>important</strong>");
  });

  it("preserves safe links with http/https", () => {
    const input = '<p>See <a href="https://example.com">here</a>.</p>';
    const out = sanitizeLlmHtml(input);
    expect(out).toContain('href="https://example.com"');
  });

  it("preserves ordered and unordered lists", () => {
    const input = "<ul><li>Alpha</li><li>Beta</li></ul><ol><li>One</li></ol>";
    const out = sanitizeLlmHtml(input);
    expect(out).toContain("<ul>");
    expect(out).toContain("<li>Alpha</li>");
    expect(out).toContain("<ol>");
    expect(out).toContain("<li>One</li>");
  });

  it("strips data: URIs in href", () => {
    const input = '<a href="data:text/html,<script>alert(1)</script>">x</a>';
    const out = sanitizeLlmHtml(input);
    expect(out).not.toContain("data:");
    expect(out).not.toContain("alert(1)");
  });

  it("strips unknown tags like <iframe>", () => {
    const input = '<iframe src="https://evil.example.com"></iframe><p>Safe</p>';
    const out = sanitizeLlmHtml(input);
    expect(out).not.toContain("<iframe");
    expect(out).toContain("<p>Safe</p>");
  });
});

describe("renderMemoryHtml XSS safety", () => {
  it("does not pass raw script tags through to HTML output", () => {
    const md = "# Journal\n\n<script>alert(1)</script>\n\nSome **bold** text.";
    const html = renderMemoryHtml(md);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("alert(1)");
    expect(html).toContain("<strong>bold</strong>");
  });

  it("strips img onerror in memory markdown", () => {
    const md = 'Notes: <img src=x onerror=alert(1)> done.';
    const html = renderMemoryHtml(md);
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("alert(1)");
  });

  it("strips javascript: href in memory markdown links", () => {
    const md = '[click me](javascript:alert(1))';
    const html = renderMemoryHtml(md);
    expect(html).not.toContain("javascript:");
  });
});

describe("renderBlogHtml XSS safety", () => {
  it("does not pass raw script tags through to HTML output", () => {
    const md = "# Dispatch\n\n<script>alert(1)</script>\n\nA **bold** headline.";
    const html = renderBlogHtml(md);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("alert(1)");
    expect(html).toContain("<strong>bold</strong>");
  });

  it("strips img onerror in blog markdown", () => {
    const md = 'Summary: <img src=x onerror=alert(1)> end.';
    const html = renderBlogHtml(md);
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("alert(1)");
  });

  it("strips javascript: href in blog markdown links", () => {
    const md = '[click](javascript:alert(1))';
    const html = renderBlogHtml(md);
    expect(html).not.toContain("javascript:");
  });
});
