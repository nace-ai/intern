import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Public demo through a Cloudflare quick tunnel (trycloudflare.com). Separate from vite.config.ts so the
// local dev server on 5173 is untouched. Run: npx vite --config vite.tunnel.config.ts
// then: cloudflared tunnel --url http://localhost:5180
// Visitors are never the demo director: the free director slot is only for a browser on this machine at localhost.
const SERVER = process.env.VICE_SERVER ?? "http://localhost:3001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    host: true,
    allowedHosts: [".trycloudflare.com"],
    hmr: { clientPort: 443 },
    proxy: {
      "/socket.io": { target: SERVER, ws: true, changeOrigin: true, xfwd: true },
      "/api": { target: SERVER, changeOrigin: true, xfwd: true },
      "/health": { target: SERVER, changeOrigin: true },
    },
  },
});
