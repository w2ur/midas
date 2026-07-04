import sanitizeHtml from "sanitize-html";

/**
 * Sanitize LLM-authored HTML before it is injected via set:html.
 *
 * Tight allowlist: only safe structural/formatting tags and a[href]
 * restricted to http/https/mailto schemes. All scripts, event handlers,
 * data URIs, and unknown attributes are stripped.
 */
export function sanitizeLlmHtml(html: string): string {
  return sanitizeHtml(html, {
    allowedTags: ["p", "br", "em", "strong", "a", "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "code", "pre"],
    allowedAttributes: {
      a: ["href"],
    },
    allowedSchemes: ["http", "https", "mailto"],
    disallowedTagsMode: "discard",
  });
}
