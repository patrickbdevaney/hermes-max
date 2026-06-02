import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed dev port and no auto-clear so its CLI can attach.
// The shell is a tiny SPA; the heavy lifting is the embedded web UI served by
// the Python backend, which Studio points a separate webview at.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: { target: "es2021", outDir: "dist", emptyOutDir: true },
});
