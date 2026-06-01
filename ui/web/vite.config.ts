import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the built assets load no matter what path the stdlib server
// (or the Tauri shell) serves index.html from. Build output → dist/, which the
// `hm` backend serves directly.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: {
    // `npm run dev` proxies API+SSE to a locally-running `hm ui` backend so the
    // frontend can be developed with HMR against real data.
    proxy: {
      "/api": { target: "http://127.0.0.1:7080", changeOrigin: true },
    },
  },
});
