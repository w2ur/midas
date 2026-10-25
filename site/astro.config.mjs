import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

export default defineConfig({
  site: "https://midas.revah.paris",
  output: "static",
  // Astro 5's lossless whitespace compression. Astro 7 defaults to "jsx", which
  // drops the line break between text and an expression or an inline element
  // and printed "honesty.42 entries" on the changelog.
  compressHTML: true,
  build: { format: "directory" },
  trailingSlash: "ignore",
  integrations: [sitemap()],
  // /about was folded into /methodology (2026-06-14). Keep old/external links alive.
  redirects: {
    "/about": "/methodology",
  },
});
