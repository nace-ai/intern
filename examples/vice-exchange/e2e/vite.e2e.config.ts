// Vite config used ONLY by the end-to-end harness (e2e/run.ts).
// It reuses the web app's own config and changes four things:
//   - the port (E2E_WEB_PORT); the proxy target comes from VICE_SERVER, which the app config reads itself,
//   - host 127.0.0.1, so the harness dev server is not exposed on the LAN,
//   - a private dependency cache, so starting it never re-optimizes the cache other running dev servers use,
//   - no hot module reload: other people edit apps/web while the harness runs, and a hot update in the middle
//     of a scenario would reset the page under test. Every page load still gets the current source.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, mergeConfig } from "vite";
import base from "../apps/web/vite.config.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.E2E_WEB_PORT ?? 5231);

export default mergeConfig(base, defineConfig({
  root: path.resolve(here, "../apps/web"),
  cacheDir: path.resolve(here, "out/.vite-cache"),
  clearScreen: false,
  server: { port, strictPort: true, host: "127.0.0.1", hmr: false },
  // The 3D city is lazy-loaded; pre-bundle its dependencies so the first 2D -> 3D switch cannot trigger a page reload.
  optimizeDeps: {
    include: [
      "react", "react-dom", "react-dom/client", "zustand", "socket.io-client", "three", "@react-three/fiber", "@react-three/drei",
      "three/examples/jsm/postprocessing/EffectComposer.js", "three/examples/jsm/postprocessing/RenderPass.js",
      "three/examples/jsm/postprocessing/UnrealBloomPass.js", "three/examples/jsm/postprocessing/OutputPass.js",
    ],
  },
}));
