import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
  plugins: [tailwindcss()],
  // Tailwind runs through its Vite plugin; no external PostCSS config is needed.
  // Avoid package.json discovery and cached parse failures during file rewrites.
  css: { postcss: { plugins: [] } },
  server: {
    host: "127.0.0.1",
    watch: { usePolling: true, interval: 300 },
    fs: {
      deny: [".env", ".env.*", "**/.private/**", "**/server/**", "**/*.pem"],
    },
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
});
