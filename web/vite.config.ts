import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: `bun run dev` on :5173, proxying the Flask API on :8765.
// Prod: `bun run build` → dist/, which Flask serves directly.
const backend = process.env.TUBEGRAB_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    proxy: {
      "/api": backend,
      "/media": backend,
      "/thumb": backend,
      "/playlist-cover": backend,
      "/collection-cover": backend,
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
