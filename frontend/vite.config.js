import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "prompt",
      includeAssets: ["cup.svg"],
      manifest: {
        name: "Beans&Beats · Christmas Together",
        short_name: "Beans&Beats",
        description: "Christmas Together 2026 booth orders",
        theme_color: "#164b3a",
        background_color: "#f8f6ef",
        display: "standalone",
        icons: [
          {
            src: "/cup.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,woff2}"],
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/docs/,
          /^\/openapi.json/,
          /^\/health/,
        ],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        // API data is stored explicitly in IndexedDB and purged on day reset.
        runtimeCaching: [],
      },
    }),
  ],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
  build: { target: ["es2020", "safari15"] },
});
