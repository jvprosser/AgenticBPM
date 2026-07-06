import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + health calls to the FastAPI backend so the frontend
// can use same-origin relative URLs in both dev and the packaged CML build.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
