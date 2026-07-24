import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

export default defineConfig({
  site: "https://midas.revah.paris",
  output: "static",
  build: { format: "directory" },
  trailingSlash: "ignore",
  integrations: [sitemap()],
  // /about was folded into /methodology (2026-06-14). Keep old/external links alive.
  redirects: {
    "/about": "/methodology",
  },
});
