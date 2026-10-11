import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build goes straight into the backend's static folder; FastAPI serves it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/static", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8400" } },
});
