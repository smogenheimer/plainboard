import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` proxies the API (incl. WebSocket) to a running app on :8080.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    chunkSizeWarningLimit: 1000, // single local app; one bundle is fine
    // The app, and the served doc sites (#118): the server fills docs.html with the page (app/plainboard/api/docs_public.py).
    rollupOptions: { input: { main: "index.html", docs: "docs.html" } },
  },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8080", ws: true }, // the Host stays: the app checks Origin against it
      "/preview": { target: "http://localhost:8080", ws: true },
    },
  },
});
