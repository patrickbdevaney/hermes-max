import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Tauri expects a fixed dev port and no auto-clear so its CLI can attach.
// `@webui` aliases the web UI's source so the shell renders the run view from
// the SAME components (Phase 3.1 shared library — not a fork).
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  resolve: {
    alias: { "@webui": fileURLToPath(new URL("../ui/web/src", import.meta.url)) },
  },
  build: { target: "es2021", outDir: "dist", emptyOutDir: true },
});
