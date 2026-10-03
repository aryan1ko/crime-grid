import { defineConfig } from 'vite';

// The backend runs on :8000. We proxy /api and /ws in dev so the frontend can
// use same-origin paths (no CORS, and it also works when the built site is
// served by the backend itself).
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
});
