import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The console calls /api/...; in development Vite forwards those calls to FastAPI.
    proxy: { "/api": "http://127.0.0.1:8001" },
  },
  test: { environment: "jsdom" },
});
