import path from "path";

import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vitest/config";

// Local Django (runserver). Override with VITE_DEV_PROXY_TARGET=http://127.0.0.1:8000
const devApiTarget =
  process.env.VITE_DEV_PROXY_TARGET || "http://127.0.0.1:8000";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: "/",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
    // Prefer TypeScript sources over stale committed .js siblings (same basename).
    extensions: [".ts", ".tsx", ".mts", ".mjs", ".js", ".jsx", ".json"],
  },
  build: {
    target: "es2020",
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        // Only split libs needed on every authenticated page. Do NOT catch-all
        // node_modules into "vendor" — that forces a huge modulepreload on first paint.
        // Heavy libs (recharts, codemirror, etc.) stay with their lazy route chunks.
        manualChunks(id) {
          const n = id.replace(/\\/g, "/");
          if (!n.includes("/node_modules/")) return;
          if (
            n.includes("/@chakra-ui/") ||
            n.includes("/@emotion/") ||
            n.includes("/framer-motion/")
          ) {
            return "chakra";
          }
          if (n.includes("/@tanstack/")) {
            return "query";
          }
          if (n.includes("/axios/")) {
            return "http";
          }
          if (
            n.includes("/react-dom/") ||
            n.includes("/react-router/") ||
            n.includes("/scheduler/") ||
            /\/node_modules\/react\//.test(n)
          ) {
            return "react-vendor";
          }
        },
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
  server: {
    proxy: {
      // Same-origin /api → Django: avoids CORS on http://localhost:5173 → http://127.0.0.1:8000
      "/api": {
        target: devApiTarget,
        changeOrigin: true,
      },
    },
  },
});
