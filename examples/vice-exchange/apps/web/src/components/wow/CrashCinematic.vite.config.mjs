// Dev-only Vite config for the CrashCinematic preview page. Not used by the app or the build.
//   cd apps/web && npx vite --config src/components/wow/CrashCinematic.vite.config.mjs
//   open http://127.0.0.1:5617/src/components/wow/CrashCinematic.preview.html
// It reuses the web app's config and changes only: the port, host 127.0.0.1, a private dependency cache (so
// starting it never re-optimizes the cache other running dev servers share) and no hot reload.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, mergeConfig } from "vite";
import base from "../../../vite.config.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "../../..");

export default mergeConfig(base, defineConfig({
  root: web,
  cacheDir: process.env.CRASH_PREVIEW_CACHE ?? path.resolve(web, "node_modules/.vite-crash-preview"),
  clearScreen: false,
  server: { port: Number(process.env.CRASH_PREVIEW_PORT ?? 5617), strictPort: true, host: "127.0.0.1", hmr: false },
}));
