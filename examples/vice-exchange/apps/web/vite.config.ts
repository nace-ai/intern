import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The authoritative game server. The browser only ever talks to the Vite origin;
// these proxies keep everything same-origin (also for a second laptop on the LAN).
const SERVER = process.env.VICE_SERVER ?? "http://localhost:3001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    host: true,
    proxy: {
      "/socket.io": { target: SERVER, ws: true, changeOrigin: true, xfwd: true },
      "/api": { target: SERVER, changeOrigin: true, xfwd: true },
      "/health": { target: SERVER, changeOrigin: true },
    },
  },
});
