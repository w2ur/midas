import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://midas.revah.paris",
  output: "static",
  build: { format: "directory" },
  trailingSlash: "ignore",
});
