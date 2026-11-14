import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The .env with NACE_API_KEY lives at the repo root; loadEnv reads it without the VITE_ prefix,
// so the key only ever reaches this proxy config and never client code.
const env = loadEnv(process.env.NODE_ENV ?? "development", path.resolve(import.meta.dirname, ".."), "");
const naceKey = env.NACE_API_KEY;

if (!naceKey) {
  console.warn("⚠️  NACE_API_KEY is not set (repo root .env). The /nace proxy will send no auth header.");
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/nace": {
        target: "https://console.nace.ai",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/nace/, "/v1"),
        headers: {
          Authorization: `Bearer ${naceKey}`,
        },
      },
    },
  },
});
