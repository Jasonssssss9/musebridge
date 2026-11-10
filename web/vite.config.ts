import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The browser always calls relative /api URLs (contract 1.1). In live mode the
// dev server forwards them to the Go API and keeps the browser's Origin header,
// which the Go API checks against PUBLIC_BASE_URL. /healthz and /readyz are
// deliberately not proxied.
// `vite --mode mock` (npm run dev) switches the app to the in-browser mock API.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  define: { 'import.meta.env.VITE_API_MODE': JSON.stringify(mode === 'mock' ? 'mock' : 'live') },
  server: {
    host: true, // listen on the LAN so a phone can reach it
    port: 8080,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
        timeout: 15000,
        proxyTimeout: 15000,
      },
    },
  },
}));
